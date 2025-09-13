# HotMic (macOS): Global hotkey push-to-talk to Brainwave (f.gpty.ai)

A tiny background helper for macOS that:

- Starts/stops mic capture with a single global hotkey (toggle).
- Streams raw PCM16 audio to the author’s public Brainwave server (`wss://f.gpty.ai/api/v1/ws`).
- Copies the transcript to clipboard and (optionally) auto-pastes at the current cursor.
- Can run headless and auto-start at login via LaunchAgent.

This replicates the core experience described in the article without any UI.

## Requirements

- macOS 12+
- Python 3.9+
- Microphone access permission for your terminal/app running the script
- Accessibility permission for auto-paste (if enabled)

## Setup (one-time)

Order at a glance: Setup → First Run → Autostart (optional)

Setup prepares Python deps and config. This is not the login autostart step.

```bash
cd path/to/your/cloned/repo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python hotmic.py
```

Default hotkey:

- Toggle: `<cmd>+u` (press once to start, again to stop)

When you stop, the transcript is copied to the clipboard and—if enabled—auto-pasted with `Cmd+V`.

## Configure

This tool reads all settings from `config.json` (we already have a good default). Edit `config.json` as needed; it must include:

- `endpoint`: e.g. `wss://f.gpty.ai/api/v1/ws` (author’s hosted service)
- `hotkey`: e.g. `"<cmd>+u"` for toggle
- `autopaste`: `true | false`
- `samplerate`, `channels`, `block_samples`, `input_device`
- `connect_timeout`, `stop_flush_wait`

Note: Input is recorded at 48kHz, 16-bit mono, matching the hosted server’s resampler.

## Permissions

- Microphone: Run `hotmic.py` once; macOS prompts for mic access.
- Accessibility (for autopaste): System Settings → Privacy & Security → Accessibility → allow Terminal/iTerm/Your app.

## Autostart (LaunchAgent)

What it is: a macOS LaunchAgent that runs HotMic in the background at login and restarts it if it stops.

Prerequisites: complete Setup and do a First Run once (to grant mic/Accessibility permissions clearly).

Quick start (recommended):

```bash
scripts/install_launchagent.sh
```

Advanced (optional – most users can ignore):

```bash
scripts/install_launchagent.sh com.example.hotmic  # choose a custom label
```

Notes:
- Logs: `~/Library/Logs/<label>.out.log` and `~/Library/Logs/<label>.err.log` (default label is `com.hotmic`).
- Uninstall default: `scripts/uninstall_launchagent.sh`.
- Uninstall custom: `scripts/uninstall_launchagent.sh com.example.hotmic`.
- The generated plist runs: change to your repo dir, then executes `.venv/bin/python hotmic.py` if present, otherwise `python3 hotmic.py`.

Permissions when autostarting (keep it simple):
- System Settings → Privacy & Security:
  - Accessibility: find the existing “python” entry and toggle it ON (needed for auto‑paste).
  - Input Monitoring: find the existing “python” entry and toggle it ON (needed for hotkeys).
- If you don’t see a “python” entry yet, trigger HotMic once or add the path printed by the installer. No need to worry about paths in the common case.

Autostart checklist (no reboot needed):
- Install/start the agent: `scripts/install_launchagent.sh` (or with a custom label).
- Enable the “python” entries in Privacy & Security:
  - Accessibility → toggle ON
  - Input Monitoring → toggle ON
  - If not listed, add the path printed by the installer.
- Reload the agent to apply permissions:
  - Easiest: re-run `scripts/install_launchagent.sh` (it reloads automatically), or
  - Advanced: `launchctl kickstart -k gui/$(id -u)/com.hotmic`
- Verify quickly:
  - `tail -n 20 ~/Library/Logs/com.hotmic.out.log` and look for “Hotkey:” lines, then
  - Press your hotkey to start/stop; text should copy (and paste if enabled).

Why both “permissions” and “launchctl” steps?
- Permissions (Accessibility/Input Monitoring): authorize the background Python process to send Cmd+V and listen for hotkeys. Without these, auto‑paste and hotkeys won’t work when launched at login.
- Launchctl commands: start/stop the LaunchAgent. Use them to reload after changing permissions or config. They do not grant permissions by themselves.

Chinese UI hints (macOS in Chinese):
- 辅助功能：系统设置 → 隐私与安全 → 辅助功能 → 启用列表中的 “python”。若没有，再添加安装脚本打印的路径。
- 输入监控：系统设置 → 隐私与安全 → 输入监控 → 启用列表中的 “python”。若没有，同样手动添加。
- 不需要重启整机：运行 `scripts/install_launchagent.sh` 以重载（或执行 `launchctl kickstart -k gui/$(id -u)/com.hotmic`）。

## Notes

- This uses the author’s public service at `f.gpty.ai` over secure WebSocket; no API key needed.
- If you prefer self-hosting, run the server from https://github.com/grapeot/brainwave and set `endpoint` to your own URL.
- If auto-paste fails, ensure Accessibility is enabled for the “python” entry and the target app is focused.
- If your log shows `This process is not trusted! Input event monitoring will not be possible until it is added to accessibility clients.`, enable “python” under Accessibility and Input Monitoring (add the printed path only if it isn’t listed yet).
- If audio capture is choppy, try increasing `block_samples` (e.g., 48000) in `config.json`.

## Uninstall

```bash
# Default label
scripts/uninstall_launchagent.sh

# Or if you installed with a custom label
scripts/uninstall_launchagent.sh com.example.hotmic
```
