# Setting up pmbot, step by step

Follow this in order. Steps 1–4 cost nothing and touch no money — you can do
them in an evening. Steps 5–8 involve a wallet. Step 9 is the only one that
risks capital, and you should not reach it for a month.

Commands assume you are in `polymarket-bot/`.

---

## Step 1 — Check you can legally trade, before anything else

```bash
curl -s https://polymarket.com/api/geoblock
```

```json
{"blocked": false, "ip": "…", "country": "GB", "region": "…"}
```

- **`"blocked": false`** → use the international exchange. Nothing to change.
- **`"blocked": true`** → order placement is blocked from your country
  (~33 of them, including the US, UK, Germany, France, Italy, Australia,
  Belgium, Poland, Singapore and Ontario). Public *data* is still open, so
  paper mode works everywhere.
  - **If you are in the US**: set `venue: us` in `bot.yaml`. That repoints
    every host at the CFTC-regulated Polymarket US exchange and switches the
    fee model to its flat 0.05 taker / 0.0125 maker-rebate schedule. Read the
    caveat in step 9 before going live there.
  - **Anywhere else blocked**: you can run paper mode and research, but you
    cannot place orders. Do not try to route around it — that is the one
    thing here that can lose you your account and your balance.

The bot re-runs this check at startup and refuses to trade if it fails.

## Step 2 — Install

Python 3.11 or newer.

```bash
cd polymarket-bot
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
```

That is everything you need for paper mode. The trading SDK and the on-chain
library are deliberately *not* installed yet — add them in step 5.

Check it works:

```bash
pytest -q          # 179 tests, no network needed
```

## Step 3 — Prove the plumbing (still no wallet, no money)

```bash
python -m scripts.phase0_check
```

This does the geoblock check, pulls the market list from Gamma, and fetches a
real order book. Four `[ok]` lines mean the data layer works end to end. If
Gamma returns nothing, you are probably behind a proxy or a corporate firewall
— fix that before going further, because the bot lives on these endpoints.

## Step 4 — Run it in paper mode

```bash
cp .env.example .env && chmod 600 .env     # leave everything blank for now
python -m pmbot
```

It will discover markets, filter them to a watchlist, connect the websocket,
and start quoting on paper. Leave it running and open a second terminal:

```bash
curl -s localhost:8787/health | python -m json.tool
python -m scripts.report
```

`/health` should show `ws_connected: true` and a `ws_market_age_s` under a
second or two. `scripts/report` shows what traded and — more usefully at this
stage — the veto reasons for what did not.

**If nothing ever trades**, that is the expected first result, not a bug. Read
the veto counts:

| Veto | What to change |
|---|---|
| `edge_below_fee_floor` | normal and healthy — most signals should die here |
| `stale_book` | your connection is lagging; check `ws_market_age_s` |
| `below_min_order_size` | raise `strategies.s1_maker.quote_size_usd` |
| `market_position_cap` / `total_deployed_cap` | your paper equity is too small; see below |
| `over_max_markets` in the universe log | fine — it is the watchlist cap |

Paper mode runs on a simulated bankroll — `paper_starting_usdc: 500` in
`bot.yaml`. **Set it to what you actually plan to trade with.** Sizing is a
percentage of equity, so a paper run at $50,000 tells you nothing about how
the bot behaves at $250.

Leave this running for a few days before you touch a wallet. You are looking
for: the feed staying connected, the universe refreshing every 15 minutes, and
the veto mix looking sane.

## Step 5 — Create a dedicated wallet

**Do not use a wallet that holds anything you care about.** This key will sit
in a file on a machine that is connected to the internet.

1. Create a brand-new wallet (MetaMask "add account", or any tool that gives
   you a private key). Nothing else should ever touch it.
2. Export the private key. It goes in `.env` and nowhere else — never in code,
   never in a commit, never in a screenshot.
