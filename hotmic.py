#!/usr/bin/env python3
import asyncio
import contextlib
import json
import os
import queue
import signal
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

# Runtime deps (install via requirements.txt):
#   websockets, sounddevice, pynput

import websockets
import sounddevice as sd
from pynput import keyboard


@dataclass
class Config:
    endpoint: str
    hotkey: str
    autopaste: bool
    samplerate: int
    channels: int
    block_samples: int  # samples per audio block
    input_device: Optional[int]
    connect_timeout: float
    stop_flush_wait: float


def load_config(path: str = "config.json") -> Config:
    if not os.path.exists(path):
        raise SystemExit(f"config.json not found at {os.path.abspath(path)}. Please create it.")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    required = {
        "endpoint",
        "hotkey",
        "autopaste",
        "samplerate",
        "channels",
        "block_samples",
        "input_device",
        "connect_timeout",
        "stop_flush_wait",
    }
    missing = [k for k in sorted(required) if k not in data]
    if missing:
        raise SystemExit(f"Missing required config keys: {', '.join(missing)}")

    return Config(
        endpoint=data["endpoint"],
        hotkey=data["hotkey"],
        autopaste=bool(data["autopaste"]),
        samplerate=int(data["samplerate"]),
        channels=int(data["channels"]),
        block_samples=int(data["block_samples"]),
        input_device=data.get("input_device"),
        connect_timeout=float(data["connect_timeout"]),
        stop_flush_wait=float(data["stop_flush_wait"]),
    )


class Recorder:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._stream: Optional[sd.RawInputStream] = None
        self._q: "queue.Queue[bytes]" = queue.Queue()
        self._running = threading.Event()
        self._running.clear()
        self._last_chunk_time = 0.0

    def start(self):
        if self._running.is_set():
            return
        self._running.set()

        def callback(indata, frames, time_info, status):
            if status:
                # Non-fatal audio status (overflows/underflows)
                pass
            if not self._running.is_set():
                return
            # indata is bytes since RawInputStream with dtype=int16
            self._q.put(bytes(indata))
            self._last_chunk_time = time.time()

        self._stream = sd.RawInputStream(
            samplerate=self.cfg.samplerate,
            channels=self.cfg.channels,
            dtype="int16",
            blocksize=self.cfg.block_samples,
            callback=callback,
            device=self.cfg.input_device,
        )
        self._stream.start()

    def stop(self):
        self._running.clear()
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
            with contextlib.suppress(Exception):
                self._stream.close()
            self._stream = None

    def get_chunk_nowait(self) -> Optional[bytes]:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None

    def drain_remaining(self, timeout: float = 0.5) -> list[bytes]:
        # Give a brief moment for final callback(s) to enqueue
        time.sleep(timeout)
        chunks: list[bytes] = []
        while True:
            try:
                chunks.append(self._q.get_nowait())
            except queue.Empty:
                break
        return chunks


