# The Prompt Library

> 32 copy-paste prompts, pre-filled with my actual parameters:
> **$50 cash account, 1% risk ($0.50) default, 3% ceiling ($1.50), 1:2 min R:R.**
> **Reachable classes: crypto spot, nano-lot forex, stocks $2–$15.**
> Paste into a Claude session that has `CLAUDE.md` loaded.

---

## SCAN

**1 — Build scan criteria**
> "Build me a pre-market scanning checklist for [SETUP TYPE] with exact filter criteria I can enter into TradingView's screener. My account is $50 cash, so keep the price filter in the $2–$15 range where I can actually afford shares. Flag any criterion that would surface stocks I can't trade."

**2 — Rank today's gappers**
> "Here are today's pre-market gappers: [PASTE LIST + %]. Rank them against my criteria in `watchlists/_TEMPLATE.md` and tell me which 3 deserve watchlist spots and why. For each, tell me how many shares $50 buys and whether it's actually tradeable at my size."

**3 — Assess the catalyst**
> "What's the news catalyst on [TICKER]? Here's the headline: [PASTE]. Is this the kind of catalyst that sustains momentum or fades by 10am? Explain the mechanism. No predictions."

---

## ANALYZE

**4 — Build the trade plans**
> "For each watchlist ticker [LIST + KEY LEVELS], draft a full trade plan using my template: entry trigger, stop at invalidation, 2R target, and size at 1% risk on $50. Run the numbers through the logic in `tools/size.py`. If a plan sizes to 0 shares, say so plainly instead of adjusting the stop to make it fit."

**5 — Score a setup**
> "Score this setup 1–10 against my rules [PASTE RULES + CHART DESCRIPTION]. List what matches, what's missing, and the strongest reason to skip it."

**6 — Pre-mortem**
> "Play devil's advocate: give me the 3 most likely ways this long fails, and the earliest warning sign for each."

**7 — Environment fit**
> "The market is [CONDITIONS]. Which of my setups in `strategy/setups.md` historically work in this environment, and which should I bench today? Remember none of mine are validated yet — answer in terms of what I should be *testing*, not trading."

---

## PLAN

**8 — Size it**
> "Calculate my share size: account $50, risk 1%, entry $[A], stop $[B]. Show the math. Tell me the position cost, the actual risk after whole-share rounding, and how much settled cash I'd have left."

**9 — Remove judgment calls**
> "Rewrite this trade idea as an if-then plan with zero judgment calls: [IDEA]. Every branch must be a price or a clock, not a feeling."

**10 — Pick the one trade**
> "I realistically get one round trip today because of T+1 settlement. Here are my candidate plans: [PASTE]. Which single plan has the best risk:reward and cleanest invalidation? Argue for your pick, then argue against it."

---

## EXECUTE + MANAGE

**11 — Read my plan back**
> "I'm in [TICKER] at [ENTRY], stop [X], target [Y], now trading at [Z]. Read my plan back to me and tell me what it says to do right now. Do not improvise. Do not give me new analysis."

**12 — Challenge the stop move**
> "I want to move my stop because [REASON]. Challenge me hard — is this risk management or loss avoidance? My rule says stops never widen."

**13 — Shutdown script**
> "I just hit my daily max loss of $1.00. Write me the shutdown script: what to log, what to review, and why walking away preserves my edge. Be brief — I need to close the platform, not read an essay."

**14 — Time stop decision**
> "Volume died on my position. My plan's time stop says [RULE]. Walk me through the exit decision."

---

## REVIEW

**15 — Grade adherence**
> "Here's today's journal: [PASTE]. Grade my plan-adherence per trade and flag every deviation with what it cost or saved. Be specific about which rule was broken and which consequence in `risk-rules.md` applies."

**16 — Find the pattern**
> "Here are my last 30 paper trades: [PASTE LOG]. Which setups, times of day, and market conditions correlate with my wins vs losses? Tell me where the sample is too small to conclude anything."

