# AI Day Trader OS — Multi-Asset

A working implementation of the "AI Day Trader OS" system — Claude as analyst and
discipline coach, never as the finger on the buy button.

**Configured for: $50 cash account · 1% risk ($0.50) default · 3% ceiling ($1.50).**
**Covers stocks, ETFs, forex, spot metals, commodities, indices and crypto** — one
risk framework, seven asset classes, and an honest answer about which of them a
$50 account can actually reach.

## Start here

1. **`CLAUDE.md`** — the operating context. Claude reads this every session.
   Contains the master prompt, my account parameters, and the corrected
   regulatory picture.
2. **`strategy/instruments.md`** — every asset class: contract specs, sessions,
   settlement, and **what fits a $50 account**. Read this before trading anything
   that isn't a share.
3. **`strategy/risk-rules.md`** — the non-negotiables and the pre-trade gate.
4. **`tracking/dashboard.html`** — open in a browser to log trades and see
   whether the process is working, broken out per asset class.

## Daily loop

| When | Do this |
|---|---|
| 30–45 min pre-session | Fill `watchlists/premarket.md` (max 3 instruments **across all classes**), then prompt #4 |
| Pre-session | Write plans into `journal/YYYY-MM-DD.md`. **No plan = no trade.** Write your **session end** if trading crypto. |
| In-trade | Prompt #11 — read the plan back. Never ask for new analysis mid-trade. |
| Post-close | Log to `tracking/dashboard.html`, complete the journal review |
| Weekly | Prompt #32 — process first, split by asset class |

## Position sizing, any asset class

```bash
python3 tools/size.py --list                 # 26 instruments and their specs

python3 tools/size.py --entry 10.50 --stop 10.20 --target 11.10
python3 tools/size.py -i crypto-spot --symbol BTCUSD --entry 63736 --stop 62500
python3 tools/size.py -i forex-nano --symbol EURUSD --entry 1.15399 --stop 1.15199
python3 tools/size.py -i gold --entry 4408.55 --stop 4393.55
python3 tools/size.py -i mes --entry 6800 --stop 6790 --margin-per-unit 50
```

Shows the math, names the binding constraint, flags sub-1:2 risk:reward, and
refuses to pretend a trade fits when it doesn't.

```bash
python3 tools/test_size.py     # 24 tests, no pytest required
```

The suite asserts across the **whole registry** that no instrument can ever risk
more than the budget — the one promise the entire system rests on.

## Why an instrument layer, and not just "add more tickers"

"Size from the stop distance" is universal. **Turning a stop distance into a
position is not.** A share moves $1 per $1; an MES contract moves **$5** per index
point. Size an MES trade with the share formula and a 10-point stop looks like $10
of risk when it is really $50 — a trade labelled 1% that is actually 100% of a $50
account.

So the contract spec — point value, minimum increment, capital requirement,
session, settlement — lives in one registry, used by both the calculator and the
docs, with a test asserting the invariant.

## What a $50 account can actually trade

Computed from the registry at live prices, not assumed. Full table in
`strategy/instruments.md`.

| Class | At $50 | Unlocks at |
|---|---|---|
| **Crypto spot** | ✅ best fit — 8-decimal sizing uses **100%** of the risk budget | any size |
| **Forex, nano lots** | ✅ works — 100-unit increments, ~$0.39 on a 20-pip stop | ~$20 |
| **Stocks $2–$15** | ✅ works, but whole-share rounding wastes ~40% of the budget | ~$30 |
| Forex micro lots | ❌ 1,000-unit minimum risks $2.00 | ~$200 |
| Gold spot / Micro Dow | ❌ 1 oz or 1 contract risks $15 | ~$1,500 |
| Silver spot | ❌ minimum is **50 oz** → $25 risked | ~$2,500 |
| MES / MNQ / MCL / MGC | ❌ one contract risks $50 — the whole account | ~$5,000 |

Three of seven classes are reachable. The tool returns **0** for the rest and says
why, which is the correct answer rather than an obstacle to route around.

