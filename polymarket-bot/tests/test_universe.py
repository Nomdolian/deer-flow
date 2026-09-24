import time

from pmbot.config import UniverseConfig
from pmbot.universe import filter_markets
from tests.helpers import make_market


def cfg(**overrides):
    return UniverseConfig(**overrides)


def test_accepts_a_healthy_market():
    accepted, rejected = filter_markets([make_market()], cfg())
    assert len(accepted) == 1 and not rejected


def test_each_filter_names_its_reason():
    now = time.time()
    cases = {
        "below_min_liquidity": make_market(token_id="a", liquidity=100),
        "below_min_volume": make_market(token_id="b", volume=10),
        "spread_too_wide": make_market(token_id="c", spread=0.10),
        "resolves_too_soon": make_market(token_id="d", end_ts=now + 600),
        "resolves_too_late": make_market(token_id="e", end_ts=now + 200 * 86400),
        "excluded_tag": make_market(token_id="f", tags=("mentions",)),
        "resolution_disputed": make_market(token_id="g"),
    }
    cases["resolution_disputed"].disputed = True
    accepted, rejected = filter_markets(list(cases.values()), cfg(), now=now)
    assert not accepted
    for reason, market in cases.items():
        assert rejected[market.token_id] == reason


def test_unknown_negrisk_is_rejected_when_required():
    market = make_market(neg_risk=None)
    _, rejected = filter_markets([market], cfg(require_negrisk_known=True))
    assert rejected[market.token_id] == "negrisk_unknown"


def test_vague_resolution_wording_is_blacklisted():
    market = make_market()
    market.resolution_criteria = "Resolves at the discretion of the market administrator."
    _, rejected = filter_markets([market], cfg())
    assert rejected[market.token_id] == "vague_resolution_criteria"


def test_preferred_tags_sort_first_then_liquidity():
    a = make_market(token_id="a", tags=("politics",), liquidity=900_000)
    b = make_market(token_id="b", tags=("geopolitics",), liquidity=30_000)
    c = make_market(token_id="c", tags=("politics",), liquidity=500_000)
    accepted, _ = filter_markets([a, b, c], cfg())
    # Zero-fee category wins even on much lower liquidity.
    assert [m.token_id for m in accepted] == ["b", "a", "c"]


def test_max_markets_truncates_and_records_why():
    markets = [make_market(token_id=str(i), liquidity=100_000 - i) for i in range(5)]
    accepted, rejected = filter_markets(markets, cfg(max_markets=2))
    assert len(accepted) == 2
    assert set(rejected.values()) == {"over_max_markets"}
