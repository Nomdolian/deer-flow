# Running the MT5 trading system 24/7 on a Windows laptop

## Read this first: what a laptop can and can't give you

MT5's Python API only works on Windows, and it attaches to a *running* MT5
terminal in your desktop session. So the machine with MT5 on it is the machine
that has to stay up. A laptop can do this — but it is not a server, and
pretending otherwise is how people lose money.

**What genuinely breaks, and what protects you:**

| Failure | Frequency | What protects you |
|---|---|---|
| Lid closed / sleep | constant, if unconfigured | `setup_windows.ps1` disables sleep + lid action |
| Process crash | occasional | Supervisor batch file restarts it; startup reconciliation recovers state |
| WiFi drop | frequent, brief | Orders fail closed while disconnected; watchdog engages the kill switch only on a *prolonged* outage (default 5 min) |
| Windows Update reboot | monthly-ish, unavoidable | Task Scheduler restarts at boot; **broker-side stop-losses protect open positions while you're down** |
| Power cut | rare | Same as reboot — plus consider a UPS |
| MT5 terminal closed/updated | occasional | Watchdog detects it, kill switch engages, supervisor retries |

**The single most important safety property:** every order is placed with its
stop-loss and take-profit attached *at the broker*. Your positions are
protected by the broker's servers, not by this process. If the laptop dies
mid-trade, the stop still executes. That's what makes a non-server host
tolerable at all.

**When a laptop is genuinely not enough:** if you're trading size where a
missed entry or a few hours of downtime matters, run MT5 on a Windows VPS
(~$15-30/month — Contabo, Vultr, or your broker's own free VPS if you qualify).
Same setup steps, but it doesn't sleep, doesn't move, and has redundant power
and network. The code is identical either way.

## Setup

### 1. Prerequisites on the Windows machine

- **MT5 terminal** installed and logged into your account
- Enable algo trading: **Tools → Options → Expert Advisors → "Allow
  algorithmic trading"**
- **Python 3.12** (python.org — check "Add python.exe to PATH")
- **Git for Windows**

### 2. Clone and install

```powershell
git clone <your-repo-url> C:\trading-system
cd C:\trading-system\trading-system
git checkout claude/trading-system-pdf-lxmxa7

pip install uv
uv venv --python 3.12 .venv
.venv\Scripts\Activate.ps1
uv pip install -e ".[mt5]"     # the [mt5] extra pulls in the MetaTrader5 package
```

### 3. Database

Postgres via Docker Desktop is easiest:

```powershell
docker compose up -d postgres
```

If Docker is blocked on your machine (common on managed laptops), install
Postgres for Windows natively and point `DATABASE_URL` at it instead.

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in:

```
DATABASE_URL=postgresql+psycopg://trading:trading@localhost:5432/trading
MT5_LOGIN=your-account-number
MT5_PASSWORD=your-password
MT5_SERVER=YourBroker-Server01
# MT5_PATH=C:\Program Files\YourBroker MT5\terminal64.exe   # only if non-default
API_AUTH_SECRET=<generate: python -c "import secrets; print(secrets.token_hex(32))">
API_TOTP_SECRET=<generate: python -c "import secrets,base64; print(base64.b32encode(secrets.token_bytes(10)).decode())">
```

Then:

```powershell
python -m scripts.init_db
```

### 4. Verify MT5 connectivity before going anywhere near live

```powershell
python -m scripts.check_mt5
```

This confirms the terminal is reachable, algo trading is on, your symbols
resolve, and reports which are tradeable right now. Fix anything it flags
before continuing.

### 5. Choose your instruments

Symbol names are **broker-specific** — `BTCUSD` on one broker is `BTCUSD.a` or
`Bitcoin` on another. `check_mt5` prints your broker's actual crypto/forex
symbol names; use those exactly.

```powershell
$key = (Select-String -Path .env -Pattern "API_AUTH_SECRET=(.*)").Matches.Groups[1].Value

# forex — trades ~24/5
curl.exe -X POST -H "x-api-key: $key" -H "Content-Type: application/json" `
  -d '{\"instrument\":\"EURUSD\",\"asset_class\":\"forex\",\"timeframe\":\"M15\",\"data_source\":\"mt5\"}' `
  http://localhost:8000/watchlist

# crypto CFD — trades 24/7 if your broker offers it
curl.exe -X POST -H "x-api-key: $key" -H "Content-Type: application/json" `
  -d '{\"instrument\":\"BTCUSD\",\"asset_class\":\"crypto_major\",\"timeframe\":\"M15\",\"data_source\":\"mt5\"}' `
  http://localhost:8000/watchlist
```

Or just use the mobile app's **Assets** tab.

### 6. Paper trade first

Do not skip this. Point it at a **demo account** in MT5 and let it run for
weeks, not days:

```powershell
python -m scripts.run_mt5_live --poll-seconds 30
```

Watch the journal (`/journal`, or the mobile Journal tab). You are looking for:
signals firing at sane times, stops and targets landing where you'd expect,
and the weekly stats job producing an expectancy you'd actually accept.

### 7. Configure 24/7 operation

In an **admin** PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
cd C:\trading-system\trading-system\deploy\windows
.\setup_windows.ps1 -ProjectDir C:\trading-system\trading-system
```

That disables sleep/hibernate/lid-suspend, registers a Scheduled Task that
starts the supervisor at boot and logon (auto-restarting if it stops), and
opens the API port to your LAN so your phone can reach it.

Start it without rebooting:

```powershell
Start-ScheduledTask -TaskName TradingSystemRunner
```

### 8. The two other processes

The trading runner is one of three. Run the API (for the mobile app) and the
scheduler (for the learning loop) too — separate windows, or add them as their
own Scheduled Tasks:

```powershell
# API — the mobile app talks to this
.venv\Scripts\uvicorn.exe app.api.main:app --host 0.0.0.0 --port 8000

# learning loop — LLM trade classification + weekly strategy re-weighting
.venv\Scripts\python.exe -m scripts.run_scheduler
```

The scheduler is deliberately a separate process: its LLM calls are slow and
must never sit on the execution path.

## Operating it

**Logs:** `C:\trading-system\logs\` — `supervisor.log` for restarts, timestamped
`runner_*.log` per run.

**Is it alive?**
```powershell
Get-ScheduledTask -TaskName TradingSystemRunner
Get-Process python
```

**Kill switch:** one tap in the mobile app, or:
```powershell
curl.exe -X POST -H "x-api-key: $key" -H "Content-Type: application/json" `
  -d '{\"reason\":\"manual halt\"}' http://localhost:8000/killswitch/engage
```

Engaging halts **new orders only** — it deliberately does not close existing
positions. Close those in the MT5 terminal directly if you want out.

**After an outage:** the watchdog engages the kill switch on a prolonged
disconnect and does *not* auto-clear it. That's intentional — a system that
silently resumed after an unexplained outage would hide the outage from you.
Re-enable from the mobile app (TOTP-gated) once you've checked what happened.

## Before you fund it

- MT5 credentials in `.env` only; never committed. `.gitignore` covers it.
- Trade-only account permissions where your broker offers them.
- Confirm your broker's terms actually permit automated trading — some
  prohibit it on retail or prop accounts, and violating that can cost you the
  account regardless of how the code behaves.
- Start at the smallest size your broker allows, not your target size.
- Re-read the risk defaults in `.env` (0.25% per trade, 3% portfolio cap).
  They are deliberately conservative. Raising them raises your ruin risk
  non-linearly.
