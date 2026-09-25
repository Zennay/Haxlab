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

The installer asks for the project-chat URL. You can also pass it non-interactively:

```bash
CHATGPT_CHAT_URL='https://chatgpt.com/c/...' bash -c "$(curl -fsSL https://raw.githubusercontent.com/Zennay/Haxlab/main/install-chatgpt-project-bot.sh)"
```

Then, once from a Linux GUI/NoMachine session:

```bash
~/.local/share/chatgpt-project-bot/login.sh
```

Log in, open the intended chat in **Chat** (not Work), set the reasoning control to **High**, then return to the terminal and press Enter. `login.sh` then starts the watchdog service automatically.

Keep the user service alive after SSH logout/reboot:

```bash
sudo loginctl enable-linger "$USER"
```

## Commands

```bash
systemctl --user status chatgpt-project-bot.service
journalctl --user -u chatgpt-project-bot.service -f
~/.local/share/chatgpt-project-bot/run.sh --status
systemctl --user disable --now chatgpt-project-bot.service
```

Edit configuration at:

```text
~/.local/share/chatgpt-project-bot/config.json
```
