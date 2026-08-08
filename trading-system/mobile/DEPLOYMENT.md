# Deploying and testing the mobile app — full guide

There are two separate things to stand up, and people usually conflate them:

1. **A backend your phone can actually reach.** `localhost:8000` on your dev
   machine only works from that same machine. Your phone needs a real
   address — either your dev machine's LAN IP (same WiFi, fine for quick
   testing) or a small always-on VPS (what the architecture actually calls
   for, and required if you want it reachable off your home network).
2. **The app on your phone.** Three tiers, in order of effort: Expo Go (no
   build, minutes), an EAS internal build (a real installed app, no dev
   server needed, ~15-30 min), or a public App/Play Store listing (days of
   review, not recommended for this app — see Part 5).

Do Part 1, then pick a tier in Part 2-4 based on how "real" you want it.

---

## Part 1 — Get the backend reachable from your phone

### Option A: your dev machine, same WiFi (fastest, good for testing today)

1. Find your machine's LAN IP:
   - macOS/Linux: `ipconfig getifaddr en0` (or `hostname -I` on Linux)
   - Windows: `ipconfig` → look for "IPv4 Address" under your active adapter
2. Start Postgres + the API bound to all interfaces (not just localhost):
   ```bash
   cd trading-system
   docker compose up -d postgres
   .venv/bin/python -m scripts.init_db
   .venv/bin/uvicorn app.api.main:app --host 0.0.0.0 --port 8000
   ```
3. Make sure your machine's firewall allows inbound connections on port 8000
   from your LAN (macOS will prompt the first time; Windows Defender
   Firewall → allow Python/uvicorn on Private networks).
4. Your phone's server URL is `http://<your-lan-ip>:8000` — e.g.
   `http://192.168.1.42:8000`. Phone and dev machine must be on the same
   WiFi network.

This only works while your machine is on, awake, and on that network — fine
for iterating on the UI, not what you'd point a real trading loop at
(re-read the top-level README's "honest bottom line" if that's the plan).

### Option B: a small always-on VPS (what the architecture is actually built for)

1. Spin up a cheap VPS — DigitalOcean, Hetzner, Linode, etc. ($6-12/mo,
   Ubuntu 22.04+ image is fine). Note its public IP.
2. SSH in and install Docker:
   ```bash
   curl -fsSL https://get.docker.com | sh
   ```
3. Get the code onto it (clone your fork/branch, or `scp` the
   `trading-system/` directory up):
   ```bash
   git clone <your-fork-url> && cd deer-flow/trading-system
   # or: git checkout claude/trading-system-pdf-lxmxa7
   ```
4. Create `.env` with **real, freshly generated** secrets — do not reuse
   anything from local testing:
   ```bash
   cp .env.example .env
   python3 -c "import secrets; print(secrets.token_hex(32))"   # -> API_AUTH_SECRET
   python3 -c "import secrets,base64; print(base64.b32encode(secrets.token_bytes(10)).decode())"  # -> API_TOTP_SECRET
   ```
   Edit `.env` and paste those in, plus `ANTHROPIC_API_KEY` if you want the
   LLM analyst layer active. Leave `MT5_*` blank until you're actually past
   paper trading (Build Order step 3 in the top-level README).
5. Bring it up:
   ```bash
   docker compose up -d --build
   docker compose exec api python -m scripts.init_db
   ```
