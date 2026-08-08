# Trading System

An autonomous, multi-asset, AI-assisted trading platform: deterministic quant
signal engines + a portfolio risk manager + broker execution adapters + an
LLM analyst/journal layer, built to run on a single always-on server. Mobile
and desktop apps are read/control clients only — they never host trading logic.

This repo implements the phased build described in the system spec, starting
with forex/indices (the recommended first asset class). **Read "What this is
and isn't" before connecting anything to a live account.**

## What this is and isn't

- **The execution host is a server, not a phone or laptop.** iOS/Android kill
  background processes and won't guarantee a loop fires overnight. Nothing
  here assumes a mobile app or browser tab stays open — see `app/orchestrator.py`
  and `scripts/run_live_paper.py`, which are meant to run under systemd/Docker
  on a VPS.
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
                 │ PC | Mobile  │   PC/mobile UI itself is not built here)
                 └──────────────┘
```

**Core principle:** signal generation and execution are deterministic and fast
(code). Review, synthesis, and mistake-pattern detection are LLM-driven and
slow — and that's fine, because they're never on the critical execution path.

## What's implemented

| Phase | Status | Notes |
|---|---|---|
| 0 — Foundations | ✅ | DB models, structured decision log, global kill switch with auto-triggers |
| 1 — Data layer | ✅ (forex) | Unified `Candle` schema, `DataProvider` interface, CSV provider (backtest/bootstrap), MT5 live provider, feed health/staleness monitor |
| 2 — Signal engines | ✅ | SMC/ICT engine (BOS/CHoCH, order blocks, FVG, liquidity sweeps, premium/discount), EMA/RSI/ATR indicator engine, isolated meme-momentum engine |
| 3 — Risk manager | ✅ | Equity-scaled position sizing, portfolio exposure cap, correlation-group caps, isolated meme bucket, daily/weekly loss limits (auto kill switch), consecutive-loss circuit breaker |
| 4 — Execution layer | ✅ | Common adapter interface, idempotent client-order-IDs, fully functional `PaperAdapter` simulator, `MT5Adapter` (Windows/live only) |
| 5 — Memory/journal | ✅ | Trade journal, LLM-assisted post-trade classification (async), weekly stats job, mechanical strategy down-weighting/pausing |
| 6 — LLM analyst | ✅ | Claude API client, thesis synthesis + contradiction flagging, weekly mistake-pattern report |
| 7 — Backtester | ✅ | Same engine functions live/backtest, spread/slippage/commission modeling, no lookahead, walk-forward split |
| 8 — Client apps | ⚠️ partial | FastAPI backend (positions/signals/journal/strategies/kill-switch) with API-key + optional TOTP auth. **PC/mobile UI is not built** — this is a backend for one to be built against. |

**Not built / explicit next steps:** crypto/stocks/commodities data+execution
adapters (forex/indices is the recommended first asset class per the spec's
Build Order); a parameter optimizer for walk-forward tuning (the walk-forward
harness here validates a *given* parameter set out-of-sample — it doesn't
search for one); multi-strategy portfolio backtesting (the backtester
validates one engine/instrument at a time, matching Build Order step 2; the
live `RiskManager` does enforce portfolio-wide caps); a pgvector-backed
semantic trade retrieval store for the LLM analyst (the relational journal
schema is ready for one to be bolted on); the PC/mobile client applications.

## Repository layout

```
app/
  config.py            settings (env-driven; see .env.example)
  logging_utils.py      structured decision log (Postgres + stdout JSON)
  db/                    SQLAlchemy models + session/engine setup
  killswitch/            global kill switch service
  data/                  Candle schema, DataProvider interface, CSV + MT5 providers, feed health
  signals/               SMC/ICT engine, indicator engine, meme engine — pure functions
  risk/                  RiskManager, correlation groups, position sizing
  execution/              ExecutionAdapter interface, PaperAdapter, MT5Adapter
  journal/                trade journal, classification, weekly stats/weighting
  llm_analyst/            Claude API client, thesis synthesis, mistake reports
  backtest/               backtest engine, metrics, walk-forward validation
  orchestrator.py         ties data -> signals -> risk -> execution -> journal for one instrument
  api/                    FastAPI read/control surface for client apps
scripts/
  init_db.py              create tables
  run_backtest.py          walk-forward backtest a strategy against local CSV history
  run_live_paper.py        run the orchestrator loop against a live/CSV feed through PaperAdapter
tests/                     pytest suite (signal engines, risk manager, backtester, execution, orchestrator)
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
   here, since they only need the API/DB, not the trading logic itself.

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
