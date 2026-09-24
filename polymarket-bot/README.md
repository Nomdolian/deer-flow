# pmbot — Polymarket CLOB V2 trading bot

Maker- and arbitrage-first trading bot for Polymarket's international CLOB V2
exchange. Built from the September 2026 blueprint. **Paper mode by default**;
live mode requires an explicit flag, a funded dedicated wallet, and a
geoblock check that passes at startup.

Every Polymarket API used here is free and most need no auth at all. The only
unavoidable costs are trading capital, taker fees (avoidable — post limit
orders), and a few cents of Polygon gas.

---

**Full step-by-step setup: [`docs/SETUP.md`](docs/SETUP.md).** Start there.

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'     # add '.[live,chain]' when you are ready to trade real money
cp .env.example .env        # chmod 600; leave PRIVATE_KEY empty for paper mode

python -m scripts.phase0_check     # geoblock + public reads (phase 0)
python -m pmbot                    # paper mode, per bot.yaml
python -m scripts.report           # what traded, what was vetoed, and why
```

`python -m pmbot --mode live` posts real orders. Do not run it until the paper
gate in `pmbot/backtest/metrics.py:gate_for_live` passes.

**US-based?** The international exchange blocks order placement from ~33
countries and the bot refuses to start there. Set `venue: us` in `bot.yaml`
for the CFTC-regulated exchange — read the caveat in step 9 of the setup guide
before going live on it.

## Where things are

```
pmbot/
├── __main__.py         entrypoint, SIGTERM/SIGINT traps (always cancels resting orders)
├── orchestrator.py     wiring and the supervised task loops
├── config.py           bot.yaml + .env, typed; PMBOT_* env overrides
├── auth.py             geoblock gate, L2 credential derive + cache, client build
├── chain.py            Polygon: allowances, redemption, split/merge (optional web3)
├── state.py            books, positions, equity — the Context strategies read
├── universe.py         Market model + the hard filters
├── fees.py             fee table, fee formula, edge-per-day
├── data/               gamma · clob_rest · ws_market · ws_user · external · data_api · book · ratelimit
├── strategies/         s1_maker · s2_arb · s3_fairvalue · s4_longshot · s5_copy
├── risk/               engine (veto chain) · sizing (fractional Kelly) · killswitch
├── execution/          engine · paper · live · registry · redeem (+ chain redeemer, set merger)
├── store/              db (SQLite) · journal (adaptive weights)
├── monitor/            telegram · health
└── backtest/           replay · metrics
scripts/                phase0_check · setup_allowances · settle · run_replay · report
docs/SETUP.md           the step-by-step guide
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
| 5 | live, $100–250, one strategy, smallest sizes | allowances (`scripts.setup_allowances`), then `--mode live` |
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

## Settlement

Three things are Polygon transactions rather than API calls, and `pmbot/chain.py`
handles all of them:

- **Allowances** — until USDC and the CTF ERC-1155 are approved to the
  exchange contracts, every signed order is rejected.
  `python -m scripts.setup_allowances` shows the plan; `--send` submits it.
  Addresses come from the SDK's own contract config, and the script refuses to
  approve a collateral contract that does not report USDC with 6 decimals.
- **Redemption** — resolved positions are swept hourly. Losers are cleared off
  the books without a transaction (there is nothing to claim, and the winner's
  `redeemPositions` settles the whole condition anyway).
- **Merging complete sets** — an unwound arb leaves you holding both sides of a
  binary market. That set is worth exactly $1 now via `mergePositions`, so the
  capital comes back weeks before resolution.

All of it defaults to `chain.dry_run: true`: it logs the transaction it would
send and books nothing, so the accounting cannot drift from what actually
happened on chain. Read one dry run, then turn it off.

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

- **The US venue is only half-verified.** `venue: us` switches the hosts and
  the flat 0.05/0.0125 fee schedule, and data plus paper trading work. Live
  order signing and the settlement contracts on that venue are *not* verified
  against this build — its addresses are not in the pinned SDK. Set
  `chain.contracts` explicitly from the US exchange's docs and test with a
  single small order first.
- **S2 does not mint sets to trade against a rich book.** It trades the book
  and merges sets it ends up holding; minting into an over-priced set would
  need gas-aware sizing and chain latency modelling, which is a different
  beast from order-book arbitrage.
- **The relayer** (`relayer-v2.polymarket.com`) is unused; EOA signing pays
  its own gas. Proxy-wallet users (`signature_type` 1 and 2) get gasless
  transactions from the exchange anyway.
- **`GET /fee-rate-bps` is best-effort.** If your deployment does not serve
  it, the table stays at the compiled-in September 2026 rates — check them
  before going live. A change that *is* served fires a Telegram alert.
- **Wallet discovery for S5 is manual.** Put candidates from the public
  leaderboard in `strategies.s5_copy.wallets`; the bot ranks them on realised
  PnL, hit rate, sample size and recency, and follows only the survivors.
  There is no leaderboard endpoint this build can verify.
- **`prices-history` has no depth**, so replay flatters maker strategies. This
  is a property of the free data, not something code can fix. Paper trading
  against the real book is the test that counts.

## Tests

```bash
pip install -e '.[dev]'
pytest -q          # 182 tests, no network, no SDK, no chain access required
ruff check pmbot tests scripts
```

The suite covers the fee model, book delta application and staleness, the
universe filters, every strategy's entry and veto conditions, the risk veto
chain and sizing caps, paper fill simulation, the order state machine and
reconciliation, journal weighting, redemption, and an end-to-end paper loop
through the orchestrator, plus the on-chain layer against a fake web3
(allowances, redemption routing, set merging) and venue selection.

**Bottom line:** the plumbing is the easy part. Spend your time on the
fee-adjusted edge model and on 30 honest days of paper trading. S1 and S2 are
shipped first because they are the only two strategies whose edge is
structurally identifiable rather than predictive.
