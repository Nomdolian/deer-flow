# Instruments — Every Asset Class, One Risk Framework

> The rule "size comes from the stop distance" is universal.
> **How a stop distance becomes a position is not.**
>
> A share moves $1 per $1. An MES contract moves $5 per index point. 1,000 units
> of EURUSD move $0.10 per pip. Silver spot moves in 50-ounce blocks whether you
> like it or not. Apply the equity formula to a futures contract and a trade you
> labelled "1% risk" is a 100% risk trade.
>
> This file is the translation layer. `tools/size.py` holds the same data as code,
> so the math is looked up, never guessed.

---

## The universal math

```
risk budget      = account × risk%
risk per unit    = |entry − stop| × point_value × quote_usd
units by risk    = floor_to_increment(risk budget ÷ risk per unit)
capital per unit = margin_per_unit  OR  notional_per unit ÷ leverage
units by capital = floor_to_increment(available ÷ capital per unit)
UNITS            = min(units by risk, units by capital)
```

Four instrument facts drive everything: **point value**, **minimum increment**,
**capital requirement**, and **session/settlement**. Get them from your broker's
contract specification — not from a forum post, and not from me.

```bash
python3 tools/size.py --list          # every instrument the tool knows
```

---

## What actually fits a $50 account

Computed from the registry at live prices (12 Aug 2026: gold $4,408.55/oz,
silver $66.11/oz, BTC $63,736, EURUSD 1.15399, WTI ~$81.96) using realistic
day-trade stop distances. "Acct @1%" is the account size at which the *smallest
tradeable position* equals a 1% risk.

| Instrument | Example stop | Min size | Risk at min size | Acct @1% | Acct @3% |
|---|---|---|---|---|---|
| `crypto-spot` | BTC $1,236 | 0.00000001 coin | ~$0.00 | **any** | **any** |
| `forex-nano` | EURUSD 20 pip | 100 units | $0.20 | **$20** | $7 |
| `stock` | $0.30 on a $10.50 stock | 1 share | $0.30 | **$30** | $10 |
| `forex-micro` | EURUSD 20 pip | 1,000 units | $2.00 | $200 | $67 |
| `xauusd` (gold spot) | $15 | 1 oz | $15.00 | $1,500 | $500 |
| `mym` (Micro Dow) | 30 pts | 1 contract | $15.00 | $1,500 | $500 |
| `forex-mini` | EURUSD 20 pip | 10,000 units | $20.00 | $2,000 | $667 |
| `xagusd` (silver spot) | $0.50 | 50 oz | $25.00 | $2,500 | $833 |
| `mes` / `mnq` / `mcl` / `mgc` | typical | 1 contract | $50.00 | $5,000 | $1,667 |

### The verdict, stated plainly

**Three classes are reachable at $50: crypto spot, nano-lot forex, and
low-priced stocks.** Everything else needs a bigger account, and no amount of
prompt engineering changes that arithmetic.

- ✅ **Crypto spot** — the best mechanical fit. 8-decimal sizing means the risk
  budget is used *exactly*: 100% versus ~60% on whole shares. No settlement cap,
  no leverage needed.
- ✅ **Forex, nano lots only** — 100-unit increments make a $0.50 budget work
  (~$0.39 risk on a 20-pip stop). Requires a broker offering nano sizing.
- ✅ **Stocks $2–$15** — the original configuration. Works, wastes 40%+ of the
  budget to whole-share rounding, and that waste is correct.
- ❌ **Spot metals** — gold's 1 oz minimum risks $10–15 per trade. Silver is worse
  and for a non-obvious reason: 1 lot = 5,000 oz, so the 0.01-lot minimum is
  **50 ounces**, ~$3,300 notional, $25 risked on a 50-cent stop. The *increment*
  is the blocker, not the price.
- ❌ **Index & commodity futures** — one MES contract on a 10-point stop risks
  $50: the entire account. `mym` (Micro Dow, $0.50/point) is the smallest door in
  and still wants ~$1,500.
- ⚠️ **CFDs** — cover every class at small increments, are unavailable to US
  retail traders, and vary so much between brokers that you must supply
  `--point-value` and `--increment` yourself.
- ⚠️ **Crypto perpetuals** — skip these. Funding costs and liquidation risk stack
  on top of your stop, and at $50 they are the fastest available route to zero.

**Indirect access:** ETFs give metals/commodities/indices exposure with plain
equity mechanics. Check the share price first — many popular ones cost more per
share than a $50 account can risk properly.

**If a trade sizes to zero, the instrument does not fit.** The tool says so and
stops. Do not round up to one unit, do not tighten the stop to make the number
work. Pick a different instrument or skip the day.

---

## Per-class mechanics

### Stocks & ETFs
| | |
|---|---|
| Unit | share, whole numbers |
| Point value | $1.00 per $1.00 |
| Capital | full notional (cash account, no margin below $2,000) |
| Session | 09:30–16:00 ET, Mon–Fri |
| Settlement | **T+1** — the binding constraint |
| Gotchas | rounding waste; sub-$5 names have wide spreads, thin books, halt risk |

### Crypto (spot)
| | |
|---|---|
| Unit | coin, down to 8 decimals |
| Point value | $1.00 per $1.00 |
| Capital | full notional |
| Session | **24/7, weekends included** |
| Settlement | instant — no settlement cap at all |
| Gotchas | no closing bell; exchange minimum order (~$1); weekend liquidity gaps |