3. Fund it on **Polygon**:
   - **USDC.e** — your bankroll. Start with $100–250, not more.
   - **~2 POL** — for gas. Approvals and redemptions are a few cents each.
   - Either bridge at `bridge.polymarket.com`, or withdraw from an exchange
     directly to the Polygon network (check the network before sending).

Fill in `.env`:

```bash
PRIVATE_KEY=0x…            # the dedicated wallet, nothing else
FUNDER_ADDRESS=0x…         # same address for a plain EOA
```

Install the trading and chain libraries now:

```bash
pip install -e '.[live,chain]'
```

Enable the commit guard so a key can never be committed:

```bash
pip install pre-commit && pre-commit install
```

### Which wallet type?

| Type | `signature_type` | Use it? |
|---|---|---|
| EOA (raw private key) | `0` | ✅ simplest, and the default in `bot.yaml` |
| Email / Magic proxy | `1` | ✅ allowances are set for you — skip step 6 |
| Safe / browser proxy | `2` | ✅ set `FUNDER_ADDRESS` to the **proxy** address |
| V2 deposit wallet | `3` | ❌ rejected at startup — the SDK signs its L1 headers with the EOA, so the key registers against the wrong address and every order fails `signer != api_key` |

## Step 6 — Approve the exchange contracts (EOA only)

Until USDC and the CTF token are approved, every order you sign is rejected.
This is one-time and costs a few cents.

```bash
python -m scripts.setup_allowances          # dry run — sends nothing
```

Read the output carefully:

```
wallet          0x…
collateral      0x…  (USDC, 6 decimals)      ← must say USDC and 6
balances        250.00 USDC · 1.9800 POL
spenders to approve:
  exchange_v2            0x…  [USDC, CTF needed]
  neg_risk_exchange_v2   0x…  [USDC, CTF needed]
  …
```

The script **refuses to proceed** if the collateral contract does not report
USDC with 6 decimals — that check exists because a wrong address here is the
one mistake in this whole setup that is expensive and irreversible. If it
refuses, fix `chain.contracts.collateral` in `bot.yaml` against a block
explorer rather than forcing past it.

When the dry run looks right:

```bash
python -m scripts.setup_allowances --send
```

It prints a Polygonscan link per transaction. Re-running it later is safe: it
skips anything already approved.

## Step 7 — Derive your API credentials

```bash
python -m scripts.phase0_check --auth
```

This signs one message with your key (L1) to mint the HMAC credentials (L2)
that every trading request uses. It is free, and idempotent — the same key
always yields the same credentials. They are cached to `.creds.json` with
`0600` permissions.

If this prints an address you do not recognise, stop: your `PRIVATE_KEY` and
`FUNDER_ADDRESS` disagree.

## Step 8 — Set up monitoring, then paper trade for 30 days

Telegram takes five minutes and is the difference between noticing a problem
today and noticing it next week:

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, copy the token.
2. Message your new bot once, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
3. Put both in `.env` as `TG_TOKEN` and `TG_CHAT`.

You now get fills, vetoes, kill-switch trips, fee-schedule changes, a
heartbeat every 15 minutes, and a daily summary at 22:00 UTC. **Silence means
the process died** — that is what the heartbeat is for.

Run it under systemd so it survives reboots:

```bash
sudo mkdir -p /opt/pmbot && sudo cp -r . /opt/pmbot/      # or edit the paths in the unit
sudo cp deploy/pmbot.service /etc/systemd/system/
sudo systemctl enable --now pmbot
journalctl -u pmbot -f
```

The unit expects the bot at `/opt/pmbot` with its venv at `/opt/pmbot/.venv`.
If you keep it elsewhere, edit `ExecStart`, `WorkingDirectory`,
`EnvironmentFile` and `ReadWritePaths` to match.

Then leave it alone for 30 days. Check `python -m scripts.report` weekly.

**The gate to go live** (`pmbot/backtest/metrics.py:gate_for_live`):

