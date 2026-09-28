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
| Solana Tracker | Required exact-mint graduation and cumulative trading-fee evidence; optional deeper due diligence | Missing key/data blocks both profiles |
| GeckoTerminal | New-pool discovery for 5x; bounded Redis retention until pools reach minimum age | Discovery does not certify graduation |
| RugCheck | Danger risks, total holders and holder/insider concentration proxy | Unknown fields remain unknown |
| Jupiter | 5x quote-only USDC → token → USDC checks at $100/$500/$1,000 | Missing key/route or stale quotes block 5x |
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
TELEGRAM_CHAT_ID            authorized destination (or previously registered Redis chat)
TELEGRAM_WEBHOOK_SECRET     verifies Telegram webhook deliveries
SCANNER_SECRET              protects /api/scan
SETUP_SECRET                protects /api/setup
UPSTASH_REDIS_REST_URL      durable state
UPSTASH_REDIS_REST_TOKEN    durable state authentication
QSTASH_TOKEN                recurring schedule control
SOLANA_TRACKER_API_KEY      graduation and fees.totalTrading verification
```

Optional:

```text
PUBLIC_BASE_URL             explicit production URL
SOLANA_RPC_URL              Solana RPC; defaults to public mainnet-beta
JUPITER_API_KEY             required when selecting the 5x profile
```

Every `PULSE_*` threshold and weight is documented in `.env.example`.

## Graduated-only profiles

Both profiles are retained. `/profile` reports the active profile; `/profile 3x`
and `/profile 5x` persist the selection in Redis for subsequent scans. The
default remains 3x. Changing profile does not start a scan, reset alert history,
or alter an in-progress scan. Historical 3x keys are preserved; 5x gets separate
history, alert deduplication, provider health, and performance-entry keys.

Mandatory for **both** profiles:

- Confirmed `status = graduated`; `graduating`, unknown, a Pump `complete`
  flag, or merely appearing on a DEX is insufficient.
- Fresh DexScreener market cap of at least **$30,000**, with the existing
  **$100,000 absolute alert ceiling** and **200-holder minimum** preserved.
- At least **2 SOL of cumulative trading fees**, using only Solana Tracker's
  `fees.totalTrading`. This is not `fees.total`, priority tips, creator fees,
  or an estimate from volume. Exactly 2 SOL qualifies. Missing/stale/ambiguous
  exact-mint evidence blocks alerts.
- Existing safety, evidence-coverage, anti-chase and momentum gates still apply.

The 5x profile additionally requires liquidity $8K–$80K, observed age 15m–12h,
RugCheck without a Danger risk, verified revoked mint/freeze authority, a
passing observed holder/insider proxy, and fresh two-way Jupiter routes for
all three order sizes. The existing $100K ceiling takes precedence over the
earlier proposed $250K ceiling. Age is earliest observed pool age where a
true token-creation timestamp is unavailable, not time since graduation.

The holder proxy requires ten observed holders with owner and insider fields,
top-ten concentration ≤60%, largest observed owner ≤30%, and observed insider
holdings ≤35%. It does **not** establish independent funding relationships;
the alert labels that limitation. Unidentified custody/LP accounts are not
silently excluded. Conservative false negatives are possible.

5x discovery uses one GeckoTerminal new-pools page per scan and retains up to
1,000 candidates for at most 12 hours. Up to four eligible-age candidates are
rotated per scan. Coverage is bounded, not exhaustive. Quotes are obtained
only after range and safety checks. Jupiter v2 `/order` is called without a
taker: no wallet connection, signing, private key, or trade execution occurs.
Quotes use USDC assuming $1, 1% slippage per leg, and reject >8% impact/round-trip
loss. Network fees are excluded; quotes do not prove a future fill or rule out
all token restrictions. 3x retains its labeled liquidity estimate.

The minimum-fee requirement means the stack is no longer fully keyless:
`SOLANA_TRACKER_API_KEY` is required for both profiles; `JUPITER_API_KEY` is also
required for 5x. Provider plan quotas apply. GMGN remains unavailable/optional;
GoPlus is not used as an EVM honeypot substitute for Solana checks.
Set keys server-side in the appropriate Vercel environment, never in this
repository. Use isolated preview Redis/Telegram credentials for mutation tests.

New/about-to-graduate alert lanes are intentionally disabled by the graduated-only
rule. The webhook requires its secret and a configured or previously registered
authorized chat; unknown chats cannot claim ownership through `/start`.

API contracts: [Tracker search](https://docs.solanatracker.io/data-api/search/token-search),
[Jupiter quote-only order](https://developers.jup.ag/docs/api-reference/swap/order),
[GeckoTerminal](https://apiguide.geckoterminal.com/),
[RugCheck](https://api.rugcheck.xyz/swagger/index.html).

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

