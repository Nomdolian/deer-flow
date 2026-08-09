# Trading System

An autonomous, multi-asset, AI-assisted trading platform: deterministic quant
signal engines + a portfolio risk manager + broker execution adapters + an
LLM analyst/journal layer, built to run on a single always-on server. Mobile
and desktop apps are read/control clients only — they never host trading logic.

This repo implements the phased build described in the system spec, starting
with forex/indices (the recommended first asset class). **Read "What this is
and isn't" before connecting anything to a live account.**

## What this is and isn't

- **The execution host is a always-on machine, never a phone.** iOS/Android
  kill background processes and won't guarantee a loop fires overnight.
  Nothing here assumes a mobile app or browser tab stays open. The `mobile/`
  app is a **client** (dashboard, signal feed, journal, kill switch, push
  alerts) — it has no trading logic and the host keeps trading whether or not
  the app is open. For live MT5 trading that host has to be **Windows**
  (MT5's Python API is Windows-only) — a laptop works if configured not to
  sleep, a Windows VPS is better; see
  [`deploy/windows/README.md`](./deploy/windows/README.md) for the honest
  tradeoff and the setup either way.
- **LLMs never generate trading signals.** Signal generation
  (`app/signals/`) and risk sizing (`app/risk/`) are pure, deterministic,
  backtestable Python — no network calls, no LLM calls, sub-millisecond. The
  LLM analyst layer (`app/llm_analyst/`) only synthesizes theses, flags
  contradictions, classifies closed trades, and writes a weekly mistake-pattern
  report. It is never on the order-placement path.
- **The Risk Manager is the single authoritative gate.** No signal engine,
  execution adapter, or LLM call can place or size an order directly. See
  `app/risk/manager.py`.
- **Fail closed, everywhere.** Missing data, an API error, a stale feed, or
  the kill switch halts new trading for the affected instrument/strategy — it
  never proceeds with a guess. See `assert_not_engaged`, `is_stale`, and the
  try/except in `Orchestrator._run_once`.
- **This is not launch-ready for real capital.** It ships the Phase 0–8
  architecture with working, tested code for one asset class (forex/indices),
  a paper-trading simulator, and a walk-forward backtester. Build Order step 3
  in the spec — weeks of paper trading with a statistically meaningful sample
  — has not been run. Do not point `MT5Adapter` at a live account without
  doing that first.

## Architecture

```
                         ┌─────────────────────────────┐
                         │      ALWAYS-ON SERVER        │
                         │   (the only execution host)  │
                         └───────────────┬───────────────┘
                                          │
   ┌───────────┬───────────────┬─────────┼─────────┬───────────────┐
   │           │               │         │         │               │
Data Ingestion  Signal Engines  Risk Manager  Execution      Journal/Memory
(app/data)      (app/signals)  (app/risk)     (app/execution) (app/journal)
   │           │               │         │         │               │
   └───────────┴───────┬───────┴─────────┴─────────┴───────────────┘
                        │
                 ┌──────┴───────┐
                 │ Orchestrator │  (app/orchestrator.py — schedules the
                 └──────┬───────┘   pipeline, enforces order of operations)
                        │
                 ┌──────┴───────┐
                 │  LLM Analyst │  (app/llm_analyst — thesis synthesis,
                 │    Layer     │   contradiction checks, post-trade review,
                 └──────┬───────┘   mistake-pattern labeling; async, off-path)
                        │
                 ┌──────┴───────┐
                 │ Kill switch/ │  (app/killswitch — one-tap halt, auto-
                 │ notification │   triggers on daily loss / feed staleness)
                 └──────┬───────┘
                        │
                 ┌──────┴───────┐
                 │  Client apps │  (app/api — FastAPI read/control surface;
                 │ PC | Mobile  │   mobile/ — Expo app; PC dashboard not built)
                 └──────────────┘
```

**Core principle:** signal generation and execution are deterministic and fast
(code). Review, synthesis, and mistake-pattern detection are LLM-driven and
slow — and that's fine, because they're never on the critical execution path.

## What's implemented

| Phase | Status | Notes |
|---|---|---|
| 0 — Foundations | ✅ | DB models, structured decision log, global kill switch with auto-triggers |
| 1 — Data layer | ✅ (forex) | Unified `Candle` schema, `DataProvider` interface, CSV provider (backtest/bootstrap), MT5 live provider, Alpha Vantage live provider (forex/equities/crypto — no MT5/broker needed), feed health/staleness monitor |
| 2 — Signal engines | ✅ | SMC/ICT engine (BOS/CHoCH, order blocks, FVG, liquidity sweeps, premium/discount), EMA/RSI/ATR indicator engine, isolated meme-momentum engine |
| 3 — Risk manager | ✅ | Equity-scaled position sizing, portfolio exposure cap, correlation-group caps, isolated meme bucket, daily/weekly loss limits (auto kill switch), consecutive-loss circuit breaker |
| 4 — Execution layer | ✅ | Common adapter interface, idempotent client-order-IDs, fully functional `PaperAdapter` simulator, and a hardened `MT5Adapter` for live trading — magic-number ownership, broker-ticket identity, auto-reconnect, market-hours detection, startup reconciliation (all covered by tests against a fake MT5 module, so it isn't untested until it's on live money) |
| 5 — Memory/journal | ✅ | Trade journal, LLM-assisted post-trade classification, weekly stats + mechanical strategy down-weighting/pausing, weekly mistake-pattern report — **all actually scheduled** via `scripts/run_scheduler.py` (APScheduler), not just callable functions |
| 6 — LLM analyst | ✅ | Claude API client, thesis synthesis + contradiction flagging, weekly mistake-pattern report |
| 7 — Backtester | ✅ | Same engine functions live/backtest, spread/slippage/commission modeling, no lookahead, walk-forward split |
| 8 — Client apps | ✅ mobile, ⚠️ no PC UI | FastAPI backend (positions/signals/journal/strategies/kill-switch/devices/watchlist) with API-key + optional TOTP auth, plus a React Native/Expo mobile app (`mobile/`) — dashboard, signal feed, journal, strategy performance, **Assets screen to pick what the system trades**, one-tap kill switch (TOTP-gated disengage), and push notifications. No PC dashboard yet. |
| — Watchlist / multi-asset | ✅ | `WatchlistRunner` trades every enabled instrument each cycle against ONE shared account, so the risk manager's portfolio/correlation caps apply across the whole selection, not per-instrument in isolation. Disabling an instrument stops new signals for it but keeps monitoring (and correctly closing) anything already open. |

**Not built / explicit next steps:** a crypto *exchange* adapter (ccxt) for
spot/perps — crypto currently trades as MT5 CFDs through your broker, which
covers 24/7 but not native exchange execution; a parameter optimizer for
walk-forward tuning (the walk-forward
harness here validates a *given* parameter set out-of-sample — it doesn't
search for one); multi-strategy portfolio backtesting (the backtester
validates one engine/instrument at a time, matching Build Order step 2; the
live `RiskManager` does enforce portfolio-wide caps); a pgvector-backed
semantic trade retrieval store for the LLM analyst (the relational journal
schema is ready for one to be bolted on); a PC desktop dashboard (the mobile
app and PC dashboard would share the same FastAPI backend).

## Repository layout

```
app/
  config.py            settings (env-driven; see .env.example)
  logging_utils.py      structured decision log (Postgres + stdout JSON)
  db/                    SQLAlchemy models + session/engine setup
  killswitch/            global kill switch service
  data/                  Candle schema, DataProvider interface, CSV + MT5 + Alpha Vantage providers, feed health
  signals/               SMC/ICT engine, indicator engine, meme engine — pure functions
  risk/                  RiskManager, correlation groups, position sizing
  execution/              ExecutionAdapter interface, PaperAdapter, MT5Adapter
  journal/                trade journal, classification, weekly stats/weighting
  llm_analyst/            Claude API client, thesis synthesis, mistake reports
  backtest/               backtest engine, metrics, walk-forward validation
  orchestrator.py         ties data -> signals -> risk -> execution -> journal for one instrument
  api/                    FastAPI read/control surface for client apps
  notifications/          device registration + Expo push fan-out for key events
  health/                 connectivity watchdog -> automatic kill-switch triggers
  watchlist/              asset selection + the multi-asset WatchlistRunner
  learning/               scheduled jobs behind the memory/learning loop
scripts/
  init_db.py              create tables
  run_backtest.py          walk-forward backtest a strategy against local CSV history
  run_live_paper.py        run the orchestrator loop against a live/CSV feed through PaperAdapter
  doctor.py                setup check: deps, .env, DB, tables, watchlist, MT5 — with fixes
  check_mt5.py             MT5 pre-flight: connection, algo trading, broker symbol names
  run_mt5_live.py          the 24/7 live runner (reconcile -> watchdog -> trade loop)
  run_watchlist.py         multi-asset paper runner
  run_scheduler.py         the learning loop (classification + weekly re-weighting)
tests/                     pytest suite (signal engines, risk manager, backtester, execution, orchestrator)
mobile/                    React Native/Expo client — dashboard, assets, signals, journal,
                            strategies, kill switch, push. See mobile/README.md.
deploy/windows/            24/7 Windows setup: supervisor batch, PowerShell setup, runbook
```

## Setup

```bash
cd trading-system
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"          # add [mt5] on a Windows host once you're past paper trading

cp .env.example .env                 # fill in DATABASE_URL, ANTHROPIC_API_KEY, etc.
docker compose up -d postgres        # or point DATABASE_URL at your own Postgres
.venv/bin/python -m scripts.init_db
```

Run the tests:

```bash
.venv/bin/pytest
```

## Backtesting a strategy

Historical candles are read from CSV (`{instrument}_{timeframe}.csv` with
`time,open,high,low,close,volume` columns) — point your data ingestion at
whatever historical source you use and export to this format, or write a new
`DataProvider`.

```bash
.venv/bin/python -m scripts.run_backtest \
  --data-dir ./data --instrument EURUSD --timeframe H1 \
  --engine smc --splits 4
```

This prints train vs. test metrics per walk-forward window — a large gap
between them is the overfitting signal to watch for. **Never optimize
parameters on the same data you validate on.**

## Paper trading

```bash
.venv/bin/python -m scripts.run_live_paper \
  --instrument EURUSD --timeframe H1 --source mt5 --starting-balance 10000
```

`--source csv` replays local history through the same loop without an MT5
connection, for dry-running the pipeline. This must run continuously on the
always-on server (a systemd unit or a long-running container), not on a
machine that sleeps — that's the whole point of the architecture.

### Live data without a broker: Alpha Vantage

If you don't have an MT5/broker connection set up yet, `AlphaVantageProvider`
gets real market data with just an API key — no broker account needed —
across every asset class this system models:

| Asset class | Route | Notes |
|---|---|---|
| forex, metals | `FX_DAILY` / `FX_INTRADAY` | metals as XAU/XAG "currency" pairs (XAUUSD, XAGUSD) |
| stocks | `TIME_SERIES_DAILY` / `TIME_SERIES_INTRADAY` | |
| crypto (major + meme) | `DIGITAL_CURRENCY_DAILY` | same endpoint for both — "major" vs "meme" is a risk-bucketing distinction, not a data-source one |
| indices | equity endpoints via ETF proxy | `US30`→DIA, `NAS100`→QQQ, `SPX500`→SPY, `US2000`→IWM — a live, liquid proxy price, **not** the literal index/futures print |
| commodities | equity endpoints via ETF proxy | `WTI`→USO, `NATURAL_GAS`→UNG only; anything else fails closed rather than guessing a ticker |

```bash
# .env: ALPHA_VANTAGE_API_KEY=your-key (free tier at alphavantage.co/support/#api-key)
.venv/bin/python -m scripts.run_live_paper \
  --instrument EURUSD --timeframe D1 --source alphavantage \
  --asset-class forex --starting-balance 10000
```

The free tier only serves **daily** bars for forex and crypto (`--timeframe
D1`) — the intraday FX/crypto endpoints return a "premium endpoint" error
without a paid Alpha Vantage plan; equity intraday (and therefore the index/
commodity ETF proxies) is available free but rate-limited.
`AlphaVantageProvider` treats an unavailable endpoint as a hard failure
rather than silently falling back to something else — same fail-closed rule
as every other data path in this system. See
`app/data/providers/alpha_vantage_provider.py` for exact routing.

**On "24/7": only crypto actually is.** Forex/metals trade ~24/5 (closed
weekends); equities, indices, and the commodity-ETF proxies trade only their
exchange's regular hours. Outside those hours there is correctly no new
candle — the feed-health monitor treats that as expected quiet, not a fault,
provided its staleness threshold is wide enough for the timeframe in use.
`scripts/run_live_paper.py` derives that threshold automatically (4× the
candle interval, so D1 tolerates a long weekend without either sitting
falsely "stale" forever or masking a genuinely dead feed) — pass
`feed_stale_seconds` explicitly to `Orchestrator` if you build your own
runner and skip that script.

## Live trading through your own MT5 terminal (24/5 forex, 24/7 crypto)

This is the setup for actually trading: MT5 on a Windows machine, this system
attached to it, running continuously. **Full step-by-step in
[`deploy/windows/README.md`](./deploy/windows/README.md)** — including the
Windows power/Task-Scheduler configuration and an honest assessment of
laptop-vs-VPS.

```powershell
uv pip install -e ".[mt5]"        # Windows only; MT5's Python API has no Linux/macOS build
python -m scripts.doctor          # checks everything and tells you what to fix
python -m scripts.check_mt5       # your broker's actual symbol names (they vary)
python -m scripts.run_mt5_live    # the 24/7 runner
```

`scripts/doctor.py` is the fastest way to find out what's wrong with a setup:
it checks Python version, packages, `.env` secrets, Postgres, tables,
watchlist, kill-switch state, and the MT5 terminal, printing the exact fix
command for anything broken. Fix the **first** failure and re-run — later
checks often fail only because an earlier one did.

**What "24/7" actually means per asset class.** Crypto CFDs genuinely run
24/7 (if your broker lists them, minus a short daily maintenance window);
forex/metals run ~24/5. The runner asks MT5 whether each symbol is tradeable
*right now* rather than assuming, so a closed market is recorded as expected
quiet rather than alerting as a broken feed.

**Symbol names are broker-specific.** `BTCUSD` on one broker is `BTCUSD.a` or
`Bitcoin` on another. `scripts/check_mt5.py --list-crypto` prints yours; use
those exact strings on the watchlist or the system will fail closed and simply
never trade them.

### What makes it survive running continuously

Getting this to genuinely last 24/7 required fixing four things that would
each have broken a live install:

| Problem | Why it broke | Fix |
|---|---|---|
| Position identity via order `comment` | MT5 brokers routinely strip/rewrite that field, so `close_position` and `modify_order` silently stopped finding their own positions | Orders are stamped with a **magic number** for ownership and tracked by **broker position ticket**, persisted on `OrderRecord.broker_position_id` |
| Position→trade map held in memory | Any restart (Windows Update, crash, lid close) orphaned open positions from their journal rows — their closes went unrecorded and strategy stats were silently wrong | The journal row *is* the link; looked up from the DB (`find_open_trade`) |
| No reconciliation on startup | Stop-losses live broker-side, so trades close while the process is down. The journal kept phantom open trades, and the risk manager counted their risk against the portfolio cap forever | `reconcile_positions()` runs at boot: journals offline closes with the broker's **real** exit price from deal history, and refuses to invent a price if it can't be verified |
| No connectivity watchdog | The spec required auto-halting on prolonged broker disconnection; it was never built | `ConnectivityWatchdog` engages the kill switch after a sustained outage (default 5 min) or a run of execution errors. Brief blips don't trip it — orders already fail closed while disconnected |

**The safety property all of this leans on:** every order is placed with its
stop-loss and take-profit attached *at the broker*. Open positions stay
protected even if this process dies, the laptop sleeps, or the kill switch is
engaged. Halting only ever stops **new** orders — it never leaves an existing
position unguarded.

**Manual trades are untouchable.** Only positions carrying this system's magic
number are visible to it. Anything you open by hand in the terminal is never
sized against by the risk manager and can never be closed by the system.

## Picking which assets to trade (the watchlist)

**No selection here guarantees a profitable trade — nothing can.** What this
gives you is control over which assets the risk-managed pipeline runs
against, with the portfolio caps (correlation groups, meme bucket, daily
loss limit) applying across your whole selection at once, not per-instrument
in isolation. That's the actual mechanism, not "the bot picks winners."

Manage the watchlist via the API or the mobile app's **Assets** tab:

```bash
API_KEY=$(grep API_AUTH_SECRET .env | cut -d= -f2)
curl -X POST -H "x-api-key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"instrument":"EURUSD","asset_class":"forex","timeframe":"D1","data_source":"alphavantage"}' \
  http://localhost:8000/watchlist
curl -H "x-api-key: $API_KEY" http://localhost:8000/watchlist
```

Then run it continuously against one shared paper account:

```bash
.venv/bin/python -m scripts.run_watchlist --starting-balance 10000 --poll-seconds 60
```

Toggling an instrument off stops new signal evaluation for it but keeps
monitoring (and correctly closing) any position it already has open — same
"halt new, don't touch existing" rule as the kill switch. Changes take
effect on the next cycle without restarting the process.

## The learning loop is a real scheduled process

Phase 5's mechanical learning loop (consecutive-loss circuit breaker, weekly
performance stats, mechanical strategy down-weighting/pausing) and Phase 6's
LLM classification/mistake-pattern report existed as callable functions
earlier in this build but nothing invoked them on a cadence.
`scripts/run_scheduler.py` fixes that — run it as a separate process from
the trading loop (the LLM calls it makes are slow and must never sit on the
execution path):

