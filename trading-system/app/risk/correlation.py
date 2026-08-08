"""Correlation groups cap total risk per group separately from the global cap
(Phase 3, item 3). Running five majors that all move on USD strength isn't
diversification — it's the same bet five times. Start with a manually curated
list; a real correlation matrix computed from rolling returns can replace this
later without changing the RiskManager's interface.
"""

DEFAULT_CORRELATION_GROUPS: dict[str, list[str]] = {
    "usd_majors": ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"],
    "us_indices": ["US30", "NAS100", "SPX500", "US2000"],
    "metals": ["XAUUSD", "XAGUSD"],
    "crypto_majors": ["BTCUSD", "ETHUSD", "BTCUSDT", "ETHUSDT"],
}


def correlation_group_for(instrument: str, groups: dict[str, list[str]] | None = None) -> str:
    groups = groups or DEFAULT_CORRELATION_GROUPS
    upper = instrument.upper()
    for name, members in groups.items():
        if upper in members:
            return name
    return f"uncorrelated:{upper}"
