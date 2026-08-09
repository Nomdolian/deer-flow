# Free setup guide — Windows laptop, £0/$0

Every component here is genuinely free, permanently. No trial periods, no
credit card. Roughly 45–60 minutes end to end.

## What "free" costs you (the honest version)

| Piece | Free option | What you give up |
|---|---|---|
| MT5 terminal | Free from any broker | Nothing |
| Trading account | **Demo account** — free, unlimited, resets on request | Not real money (which is the correct place to start anyway) |
| Market data | Comes with MT5 — real live prices | Nothing |
| Database | **SQLite** — no install, no server | Fine for one machine; you'd want Postgres only if the API ran on a different box |
| Python | Free | Nothing |
| Mobile app | **Expo Go** — free | Must run `npx expo start` on the laptop to use the app; a standalone install needs a paid Apple account (Android is free) |
| LLM trade review | **Skip it** | You lose the written trade classification + weekly narrative. The *mechanical* learning loop (expectancy stats, auto down-weighting/pausing underperformers) still works — that part never used an LLM |
| Hosting | Your laptop | It sleeps, reboots for updates, moves. See "Reality check" below |

**What is NOT free, and that's fine to skip:** the Anthropic API key (LLM
review) and a Windows VPS. Neither is needed for the system to trade.

## Reality check before you start

- **Start on a demo account and stay there for weeks.** Not as a formality —
  the learning loop needs a real sample of closed trades before its expectancy
  numbers mean anything, and you need to see the thing behave across a weekend
  and a news event.
- **No system guarantees profitable trades.** This one manages *risk* per trade
  (position sizing, broker-side stops) and mechanically reduces exposure to
  strategies that underperform. That's it. Anything promising more is lying.
- **A laptop is not a server.** It'll be fine most of the time; it will also
  reboot for Windows Update at 3am. What saves you is that every stop-loss sits
  at the broker, so positions stay protected while your machine is down, and
  the system reconciles what happened when it restarts.

---

# Step 1 — Install the free software

Install these four, all free:

