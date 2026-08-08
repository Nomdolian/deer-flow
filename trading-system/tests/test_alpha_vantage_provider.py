import httpx
import pytest

from app.data.providers.alpha_vantage_provider import AlphaVantageProvider
from app.db.models import AssetClass


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _fx_daily_payload() -> dict:
    return {
        "Meta Data": {"1. Information": "Forex Daily Prices"},
        "Time Series FX (Daily)": {
            "2026-08-07": {"1. open": "1.15240", "2. high": "1.15800", "3. low": "1.15160", "4. close": "1.15580"},
            "2026-08-06": {"1. open": "1.15520", "2. high": "1.15590", "3. low": "1.15140", "4. close": "1.15240"},
        },
    }


def test_fx_daily_returns_sorted_candles():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["function"] == "FX_DAILY"
        assert request.url.params["from_symbol"] == "EUR"
        assert request.url.params["to_symbol"] == "USD"
        assert request.url.params["apikey"] == "test-key"
        return httpx.Response(200, json=_fx_daily_payload())

    provider = AlphaVantageProvider(AssetClass.forex, api_key="test-key", client=_client(handler))
    candles = provider.historical("EURUSD", "D1", limit=10)

    assert len(candles) == 2
    assert candles[0].time < candles[1].time  # oldest first
    assert candles[-1].close == 1.15580
    assert candles[0].instrument == "EURUSD"
    assert candles[0].asset_class == AssetClass.forex


def test_historical_respects_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_fx_daily_payload())

    provider = AlphaVantageProvider(AssetClass.forex, api_key="test-key", client=_client(handler))
    candles = provider.historical("EURUSD", "D1", limit=1)
    assert len(candles) == 1
    assert candles[0].close == 1.15580  # the most recent one


def test_latest_returns_most_recent_candle():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_fx_daily_payload())

    provider = AlphaVantageProvider(AssetClass.forex, api_key="test-key", client=_client(handler))
    latest = provider.latest("EURUSD", "D1")
    assert latest is not None
    assert latest.close == 1.15580


def test_premium_endpoint_error_raises_runtime_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Information": "This is a premium endpoint"})

    provider = AlphaVantageProvider(AssetClass.forex, api_key="test-key", client=_client(handler))
    with pytest.raises(RuntimeError, match="premium endpoint"):
        provider.historical("EURUSD", "M5", limit=10)


def test_rate_limit_note_raises_runtime_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Note": "Thank you for using Alpha Vantage! Our standard API rate limit..."})

    provider = AlphaVantageProvider(AssetClass.forex, api_key="test-key", client=_client(handler))
    with pytest.raises(RuntimeError, match="rate limit"):
        provider.historical("EURUSD", "D1", limit=10)


def test_missing_series_key_fails_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Meta Data": {}})  # malformed/unexpected shape

    provider = AlphaVantageProvider(AssetClass.forex, api_key="test-key", client=_client(handler))
    with pytest.raises(RuntimeError, match="missing"):
        provider.historical("EURUSD", "D1", limit=10)


