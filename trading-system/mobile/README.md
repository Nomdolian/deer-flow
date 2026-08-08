# Trading System — mobile client

A React Native / Expo app for monitoring and controlling the trading system
from your phone. **This app has no trading logic of its own.** It is a client
of the always-on server built in `../app` — the same architecture boundary
the whole system is built around: the server trades autonomously whether or
not this app is open; the phone can approve/override/kill-switch, never
execute.

If you're looking for where signals actually get generated, sized, and
executed, that's server-side (`../app/signals`, `../app/risk`,
`../app/execution`, `../app/orchestrator.py`) — see the top-level README.

## What's here

- **Dashboard** — recent/open orders, a persistent banner when the kill
  switch is engaged.
- **Signals** — the live signal feed with confluences and the risk manager's
  accept/reject decision for each one.
- **Journal** — the trade journal (open + closed trades, R multiple, P&L,
  post-trade classification).
- **Strategies** — per-strategy-version state: active/paused, size
  multiplier, consecutive losses, backtested baseline.
- **Kill Switch** — one-tap engage (no second factor — halting is always safe
  to make easy); disengage requires the current TOTP code when the server has
  `API_TOTP_SECRET` configured.
- **Settings** — server URL + API key (stored in the device's secure storage
  via `expo-secure-store`, never in plain app storage), connection test, push
  notification opt-in.

Push notifications fire for: new high-confidence signals (≥75% confidence),
positions opened/closed, the daily loss limit approaching (80% of the hard
cap), kill switch engagement, and feed-health issues — see
`../app/notifications/service.py` for the server side of this.

## Setup

```bash
cd trading-system/mobile
npm install
```

Run against your server:

```bash
npx expo start
```

Scan the QR code with **Expo Go** (iOS/Android) for the fastest loop during
development. On first launch the app shows the Settings screen — enter:

- **Server URL**: `https://your-vps.example.com` (or a local network address
  like `http://192.168.1.50:8000` if the server and phone are on the same
  network — Expo Go can't reach `localhost` on your dev machine from a
  physical phone).
- **API key**: whatever you set `API_AUTH_SECRET` to on the server
  (`trading-system/.env`).

Tap **Test connection** to confirm before saving.

## Push notifications

Push tokens need an EAS project ID to register (Expo's push service ties
tokens to a project). One-time setup:

```bash
npx eas init      # creates/links an EAS project, writes the id into app.json
```

Then toggle push notifications on in the Settings screen. Without an EAS
project ID, the rest of the app still works — you'll just poll instead of
getting pushed alerts (every screen refreshes on an interval and pull-to-
refresh).

## Deploying and installing a real app

**See [`DEPLOYMENT.md`](./DEPLOYMENT.md) for the full step-by-step guide** —
covers getting the backend reachable from your phone (LAN vs. a proper VPS
with HTTPS), testing immediately via Expo Go, building a standalone
installed app with EAS (no dev server needed), and why a public App/Play
Store listing isn't recommended for this particular app.

## Security notes

- The API key is stored via `expo-secure-store` (iOS Keychain / Android
  Keystore) — not AsyncStorage, not plain text.
- This app never asks for or stores broker/exchange credentials. Those live
  only on the server (`../.env`), scoped trade-only, never touched by any
  client.
- TOTP codes are entered ad hoc from your separate authenticator app when
  disengaging the kill switch — the app does not store the TOTP secret
  itself, so a compromised phone can't silently re-enable trading.
- Treat the API key like a password: anyone with your server URL + API key
  can view your positions/journal and engage the kill switch (engage is
  intentionally not gated behind 2FA — halting trading is always the safe
  direction). Put the server behind HTTPS and don't share the key.
