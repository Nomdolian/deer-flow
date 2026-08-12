# Watchlist — YYYY-MM-DD

> Built 30–45 min before my session opens. **Max 3 instruments, across all classes
> combined.** More = scattered attention = bad entries.

## Today's session plan

- Classes I'm trading today: ☐ Stocks ☐ Crypto ☐ Forex (nano)
- **Session start:** ____ | **Session end (REQUIRED for crypto):** ____
- Trades already taken today: ____ / 3
- Settled cash (equities only): $____ | Available capital: $____

## Market context first

- Index futures: ____ | Direction: ☐ Up ☐ Down ☐ Flat
- DXY / dollar direction: ____ *(drives forex AND metals — one bet, not two)*
- BTC direction & dominance: ____ *(drives most of crypto)*
- Scheduled events (Fed, CPI, jobs, earnings): ____
- Environment: ☐ Trending ☐ Choppy ☐ Unclear
- If unclear → **the honest answer is often "don't trade today."**

## Correlation check — before anything else

If two candidates sit in the same group, **I get one of them, not both.**

| Group | Candidates today | Which one earns the slot |
|---|---|---|
| usd_majors | | |
| crypto_majors | | |
| us_indices | | |
| metals | | |

## Scan filters used

Same criteria every single day, per class. The tool matters less than the consistency.

**Stocks:** price $2–$15 · pre-market volume 500K+ · gap 4%+ · news catalyst required
**Crypto:** top-50 by volume only · 24h volume threshold ____ · spread under ____
**Forex:** majors only (tightest spreads) · nano lots · avoid the pre-London lull

*Sources: TradingView, broker scanner, finviz.com, or `data/live-data.md` for
programmatic quotes across every class.*

---

## Candidates

### 1. SYMBOL ____

- **Asset class:** ☐ Stock ☐ Crypto ☐ Forex ☐ Other: ____
- **`--instrument` flag to use:** ____ *(e.g. stock, crypto-spot, forex-nano)*
- **Correlation group:** ____ | Conflicts with another candidate? ☐ Yes ☐ No
- **Setup:** ____ | **Paper-validated in THIS class?** ☐ Yes ☐ No → *if No, paper only*
- **Catalyst:** ____
- **Is this catalyst the kind that sustains momentum, or fades within the hour?** ____
- Volume: ____ | Gap / 24h move: ____%
- Prior session high / low: ____ / ____
- Key levels: ____
- **Session window:** opens ____ closes ____ | my exit-by time: ____
- **Sizing check** — run it, don't estimate:
  ```
  python3 tools/size.py -i ____ --symbol ____ --entry ____ --stop ____ --target ____
  ```
  Size: ____ | Risk: $____ | Budget used: ____% | ☐ Viable ☐ **Sized to 0 → skip**

### 2. TICKER ____

*(same block)*

### 3. TICKER ____

*(same block)*

---

## Ranking

| Rank | Symbol | Class | Why it earns the spot | Cleanest invalidation |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |

**If #1 is a stock:** T+1 settlement means it's realistically my only trade today.
**If #1 is crypto or forex:** nothing external stops me after it closes. My 3-trade
rule is the only limit, and I am counting manually.

**Is #1 genuinely the best one, or just the most exciting one?** ____

**Am I reaching into a class I can't afford** (metals, futures) because the reachable
ones look boring today? ☐ Yes → **stop** ☐ No

## Bench

Tickers I looked at and rejected — and the rule-based reason why:

- ____
