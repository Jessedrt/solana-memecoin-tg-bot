# Pulse — Solana early-candidate engine

Pulse discovers young Solana tokens, normalizes evidence by mint, applies a deterministic hard-rug gate, scores surviving candidates, explains the result, alerts Telegram only above the configured threshold, and grades alerts over time.

This is a research/alerting bot, not an auto-buyer. A candidate score is a ranking signal, not a probability or guarantee.

## Architecture

- `pulse/providers/` — isolated Pump.fun, DexScreener, GMGN, Fomo, and Solana adapters with timeouts, retries, rate limits, caching, validation, health, and graceful failure.
- `pulse/models.py` — normalized token, evidence, safety, window, and decision models.
- `pulse/evaluation.py` — hard safety gate, demand quality, acceleration, liquidity/executability, late-pump penalty, and configurable 0–100 scoring.
- `pulse/reasoning.py` — mandatory structured reasoning over normalized evidence. It cannot override a safety failure.
- `pulse/engine.py` — parallel discovery, mint deduplication, staged enrichment, conflict detection, and evaluation.
- `serverless.py` — Redis history/evaluations/alerts/performance, QStash control, scanner lock, and provider status.
- `api/telegram.py` — `/on`, `/off`, `/status`, `/scan`, and `/performance`.
- `api/scan.py`, `api/setup.py`, `api/health.py` — Vercel endpoints.

## Provider truthfulness

| Provider | Current capability | Status semantics |
|---|---|---|
| Pump.fun | Best-effort new-launch discovery and bonding metadata via its frontend feed | `LIMITED`; Pump.fun does not publish a stable public API guarantee for this feed |
| DexScreener | Secondary token-profile discovery and market/pair enrichment | `ONLINE` only after a successful response |
| Solana RPC | Authoritative mint/freeze authority, supply, and largest-account concentration | `ONLINE` only after a successful RPC response |
| Solana Tracker | Optional creator/bundle/sniper/holder due diligence using the existing keyed integration | Used only when `SOLANA_TRACKER_API_KEY` is set |
| GMGN | Adapter exists; no supported public API is configured | `UNAVAILABLE`; no synthetic data |
| Fomo | Adapter exists; no supported public API is configured | `UNAVAILABLE`; no synthetic data |

Unavailable safety information is `UNKNOWN`, never `PASS`. Critical unknowns and provider conflicts cap the score below high-conviction.

## Pipeline

`discover → normalize → deduplicate → enrich → hard rug gate → creator/holder/wallet checks → liquidity/executability → acceleration → structured reasoning → score → deduplicated alert → performance grading`

Safety failures always produce `REJECTED`, regardless of momentum. Default weights are Safety 30, Wallet 20, Momentum 15, Liquidity/Executability 15, Early Entry 10, Social 10. Default classifications are high conviction at 85+, strong watch at 75+, watch at 65+, and no alert below 65.

## Environment

Copy `.env.example`. Required for Vercel operation:

```text
TELEGRAM_BOT_TOKEN          Telegram bot authentication
TELEGRAM_WEBHOOK_SECRET     verifies Telegram webhook deliveries
SCANNER_SECRET              protects /api/scan
SETUP_SECRET                protects /api/setup
UPSTASH_REDIS_REST_URL      durable state
UPSTASH_REDIS_REST_TOKEN    durable state authentication
QSTASH_TOKEN                recurring schedule control
```

Optional:

```text
TELEGRAM_CHAT_ID            fixed destination; otherwise /start registers one
PUBLIC_BASE_URL             explicit production URL
SOLANA_RPC_URL              Solana RPC; defaults to public mainnet-beta
SOLANA_TRACKER_API_KEY      deeper creator/bundle/sniper evidence
```

Every `PULSE_*` threshold and weight is documented in `.env.example`.

## Deploy and operate

1. Deploy the repository to Vercel and configure the required variables.
2. Attach Upstash Redis and QStash.
3. Call `https://YOUR-PROJECT.vercel.app/api/setup?key=YOUR_SETUP_SECRET` once.
4. Send `/start`, then `/status` in Telegram.

`/on` creates or refreshes the QStash schedule and enables the Redis gate. `/off` disables the gate and enumerates/deletes remote QStash schedules targeting `/api/scan`. Scheduled invocations also check the gate before scanning.

## Verification

```bash
python -m compileall -q bot.py serverless.py api pulse
python -m unittest discover -v
```

The tests cover provider failure/retry, normalization and mint deduplication, stale/conflicting evidence, hard rug rejection, unknown safety, creator/holder/bundle/sniper/wallet/wash-trading risks, buyer/volume/liquidity acceleration, late-pump penalty, scoring/safety override, alert deduplication, executable exits, performance records, scheduler on/off, status, and Telegram formatting.