6. **Put a reverse proxy with real TLS in front of it.** The API key travels
   in a plain header — don't send it over bare HTTP to a public IP. The
   easiest option is [Caddy](https://caddyserver.com), which gets you free
   auto-renewing HTTPS in one file. Point a domain/subdomain's A record at
   the VPS IP first, then:
   ```bash
   sudo apt install -y caddy   # or the Caddy install method for your distro
   ```
   `/etc/caddy/Caddyfile`:
   ```
   trading.yourdomain.com {
       reverse_proxy localhost:8000
   }
   ```
   ```bash
   sudo systemctl reload caddy
   ```
7. Lock down the firewall so port 8000 is never reachable directly, only
   through Caddy on 443:
   ```bash
   sudo ufw allow 22/tcp
   sudo ufw allow 443/tcp
   sudo ufw allow 80/tcp   # needed for Caddy's HTTPS cert issuance
   sudo ufw --force enable
   ```
8. Confirm from your own machine: `curl https://trading.yourdomain.com/health`
   should return `{"status":"ok"}`.

Your phone's server URL is now `https://trading.yourdomain.com` — reachable
from anywhere, not just your home WiFi.

---

## Part 2 — Fastest path: test right now with Expo Go

No build, no store, running in a couple of minutes.

1. Install **Expo Go** on your phone (App Store / Google Play — it's free).
2. On your dev machine:
   ```bash
   cd trading-system/mobile
   npm install
   npx expo start
   ```
   If your phone is **not** on the same WiFi as your dev machine, add
   `--tunnel` (routes through Expo's tunnel service — slower, but works
   across networks; needs outbound access to Expo's infra, which some
   locked-down networks/sandboxes block).
3. Scan the QR code the terminal prints — iOS: point the Camera app at it;
   Android: use Expo Go's built-in scanner.
4. The app opens inside Expo Go and lands on **Settings** (nothing else works
   until it's configured). Enter:
   - **Server URL**: from Part 1 (`http://192.168.x.x:8000` or
     `https://trading.yourdomain.com`)
   - **API key**: your `API_AUTH_SECRET` value
   - Tap **Test connection**, confirm it says "Connected ✓", then **Save**.
5. You now have working Dashboard / Signals / Journal / Strategies / Kill
   Switch tabs against your real backend.

### Setting up the TOTP code for disengaging the kill switch

If you set `API_TOTP_SECRET` on the server, disengaging requires a live
6-digit code, not the secret itself (the app never stores the secret — see
the security notes in `mobile/README.md`). Load the secret into an
authenticator app once:

- Google Authenticator / Authy / 1Password → "Add account" → "Enter setup
  key manually" → paste the base32 string from `API_TOTP_SECRET`, any
  account name, time-based.
- Or generate a scannable QR from the secret:
  ```bash
  python3 -c "
  import pyotp
  print(pyotp.totp.TOTP('<your API_TOTP_SECRET>').provisioning_uri(name='trading-system', issuer_name='trading-system'))
  " 
  ```
  Paste that `otpauth://...` URI into a QR generator (e.g.
  [qr-code-generator.com](https://www.qr-code-generator.com)) and scan it
  with your authenticator app.

### Push notifications

These need an Expo project ID (one-time):

```bash
cd trading-system/mobile
npx eas login          # free expo.dev account
npx eas init            # writes a projectId into app.json
```

Reload the app (shake device → Reload, or restart `expo start`), then toggle
push notifications on from Settings. Test it by triggering a server-side
event, e.g. engaging the kill switch from `curl`:

```bash
curl -X POST https://trading.yourdomain.com/killswitch/engage \
  -H "x-api-key: <your API key>" -H "Content-Type: application/json" \
  -d '{"reason": "testing push notifications"}'
```

You should get a push within a few seconds.

---

## Part 3 — A standalone installed app (no Expo Go, no dev server running)

This gets you a real app icon on your home screen that works even when
`expo start` isn't running on your machine. Still not on any public store —
just installed directly on your own device(s). Free for Android; iOS needs
an Apple Developer account.

```bash
cd trading-system/mobile
npm install -g eas-cli    # or use `npx eas ...` each time, no global install
eas login
eas build:configure        # writes eas.json with development/preview/production profiles
```

### Android (no paid account needed)

```bash
eas build --platform android --profile preview
```

This builds in Expo's cloud (a few minutes, free tier covers this). When it
finishes, `eas build:list` or the link printed in your terminal gives you a
download URL for an `.apk`. Open that URL on your phone's browser and
install it directly (Android will prompt to allow installs from that
browser the first time). No Play Store involved.

### iOS (needs an Apple Developer Program membership, $99/year)

Two ways to get it on your own device without a public listing:

**Ad-hoc build** (install directly, like the Android APK):
```bash
eas device:create           # registers your iPhone's UDID with Apple
eas build --platform ios --profile preview
```
Scan the QR/link from the build result on your iPhone to install.

**TestFlight** (Apple's internal-testing distribution — no public review,
just an "internal tester" step that's usually near-instant):
```bash
eas build --platform ios --profile production
eas submit --platform ios
```
Then add yourself as an internal tester in App Store Connect and install via
the TestFlight app.

---

## Part 4 — Public App Store / Google Play listing

Technically possible (`eas submit` handles both), but **not recommended for
this app**: it needs app icons/screenshots, a privacy policy URL, and days
of review — and financial/trading-control apps get extra scrutiny on both
stores' review guidelines. There's no upside to a public listing for a
personal control surface over your own trading account; Part 3's internal
build/TestFlight route gets you a "real app" experience without any of that
overhead or exposure. Skip this unless you specifically want to distribute
it to other people.

---

## Decision guide

| You want | Do this |
|---|---|
| To poke at it in the next 5 minutes | Part 2 (Expo Go) |
| A real app icon, works without a dev server | Part 3 (EAS internal build) |
| Other people to install it from a store | Part 4 (not recommended here) |

## Before connecting this to a real broker/exchange account

None of the above changes the warnings in the top-level README: paper trade
for weeks on the Build Order path, keep broker API keys trade-only, and
don't skip HTTPS + a real API key + TOTP once the server is public (Part 1,
Option B, steps 4 and 6-7 above exist specifically so a phone that can reach
your kill switch is the only thing that can reach it).