class Session:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._rx_task: Optional[asyncio.Task] = None
        self._rx_stop = asyncio.Event()
        self.transcript = ""
        self._final_event = asyncio.Event()  # set when status: idle after stop
        self._open = False
        self._awaiting_final = False

    async def connect(self):
        self._final_event.clear()
        self._rx_stop.clear()
        self.transcript = ""
        self.ws = await asyncio.wait_for(
            websockets.connect(self.cfg.endpoint), timeout=self.cfg.connect_timeout
        )
        self._open = True
        self._rx_task = asyncio.create_task(self._receiver())

    async def _receiver(self):
        try:
            async for message in self.ws:
                # Server uses JSON text frames
                try:
                    data = json.loads(message)
                except Exception:
                    continue
                typ = data.get("type")
                if typ == "text":
                    if data.get("isNewResponse"):
                        self.transcript = data.get("content", "")
                    else:
                        self.transcript += data.get("content", "")
                elif typ == "status":
                    # After stop_recording flow completes, server sends 'idle'
                    if data.get("status") == "idle" and self._awaiting_final:
                        self._final_event.set()
                elif typ == "error":
                    # Treat errors as terminal for this utterance
                    self._final_event.set()
        except Exception:
            pass
        finally:
            self._open = False
            self._rx_stop.set()

    async def start_recording(self):
        # Reconnect if no socket or previously closed
        if (self.ws is None) or getattr(self.ws, "closed", True) or (not self._open):
            await self.connect()
        await self.ws.send(json.dumps({"type": "start_recording"}))

    async def send_audio(self, chunk: bytes):
        if self.ws and self._open and chunk:
            await self.ws.send(chunk)

    async def stop_recording(self):
        # The server expects any remaining audio first, then a small delay, then stop message.
        self._awaiting_final = True
        await self.ws.send(json.dumps({"type": "stop_recording"}))

    async def wait_final(self, timeout: float = 20.0):
        try:
            await asyncio.wait_for(self._final_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._awaiting_final = False
            self._final_event.clear()

    async def close(self):
        if self.ws:
            with contextlib.suppress(Exception):
                await self.ws.close()
        if self._rx_task:
            self._rx_task.cancel()
            with contextlib.suppress(Exception):
                await self._rx_task
        self._open = False
        self.ws = None
        self._rx_task = None
        self._awaiting_final = False


class HotMic:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.rec = Recorder(cfg)
        self.loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()
        self._sending_task: Optional[asyncio.Future] = None
        self._sess: Optional[Session] = None
        self._active = False
        self._kb_controller = keyboard.Controller()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _call_soon(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _ensure_session(self) -> Session:
        if not self._sess:
            self._sess = Session(self.cfg)
        return self._sess

    def start(self):
        if self._active:
            return
        self._active = True
        print("[hotmic] start recording …")
        self.rec.start()
        sess = self._ensure_session()
        # Connect and send start
        self._call_soon(sess.start_recording()).result()

        # Spawn sender
        self._sending_task = self._call_soon(self._sender_loop())

    async def _sender_loop(self):
        assert self._sess
        try:
            while self._active:
                chunk = self.rec.get_chunk_nowait()
                if chunk is None:
                    await asyncio.sleep(0.01)
                    continue
                await self._sess.send_audio(chunk)
        except Exception:
            pass

    def stop(self):
        if not self._active:
            return
        print("[hotmic] stop recording …")
        self._active = False
        self.rec.stop()

        # Flush any remaining chunks
        remaining = self.rec.drain_remaining(self.cfg.stop_flush_wait)

        async def _finalize():
            assert self._sess
            for c in remaining:
                await self._sess.send_audio(c)
            # server expects small delay before stop
            await asyncio.sleep(0.1)
            await self._sess.stop_recording()
            await self._sess.wait_final(timeout=30.0)
            text = self._sess.transcript.strip()
            # Close session so next start is clean
            await self._sess.close()
            return text

        fut = self._call_soon(_finalize())
        text = fut.result()
        if text:
            self._to_clipboard(text)
            if self.cfg.autopaste:
                self._paste_keystroke()
        else:
            print("[hotmic] (no transcript received)")

    def _to_clipboard(self, text: str):
        try:
            p = os.popen("pbcopy", "w")
            p.write(text)
            p.close()
            print("[hotmic] copied transcript to clipboard")
        except Exception as e:
            print(f"[hotmic] failed to copy to clipboard: {e}")

    def _paste_keystroke(self):
        try:
            self._kb_controller.press(keyboard.Key.cmd)
            self._kb_controller.press('v')
            self._kb_controller.release('v')
            self._kb_controller.release(keyboard.Key.cmd)
            print("[hotmic] pasted at cursor")
        except Exception as e:
            print(f"[hotmic] failed to paste: {e}")

    def shutdown(self):
        with contextlib.suppress(Exception):
            self.stop()
        if self._sess:
            self._call_soon(self._sess.close()).result(timeout=5)
        with contextlib.suppress(Exception):
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self._loop_thread.is_alive():
            self._loop_thread.join(timeout=1)

    # Single-key toggle support
    def toggle(self):
        if self._active:
            self.stop()
        else:
            self.start()

    # Safe wrappers to avoid crashing hotkey listener on exceptions
    def safe_toggle(self):
        try:
            self.toggle()
        except Exception as e:
            print(f"[hotmic] toggle error: {e}")

    def safe_start(self):
        try:
            self.start()
        except Exception as e:
            print(f"[hotmic] start error: {e}")

    def safe_stop(self):
        try:
            self.stop()
        except Exception as e:
            print(f"[hotmic] stop error: {e}")


def parse_hotkey(hotkey: str):
    # pynput GlobalHotKeys uses strings such as '<cmd>+<alt>+r'
    return hotkey


def main():
    cfg = load_config()
    hotmic = HotMic(cfg)

    # Hotkeys
    bindings = {parse_hotkey(cfg.hotkey): lambda: hotmic.safe_toggle()}

    print("Hotkey:")
    print(f"  toggle: {cfg.hotkey}")
    print(f"endpoint: {cfg.endpoint}")
    print("Press the start hotkey to begin recording.")

    listener = keyboard.GlobalHotKeys(bindings)

    # Handle Ctrl+C and SIGTERM gracefully
    def _sig_handler(signum, frame):
        print("\n[hotmic] exiting…")
        with contextlib.suppress(Exception):
            listener.stop()
        hotmic.shutdown()
        sys.exit(0)

    for s in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(Exception):
            signal.signal(s, _sig_handler)

    with listener:
        listener.join()


if __name__ == "__main__":
    main()