```bash
.venv/bin/python -m scripts.run_scheduler --classification-interval-minutes 15
```

- Every 15 minutes (configurable): sweeps closed-but-unclassified trades
  through the LLM analyst (`app/journal/classification.py`).
- Weekly: recomputes win rate/expectancy/drawdown per strategy version per
  instrument and applies the hard-coded down-weight/pause rule
  (`app/journal/stats.py`) — mechanical, not LLM judgment.
- Weekly: generates the LLM mistake-pattern report
  (`app/llm_analyst/mistake_report.py`) — a recommendation logged for you to
  review, never an auto-applied rule change.

## Mobile app

```bash
cd mobile
npm install
npx expo start   # scan the QR code with Expo Go
```

Point it at the FastAPI server's URL + `API_AUTH_SECRET` from the Settings
screen. See `mobile/README.md` for push notification setup (needs an EAS
project ID) and building a standalone app for the App/Play Store.

## Build order (matches the spec)

1. Phase 0 + 1 for forex/indices (done here).
2. Phase 2 + 3 + 7: prove a strategy profitable in walk-forward backtest for
   that one asset class before touching live/paper execution.
3. Phase 4 on that one asset class, **paper/demo only, for weeks — not days —
   before any real capital.**
4. Phase 5 + 6 once there's real trade data to review.
5. Only after that's stable: extend to additional asset classes one at a
   time (crypto next is natural; meme coins and stocks last, meme coins with
   their isolated risk bucket from day one — already implemented in
   `RiskManager`).
