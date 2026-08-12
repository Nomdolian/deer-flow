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
- **Assets** — pick which instruments the system trades autonomously. This
  doesn't guarantee winning trades — nothing can — it controls which assets
  the risk-managed pipeline evaluates, with portfolio/correlation caps
  applying across your whole selection on one shared account, not per
  instrument. Disabling an asset stops new signals for it but keeps
  monitoring (and correctly closing) anything already open.
- **Signals** — the live signal feed with confluences and the risk manager's
  accept/reject decision for each one.
- **Journal** — the trade journal (open + closed trades, R multiple, P&L,
  post-trade classification).
- **Strategies** — per-strategy-version state: active/paused, size
  multiplier, consecutive losses, backtested baseline. Automatic pauses latch
  (they never clear themselves), so this tab is also where you resume a paused
  strategy — TOTP-gated, since it re-enables risk-taking after an automatic
  halt.
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

### Verified working

The app has been run end to end, not just typechecked:

```bash
npx expo export --platform android   # 910 modules, 2MB Hermes bundle
npx expo export --platform web       # 595 modules
npx expo-doctor                      # 18/20 (the 2 failures are network-only checks)
```

Driven in a mobile viewport against a live server, it boots to the Settings
gate, saves credentials, and all six tabs render real data with no console
errors. Two bugs only that run could have found — see below.

### Running it in a browser

`react-dom`, `react-native-web` and `@expo/metro-runtime` are installed, so the
same code runs as a web app:

```bash
npx expo start --web
```

Useful for checking the UI quickly without a phone. Two caveats:

- **Browsers enforce CORS; native apps don't.** The server rejects browser
  origins unless you name them, so set `API_CORS_ORIGINS` in
  `trading-system/.env` to the exact origin you're serving from (e.g.
  `http://localhost:8081`). A wildcard is deliberately refused rather than
  honoured — this API can halt or resume live trading.
- **Credentials are stored less securely on web.** `expo-secure-store` has no
  web implementation, so the web build falls back to `localStorage`. The
  Settings screen says which backend is in use. Use the native app for anything
  you care about.

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

## Building an installable app

`eas.json` defines three profiles. Android needs no paid account:

```bash
npx eas login
npx eas build --profile preview --platform android    # installable APK
```

`preview` produces an APK you can sideload directly. `production` produces an
app bundle for Play Store submission, and `development` a dev client. iOS builds
need a paid Apple Developer account; Expo Go covers iOS without one.

`app.json` carries `com.tradingsystem.mobile` as the bundle identifier for both
platforms — change it before any store submission, since identifiers must be
globally unique.

## Two bugs that only running it could find

Worth recording, because both were invisible to `tsc`:

- **Blank screen on boot.** `SettingsContext` called `expo-secure-store`
  unconditionally, and that package ships `export default {}` as its entire web
  implementation — so `getValueWithKeyAsync is not a function` took the whole app
  down. Worse on native: the load ran in an unguarded async IIFE, so any keychain
  error left `loaded` false forever and the app sat on a spinner with no way out.
  Storage is now platform-aware (`src/storage/credentialStore.ts`), every path is
  guarded, and an `ErrorBoundary` wraps the tree so nothing renders blank.
- **Raw float precision in the UI.** The journal showed
  `exit 114816.33996419217`. Prices now format by magnitude (`src/format.ts`) —
  5 decimals for FX, 2 for indices and crypto.

## Security notes

- On iOS/Android the API key is stored via `expo-secure-store` (Keychain /
  Keystore) — not AsyncStorage, not plain text. On web it falls back to
  `localStorage` because SecureStore has no web implementation; the Settings
  screen reports which backend is active rather than implying the key is
  protected when it isn't.
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