- at least 100 paper trades,
- positive expectancy *after* modelled fees,
- max drawdown below your daily kill threshold,
- Sharpe above 1 on the daily equity curve,
- and a win rate that held up across two different market regimes.

Do not compress this. Going live on a strategy that was never observed against
real order-book dynamics is the single most common way this ends badly.

## Step 9 — Go live, small

```yaml
# bot.yaml
mode: live
strategies:
  s1_maker: {enabled: true, quote_size_usd: 5, max_inventory_usd: 25}
  s2_arb:   {enabled: false}      # one strategy at a time
chain:
  dry_run: true                   # settlement still logs only, for now
```

```bash
sudo systemctl restart pmbot
journalctl -u pmbot -f
```

Watch the first fills land in Telegram. Then, once a position resolves, read
what the settlement sweep *would* do:

```bash
python -m scripts.settle          # dry run
```

When that output looks right, set `chain.dry_run: false` in `bot.yaml` and
restart. From then on the bot merges complete sets and redeems resolved
positions on its own, hourly.

> **US venue caveat.** With `venue: us`, market data, the fee model and paper
> trading all work. Live order signing and the settlement contracts on that
> venue have **not** been verified against this build — its contract addresses
> are not in the pinned SDK. If you are trading there for real, set
> `chain.contracts` explicitly from the US exchange's own documentation and
> test with a single $5 order before anything else.

Sweep profits to a cold wallet weekly. The key on this machine is an
internet-exposed private key, and the balance it can lose should always be a
balance you can afford to lose.

---

## Day-to-day operation

| Task | Command |
|---|---|
| How is it doing? | `python -m scripts.report --days 7` |
| Is it alive? | `curl -s localhost:8787/health` |
| Logs | `journalctl -u pmbot -f` |
| Settle by hand | `python -m scripts.settle --send` |
| Test a strategy on history | `python -m scripts.run_replay <token_id> --strategy s1_maker` |
| Stop it cleanly | `sudo systemctl stop pmbot` (cancels every resting order) |
| Back up the journal | `deploy/backup.sh` from cron, nightly |

Always stop the bot with SIGTERM (`systemctl stop`, or Ctrl-C), never
`kill -9`: the clean path cancels every resting order on the way out, and an
orphaned order is a free option for everyone else on the book.

## When something breaks

| Symptom | Cause and fix |
|---|---|
| `geoblocked` at startup | see step 1; `venue: us` if you are US-based |
| `signer != api_key` | `signature_type` or `FUNDER_ADDRESS` is wrong for your wallet type |
| Orders rejected immediately | allowances not set — re-run step 6 |
| `kill_switch: clock_skew_…` | the machine's clock drifted; install NTP (`timedatectl set-ntp true`) |
| `kill_switch: ws_gap_…` | connectivity; the bot already cancelled its quotes and will resume |
| `kill_switch: daily_drawdown_…` | it lost your daily limit and stopped for 24h. **Read the journal before clearing it** — that is the point of the halt |
| Everything vetoed as `stale_book` | the feed is behind; check the host's latency to `eu-west-2` |
| `on-chain settlement unavailable` | `pip install -e '.[chain]'`, or the RPC is unreachable |
| Positions stuck after resolution | `chain.dry_run` is still `true`, or UMA has not resolved yet (~2h, longer if disputed) |

## The five numbers that actually matter

From `scripts/report` and the weekly Telegram digest:

1. **Fees paid vs. rebates earned** — if fees dominate, you are taking when
   you meant to be making.
2. **Maker vs. taker fill ratio** — this bot is designed maker-first; a
   taker-heavy book means something is crossing the spread.
3. **Realised edge ÷ predicted edge** — below 1 means the model is optimistic.
   This is the honest measure of whether your edge is real.
4. **Veto mix** — which gate is starving the bot, and is that gate right?
5. **Max drawdown vs. your kill threshold** — how close you have come to the
   switch that stops everything.
