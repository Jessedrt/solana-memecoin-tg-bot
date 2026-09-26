# Solana Memecoin Telegram Scanner

Telegram scanner for early Solana memecoin traction using GeckoTerminal new-pool discovery, RugCheck risk data, and DexScreener market enrichment.

This is a **scanner/alert bot, not an auto-buyer**.

## Vercel architecture

The original bot used a permanent `while True` loop and Telegram `getUpdates`. That works on a VPS, but not reliably on Vercel.

This repo now uses:

- `api/telegram.py` — Telegram webhook
- `api/scan.py` — one bounded scanner invocation
- `api/setup.py` — one-time Telegram webhook + QStash schedule setup
- `api/health.py` — deployment/config health
- `serverless.py` — Upstash Redis state, scan locking and QStash helpers
- `bot.py` — existing discovery/scoring engine and optional local runner

## Required Vercel environment variables

```
TELEGRAM_BOT_TOKEN
TELEGRAM_WEBHOOK_SECRET
SCANNER_SECRET
SETUP_SECRET
UPSTASH_REDIS_REST_URL
UPSTASH_REDIS_REST_TOKEN
QSTASH_TOKEN
```

`TELEGRAM_CHAT_ID` is optional. If empty, `/start` stores the chat ID in Redis.

## Deploy

1. Import this GitHub repo into Vercel.
2. Add the variables in `.env.example`.
3. Attach Upstash Redis and Upstash QStash.
4. Redeploy.
5. Open:

```
https://YOUR-PROJECT.vercel.app/api/setup?key=YOUR_SETUP_SECRET
```

6. In Telegram send:

```
/start
/status
/scan
```

Health route:

```
https://YOUR-PROJECT.vercel.app/api/health
```

## Defaults

- age ≤ 90 minutes
- market cap $4k–$350k
- min Pump.fun replies 2
- min score 55
- duplicate alert cooldown 180 minutes
- scheduled scan every 2 minutes
- max enriched candidates per run 10
- GeckoTerminal new-pool pages per run 2

## Security

Never commit bot tokens, Redis tokens, QStash tokens, wallet seed phrases or private keys.

<!-- vercel-deploy-trigger -->

<!-- deploy-latest-redis-url-support -->

<!-- trigger-after-vercel-git-reconnect -->
