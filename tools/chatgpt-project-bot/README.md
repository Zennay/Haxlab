# ChatGPT Project Bot

Linux watchdog that keeps a normal ChatGPT project chat moving without using Work mode.

## Behavior

- Checks the chat every **1 minute** with a systemd user timer.
- If ChatGPT is still generating, it waits.
- As soon as the response is finished, the next minute-check sends the continuation prompt.
- If one generation remains busy for **20 minutes**, it clicks **Stop** and sends the continuation prompt anyway.
- Reuses one persistent Chromium profile.
- Tries to keep ordinary Chat reasoning on **High** and never intentionally selects Work.
- Stores watchdog state in `state.json`.
- Uses a file lock to prevent overlapping runs.

## One-line install

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Zennay/Haxlab/main/install-chatgpt-project-bot.sh)"
```

The installer asks for your project-chat URL.

Non-interactive:

```bash
CHATGPT_CHAT_URL='https://chatgpt.com/c/...' bash -c "$(curl -fsSL https://raw.githubusercontent.com/Zennay/Haxlab/main/install-chatgpt-project-bot.sh)"
```

## One-time login

From your Linux GUI / NoMachine session:

```bash
~/.local/share/chatgpt-project-bot/run.sh --login
```

Log in, open the intended project chat in normal **Chat** (not Work), set reasoning to **High**, then return to the terminal and press Enter.

Keep the user timer alive after SSH logout/reboot:

```bash
sudo loginctl enable-linger "$USER"
```

## Useful commands

```bash
systemctl --user status chatgpt-project-bot.timer
journalctl --user -u chatgpt-project-bot.service -f
~/.local/share/chatgpt-project-bot/run.sh --status
systemctl --user disable --now chatgpt-project-bot.timer
```

Configuration:

```text
~/.local/share/chatgpt-project-bot/config.json
```
