# Position Sizing

> Size comes from the stop distance. Never from "how much can I buy."

## The method — every asset class

```
risk budget      = account × risk %
risk per unit    = |entry − stop| × point_value × quote_usd
units by risk    = floor_to_increment(risk budget ÷ risk per unit)
capital per unit = margin_per_unit  OR  notional per unit ÷ leverage
units by capital = floor_to_increment(available ÷ capital per unit)
UNITS            = min(units by risk, units by capital)
```

The equity version everyone learns is just this with `point_value = 1`,
`increment = 1`, and `leverage = 1`. Three instrument facts generalize it:

| Fact | Stock | BTC spot | EURUSD nano | MES | Gold spot |
|---|---|---|---|---|---|
| **point value** ($ per 1.00 move, per unit) | $1 | $1 | $1 | **$5** | $1 |
| **increment** | 1 share | 0.00000001 | 100 units | 1 contract | 1 oz |
| **capital** | full notional | full notional | notional ÷ 30 | broker margin | notional ÷ 20 |

Run it:

```bash
python3 tools/size.py --entry 10.50 --stop 10.20 --target 11.10
python3 tools/size.py -i crypto-spot --symbol BTCUSD --entry 63736 --stop 62500
python3 tools/size.py -i forex-nano --symbol EURUSD --entry 1.15399 --stop 1.15199
python3 tools/size.py -i mes --entry 6800 --stop 6790 --margin-per-unit 50
python3 tools/size.py --list
```

Flags: `--instrument/-i`, `--symbol`, `--risk-pct` (default 1), `--account`
(default 50), `--cash`, `--target`, `--point-value`, `--increment`, `--leverage`,
`--margin-per-unit`, `--quote-usd`.

**Why point value matters more than anything else here:** a 10-point stop on MES
is not $10 of risk, it's **$50** — the whole account. Sizing it with the share
formula gives 5 contracts and a 500% risk trade that you labelled 1%. That single
substitution is the most expensive mistake available in multi-asset trading, and
it's why the spec lives in code with a test asserting it.

---

## Worked example — the PDF's trade on my account

Entry $10.50, stop $10.20, target $11.10. Risk per share **$0.30**, R:R **1:2**.

| At 1% risk ($0.50) | At 3% ceiling ($1.50) |
|---|---|
| shares by risk = floor(0.50 / 0.30) = **1** | shares by risk = floor(1.50 / 0.30) = **4** |
| Position cost **$10.50** (21% of account) | Position cost **$42.00** (84% of account) |
| Actual risk **$0.30** (0.60%) | Actual risk **$1.20** (2.40%) |
| Profit at target **$0.60** | Profit at target **$2.40** |

Both are correct trades. Both make or lose about the price of a snack. **That is
the honest picture of a $50 account, and it is fine** — the return on this
account is measured in reps and data, not dollars.

## The same 1% budget across four classes

$50 account, 1% risk = **$0.50**, all with a genuine 1:2 setup. Live prices,
12 Aug 2026.

| | Stock $10.50 | BTC $63,736 | EURUSD nano | Gold spot |
|---|---|---|---|---|
| Stop distance | $0.30 | $1,236 | 20 pips | $15 |
| Size | 1 share | 0.00040453 coin | 200 units | **0 oz** |
| Notional | $10.50 | $25.78 | $230.80 | — |
| Capital used | $10.50 | $25.78 | $7.69 | — |
| Actual risk | $0.30 | **$0.50** | $0.40 | — |
| **Budget used** | **60%** | **100%** | **80%** | **0%** |

Read the bottom row. It is the clearest argument in this whole system for *which
class a small account should actually trade*: fractional sizing converts the risk
budget into position at 100% efficiency, whole shares at 60%, and gold not at all.

Note also what leverage did in the forex column: it cut the *capital* from $230.80
to $7.69 and left the *risk* at $0.40. That is the only legitimate use of
leverage — reaching a tradeable increment, not enlarging a position.

## Three things this math will do to me

**1. Whole-share rounding wastes most of my risk budget.**
At 1% on a $10 stock, I get 1 share and risk $0.30 of a $0.50 budget — 60% of it.
The temptation is to widen the stop so the numbers "fit."
**Never do this.** The stop belongs at invalidation. Wasted budget is the correct
outcome; a stop in the wrong place is a broken system.

**2. Higher-priced stocks silently become unaffordable.**
Above roughly $50/share I can't buy a single share. The scanner's "$5–$50" filter
should be **$2–$15** in practice, and the cheap end of that behaves differently:
wider spreads, thinner books, more halts.

**3. A 3% risk trade deploys most of the account.**
$42 of $50 in one position → settled cash near $0 → **no second trade until
tomorrow.** At 3%, plan for exactly one trade per day and make it the best one.

## The affordability rule

For a **cash equity** position (point value 1, increment 1 share):

```
stop distance ÷ entry price  ≥  risk %
```

At 1% risk I need a stop at least 1% away from entry — nearly always true.
At 3% risk I need a stop at least 3% away — often *not* true for tight setups,
and that's when settled cash becomes the binding constraint instead.

**The general form**, which is what actually decides whether any instrument fits:

```
|entry − stop| × point_value × min_increment  ≤  risk budget
```

The minimum *increment* is the term that surprises people. Silver spot isn't
unaffordable because silver is expensive at $66/oz — it's unaffordable because the
smallest position is **50 ounces**, so the cheapest possible silver trade risks
$25. Always check the increment before the price.

## Fourth thing this math will do to me

**4. Every class has a different "smallest possible loss," and it has nothing to
do with the price of the thing.** Ranked by the risk of the minimum position on a
realistic stop: crypto ~$0, nano forex $0.20, a $10 stock $0.30, micro forex $2,
gold $15, silver $25, one MES contract $50. My $0.50 budget draws the line after
the third entry on that list.

## Fractional shares

Some brokers offer them, which removes the rounding waste. **Two cautions:**
fractional orders are often market-only, so my stop-loss may not be placeable as
a real order — and rule one says the stop must be a real order, not mental. If my
broker can't hold a stop on a fractional position, **don't use fractionals.**
Check this before assuming it solves the problem.

## Before every entry

- [ ] Ran the calculator — didn't eyeball it
- [ ] **Used the right `--instrument`** (a wrong point value is a silent 5x error)
- [ ] **Passed `--quote-usd` if the pair isn't USD-quoted**
- [ ] **Passed `--margin-per-unit` if it's a futures contract**
- [ ] Stop is at invalidation, not at a round number or a pain threshold
- [ ] R:R ≥ 1:2 *(calculator warns if not)*
- [ ] Size > 0 *(if 0, the trade doesn't fit — skip it, don't force it)*
- [ ] Available capital covers the requirement
- [ ] No open position in the same correlation group
- [ ] I know my dollar risk and I'm at peace with losing it today
