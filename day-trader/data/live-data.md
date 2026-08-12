# Live Data — Quotes For Every Asset Class

> Going multi-asset means one scanner and one chart platform stop being enough.
> This maps each class to a data source, so a watchlist can be built without
> tab-hopping across five sites.
>
> **Claude: never invent a price.** If a quote is missing, ask for it or fetch it.
> A plan built on a guessed level is worse than no plan.

---

## Alpha Vantage MCP endpoints by class

Verified available in this workspace's MCP server. Names are exact.

| Class | Endpoints |
|---|---|
| **Stocks / ETFs** | `GLOBAL_QUOTE`, `REALTIME_BULK_QUOTES`, `TIME_SERIES_INTRADAY`, `TIME_SERIES_DAILY`, `ETF_PROFILE`, `TOP_GAINERS_LOSERS` |
| **Forex** | `CURRENCY_EXCHANGE_RATE`, `FX_INTRADAY`, `FX_DAILY`, `FX_WEEKLY`, `FX_MONTHLY` |
| **Crypto** | `CURRENCY_EXCHANGE_RATE` (works for BTC/ETH), `CRYPTO_INTRADAY`, `DIGITAL_CURRENCY_DAILY/WEEKLY/MONTHLY` |
| **Metals** | `GOLD_SILVER_SPOT`, `GOLD_SILVER_HISTORY` |
| **Commodities** | `WTI`, `BRENT`, `NATURAL_GAS`, `COPPER`, `ALUMINUM`, `WHEAT`, `CORN`, `COFFEE`, `SUGAR`, `COTTON`, `ALL_COMMODITIES` |
| **Indices** | `INDEX_DATA`, `INDEX_CATALOG` |
| **Session state** | `MARKET_STATUS` — open/closed across global venues |
| **Catalysts** | `NEWS_SENTIMENT`, `EARNINGS_CALENDAR`, `IPO_CALENDAR` |
| **Indicators** | `VWAP`, `ATR`, `RSI`, `EMA`, `SMA`, `MACD`, `BBANDS`, `STOCH`, `OBV`, `ADX` |

The indicator endpoints matter for this system specifically: `VWAP` backs the VWAP
Reclaim setup, `EMA` the Moving Average Pullback, and `ATR` is the honest way to
judge whether a stop distance is normal for an instrument before assuming a
position will fit.

### Two gaps to know about

1. **No futures contract quotes.** `WTI`/`CORN` etc. return *spot/benchmark* prices,
   not `MCL`/`ZC` contract prices, and there's no CME contract feed here. Since a
   $50 account can't trade futures anyway (see `instruments.md`) this doesn't bite
   yet — but don't size a futures trade off a spot benchmark.
2. **Commodity series are low-frequency.** `WTI --interval daily` is a daily
   benchmark series, not an intraday feed. Fine for context, useless for entries.

### Rate limits and staleness

Free-tier keys are heavily rate-limited. Practical consequences:

- Fetch a watchlist **once** pre-session and write the levels into the journal.
  Don't poll during a trade — that's how you end up seeking new opinions mid-trade,
  which rule 4 exists to prevent.
- **Check the timestamp on every quote.** `GOLD_SILVER_SPOT` returns one; use it.
  A stale price that looks live is the most dangerous data failure mode there is.
- The `trading-system/` service in this repo treats a feed older than
  `feed_stale_seconds` (default 60) as unhealthy and refuses to act on it. Apply
  the same instinct manually here.

---

## Worked example — grounding the affordability math

The access table in `instruments.md` was computed from real quotes fetched this way,
not from assumptions:

```
GOLD_SILVER_SPOT symbol=XAU      -> 4,408.55  (2026-08-12 08:13 UTC)
GOLD_SILVER_SPOT symbol=XAG      ->    66.11  (2026-08-12 08:14 UTC)
CURRENCY_EXCHANGE_RATE BTC->USD  -> 63,736.68 (2026-08-12 08:14 UTC)
CURRENCY_EXCHANGE_RATE EUR->USD  ->   1.15399 (2026-08-12 08:14 UTC)
WTI interval=daily               ->    81.96  (2026-08-03)
```

Then fed straight into the sizer:

```bash
python3 tools/size.py -i gold   --symbol XAUUSD --entry 4408.55 --stop 4393.55
python3 tools/size.py -i silver --symbol XAGUSD --entry 66.11   --stop 65.61
python3 tools/size.py -i crypto-spot --symbol BTCUSD --entry 63736 --stop 62500
```

Gold and silver both return **0** — which is the whole point. The numbers in the
docs are reproducible, and when prices move you re-run rather than trusting a
table someone wrote once.

---

## A note on what data can and cannot do

More data does not create an edge. It removes excuses for guessing, which is a
smaller and more boring benefit — and the only honest one on offer.

The failure mode to watch: multi-asset access plus live quotes across seven classes
means there is *always* something moving somewhere. That is not opportunity, it's
the single strongest pull toward overtrading in this whole extension. The watchlist
cap of 3 and the 3-trade limit exist precisely because the data feed has no opinion
about whether you should be trading today.

---

*Education, not financial advice. Verify every price against your broker's own feed
before acting — that's the price you'll actually be filled at, and it's the only one
that matters.*