### Forex (spot)
| | |
|---|---|
| Unit | units of the **base** currency (100 = nano, 1k = micro, 10k = mini, 100k = standard) |
| Point value | $1.00 per 1.00 of quote per unit — a pip is 0.0001 (0.01 on JPY pairs) |
| Capital | notional ÷ leverage (US ~50:1 majors, EU/UK 30:1 — verify yours) |
| Session | 24/5, Sun 17:00 ET → Fri 17:00 ET |
| Settlement | rolling spot; no good-faith-violation concept |
| Gotchas | **non-USD-quoted pairs need `--quote-usd`** or the risk figure is in the wrong currency; daily rollover/swap; Sunday-open gaps |

### Spot metals
| | |
|---|---|
| Unit | troy ounce |
| Point value | $1.00 per $1.00 per oz |
| Increment | gold 1 oz; **silver 50 oz** |
| Capital | notional ÷ leverage (~20:1 typical) |
| Session | ~23/5, daily break around 17:00 ET |
| Gotchas | the increment, not the price, decides affordability |

### Futures (indices & commodities)
| | |
|---|---|
| Unit | contract, whole numbers |
| Point value | fixed multiplier: MES $5, ES $50, MNQ $2, NQ $20, MYM $0.50, YM $5, MCL $100, CL $1,000, MGC $10, GC $100, NG $10,000, ZC/ZW $50 per cent |
| Capital | broker day-trade margin — **changes, and is not hardcoded here** |
| Session | nearly 23/5 on CME Globex |
| Gotchas | grains quote in **cents** per bushel; contract rollover; margin calls are intraday |

Pass `--margin-per-unit` for futures. Without it the tool sizes on risk alone and
warns loudly that the capital check was skipped — it will not invent a margin
number for you.

---

## Sessions: "flat by close" needs redefining

The original rule assumed a 16:00 ET bell. Across classes:

| Class | Close | The rule becomes |
|---|---|---|
| Stocks/ETFs | 16:00 ET | unchanged — flat by the bell |
| Futures | ~17:00 ET daily halt | flat before the halt; overnight is a different risk profile |
| Forex | Fri 17:00 ET weekly | flat by *your* session end; **never hold into Friday close** (Sunday gaps) |
| Spot metals | ~17:00 ET daily | flat before the break |
| Crypto | **never** | **you must invent the close** |

**Crypto's missing close is the single biggest behavioural hazard in this
extension.** There is no bell to enforce discipline, and "I'll just watch it a
bit longer" runs to 3am. Write a session end time in the journal *before* the
first trade and be flat by it.

---

## Settlement: the guardrail you are about to lose

At $50 in a cash equity account, T+1 settlement capped you at roughly one round
trip per day. **That was accidentally protecting you from overtrading.**

| Class | Settlement | Trades per day |
|---|---|---|
| Stocks/ETFs | T+1 | ~1 round trip — hard limit |
| Forex | rolling | unlimited |
| Futures | margin | unlimited |
| Crypto | instant | unlimited |

Move to crypto or forex and that brake disappears. The **max 3 trades/day** rule
was previously redundant; it is now the *only* thing standing between you and
twenty revenge trades on a Saturday night. It stops being a formality and becomes
the most load-bearing rule in `risk-rules.md`.

---

## Correlation: one bet wearing several hats

Multi-asset makes it easy to take the same position three times and call it
diversification. Long gold + short EURUSD + long DXY is one dollar bet at 3R.

Groups (matching `tools/size.py` and the `trading-system` risk manager):

| Group | Members |
|---|---|
| `usd_majors` | EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF, USDJPY |
| `us_indices` | ES/MES, NQ/MNQ, YM/MYM, RTY/M2K, and their ETFs |
| `metals` | XAUUSD, XAGUSD, GC/MGC, SIL, miners |
| `crypto_majors` | BTC, ETH and the large caps that follow them |
| `energy` | CL/MCL, NG, Brent |
| `grains` | ZC, ZW, ZS |

**Rule: total open risk within one correlation group counts as a single position
against your daily limit.** Altcoins are not a diversifier from BTC — in a
drawdown they are BTC with extra beta.

---

## Leverage: what it does and does not change

Leverage changes the **capital required to hold a position**. It never changes
the risk budget.

$50 at 30:1 on 200 units of EURUSD controls $230.80 of notional using $7.69 of
capital, and risks $0.40 — *provided the stop fills*. That proviso is the whole
story: on a gap or a liquidity hole, a leveraged position can lose more than the
stop implied, and unlike a cash equity position it can lose more than you put in.

- Never size up because leverage "allows" it. The budget is the budget.
- Never treat available leverage as free capital.
- At $50, use leverage only where it is unavoidable to reach a tradeable
  increment (nano forex), never to increase position size.

---

## Adding an instrument

Anything unlisted goes through the generic CFD path with explicit specs:

```bash
python3 tools/size.py --instrument cfd --symbol GER40 \
  --point-value 1.0 --increment 0.01 --leverage 20 \
  --entry 18500 --stop 18450 --target 18600
```

To make it permanent, add an `Instrument(...)` entry to `REGISTRY` in
`tools/size.py` with sourced values, then run `python3 tools/test_size.py` — the
suite asserts across the whole registry that no instrument can ever risk more
than the budget.

---

*Education, not financial advice. Adding asset classes adds ways to lose money,
not an edge. Every setup still needs 30+ paper trades per instrument class before
it goes live.*
