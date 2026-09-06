# pmbot — Polymarket CLOB V2 trading bot

Maker- and arbitrage-first trading bot for Polymarket's international CLOB V2
exchange. Built from the September 2026 blueprint. **Paper mode by default**;
live mode requires an explicit flag, a funded dedicated wallet, and a
geoblock check that passes at startup.

Every Polymarket API used here is free and most need no auth at all. The only
unavoidable costs are trading capital, taker fees (avoidable — post limit
orders), and a few cents of Polygon gas.

---

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .            # add '.[live]' only when you are ready to trade real money
cp .env.example .env        # chmod 600; leave PRIVATE_KEY empty for paper mode

python -m scripts.phase0_check     # geoblock + public reads (phase 0)
python -m pmbot                    # paper mode, per bot.yaml
python -m scripts.report           # what traded, what was vetoed, and why
```

`python -m pmbot --mode live` posts real orders. Do not run it until the paper
gate in `pmbot/backtest/metrics.py:gate_for_live` passes.

## Where things are

```
pmbot/
├── __main__.py         entrypoint, SIGTERM/SIGINT traps (always cancels resting orders)
├── orchestrator.py     wiring and the supervised task loops
├── config.py           bot.yaml + .env, typed; PMBOT_* env overrides
├── auth.py             geoblock gate, L2 credential derive + cache, client build
├── state.py            books, positions, equity — the Context strategies read
├── universe.py         Market model + the hard filters
├── fees.py             fee table, fee formula, edge-per-day
├── data/               gamma · clob_rest · ws_market · ws_user · external · data_api · book · ratelimit
├── strategies/         s1_maker · s2_arb · s3_fairvalue · s4_longshot · s5_copy
├── risk/               engine (veto chain) · sizing (fractional Kelly) · killswitch
├── execution/          engine · paper · live · registry · redeem
├── store/              db (SQLite) · journal (adaptive weights)
├── monitor/            telegram · health
└── backtest/           replay · metrics
scripts/                phase0_check · run_replay · report
deploy/                 systemd unit · nightly SQLite backup
```

Data flows one way: **data → universe → books → strategies → risk → execution
→ journal**. Strategies never do IO and never size themselves past the risk
engine; the risk engine is the only veto; the execution engine is the only
thing that talks to the exchange.

## The two things that actually decide whether this makes money

**Fees.** Every signal must clear `edge > cost + slippage` before anything
else happens. Makers pay zero and earn a rebate; takers pay
`shares × rate × price × (1−price)`, which is brutal at mid prices and nearly
free at the extremes. Rates are re-fetched at runtime and a change fires a
Telegram alert — hardcoded rates silently turn edge into loss, and they moved
three times in 2026.

**Adverse selection.** A maker gets filled precisely when it is wrong. S1
pulls quotes near resolution, on volume spikes, and whenever the book is
stale; it skews quotes against inventory; it caps inventory per market and
exposure per *event*, because ten markets on one election are one bet.

## Running order (do not compress phases 4 and 5)

| Phase | What | How |
|---|---|---|
| 0 | geoblock, public reads, L2 creds | `python -m scripts.phase0_check [--auth]` |
| 1 | discovery + universe filter + SQLite | `python -m pmbot` (paper), then `scripts.report` |
| 2 | websocket books, staleness, reconnect | same run; watch `ws_market_age_s` on `/health` |
| 3 | fee model + risk + paper execution + S2 | S1/S2 are on by default in `bot.yaml` |
| 4 | S1 + S3, **30 days of paper**, Telegram live | set `TG_TOKEN`/`TG_CHAT` in `.env` |
| 5 | live, $100–250, one strategy, smallest sizes | `--mode live` once the gate passes |
| 6 | journal → adaptive weighting, add S4/S5 | weights recompute nightly and scale caps |

The gate for step 5 (`gate_for_live`): ≥100 trades, positive expectancy after
modelled fees, max drawdown below the daily kill threshold, and Sharpe > 1.

## Paper mode is honest on purpose

- A maker order fills only when the book trades **through** its price, never
  at it — you are last in queue at your own level.
- Taker fills pay the real fee formula plus one tick of adverse slippage.
- Maker fills pay zero fee, and the **rebate is not credited**: don't paper
  trade a revenue line you have not seen land.
- `backtest/replay.py` runs on `/prices-history`, which is mid/last-trade
  only. There is no depth and no queue, so its maker results are optimistic by
  construction. It catches broken logic; it does not prove edge.

## Kill switches

All latch, none auto-resume on a hunch:

| Trigger | Action |
|---|---|
| daily drawdown ≥ `daily_loss_kill_pct` | halt 24h |
| consecutive losses ≥ limit | halt 24h |
| websocket gap > 30s | halt 2m **and cancel every resting order** |
| clock skew > 5s | halt 5m (HMAC timestamps would fail anyway) |
| API error rate ≥ 20/min | halt 10m, back off |
| market disputed / resolution unclear | vetoed per signal, never entered |

Cancels are never vetoed — de-risking is allowed while halted.

## Wallet and keys

Use a **dedicated wallet funded with only what the bot may lose**, signature
type 0 (EOA) or 2 (Safe proxy). Type 3 (V2 deposit wallet / POLY_1271) is
rejected at startup: `create_or_derive_api_key` signs the L1 headers with the
EOA, so the key registers against the wrong address and every order fails
`signer != api_key`.

`.env` is chmod 600 and gitignored; `.creds.json` is written 0600. The
pre-commit hook in `.pre-commit-config.yaml` refuses any commit containing a
32-byte hex string or a `.env` file, and the JSON logger redacts them too.
Sweep profits to cold storage — a bot key on a VPS is an internet-exposed
private key.

## Deployment

`deploy/pmbot.service` (systemd, `Restart=always`, hardened) and
`deploy/backup.sh` (nightly `sqlite3 .backup`). Run in `eu-west-1`, the
closest non-georestricted region to Polymarket's `eu-west-2` servers.
`/health` on port 8787 returns feed age, open orders, equity and kill-switch
state; it 503s when the feed is stale or a switch is tripped.

## Deviations from the blueprint, and why

1. **SDK import path.** The package is `py_clob_client_v2`, not
   `py_clob_client`, and the method is `create_or_derive_api_key()`, not
   `create_or_derive_api_creds()`. Verified against `py-clob-client-v2==1.1.0`.
2. **`exec/` → `execution/`.** `exec` shadows a builtin and reads badly at
   every import site.
3. **Extra modules** the scaffold implied but did not list: `state.py`
   (the Context strategies read), `orchestrator.py` (kept out of
   `__main__.py` so it is testable), `data/ratelimit.py` (the token bucket),
   `data/data_api.py`, `logging_utils.py`, `scripts/`.
4. **Maker edge floor.** A single `fee_adjusted_edge_min_cents: 1.5` would
   have vetoed every S1 quote — a maker's business is capturing half a cent
   of spread at zero fee, many times. Floors are now per strategy
   (`edge_floor_cents_by_strategy`), with the wide floor kept for predictive
   strategies.
5. **Structural vs. Kelly sizing.** Kelly on a half-cent maker edge sizes to
   zero, and an arbitrage leg is sized by how much of the set the book will
   sell you. S1 and S2 size off structure and are then cut by every exposure
   cap; S3/S4/S5 are Kelly-sized as specified.
6. **Maker quoting.** Quotes improve the touch by a tick, bounded by an
   inventory-skewed reservation price, rather than sitting a fixed tick off
   mid — the fixed version gave away most of the spread in a wide book. The
   reported edge is the honest distance from the quote to mid.
7. **Python 3.11+**, not 3.12 — nothing here needs 3.12 and 3.11 is what most
   free-tier images ship.

## Known gaps (deliberate, not hidden)

- **Live redemption is not automated.** Winning tokens redeem 1:1 through the
  CTF contract, which is an on-chain call and would need a web3 dependency and
  a second signing path. `RedeemWorker` detects resolved positions and alerts
  you to redeem them; in paper mode it books the payout. Supply a `redeemer`
  callable to close this.
- **S2 does not mint/merge complete sets.** It trades the book only. Split and
  merge against the CTF contract is the same on-chain gap as above.
- **Polymarket US** (`docs.polymarket.us`, flat 0.05 taker / −0.0125 maker
  rebate) is not implemented. If you are US-based the geoblock gate will stop
  the bot at startup, which is the intended behaviour.
- **The relayer** (`relayer-v2.polymarket.com`) is unused; EOA signing pays
  its own gas.
- **`GET /fee-rate-bps` is best-effort.** If your deployment does not serve
  it, the table stays at the compiled-in September 2026 rates — check them
  before going live.
- **S5's wallet ranking is a stub filter** on realised PnL. It is good for
  market selection and weak as a standalone edge, as the blueprint says.

## Tests

```bash
pip install -e '.[dev]'
pytest -q          # 142 tests, no network, no SDK required
ruff check pmbot tests scripts
```

The suite covers the fee model, book delta application and staleness, the
universe filters, every strategy's entry and veto conditions, the risk veto
chain and sizing caps, paper fill simulation, the order state machine and
reconciliation, journal weighting, redemption, and an end-to-end paper loop
through the orchestrator.

**Bottom line:** the plumbing is the easy part. Spend your time on the
fee-adjusted edge model and on 30 honest days of paper trading. S1 and S2 are
shipped first because they are the only two strategies whose edge is
structurally identifiable rather than predictive.
