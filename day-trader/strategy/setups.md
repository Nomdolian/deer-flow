# Setups — Entry & Exit Rules

> **Nothing here is validated yet.** Every setup starts at 0 paper trades and
> stays on paper until it has 30+ logged trades with positive expectancy.
> The structure is filled in; **the specific trigger values are mine to define
> and test.** A setup I copied without testing is not a setup — it's a rumor.

## Validation is per setup AND per asset class

A VWAP reclaim on a $6 stock and a VWAP reclaim on BTC are **not the same setup.**
Different participants, different liquidity, different session structure, different
failure modes. 30 validated stock trades tell you nothing about crypto.

So every setup carries a counter **per class it's traded in**, and each starts at 0.

| Setup | Stocks | Crypto | Forex | Status |
|---|---|---|---|---|
| Gap & Go | 0 / 30 | n/a¹ | n/a¹ | 🔴 Not validated |
| Opening Range Breakout | 0 / 30 | 0 / 30² | 0 / 30² | 🔴 Not validated |
| VWAP Reclaim | 0 / 30 | 0 / 30 | 0 / 30³ | 🔴 Not validated |
| Moving Average Pullback | 0 / 30 | 0 / 30 | 0 / 30 | 🔴 Not validated |
| Session Open Range (24h markets) | n/a | 0 / 30 | 0 / 30 | 🔴 Not validated |

¹ Gap & Go needs an overnight gap, which needs a market that closes. Crypto never
closes, and forex gaps only at the Sunday open — a once-a-week event, not a setup.
² "The open" must be redefined per market: for crypto pick a fixed UTC hour and
test it; for forex use the London or New York session open.
³ Forex VWAP is session-anchored and resets — decide which session anchor you're
using and never mix anchors within one sample.

**🔴 Not validated → paper only. 🟡 30+ trades, expectancy ≤ 0 → keep testing or
retire. 🟢 30+ trades, positive expectancy, adherence >90% → eligible for real money.**

**Classes my account can't reach** (metals, futures — see `instruments.md`) get no
counters. There's no point paper-trading toward a live setup I can't afford to take.

---

## 1. Gap & Go

**Thesis:** a stock gapping up on real news with real volume keeps going in the
first minutes because demand outstrips available supply at the open.

**Scan criteria:**

- Price $5–$50 *(note: at a $50 account, cheaper end only — see sizing)*
- Pre-market volume 500K+
- Gap 4%+ from previous close
- Has an identifiable news catalyst (not a random drift)

**Entry trigger:** break above the pre-market high, **with** volume expanding on
the breaking candle. Not before, not on a quiet drift through the level.

**Stop:** below the pre-market consolidation low. That's where "buyers control
this" is disproven.

**Target 1:** 2R — sell half, move stop to breakeven.
**Target 2:** trail the remainder or exit at the next resistance level.

**Invalid if:** loses the level, volume dies, or the broad market flips red.

**Time stop:** if it hasn't worked in 15 minutes, exit. Capital has better uses.

**Known failure modes:** gap fills immediately at the open; the catalyst is
already priced in; the "volume" was one block print and there's no real interest.

---

## 2. Opening Range Breakout (ORB)

**Thesis:** the first N minutes establish a fair-value range; a decisive break
of that range with volume signals which side won the auction.

