from datetime import UTC, datetime

import httpx

from app.config import settings
from app.data.provider_base import DataProvider
from app.data.schema import Candle
from app.db.models import AssetClass

_BASE_URL = "https://www.alphavantage.co/query"

_INTRADAY_INTERVALS = {
    "M1": "1min",
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "60min",
}

# Alpha Vantage has no direct index-futures or macro-commodity-candle endpoint,
# so these route through the equity endpoints against a highly liquid, long-
# established ETF that tracks the underlying — genuinely live OHLCV, but a
# proxy price, not the literal index/futures print. Only mapped where the ETF
# choice is unambiguous; anything else in these asset classes fails closed
# rather than guessing a ticker.
_INDEX_ETF_PROXIES = {
    "US30": "DIA",  # SPDR Dow Jones Industrial Average ETF
    "NAS100": "QQQ",  # Invesco QQQ (Nasdaq-100)
    "SPX500": "SPY",  # SPDR S&P 500 ETF
    "US2000": "IWM",  # iShares Russell 2000 ETF
}
_COMMODITY_ETF_PROXIES = {
    "WTI": "USO",  # United States Oil Fund
    "NATURAL_GAS": "UNG",  # United States Natural Gas Fund
}


class AlphaVantageProvider(DataProvider):
    """Live market data via Alpha Vantage's REST API, covering every asset
    class in this system:

    - forex/metals: FX_DAILY / FX_INTRADAY (metals as XAU/XAG "currency" pairs)
    - equities: TIME_SERIES_DAILY / TIME_SERIES_INTRADAY
    - crypto majors + meme coins: DIGITAL_CURRENCY_DAILY (same endpoint —
      "major" vs "meme" is a risk-bucketing distinction elsewhere, not a data-
      source one; whatever Alpha Vantage lists as a digital currency works)
    - indices: proxied through _INDEX_ETF_PROXIES (see above)
    - commodities: proxied through _COMMODITY_ETF_PROXIES where the mapping is
      unambiguous; gold/silver go through AssetClass.metals as XAUUSD/XAGUSD
      instead, not this path

    Only crypto is genuinely 24/7. Forex/metals trade ~24/5 (closed weekends);
    equities, indices, and commodity-ETF proxies only trade their exchange's
    regular hours. Outside those hours there is correctly no new candle to
    trade on — the feed-health monitor treats that as expected quiet, not a
    fault, as long as its staleness threshold is set wide enough for the
    timeframe in use (see Orchestrator's feed_stale_seconds).

    Alpha Vantage's free tier only serves DAILY bars for FX and crypto — the
    intraday FX/crypto endpoints return a "premium endpoint" error without a
    paid plan (equity intraday is available free, rate-limited). This
    surfaces as a RuntimeError rather than silently substituting different
    data — fail closed, same as every other data path in this system
    (non-negotiable constraint: missing/unavailable data halts, it never
    proceeds with a guess).
    """

    name = "alpha_vantage"

    def __init__(self, asset_class: AssetClass, api_key: str | None = None, client: httpx.Client | None = None):
        self.asset_class = asset_class
        self.api_key = api_key or settings.alpha_vantage_api_key
        if not self.api_key:
            raise RuntimeError(
                "ALPHA_VANTAGE_API_KEY not configured — get a free key at "
                "alphavantage.co/support/#api-key and set it in .env"
            )
        self._client = client or httpx.Client(timeout=15.0)

    def historical(self, instrument: str, timeframe: str, limit: int) -> list[Candle]:
        candles = self._fetch(instrument, timeframe)
        return candles[-limit:]

    def latest(self, instrument: str, timeframe: str) -> Candle | None:
        candles = self.historical(instrument, timeframe, limit=1)
        return candles[0] if candles else None

    def _fetch(self, instrument: str, timeframe: str) -> list[Candle]:
        if self.asset_class in (AssetClass.forex, AssetClass.metals):
            return self._fetch_fx(instrument, timeframe)
        if self.asset_class == AssetClass.stocks:
            return self._fetch_equity(instrument, timeframe)
        if self.asset_class in (AssetClass.crypto_major, AssetClass.crypto_meme):
            return self._fetch_crypto_daily(instrument, timeframe)
        if self.asset_class == AssetClass.indices:
            return self._fetch_proxied(instrument, timeframe, _INDEX_ETF_PROXIES, "index")
        if self.asset_class == AssetClass.commodities:
            return self._fetch_proxied(instrument, timeframe, _COMMODITY_ETF_PROXIES, "commodity")
        raise ValueError(
            f"AlphaVantageProvider does not support asset class {self.asset_class.value!r} — "
            "Alpha Vantage has no clean retail endpoint for it; use a dedicated data vendor instead."
        )

    def _fetch_fx(self, instrument: str, timeframe: str) -> list[Candle]:
        from_symbol, to_symbol = self._split_pair(instrument)
        if timeframe == "D1":
            params = {"function": "FX_DAILY", "from_symbol": from_symbol, "to_symbol": to_symbol, "outputsize": "full"}
            series_key = "Time Series FX (Daily)"
        elif timeframe in _INTRADAY_INTERVALS:
            interval = _INTRADAY_INTERVALS[timeframe]
            params = {
                "function": "FX_INTRADAY",
                "from_symbol": from_symbol,
                "to_symbol": to_symbol,
                "interval": interval,
                "outputsize": "full",
            }
            series_key = f"Time Series FX ({interval})"
        else:
            raise ValueError(f"unsupported timeframe {timeframe!r} for FX via Alpha Vantage")
        payload = self._request(params)
        return self._parse_series(payload, series_key, instrument, timeframe, has_volume=False)

    def _fetch_equity(self, instrument: str, timeframe: str) -> list[Candle]:
        if timeframe == "D1":
            params = {"function": "TIME_SERIES_DAILY", "symbol": instrument, "outputsize": "full"}
            series_key = "Time Series (Daily)"
        elif timeframe in _INTRADAY_INTERVALS:
            interval = _INTRADAY_INTERVALS[timeframe]
            params = {"function": "TIME_SERIES_INTRADAY", "symbol": instrument, "interval": interval, "outputsize": "full"}
            series_key = f"Time Series ({interval})"
        else:
            raise ValueError(f"unsupported timeframe {timeframe!r} for equities via Alpha Vantage")
        payload = self._request(params)
        return self._parse_series(payload, series_key, instrument, timeframe, has_volume=True)

    def _fetch_crypto_daily(self, instrument: str, timeframe: str) -> list[Candle]:
        if timeframe != "D1":
            raise ValueError(
                f"unsupported timeframe {timeframe!r} for crypto via Alpha Vantage's free tier — "
                "only D1 (DIGITAL_CURRENCY_DAILY) is available without a premium plan"
            )
        symbol, market = instrument[:-3], instrument[-3:]
        params = {"function": "DIGITAL_CURRENCY_DAILY", "symbol": symbol, "market": market}
        payload = self._request(params)
        return self._parse_series(payload, "Time Series (Digital Currency Daily)", instrument, timeframe, has_volume=True)

    def _fetch_proxied(self, instrument: str, timeframe: str, proxies: dict[str, str], kind: str) -> list[Candle]:
        proxy_symbol = proxies.get(instrument)
        if proxy_symbol is None:
            raise ValueError(
                f"no ETF proxy configured for {kind} {instrument!r} via Alpha Vantage — "
                f"known proxies: {sorted(proxies)}. Alpha Vantage has no direct {kind} endpoint; "
                "add a proxy mapping (if one exists you're confident in) or use a dedicated data vendor."
            )
        proxy_candles = self._fetch_equity(proxy_symbol, timeframe)
        # Re-tag as the requested instrument (not the ETF ticker) so signals,
        # risk sizing, and the journal all see what was actually asked for.
        return [
            Candle(
                instrument=instrument,
                asset_class=self.asset_class,
                timeframe=c.timeframe,
                time=c.time,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
            )
            for c in proxy_candles
        ]

    def _split_pair(self, instrument: str) -> tuple[str, str]:
        if len(instrument) != 6:
            raise ValueError(f"expected a 6-character pair like EURUSD or XAUUSD, got {instrument!r}")
        return instrument[:3], instrument[3:]

    def _request(self, params: dict) -> dict:
        query = {**params, "apikey": self.api_key, "datatype": "json"}
        response = self._client.get(_BASE_URL, params=query)
        response.raise_for_status()
        payload = response.json()
        # Alpha Vantage returns HTTP 200 even on errors, rate limits, and
        # premium-plan walls — the failure shows up as one of these keys
        # instead of an HTTP status, so a raw status check would miss it.
        for error_key in ("Error Message", "Note", "Information"):
            if error_key in payload:
                raise RuntimeError(f"Alpha Vantage request failed: {payload[error_key]}")
        return payload

    def _parse_series(
        self, payload: dict, series_key: str, instrument: str, timeframe: str, *, has_volume: bool
    ) -> list[Candle]:
        series = payload.get(series_key)
        if series is None:
            raise RuntimeError(f"Alpha Vantage response missing {series_key!r}: got keys {list(payload.keys())}")

        candles = []
        for date_str, bar in series.items():
            time = datetime.fromisoformat(date_str.replace(" ", "T")).replace(tzinfo=UTC)
            candles.append(
                Candle(
                    instrument=instrument,
                    asset_class=self.asset_class,
                    timeframe=timeframe,
                    time=time,
                    open=float(bar["1. open"]),
                    high=float(bar["2. high"]),
                    low=float(bar["3. low"]),
                    close=float(bar["4. close"]),
                    volume=float(bar["5. volume"]) if has_volume and "5. volume" in bar else 0.0,
                )
            )
        candles.sort(key=lambda c: c.time)
        return candles