6. Client apps (Phase 8) can be built in parallel against the FastAPI backend
   here, since they only need the API/DB, not the trading logic itself — the
   mobile app already is one; a PC dashboard would be another.

## Non-negotiable constraints (enforced in code, not just documented)

- No component other than `app/execution/*` ever calls a broker/exchange
  order endpoint.
- No signal is acted on without passing through `RiskManager.evaluate` —
  see `Orchestrator._handle_signal`.
- No LLM call sits on the real-time execution path — grep `app/llm_analyst`
  callers: `journal/classification.py` runs post-close, async;
  `llm_analyst/mistake_report.py` runs on a weekly schedule.
- Every trading decision is logged via `log_decision` before/as it happens
  (`decision_log` table), not reconstructed after the fact.
- Fail closed: see `assert_not_engaged`, `is_stale`, and the data-fetch
  try/except in `orchestrator.py` — errors halt, they never guess.

## Security

- Broker/exchange API keys: trade-only scope, withdrawal/transfer disabled at
  the broker, injected via `.env`/secrets manager — never commit `.env`.
- `app/api/auth.py` implements a static API key (required) plus optional TOTP
  for anything with kill-switch/trade authority. This is a starting point —
  put the API behind a VPN or add proper OAuth/session auth before it touches
  a live account.
- The global kill switch (`app/killswitch`) can be engaged with one call and
  requires no second factor (halting is always safe to make easy);
  disengaging requires TOTP when configured.
