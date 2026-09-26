# PULSE Memecoin Rules

These rules are enforced in code where data is available. They are not guarantees of profit.

1. **Address first.** A ticker, screenshot, or social post is never treated as identity. The mint address is the canonical token identifier.
2. **Exit before entry.** Every alert must pass an exit-capacity check. PULSE estimates proceeds for $1K, $10K, and $100K sells. For live Pump.fun curves it uses Pump curve reserves; for migrated DEX pools it uses pool liquidity.
3. **Unchecked is unsafe.** Missing core price, market cap, age, liquidity, RugCheck, or Pump curve state blocks actionable alerts.
4. **Pump.fun uses real curve SOL.** Virtual/chart liquidity is not treated as cash you can necessarily withdraw. PULSE records the real SOL reserve for live curves.
5. **Do not chase extension.** Already-exploded short-term moves are penalized for Early Pump detection. Momentum must be accelerating while the token is still relatively early.

## Alert requirements

An actionable alert requires:
- valid Solana mint
- successful market-data enrichment
- known token age
- non-zero price, market cap, and liquidity
- a RugCheck result
- Pump curve verification when applicable
- acceptable $1K estimated exit capacity
- either the normal PULSE score or the Early Pump score threshold

## Early Pump engine

The Early Pump score looks for:
- fresh age
- early market-cap range
- 5-minute volume
- 5-minute buy velocity
- buy/sell imbalance
- short-term price acceleration without extreme extension
- volume-to-market-cap intensity
- rising volume, buys, and liquidity between consecutive scanner observations
- socials
- low RugCheck risk

The score is a signal detector, not a prediction. A high score means the observed conditions match the configured setup; it does not mean a pump is certain.