**Define the range:** first **15 minutes** (5-min variant is faster but noisier —
test them separately, don't mix results).

**Entry trigger:** break above the opening range high (long) or below the range
low (short), on expanding volume, after the range period is fully complete.

**Stop:** the opposite side of the opening range, or the midpoint for a tighter
version. **Test one, don't switch mid-sample.**

**Target 1:** 1× the range height projected from the breakout point.
**Target 2:** 2× range height, or the next daily level.

**Invalid if:** price closes back inside the range → failed breakout, exit
immediately. Don't wait for the stop.

**Time stop:** 20 minutes.

**Known failure modes:** range too wide → stop is unaffordable at my size;
low-volatility days produce endless false breaks in both directions.

---

## 3. VWAP Reclaim

**Thesis:** VWAP is where the average participant is flat. Losing it and
reclaiming it flips the intraday balance of power.

**Setup conditions:** stock trades below VWAP, then pushes back above it and
**holds** — not a wick through.

**Entry trigger:** first pullback that holds above VWAP after the reclaim.
Enter on the hold, not on the initial cross.

**Stop:** below the pullback low, or below VWAP with a defined buffer.
Buffer must be written down in advance, not eyeballed.

**Target 1:** 2R or the prior swing high, whichever is closer.
**Target 2:** the high of day.

**Invalid if:** price loses VWAP again and stays below it.

**Time stop:** 20 minutes.

**Known failure modes:** chop straight through VWAP repeatedly on low-volume
days — each cross looks like a reclaim and none of them are.

---

## 4. Moving Average Pullback

**Thesis:** in an established intraday trend, a pullback to a moving average is
where trend-followers re-enter and the trend resumes.

**Setup conditions:** clear intraday uptrend (higher highs and higher lows),
price pulls back to the **9 EMA or 20 EMA** *(pick one and test it alone)*.

**Entry trigger:** a reversal candle at the moving average confirming the
pullback is done — **define exactly which candle pattern counts before testing.**

**Stop:** below the pullback low.

**Target 1:** 2R or the prior high.
**Target 2:** trend continuation, trailed by the moving average.

**Invalid if:** price closes decisively below the moving average, or the trend
structure breaks (a lower low prints).

**Time stop:** 20 minutes.

**Known failure modes:** "trend" identified retrospectively on a chart that was
actually ranging; the first pullback in a *reversing* trend looks identical to a
continuation pullback until it isn't.

---

---

## 5. Session Open Range — for 24-hour markets

**Thesis:** even in a market that never closes, liquidity arrives in waves. The
London and New York forex opens, and the US equity open for crypto-by-proxy, bring
real volume into an otherwise thin book, and the range set in that first window
frames the session.

**Why this setup exists:** Gap & Go and the equity ORB both depend on a market that
*closes*. Crypto has no close, so those setups have no anchor. Rather than pretend
they transfer, this is the replacement.

**Setup conditions:** pick **one** fixed session open and test only that one.
Candidates: London 08:00 UTC, New York 13:30 UTC, or a fixed personal 00:00 UTC
boundary for crypto.

**Define the range:** first 30 minutes after the chosen open. Longer than the equity
version because 24-hour markets have no pre-market to establish levels.

**Entry trigger:** break of the range high/low on expanding volume, after the range
window is fully complete.

**Stop:** opposite side of the range.

**Target 1:** 1× range height. **Target 2:** 2× range height.

**Invalid if:** price closes back inside the range, or the "break" happens on the
thin volume between sessions rather than on the open's volume.

**Time stop:** 30 minutes.

**Known failure modes:** the chosen session open isn't actually where volume shows
up for your instrument — verify before testing. Weekend crypto produces range breaks
on almost no volume that look identical to real ones. Testing two session anchors
and pooling the results produces a meaningless sample.

**Paper trades:** crypto 0 / 30, forex 0 / 30

---

## Adapting an existing setup to a new class

Before assuming a setup transfers, answer all five. Any "I don't know" means you're
testing an unvalidated setup, and the counter starts at 0 regardless of how well it
works elsewhere.

1. **Does the anchor exist?** VWAP needs a session anchor; a gap needs a close.
2. **Who's on the other side?** Retail momentum, market makers, and algos leave
   different footprints. A setup that farms one won't farm another.
3. **What's the liquidity profile at my size?** At $50 this rarely binds — but
   spreads on thin crypto pairs and exotic FX crosses can exceed the whole edge.
4. **Does the session structure match?** A "first 5 minutes" rule is meaningless
   in a market with no open.
5. **What replaces the failure mode?** Every setup fails specifically. Write the new
   class's failure modes down *before* testing, not after losing.

## Adding a new setup

Copy this block. Do not trade it until the paper counter hits 30 **for that class.**

```markdown
## [Name]
**Thesis:** why this should work, mechanically
**Asset classes:** which ones, and why it transfers (or doesn't)
**Scan criteria:** what makes an instrument eligible
**Entry trigger:** the exact if-then, no judgment calls
**Stop:** the price that proves this wrong
**Target 1 / Target 2:** with R multiples
**Invalid if:** conditions that void the setup pre-entry
**Time stop:** how long before capital moves on
**Session/close handling:** especially for 24-hour markets
**Known failure modes:** how this loses, per class
**Paper trades:** class A 0 / 30, class B 0 / 30
```