**17 — Design a guardrail**
> "I keep making this mistake: [PATTERN]. Design one specific process guardrail — a rule, a checklist item, or a friction step — that makes it harder to repeat."

**18 — Expectancy math**
> "My win rate is [X]% and average winner/loser is [Y/Z]. Is this math sustainable? Show the expectancy calculation. Tell me honestly whether my sample size supports the conclusion."

**19 — Weekly review**
> "Write my weekly review: [PASTE STATS + JOURNAL]. Lead with process, not P&L. What's the one thing to fix next week?"

**20 — The graduation test**
> "Am I ready for real money? Here's my paper record: [STATS]. Judge it against: 30+ trades, positive expectancy, plan-adherence above 90%, max drawdown handled calmly. Be brutally honest. If the answer is no, say no."

---

## My additions — prompts the doc didn't include

**21 — The pre-trade gate**
> "Walk me through the pre-trade gate in `risk-rules.md` one item at a time. Ask me each question and wait for my answer. Don't let me skip any."

**22 — Settlement check**
> "I traded this morning and want to trade again. Here's my account state: [PASTE]. Do I have settled cash, or would this be a good-faith violation?"

**23 — The tilt check**
> "I want to take a trade that isn't on my watchlist. Ask me the three questions that will tell us both whether this is opportunity or tilt."

**24 — Reality check on scale**
> "Remind me what my realistic outcome range is on a $50 account today, and what I should actually be optimizing for."

---

## MULTI-ASSET — prompts for everything that isn't a stock

**25 — Can I even trade this?**
> "I want to trade [INSTRUMENT]. Check it against the access table in `strategy/instruments.md`: what's the minimum tradeable size, what does that size risk on a realistic stop, and what account balance would I need for that to be 1%? If my $50 can't reach it, say so with the number and tell me the closest reachable alternative. Do not help me force it."

**26 — Size it in the right units**
> "Size this trade: [INSTRUMENT], entry [A], stop [B], target [C]. Use the correct `--instrument` key from `tools/size.py` — point value and minimum increment from the registry, not assumed. Show me the exact command, the math, the binding constraint, and what % of my risk budget the rounding wastes. If it's a non-USD-quoted FX pair, ask me for the quote rate instead of guessing it."

**27 — The correlation audit**
> "Here are my open positions and today's candidates: [LIST]. Group them by correlation group and tell me where I'm taking the same bet twice. Remember: long gold + short EURUSD + long an index can be one dollar bet at 3R. Which single position expresses the thesis best?"

**28 — Define my session end**
> "I'm trading crypto today, which never closes. Walk me through setting a hard session end time: ask what time I started, when my judgment historically degrades, and what I'll do with an open position at that boundary. Then hold me to the number I give you for the rest of the session."

**29 — The settlement brake check**
> "I'm moving from stocks to [crypto/forex] today. Explain what happens to my trade-count protection when T+1 settlement stops applying, and what I have to do manually to replace it. Be blunt — I'm about to lose a guardrail I didn't know I was relying on."

**30 — Does this setup transfer?**
> "I want to run my [SETUP] on [NEW ASSET CLASS]. Work through the five transfer questions in `strategy/setups.md`: does the anchor exist, who's on the other side, what's the liquidity profile, does the session structure match, and what replaces the failure modes? Then tell me plainly that my paper counter for this class starts at 0 regardless of how it's performed elsewhere."

**31 — Leverage challenge**
> "I want to use leverage to take a bigger position because [REASON]. Challenge me hard. My rule says leverage changes the capital required, never the risk budget. Is this reaching a tradeable increment, or is it sizing up?"

**32 — Cross-class weekly review**
> "Here's my log: [PASTE]. Break performance down by asset class as well as by setup. Where is my sample too small to conclude anything — which is probably everywhere? Tell me if one class is quietly responsible for most of my rule breaks, and lead with process, not P&L."
