"""SQLite persistence. Zero config, file-based, survives restarts.

Everything the bot decides is written here — including the signals it
rejected and why. A trade log tells you what happened; the veto log tells
you what the bot thought it could not do, which is where most of the
diagnostic value is.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets(token_id TEXT PRIMARY KEY, condition_id TEXT, event_id TEXT,
  question TEXT, category TEXT, neg_risk INT, tick REAL, min_size REAL,
  end_ts INT, updated_ts INT);
CREATE TABLE IF NOT EXISTS book_snaps(ts INT, token_id TEXT, bid REAL, ask REAL, mid REAL,
  bid_sz REAL, ask_sz REAL);
CREATE TABLE IF NOT EXISTS signals(id TEXT PRIMARY KEY, ts INT, strategy TEXT, token_id TEXT,
  side TEXT, px REAL, sz REAL, model_p REAL, edge REAL, accepted INT, veto_reason TEXT);
CREATE TABLE IF NOT EXISTS orders(order_id TEXT PRIMARY KEY, client_id TEXT, ts INT, strategy TEXT,
  token_id TEXT, side TEXT, px REAL, sz REAL, filled REAL, status TEXT, fee REAL);
CREATE TABLE IF NOT EXISTS fills(ts INT, order_id TEXT, px REAL, sz REAL, fee REAL, is_maker INT);
CREATE TABLE IF NOT EXISTS positions(token_id TEXT PRIMARY KEY, qty REAL, avg_px REAL,
  realized REAL, unrealized REAL, updated_ts INT);
CREATE TABLE IF NOT EXISTS equity(ts INT PRIMARY KEY, usdc REAL, position_value REAL, total REAL);
CREATE TABLE IF NOT EXISTS strategy_stats(strategy TEXT PRIMARY KEY, trades INT, wins INT,
  pnl REAL, weight REAL, updated_ts INT);
CREATE INDEX IF NOT EXISTS idx_book_snaps_token_ts ON book_snaps(token_id, ts);
CREATE INDEX IF NOT EXISTS idx_signals_strategy_ts ON signals(strategy, ts);
CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills(ts);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> Database:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        log.info("db: ready at %s", self.path)
        return self

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("database not connected")
        return self._db

    # ---- writes -----------------------------------------------------------

    async def upsert_market(self, market) -> None:
        await self.db.execute(
            """INSERT INTO markets(token_id, condition_id, event_id, question, category,
                 neg_risk, tick, min_size, end_ts, updated_ts)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(token_id) DO UPDATE SET question=excluded.question,
                 category=excluded.category, neg_risk=excluded.neg_risk, tick=excluded.tick,
                 min_size=excluded.min_size, end_ts=excluded.end_ts,
                 updated_ts=excluded.updated_ts""",
            (market.token_id, market.condition_id, market.event_id, market.question,
             market.category, int(bool(market.neg_risk)), market.tick_size,
             market.min_order_size, int(market.end_ts), int(time.time())),
        )
        await self.db.commit()

    async def insert_book_snap(self, book) -> None:
        levels_bid = book.levels("BUY")
        levels_ask = book.levels("SELL")
        await self.db.execute(
            "INSERT INTO book_snaps(ts, token_id, bid, ask, mid, bid_sz, ask_sz) VALUES(?,?,?,?,?,?,?)",
            (int(book.last_update_ts), book.token_id, book.best_bid, book.best_ask, book.mid,
             levels_bid[0].size if levels_bid else 0.0,
             levels_ask[0].size if levels_ask else 0.0),
        )

    async def insert_signal(self, signal, accepted: bool, veto_reason: str = "") -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO signals(id, ts, strategy, token_id, side, px, sz,
                 model_p, edge, accepted, veto_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (signal.id, int(signal.ts), signal.strategy, signal.token_id, signal.side,
             signal.price, signal.size, signal.model_p, signal.edge, int(accepted), veto_reason),
        )
        await self.db.commit()

    async def upsert_order(self, order) -> None:
        await self.db.execute(
            """INSERT INTO orders(order_id, client_id, ts, strategy, token_id, side, px, sz,
                 filled, status, fee) VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(order_id) DO UPDATE SET filled=excluded.filled,
                 status=excluded.status, fee=excluded.fee""",
            (order.id or order.client_id, order.client_id, int(order.created_ts), order.strategy,
             order.token_id, order.side, order.price, order.size, order.filled,
             order.status.value if hasattr(order.status, "value") else str(order.status),
             order.fee_paid),
        )
        await self.db.commit()

    async def insert_fill(self, fill) -> None:
        await self.db.execute(
            "INSERT INTO fills(ts, order_id, px, sz, fee, is_maker) VALUES(?,?,?,?,?,?)",
            (int(fill.ts), fill.client_id, fill.price, fill.size, fill.fee, int(fill.is_maker)),
        )
        await self.db.commit()

    async def upsert_position(self, position, unrealized: float = 0.0) -> None:
        await self.db.execute(
            """INSERT INTO positions(token_id, qty, avg_px, realized, unrealized, updated_ts)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(token_id) DO UPDATE SET qty=excluded.qty, avg_px=excluded.avg_px,
                 realized=excluded.realized, unrealized=excluded.unrealized,
                 updated_ts=excluded.updated_ts""",
            (position.token_id, position.qty, position.avg_px, position.realized,
             unrealized, int(time.time())),
        )
        await self.db.commit()

    async def insert_equity(self, usdc: float, position_value: float) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO equity(ts, usdc, position_value, total) VALUES(?,?,?,?)",
            (int(time.time()), usdc, position_value, usdc + position_value),
        )
        await self.db.commit()

    async def upsert_strategy_stats(self, strategy: str, trades: int, wins: int, pnl: float,
                                    weight: float) -> None:
        await self.db.execute(
            """INSERT INTO strategy_stats(strategy, trades, wins, pnl, weight, updated_ts)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(strategy) DO UPDATE SET trades=excluded.trades, wins=excluded.wins,
                 pnl=excluded.pnl, weight=excluded.weight, updated_ts=excluded.updated_ts""",
            (strategy, trades, wins, pnl, weight, int(time.time())),
        )
        await self.db.commit()

    async def commit(self) -> None:
        await self.db.commit()

    # ---- reads ------------------------------------------------------------

    async def fetch_strategy_stats(self) -> list[dict]:
        async with self.db.execute("SELECT * FROM strategy_stats") as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def fetch_equity_curve(self, since_ts: int = 0) -> list[tuple[int, float]]:
        async with self.db.execute(
            "SELECT ts, total FROM equity WHERE ts >= ? ORDER BY ts", (since_ts,)
        ) as cursor:
            return [(row["ts"], row["total"]) for row in await cursor.fetchall()]

    async def fetch_veto_counts(self, since_ts: int = 0) -> dict[str, int]:
        async with self.db.execute(
            """SELECT veto_reason, COUNT(*) AS n FROM signals
               WHERE accepted = 0 AND ts >= ? GROUP BY veto_reason ORDER BY n DESC""",
            (since_ts,),
        ) as cursor:
            return {row["veto_reason"]: row["n"] for row in await cursor.fetchall()}

    async def fetch_fills(self, since_ts: int = 0) -> list[dict]:
        async with self.db.execute(
            """SELECT f.*, o.strategy, o.token_id FROM fills f
               LEFT JOIN orders o ON o.client_id = f.order_id
               WHERE f.ts >= ? ORDER BY f.ts""",
            (since_ts,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]
