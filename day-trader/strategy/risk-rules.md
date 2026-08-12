# Risk Rules — The Non-Negotiables

> Day trading survival is 90% risk management. These are not guidelines.
> If a rule here conflicts with something I want to do in the moment,
> **the rule wins.** That is the whole point of writing it down in advance.

---

## The twelve hard rules

- [ ] **Max 1% of account risked per trade.** 3% is an absolute ceiling that
      requires a written reason in the journal — not a default.
      At $50: **$0.50 standard, $1.50 absolute max.**
- [ ] **Daily max loss: 2% ($1.00).** Hit it → close the platform, walk away.
      The market opens again tomorrow.
- [ ] **Two losses in a row = mandatory 30-minute break** away from screens.
- [ ] **Max 3 trades per day** while learning. Overtrading is the #1 account
      killer. *(In equities T+1 caps me at ~1 anyway. In crypto and forex nothing
      caps me but this rule — see "Settlement" below.)*
- [ ] **No trades in the first 5 minutes** after a session open unless a tested
      plan says otherwise. The open is chop — this applies to the equity open, the
      futures open, and the Sunday forex open alike.
- [ ] **Flat by session close** — and on 24-hour markets, flat by **my written
      session end**, decided before the first trade of the day.
- [ ] **Stop-loss order placed IMMEDIATELY after entry.** Real order, not mental.
- [ ] **Never widen a stop. Never average down.** Both are loss-avoidance
      wearing a risk-management costume.
- [ ] **30+ paper trades on any new setup** before a single real dollar —
      **counted per setup AND per asset class.** A VWAP reclaim on a $6 stock and
      a VWAP reclaim on BTC are two different setups with two different counters.
- [ ] **One correlation group = one position.** Total open risk inside a group
      counts once against my daily limit. Long gold + short EURUSD + long an index
      is one dollar bet at 3R, not three diversified trades.
- [ ] **Leverage never increases position size.** It changes the capital required
      to hold a position, nothing else. If leverage is what makes a trade "fit,"
      the trade does not fit.
- [ ] **Spot only.** No crypto perpetuals, no options, no leveraged ETFs. Funding
      and liquidation risk stack on top of my stop and I haven't sized for them.

## The four rules that make the plan work

1. **Stop goes where the setup is WRONG**, not at a pain threshold.
   If I can't articulate what price proves the idea failed, I don't have a setup.
2. **Size comes from the stop distance** — never "how much can I buy."
3. **Size uses the instrument's real contract spec.** Point value and minimum
   increment come from `strategy/instruments.md` or the broker, never from
   assuming a share-like $1-per-$1. This is how a "1% trade" becomes a 100% trade.
4. **Risk:reward under 1:2 = skip the trade.** I don't need every trade;
   I need good ones.

## If it sizes to zero, it does not fit

`tools/size.py` returns 0 when the smallest tradeable increment risks more than my
budget. That is a complete answer, not an obstacle to route around.

- [ ] Never round 0 up to "just one."
- [ ] Never tighten the stop away from invalidation to make the size work.
- [ ] Never raise risk % to reach a class my account can't afford.
- [ ] The correct response is a different instrument, or no trade.

At $50 this rules out spot metals, index futures, and commodity futures entirely.
That isn't the system failing — it's the system telling me the truth about the
size of my account.

---

## Settlement rules — and where they stop applying

**Stocks and ETFs (cash account):**

- [ ] Confirm **settled cash** before every entry. Not account value — settled cash.
- [ ] Never buy with unsettled proceeds. That's a **good-faith violation**.
- [ ] 3 good-faith violations in 12 months → **90-day settled-cash-only restriction**.
- [ ] Assume **one round trip per day.** Plan the day around a single best setup.

**Forex, futures, crypto:** none of the above applies. Settlement is instant or
rolling, so there is **no external limit on how many times I can trade.**

- [ ] Count my trades manually. The platform will not stop me.
- [ ] After trade 3, close the platform regardless of how the day is going.
- [ ] Treat a 24/7 market's availability as a hazard, not an opportunity. The
      market being open at 2am on a Sunday is not a reason to trade at 2am on a
      Sunday.

**The honest risk:** T+1 was doing my discipline for me. Without it, "max 3
trades" is the only brake — and the one rule most likely to be quietly abandoned
on a bad night.

## Session rules per class

- [ ] **Crypto:** write a session end time in the journal *before* the first trade.
      Be flat by it. No exceptions for "it's just about to break out."
- [ ] **Forex:** flat before **Friday 17:00 ET**. Sunday's open gaps and my stop
      does not protect me across it.
- [ ] **Futures:** flat before the daily halt.
- [ ] **Equities:** flat by 16:00 ET.
- [ ] **Any class:** no new position within 15 minutes of my session end. There
      isn't time for the plan to work.

## Leverage rules

- [ ] Leverage sets the **capital required**, never the size. Size comes from the
      stop, always.
- [ ] Never treat unused leverage as spare capital.
- [ ] A leveraged loss can exceed the stop on a gap, and can exceed the account.
      A cash equity position cannot. That asymmetry is the whole reason for these
      rules.
- [ ] Use leverage only where it's unavoidable to reach a tradeable increment
      (nano-lot forex) — never to hold a bigger position.

## Regulatory notes (current as of August 2026)

- The **PDT rule was eliminated June 4, 2026** (FINRA Notice 26-10). No
  designation, no $25K minimum, no 3-per-5-days counting.
- **$2,000 minimum equity** applies to *leveraged* trading. I'm below it →
  cash account, no margin.
- Broker phase-in runs to **October 20, 2027**. My broker may still enforce old
  rules. **Verify with them rather than assuming.**

---

## Escalation ladder — what happens when I break a rule

| Breach | Consequence |
|---|---|
| Widened a stop | Log it. That trade's result is void as evidence — it tells me nothing about the setup. |
| Exceeded daily max loss | No trading tomorrow. Full stop. |
| 3rd rule break in a week | Back to paper for 20 trades. No exceptions. |
| Traded an unvalidated setup with real money | Back to paper for that setup, counter resets to 0. |
| Traded past my written session end | Crypto goes to paper only for a week. This is the rule most likely to slide. |
| Forced a zero-size trade (rounded up, or tightened the stop) | Void the result and re-read `instruments.md`. The account told me the truth and I overrode it. |
| Took a 4th+ trade in a no-settlement class | No trading tomorrow, same as a loss-limit breach. Trade count is the only brake there. |
| Used leverage to size up | Back to paper for 20 trades. This is the failure mode that ends accounts. |

**Claude's job:** when I report a breach, log it plainly and apply the ladder.
Don't soften it, and don't lecture me either — just hold the line and move on.

---

## The pre-trade gate

Every one of these must be YES before entry. Any NO = no trade.

- [ ] The setup is in `setups.md` with written rules
- [ ] The setup has 30+ paper trades logged **in this asset class**
- [ ] I have a written plan with entry, stop, target, size
- [ ] I know the exact price that invalidates this idea
- [ ] Risk:reward is at least 1:2
- [ ] **I used the correct instrument spec** — point value and increment from
      `instruments.md`, not assumed
- [ ] Risk in dollars is ≤ my per-trade limit
- [ ] **Size came back greater than zero** without me tightening the stop
- [ ] I have settled cash / available capital to cover it
- [ ] **No open position in the same correlation group**
- [ ] I have not hit my daily loss limit or trade count
- [ ] It is not the first 5 minutes after a session open
- [ ] **My session end is written down, and this trade has time to work before it**
- [ ] I am not angry, bored, or trying to make back a loss