The silver case is the instructive one: it's not blocked by the price ($66/oz) but
by the **increment** — 1 lot is 5,000 oz, so the smallest position is 50 ounces.
Always check the increment before the price.

## Three things multi-asset changes about the rules

**1. T+1 was doing your discipline for you.** In a cash equity account, settlement
capped you at ~1 round trip per day. Forex, futures and crypto settle instantly or
roll — **no trade-count limit at all.** The "max 3 trades/day" rule was a formality
in equities; outside them it's the only brake left, and the one most likely to be
abandoned on a bad night.

**2. "Flat by close" needs a close to exist.** Crypto never closes. You must write
a session end time in the journal *before* the first trade and be flat by it.
Forex closes weekly — never hold into Friday 17:00 ET, because Sunday's open gaps.

**3. Validation is per setup *and* per class.** A VWAP reclaim on a $6 stock and one
on BTC are different setups: different participants, liquidity and failure modes.
30 validated stock trades tell you nothing about crypto, so every counter starts at
0 and the dashboard's readiness gate now requires 30+ in a single setup-and-class
pairing rather than 30 scattered across five.

Also new: a **correlation cap**. Long gold + short EURUSD + long an index is one
dollar bet at 3R, not three diversified trades. Total open risk within a
correlation group counts once against the daily limit.

## Two things that differ from the source PDF

**1. The PDT rule is gone.** The PDF says accounts under $25K get 3 day trades
per 5 business days. FINRA eliminated the pattern day trader designation
entirely on **June 4, 2026** (Regulatory Notice 26-10) — no designation, no $25K
minimum, no trade counting. Broker phase-in runs to October 2027, so individual
brokers may still enforce the old rule. Verify with yours.

**2. The real constraint is T+1 settlement.** Below $2,000 there's no margin, so
this is a cash account. Sell proceeds settle the next business day, which caps
you at roughly **one round trip per day** — and reusing unsettled funds is a
good-faith violation. Three in 12 months = 90-day restriction.

## What a $50 account is for

The P&L is noise. One R is about $0.50; a great day makes a dollar. That's not a
flaw in the plan — it's a **real-money paper trading account**, where the
emotions and the order entry are real but being wrong costs less than a coffee.

The dashboard is built to reflect that: **plan-adherence % is the hero metric**,
and dollars are relegated to a footnote. Scaling risk up to make the numbers feel
meaningful is the fastest way to lose the account, and `CLAUDE.md` instructs
Claude to push back if you propose it.

## Structure

```
day-trader/
├── CLAUDE.md                    # operating context + master prompt
├── strategy/
│   ├── instruments.md           # 7 asset classes: specs, sessions, what fits $50
│   ├── setups.md                # 5 setups, unvalidated, per-class counters
│   ├── risk-rules.md            # 12 non-negotiables, escalation ladder, pre-trade gate
│   └── position-sizing.md       # generalized method + worked examples per class
├── journal/_TEMPLATE.md         # daily plan → trades → honest review
├── watchlists/_TEMPLATE.md      # max 3 instruments, correlation + sizing checks
├── prompts/prompt-library.md    # 32 prompts, pre-filled with my numbers
├── data/live-data.md            # quote sources per asset class
├── tracking/
│   ├── trades.csv               # one row per trade, incl. asset_class
│   └── dashboard.html           # adherence, expectancy, per-class breakdown, gate
└── tools/
    ├── size.py                  # multi-asset position size calculator (26 instruments)
    └── test_size.py             # 24 tests; risk ≤ budget for every instrument
```

## Before real money

The dashboard's readiness gate tracks five conditions: 30+ trades overall, **30+ in
a single setup-and-asset-class pairing**, positive expectancy, plan-adherence above
90%, and a drawdown you handled calmly. Four are measured automatically. The fifth
is yours to answer honestly.

---

*Education, not financial advice. Day trading is high-risk, most day traders lose
money, and no system — this one included — changes that math by itself.*