def test_equity_daily_uses_correct_function_and_parses_volume():
    payload = {
        "Time Series (Daily)": {
            "2026-08-07": {
                "1. open": "311.45", "2. high": "314.81", "3. low": "310.74", "4. close": "313.33", "5. volume": "34437191",
            },
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["function"] == "TIME_SERIES_DAILY"
        assert request.url.params["symbol"] == "AAPL"
        return httpx.Response(200, json=payload)

    provider = AlphaVantageProvider(AssetClass.stocks, api_key="test-key", client=_client(handler))
    candles = provider.historical("AAPL", "D1", limit=10)
    assert candles[0].volume == 34437191.0


def test_crypto_daily_splits_symbol_and_market():
    payload = {
        "Time Series (Digital Currency Daily)": {
            "2026-08-08": {"1. open": "64891.6", "2. high": "64925.97", "3. low": "64789.89", "4. close": "64840.18", "5. volume": "63.36"},
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["function"] == "DIGITAL_CURRENCY_DAILY"
        assert request.url.params["symbol"] == "BTC"
        assert request.url.params["market"] == "USD"
        return httpx.Response(200, json=payload)

    provider = AlphaVantageProvider(AssetClass.crypto_major, api_key="test-key", client=_client(handler))
    candles = provider.historical("BTCUSD", "D1", limit=10)
    assert candles[0].close == 64840.18


def test_crypto_intraday_unsupported_on_free_tier():
    provider = AlphaVantageProvider(AssetClass.crypto_major, api_key="test-key", client=_client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ValueError, match="premium plan"):
        provider.historical("BTCUSD", "M5", limit=10)


def test_index_routes_through_etf_proxy_and_retags_instrument():
    payload = {
        "Time Series (Daily)": {
            "2026-08-07": {"1. open": "440.0", "2. high": "442.5", "3. low": "438.1", "4. close": "441.2", "5. volume": "70000000"},
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["function"] == "TIME_SERIES_DAILY"
        assert request.url.params["symbol"] == "SPY"  # the proxy ticker, not the requested instrument
        return httpx.Response(200, json=payload)

    provider = AlphaVantageProvider(AssetClass.indices, api_key="test-key", client=_client(handler))
    candles = provider.historical("SPX500", "D1", limit=10)
    assert candles[0].instrument == "SPX500"  # re-tagged back to what was asked for
    assert candles[0].close == 441.2


def test_commodity_routes_through_etf_proxy():
    payload = {
        "Time Series (Daily)": {
            "2026-08-07": {"1. open": "75.0", "2. high": "76.2", "3. low": "74.5", "4. close": "75.8", "5. volume": "5000000"},
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "USO"
        return httpx.Response(200, json=payload)

    provider = AlphaVantageProvider(AssetClass.commodities, api_key="test-key", client=_client(handler))
    candles = provider.historical("WTI", "D1", limit=10)
    assert candles[0].instrument == "WTI"
    assert candles[0].close == 75.8


def test_index_without_a_configured_proxy_fails_closed():
    provider = AlphaVantageProvider(AssetClass.indices, api_key="test-key", client=_client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ValueError, match="no ETF proxy configured"):
        provider.historical("FTSE100", "D1", limit=10)


def test_commodity_without_a_configured_proxy_fails_closed():
    provider = AlphaVantageProvider(AssetClass.commodities, api_key="test-key", client=_client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ValueError, match="no ETF proxy configured"):
        provider.historical("COFFEE", "D1", limit=10)


def test_crypto_meme_uses_same_endpoint_as_crypto_major():
    payload = {
        "Time Series (Digital Currency Daily)": {
            "2026-08-08": {"1. open": "0.18", "2. high": "0.19", "3. low": "0.17", "4. close": "0.184", "5. volume": "1000000"},
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["function"] == "DIGITAL_CURRENCY_DAILY"
        assert request.url.params["symbol"] == "DOGE"
        return httpx.Response(200, json=payload)

    provider = AlphaVantageProvider(AssetClass.crypto_meme, api_key="test-key", client=_client(handler))
    candles = provider.historical("DOGEUSD", "D1", limit=10)
    assert candles[0].close == 0.184


def test_missing_api_key_fails_closed(monkeypatch):
    # Force the settings fallback to empty regardless of the local .env, so
    # this test doesn't depend on whether a real key happens to be configured.
    monkeypatch.setattr("app.data.providers.alpha_vantage_provider.settings.alpha_vantage_api_key", "")
    with pytest.raises(RuntimeError, match="ALPHA_VANTAGE_API_KEY"):
        AlphaVantageProvider(AssetClass.forex, api_key="")


def test_invalid_pair_length_raises_value_error():
    provider = AlphaVantageProvider(AssetClass.forex, api_key="test-key", client=_client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ValueError, match="6-character pair"):
        provider.historical("EU", "D1", limit=10)
