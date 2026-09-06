import asyncio
import json

from pmbot.data.book import OrderBook
from pmbot.data.ws_market import MarketFeed
from pmbot.monitor.health import HealthServer
from tests.helpers import make_book


class FakeRest:
    """Stands in for ClobRestClient's snapshot call."""

    def __init__(self, books=None):
        self.books = books or {}
        self.calls = []

    async def get_books(self, token_ids, chunk=50):
        self.calls.append(list(token_ids))
        return {t: self.books.get(t, make_book(token_id=t)) for t in token_ids}


def feed(books=None, rest=None, on_update=None):
    books = books if books is not None else {}
    return MarketFeed("wss://example", rest or FakeRest(), books, on_update=on_update)


async def test_snapshot_message_populates_the_book():
    books: dict[str, OrderBook] = {}
    market_feed = feed(books)
    await market_feed._handle(json.dumps({
        "event_type": "book", "asset_id": "tok", "timestamp": "1700000000000",
        "bids": [{"price": "0.49", "size": "100"}],
        "asks": [{"price": "0.51", "size": "100"}],
    }))
    assert books["tok"].best_bid == 0.49 and books["tok"].best_ask == 0.51
    assert not books["tok"].needs_resnapshot


async def test_delta_updates_a_snapshotted_book():
    books = {"tok": make_book()}
    await feed(books)._handle(json.dumps({
        "event_type": "price_change", "asset_id": "tok",
        "changes": [{"side": "BUY", "price": "0.50", "size": "300"}],
    }))
    assert books["tok"].best_bid == 0.50


async def test_delta_on_an_unsnapshotted_book_triggers_a_resnapshot():
    books = {"tok": OrderBook(token_id="tok")}  # never snapshotted
    rest = FakeRest()
    market_feed = feed(books, rest)
    market_feed.token_ids = ["tok"]
    await market_feed._handle(json.dumps({
        "event_type": "price_change", "asset_id": "tok",
        "changes": [{"side": "BUY", "price": "0.50", "size": "300"}],
    }))
    assert rest.calls == [["tok"]]  # we refuse to apply a delta to a book with holes


async def test_tick_size_change_is_applied():
    books = {"tok": make_book(tick=0.01)}
    await feed(books)._handle(json.dumps({
        "event_type": "tick_size_change", "asset_id": "tok", "new_tick_size": "0.001",
    }))
    assert books["tok"].tick_size == 0.001


async def test_disconnect_marks_every_book_stale():
    books = {"a": make_book(token_id="a"), "b": make_book(token_id="b")}
    market_feed = feed(books)
    market_feed._mark_all_stale()
    assert all(book.is_stale(1500) for book in books.values())


async def test_book_updates_are_forwarded_to_the_strategy_layer():
    seen = []

    async def on_update(book):
        seen.append(book.token_id)

    books = {"tok": make_book()}
    await feed(books, on_update=on_update)._handle(json.dumps({
        "event_type": "price_change", "asset_id": "tok",
        "changes": [{"side": "BUY", "price": "0.50", "size": "300"}],
    }))
    assert seen == ["tok"]


async def test_message_age_reports_infinity_before_the_first_message():
    assert feed().age_s() == float("inf")


async def test_health_endpoint_reports_unhealthy_with_a_503():
    server = HealthServer(0, lambda: {"healthy": False, "ws_market_age_s": 999})
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
    await writer.drain()
    raw = await reader.read(-1)
    writer.close()
    await server.stop()
    head, _, body = raw.decode().partition("\r\n\r\n")
    assert "503" in head.splitlines()[0]
    payload = json.loads(body)
    assert payload["ws_market_age_s"] == 999 and "uptime_s" in payload
