# Solana Memecoin Telegram Scanner

Self-hosted Telegram bot that watches Pump.fun, RugCheck, and DexScreener for Solana memecoins with early traction and DMs you alerts.

This is a scanner, not an auto-buyer. Most memecoins go to zero.

## Setup

1. Message @BotFather on Telegram, send /newbot, copy the token.
2. Message your bot /start. Get your chat id from @userinfobot.
3. Install and run:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
python bot.py
```

Commands: /start /scan /status /help

Raise MIN_SCORE to 70 for fewer, stronger alerts.
Never put a wallet private key in this project.