1. **Python 3.12 or 3.13** — [python.org/downloads](https://www.python.org/downloads/)
   → During install, **tick "Add python.exe to PATH"**. This one checkbox
   causes most setup problems when missed.
   → Either version works (MetaTrader5 publishes wheels for both). Avoid 3.14
   for now — some scientific packages still lag on it.
2. **Git for Windows** — [git-scm.com/download/win](https://git-scm.com/download/win)
   → Accept all defaults.
3. **MetaTrader 5** — from your broker's site, or
   [metatrader5.com](https://www.metatrader5.com/en/download)
4. **Node.js LTS** *(only if you want the phone app)* —
   [nodejs.org](https://nodejs.org)

Open **PowerShell** (Start menu → type "PowerShell") and confirm:

```powershell
python --version    # expect 3.12.x or 3.13.x
git --version
```

If `python` isn't recognised, the PATH checkbox was missed — reinstall Python
and tick it.

# Step 2 — Get a free demo account

If you don't already have MT5 open with an account:

1. Open MT5 → **File → Open an Account**
2. Pick any broker from the list (or your own broker's server)
3. Choose **Demo account**, fill in the form, note down the
   **login number, password, and server name** — you need all three shortly.

Then **enable algo trading** — the system cannot place orders without it:

**Tools → Options → Expert Advisors → tick "Allow algorithmic trading" → OK**

Leave MT5 running. The system attaches to this running terminal; it does not
log in separately.

# Step 3 — Get the code

```powershell
git clone <your-repo-url> C:\trading-system
cd C:\trading-system\trading-system
git checkout claude/trading-system-pdf-lxmxa7
```

Replace `<your-repo-url>` with your repo's clone URL (green **Code** button on
GitHub).

# Step 4 — Install Python packages

```powershell
pip install uv
uv venv --python 3.12 .venv     # or --python 3.13 if that's what you installed
.venv\Scripts\Activate.ps1
```

If that last line errors with *"running scripts is disabled"*, run this once
and retry:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Then:

```powershell
uv pip install -e ".[mt5,dev]"
```

If this fails with an error about **ABI tags** or *"only found wheels for
cp36m, cp37m..."*, your Python is a version MetaTrader5 has no wheel for.
Check with `python --version` and use 3.12 or 3.13.

The `[mt5]` extra is **Windows-only** — every MetaTrader5 wheel is `win_amd64`.
On Linux/macOS use `uv pip install -e ".[dev]"` instead; everything except live
MT5 execution still works there.

Your prompt should now be prefixed `(.venv)`. **Every command from here needs
that prefix** — if you open a new PowerShell window, re-run
`.venv\Scripts\Activate.ps1` first.

# Step 5 — Configure (this is where the free DB choice happens)

```powershell
Copy-Item .env.example .env
notepad .env
```

Generate your API key first — run this in PowerShell and copy the output:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

And a 2FA secret (recommended — it gates the kill-switch re-enable):

```powershell
python -c "import secrets,base64; print(base64.b32encode(secrets.token_bytes(10)).decode())"
```

Now edit `.env` so these lines read:

```
DATABASE_URL=sqlite:///./trading.db

MT5_LOGIN=12345678
MT5_PASSWORD=your-demo-password
MT5_SERVER=YourBroker-Demo

API_AUTH_SECRET=<the long hex string from above>
API_TOTP_SECRET=<the base32 string from above>
```

**Note `DATABASE_URL` uses SQLite** — that's the free, zero-install database.
No Docker, no Postgres, no server. It's a single file (`trading.db`) in the
project folder, and it's what the entire test suite runs on.

Leave `ANTHROPIC_API_KEY` blank. The system skips the LLM review layer cleanly
when it's empty.

Save and close Notepad. Then create the tables:

```powershell
python -m scripts.init_db
```

# Step 6 — Check everything before going further

```powershell
python -m scripts.doctor
```

This is the important step. It checks your Python version, packages, `.env`
secrets, database, tables, watchlist, kill-switch state, and MT5 connection —
and prints the **exact fix command** for anything wrong.

Fix the **first** failure and re-run. Later checks often fail only because an
earlier one did.

Two warnings are expected and fine at this point:
- *"watchlist is empty"* — you haven't picked instruments yet (Step 8)
- *"ANTHROPIC_API_KEY not set"* — deliberate, that's the paid part you're skipping

# Step 7 — Find your broker's real symbol names

```powershell
python -m scripts.check_mt5 --list-crypto
```

**Don't skip this.** Symbol names differ per broker: `BTCUSD` on one is
`BTCUSD.a`, `BTCUSD.raw`, or `Bitcoin` on another. If you use a name your
broker doesn't have, the system fails closed and **silently never trades it** —
you'd sit there wondering why nothing happens.

Write down the exact names it prints for what you want to trade.

# Step 8 — Pick your instruments

Start the API (it's how you manage the list, and what the phone app talks to).
In **a new PowerShell window**:

```powershell
cd C:\trading-system\trading-system
.venv\Scripts\Activate.ps1
.venv\Scripts\uvicorn.exe app.api.main:app --host 0.0.0.0 --port 8000
```

Leave that running. Windows Firewall will prompt — click **Allow access**
(needed for your phone later).

Back in your **first** window, add instruments — use the exact symbol names
from Step 7:

```powershell
$key = (Get-Content .env | Select-String "^API_AUTH_SECRET=").ToString().Split("=")[1]

# Forex — trades ~24/5 (closed weekends)
$body = '{"instrument":"EURUSD","asset_class":"forex","timeframe":"M15","data_source":"mt5"}'
Invoke-RestMethod -Method Post -Uri http://localhost:8000/watchlist `
  -Headers @{"x-api-key"=$key} -ContentType "application/json" -Body $body

# Crypto CFD — trades 24/7 (use YOUR broker's exact name from Step 7)
$body = '{"instrument":"BTCUSD","asset_class":"crypto_major","timeframe":"M15","data_source":"mt5"}'
Invoke-RestMethod -Method Post -Uri http://localhost:8000/watchlist `
  -Headers @{"x-api-key"=$key} -ContentType "application/json" -Body $body
```

Confirm:

```powershell
python -m scripts.doctor
```

The watchlist warning should be gone, and it now verifies your symbols actually
resolve at your broker.

`check_mt5` is worth a second look here too — it now prints each symbol's
contract terms and what your risk setting works out to in lots:

```powershell
python -m scripts.check_mt5
```

If it says a symbol's position would fall **under your broker's minimum lot**,
that symbol cannot trade on this account at your current risk setting. The
system rejects rather than rounding up past the risk you authorized, so it would
simply never trade it. On a small demo account this is common for Bitcoin —
pick a lower-priced instrument, or accept that BTC needs more equity.

# Step 9 — Prove it can actually place an order

Everything so far shows the system *could* trade. This shows that it *does*:

```powershell
python -m scripts.verify_mt5_trade --symbol EURUSD
```

It places **one minimum-size order** and closes it a couple of seconds later,
which exercises the whole chain: contract terms, lot conversion, filling mode,
broker-side stop and target, finding the position again by ticket, and closing
it. Nothing is written to the trade journal — it's a broker test, not a strategy
trade.

It refuses to run unless MT5 reports a **demo** account. That guard is the point;
don't work around it until you've seen it pass on demo.

If it fails, the error names the cause — invalid volume, invalid stops,
unsupported filling mode, algo trading off, insufficient margin. Every one of
those would otherwise have shown up as "the bot never trades", days later, with
no explanation.

Run it once per broker, and again any time you change broker, account type, or
symbol.

# Step 10 — Start trading (demo)

Smoke-test it first — three cycles, then it exits on its own:

```powershell
python -m scripts.run_mt5_live --poll-seconds 5 --max-cycles 3
```

You should see it connect, reconcile, name your instruments, and print an
`equity=... open_positions=...` line per cycle. If that looks right, start it
for real:

```powershell
python -m scripts.run_mt5_live --poll-seconds 30
```

You'll see it reconcile, connect, and then print a line each cycle. Leave it
running.

What to look for over the first days:
- Signals firing at plausible times, not constantly
- Stops and targets landing where you'd expect for the instrument
- Forex going quiet over the weekend while crypto keeps going — that's the
  24/5 vs 24/7 distinction working, logged as `market_closed`, not an error

# Step 11 — The phone app (free via Expo Go)

Install **Expo Go** from the App Store / Play Store (free).

In a **third** PowerShell window:

```powershell
cd C:\trading-system\trading-system\mobile
npm install
npx expo start
```

Scan the QR code (iOS: Camera app; Android: Expo Go's scanner). Phone and
laptop must be on the **same WiFi**.

In the app's **Settings** screen:
- **Server URL**: `http://<your-laptop-ip>:8000` — find the IP with `ipconfig`,
  look for *IPv4 Address* (e.g. `http://192.168.1.42:8000`)
- **API key**: your `API_AUTH_SECRET`
- Tap **Test connection** → **Save**

You now have Dashboard, Assets, Signals, Journal, Strategies, and the Kill
Switch on your phone.

For the TOTP code (needed to *disengage* the kill switch), add your
`API_TOTP_SECRET` to any free authenticator app (Google Authenticator, Authy)
via "enter setup key manually".

Push notifications need a free Expo account (`npx eas init`) — optional; every
screen polls and pull-to-refreshes without it.

# Step 12 — Keep it running 24/7

In an **admin** PowerShell (right-click PowerShell → Run as administrator):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
cd C:\trading-system\trading-system\deploy\windows
.\setup_windows.ps1 -ProjectDir C:\trading-system\trading-system
```

This disables sleep/hibernate/lid-suspend, registers a Task Scheduler job that
starts the system at boot and restarts it if it crashes, and opens the firewall
port for your phone.

Then start it without rebooting:

```powershell
Start-ScheduledTask -TaskName TradingSystemRunner
```

**Also keep the laptop plugged in.** Battery-powered, Windows will throttle and
eventually sleep regardless of settings.

# Step 13 — Optional: the learning loop

The *mechanical* half (expectancy stats per strategy, automatic
down-weighting/pausing of underperformers) needs no API key and no LLM:

```powershell
python -m scripts.run_scheduler
```

Without `ANTHROPIC_API_KEY` it logs that the LLM classification is skipped and
carries on with the statistical work. That's the part that actually adjusts
position sizing, so it's worth running.

---

# Daily operation

**Check it's alive:**
```powershell
Get-ScheduledTask -TaskName TradingSystemRunner
python -m scripts.doctor
```

**Logs:** `C:\trading-system\logs\`

**Emergency stop:** tap the kill switch in the mobile app. It halts **new
orders only** — existing positions keep their broker-side stops. To exit a
position, close it in the MT5 terminal directly.

**If the kill switch engaged on its own:** it does that after a prolonged
broker disconnect or a loss limit, and deliberately does *not* auto-clear.
Check `logs\` and the Journal tab for why, then re-enable from the app.

# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `python` not recognised | PATH checkbox missed during install — reinstall Python |
| `running scripts is disabled` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `ModuleNotFoundError` | venv not active — run `.venv\Scripts\Activate.ps1` |
| `MT5 initialize() failed` | Terminal not running/logged in, or algo trading off (Step 2) |
| Nothing ever trades | Wrong symbol names — re-run Step 7; or the market is closed; or `doctor` shows the kill switch engaged; or the position would be under your broker's minimum lot (`check_mt5` says so) |
| `invalid volume` / `invalid stops` / `unsupported filling` | Broker contract terms — run Step 9's `verify_mt5_trade`, which reproduces it in isolation and names the cause |
| It traded for a while, then stopped | A strategy hit the consecutive-loss breaker and is **paused**. This latches on purpose. Check the Strategies tab for a PAUSED badge, read why in the journal, then resume it there (TOTP-gated) |
| `cannot open the SQLite file` | Path problem — `doctor` prints the correct slash syntax |
| Phone can't connect | Different WiFi, or firewall — re-run Step 12's script |

**When stuck, run `python -m scripts.doctor` first.** It's designed to tell you
what's actually wrong instead of leaving you guessing.

# When free stops being enough

The one genuine limitation of this setup is **uptime**. If you get to the point
where a few hours of downtime would actually cost you money, move MT5 and this
system to a Windows VPS (~$15–30/month; some brokers give one free above a
deposit threshold). The setup steps are identical — nothing in the code
changes.
