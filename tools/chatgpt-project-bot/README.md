# ChatGPT Project Bot

A small Linux watchdog that keeps a normal ChatGPT project chat moving without using Work mode.

## Behavior

- one persistent systemd daemon keeps the browser open and checks every **1 minute**;
- if ChatGPT is still generating, the bot does nothing;
- as soon as the response is finished, the next check sends the continuation prompt;
- if one generation stays busy for **20 minutes**, the bot clicks **Stop** and sends the continuation prompt anyway;
- it reuses one persistent Chromium profile;
- it tries to keep the ordinary Chat reasoning level at **High** and never intentionally selects Work;
- state survives process restarts in `state.json`;
- a file lock prevents overlapping runs;
- systemd restarts the daemon automatically if Chromium or the bot crashes.

## One-line Linux install

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Zennay/Haxlab/main/install-chatgpt-project-bot.sh)"
```

The installer asks for the project-chat URL. Re-running the installer keeps an existing `config.json`.

## First ChatGPT login

The watchdog itself is headless, but the first ChatGPT login needs a visible browser.

If you use SSH, first connect to the VPS with **NoMachine** and leave that desktop session active. Then you may run this from SSH:

```bash
~/.local/share/chatgpt-project-bot/login.sh
```

`login.sh` automatically searches the current user's processes for an active X11/NoMachine `DISPLAY`, `XAUTHORITY` and D-Bus session. If no graphical session exists, it exits with instructions instead of crashing Playwright.

Log in, open the intended chat in **Chat** (not Work), set reasoning to **High**, then return to the terminal and press Enter. The watchdog service starts automatically.

## Persistence and logs

```bash
sudo loginctl enable-linger "$USER"
systemctl --user status chatgpt-project-bot.service
journalctl --user -u chatgpt-project-bot.service -f
~/.local/share/chatgpt-project-bot/run.sh --status
```

Configuration lives at:

```text
~/.local/share/chatgpt-project-bot/config.json
```
