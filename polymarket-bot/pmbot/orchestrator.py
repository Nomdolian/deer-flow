"""The bot: wiring, loops, and supervision.

Data -> universe -> books -> strategies -> risk -> execution -> journal.
Every arrow is one-way. Strategies do no IO, the risk engine has the only
veto, and the execution engine is the only thing that talks to the exchange.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx

from pmbot.auth import authenticate, check_geoblock, clock_skew_seconds
from pmbot.config import BotConfig, Secrets
from pmbot.data.clob_rest import ClobRestClient
from pmbot.data.data_api import DataApiClient
from pmbot.data.external import BinanceFeed
from pmbot.data.gamma import GammaClient
from pmbot.data.ws_market import MarketFeed
from pmbot.data.ws_user import UserFeed
from pmbot.execution.engine import ExecutionEngine, Fill
from pmbot.execution.paper import PaperExecutor
from pmbot.execution.redeem import RedeemWorker
from pmbot.execution.registry import OrderRegistry
from pmbot.fees import FeeTable
from pmbot.monitor.health import HealthServer
from pmbot.monitor.telegram import TelegramAlerter
from pmbot.risk.engine import RiskEngine
from pmbot.risk.killswitch import KillSwitch
from pmbot.state import Context, Portfolio
from pmbot.store.db import Database
from pmbot.store.journal import Journal, TradeResult
from pmbot.strategies.s1_maker import MakerStrategy
from pmbot.strategies.s2_arb import ArbStrategy
from pmbot.strategies.s3_fairvalue import FairValueStrategy
from pmbot.strategies.s4_longshot import LongshotStrategy
from pmbot.strategies.s5_copy import CopyStrategy
from pmbot.universe import filter_markets

log = logging.getLogger(__name__)


class Bot:
    def __init__(self, cfg: BotConfig, secrets: Secrets):
        self.cfg = cfg
        self.secrets = secrets
        self.http = httpx.AsyncClient(timeout=20.0)
        self.gamma = GammaClient(cfg.gamma_host, self.http)
        self.rest = ClobRestClient(cfg.host, self.http)
        self.data_api = DataApiClient(cfg.data_host, self.http)
        self.db = Database(cfg.db_path)
        self.registry = OrderRegistry()
        self.journal = Journal()
        self.kill = KillSwitch()
        self.alerter = TelegramAlerter(secrets.tg_token, secrets.tg_chat,
                                       cfg.monitor.telegram_enabled, self.http)
        self.fees = FeeTable(on_change=self.alerter.fee_change)
        self.kill.on_trip = self.alerter.kill_switch

        self.books: dict = {}
        self.ctx = Context(books=self.books, markets={}, portfolio=Portfolio(),
                           fees=self.fees, mode=cfg.mode)
        self.market_feed = MarketFeed(cfg.ws_host, self.rest, self.books,
                                      on_update=self.on_book_update)
        self.binance = BinanceFeed(cfg.binance_ws, ["btcusdt", "ethusdt"])
        self.ctx.spot = self.binance.feeds
        self.risk = RiskEngine(cfg.risk, self.kill)
        self.client = None
        self.user_feed: UserFeed | None = None
        self.health = HealthServer(cfg.monitor.health_port, self.health_snapshot)

        self.paper = PaperExecutor(self.registry, self.ctx.fee_rate, self.fees.exponent)
        self.executor = self.paper
        self.engine = ExecutionEngine(self.executor, self.registry, self.ctx,
                                      on_fill=self.on_fill)
        self.redeemer = RedeemWorker(self.gamma, self.ctx.portfolio, self.ctx.markets,
                                     mode=cfg.mode, alert=self.alerter.send)
        self.strategies = self._build_strategies()
        self._tasks: list[asyncio.Task] = []
        self._shutting_down = False

    def _build_strategies(self) -> list:
        s = self.cfg.strategies
        stale = self.cfg.risk.stale_book_max_ms
        self.copy_strategy = CopyStrategy(s.s5_copy, stale)
        return [
            MakerStrategy(s.s1_maker, self.registry.live_orders, stale),
            ArbStrategy(s.s2_arb, stale),
            FairValueStrategy(s.s3_fairvalue, stale),
            LongshotStrategy(s.s4_longshot, stale),
            self.copy_strategy,
        ]

    # ---- startup ----------------------------------------------------------

    async def start(self) -> None:
        await self.db.connect()
        self.alerter.start()

        geo = await check_geoblock(self.cfg.geoblock_url, self.http)
        self.risk.geoblock_passed = not geo.get("blocked", True)

        if self.cfg.mode == "live":
            await self._go_live()
        else:
            log.info("mode: PAPER — no orders will be posted")

        await self.refresh_universe()
        await self.refresh_fee_rates()
        self.market_feed.start(list(self.ctx.markets))
        self.binance.start()
        await self.health.start()

        self.ctx.portfolio.daily_starting_equity = self.ctx.equity()
        self._tasks = [
            asyncio.create_task(self._loop(self.tick, self.cfg.loop_interval_s), name="tick"),
            asyncio.create_task(self._loop(self.refresh_universe,
                                           self.cfg.universe.refresh_seconds), name="universe"),
            asyncio.create_task(self._loop(self.reconcile, 60), name="reconcile"),
            asyncio.create_task(self._loop(self.snapshot_equity, 60), name="equity"),
            asyncio.create_task(self._loop(self.heartbeat,
                                           self.cfg.monitor.heartbeat_minutes * 60), name="heartbeat"),
            asyncio.create_task(self._loop(self.redeem, 3600), name="redeem"),
            asyncio.create_task(self._loop(self.refresh_fee_rates, 6 * 3600), name="fees"),
            asyncio.create_task(self._loop(self.poll_copy_wallets, 120), name="copy"),
            asyncio.create_task(self.daily_summary_loop(), name="daily_summary"),
        ]

    async def _go_live(self) -> None:
        from pmbot.execution.live import LiveExecutor

        self.client, creds = await authenticate(self.cfg, self.secrets)
        skew = await clock_skew_seconds(self.client)
        if self.kill.check_clock_skew(skew, self.cfg.risk.max_clock_skew_s):
            raise RuntimeError(f"clock skew {skew:.1f}s exceeds tolerance; fix NTP before trading")

        self.executor = LiveExecutor(self.client, self.registry,
                                     on_api_error=lambda _: self.kill.record_api_error())
        self.engine.executor = self.executor
        self.redeemer.mode = "live"
        balance = await self.executor.fetch_balance()
        if balance is not None:
            self.ctx.portfolio.free_usdc = balance
        self.user_feed = UserFeed(self.cfg.ws_host, creds.api_key, creds.api_secret,
                                  creds.api_passphrase, self.on_user_event)
        self.user_feed.start()
        log.warning("mode: LIVE — orders will be posted with real capital")
        self.alerter.send(f"pmbot started LIVE with ${self.ctx.portfolio.free_usdc:.2f} free USDC")

    # ---- data -------------------------------------------------------------

    async def refresh_universe(self) -> None:
        try:
            discovered = await self.gamma.fetch_markets(limit=1000)
        except httpx.HTTPError as exc:
            log.warning("universe refresh failed: %s", exc)
            self.kill.record_api_error()
            return
        watchlist, rejected = filter_markets(discovered, self.cfg.universe)
        self.ctx.markets = {m.token_id: m for m in watchlist}
        self.redeemer.markets = self.ctx.markets
        for market in watchlist:
            await self.db.upsert_market(market)
        self.market_feed.set_universe(list(self.ctx.markets))
        log.info("universe: %d watched, %d filtered out", len(watchlist), len(rejected))

    async def refresh_fee_rates(self) -> None:
        rates = await self.rest.get_fee_rates()
        if rates:
            changed = self.fees.update(rates)
            if changed:
                log.warning("fee rates changed: %s", changed)

    async def poll_copy_wallets(self) -> None:
        cfg = self.cfg.strategies.s5_copy
        if not cfg.enabled or not cfg.wallets:
            return
        for wallet in cfg.wallets:
            try:
                fills = await self.data_api.trades(wallet, limit=50)
            except httpx.HTTPError as exc:
                log.warning("copy poll failed for %s: %s", wallet, exc)
                continue
            self.copy_strategy.ingest(fills)

    # ---- event handling ---------------------------------------------------

    async def on_book_update(self, book) -> None:
        self.ctx.now = time.time()
        await self.db.insert_book_snap(book)

        if self.cfg.mode == "paper":
            for fill in self.paper.on_book(book, self.ctx.now):
                await self.engine.handle_fill(fill.client_id, fill.price, fill.size,
                                              fill.fee, fill.is_maker, fill.ts)

        signals = []
        for strategy in self.strategies:
            if strategy.enabled:
                signals.extend(strategy.on_book(book, self.ctx))
        await self.process_signals(signals)

    async def on_user_event(self, message: dict) -> None:
        """Fast-path fill notifications from the user channel."""
        if message.get("event_type") != "trade":
            return
        order_id = str(message.get("taker_order_id") or message.get("id") or "")
        order = self.registry.get(order_id)
        if order is None:
            return
        try:
            price = float(message.get("price", 0))
            size = float(message.get("size", 0))
            fee = float(message.get("fee_rate_bps", 0) or 0) / 10_000.0 * price * size
        except (TypeError, ValueError):
            return
        is_maker = str(message.get("maker_address", "")).lower() != ""
        await self.engine.handle_fill(order.client_id, price, size, fee, is_maker, time.time())

    async def tick(self) -> None:
        self.ctx.now = time.time()
        self.check_kill_switches()
        signals = []
        for strategy in self.strategies:
            if strategy.enabled:
                signals.extend(strategy.on_tick(self.ctx.now, self.ctx))
        await self.process_signals(signals)

    async def process_signals(self, signals: list) -> None:
        if not signals:
            return
        self.risk.open_order_count = self.registry.open_count()

        groups: dict[str, list] = {}
        singles: list = []
        for signal in signals:
            if signal.group_id and not signal.cancel_order_ids:
                groups.setdefault(signal.group_id, []).append(signal)
            else:
                singles.append(signal)

        for signal in singles:
            decision = self.risk.evaluate(signal, self.ctx)
            await self.db.insert_signal(signal, decision.accepted,
                                        "" if decision.accepted else decision.reason)
            if not decision.accepted:
                self.alerter.veto(signal, decision.reason)
                continue
            order = await self.engine.submit(signal, decision.size)
            if order is not None:
                await self.db.upsert_order(order)

        for group_id, legs in groups.items():
            decisions = [self.risk.evaluate(leg, self.ctx) for leg in legs]
            for leg, decision in zip(legs, decisions):
                await self.db.insert_signal(leg, decision.accepted,
                                            "" if decision.accepted else decision.reason)
            if not all(d.accepted for d in decisions):
                reasons = {d.reason for d in decisions if not d.accepted}
                log.info("arb group %s vetoed: %s", group_id, reasons)
                continue
            # Every leg trades the same number of shares or the set is not a set.
            shares = min(d.size for d in decisions)
            orders = await self.engine.submit_group(legs, [shares] * len(legs))
            for order in orders:
                await self.db.upsert_order(order)

    async def on_fill(self, fill: Fill) -> None:
        await self.db.insert_fill(fill)
        order = self.registry.get(fill.client_id)
        if order is not None:
            await self.db.upsert_order(order)
        position = self.ctx.portfolio.position(fill.token_id)
        book = self.ctx.book(fill.token_id)
        mark = book.mid if book and book.mid else position.avg_px
        await self.db.upsert_position(position, position.unrealized_at(mark))
        self.alerter.fill(fill)

        if fill.side.upper() == "SELL":
            self.risk.note_close(fill.strategy, fill.price * fill.size)
            realized = position.realized
            self.journal.record(TradeResult(strategy=fill.strategy, pnl=realized, ts=fill.ts))
            self.ctx.portfolio.consecutive_losses = self.journal.consecutive_losses()
        else:
            self.risk.note_fill(fill.strategy, fill.price * fill.size)

    # ---- periodic work ----------------------------------------------------

    def check_kill_switches(self) -> None:
        equity = self.ctx.equity()
        self.kill.check_daily_drawdown(self.ctx.portfolio.daily_starting_equity, equity,
                                       self.cfg.risk.daily_loss_kill_pct, self.ctx.now)
        self.kill.check_consecutive_losses(self.ctx.portfolio.consecutive_losses,
                                           self.cfg.risk.max_consecutive_losses, self.ctx.now)
        self.kill.check_error_rate(now=self.ctx.now)
        ws_age = self.market_feed.age_s(self.ctx.now)
        if self.kill.check_feed_gap(ws_age, self.cfg.risk.ws_disconnect_flatten_s, self.ctx.now):
            # Quoting blind is worse than not quoting: pull everything.
            asyncio.create_task(self.engine.executor.cancel_all())

    async def reconcile(self) -> None:
        fetch = getattr(self.executor, "fetch_open_orders", None)
        if fetch is None:
            return
        divergences = self.registry.reconcile(await fetch())
        if divergences:
            log.warning("reconciliation found %d divergences", len(divergences))
            self.alerter.send("reconciliation: " + "; ".join(divergences[:5]))

    async def snapshot_equity(self) -> None:
        marks = self.ctx.marks()
        await self.db.insert_equity(self.ctx.portfolio.free_usdc,
                                    self.ctx.portfolio.position_value(marks))

    async def redeem(self) -> None:
        try:
            redemptions = await self.redeemer.sweep()
        except httpx.HTTPError as exc:
            log.warning("redeem sweep failed: %s", exc)
            return
        if redemptions:
            self.alerter.send(f"redeemed {len(redemptions)} resolved positions")

    async def heartbeat(self) -> None:
        self.alerter.heartbeat(self.ctx.equity(), self.registry.open_count(),
                               self.market_feed.age_s())

    async def daily_summary_loop(self) -> None:
        while True:
            await asyncio.sleep(self._seconds_until_summary())
            weights = self.journal.recompute_weights()
            # Close the loop: tonight's weights scale tomorrow's caps.
            self.risk.strategy_weights = weights
            for strategy, stats in self.journal.stats.items():
                await self.db.upsert_strategy_stats(strategy, stats.trades, stats.wins,
                                                    stats.pnl, weights.get(strategy, 1.0))
            fees_paid = sum(o.fee_paid for o in self.registry.orders.values())
            self.alerter.daily_summary(self.journal.summary(), self.ctx.equity(), fees_paid)
            # New trading day: reset the drawdown baseline.
            self.ctx.portfolio.daily_starting_equity = self.ctx.equity()
            self.ctx.portfolio.daily_realized_pnl = 0.0

    def _seconds_until_summary(self) -> float:
        hour, minute = (int(part) for part in self.cfg.monitor.daily_summary_utc.split(":"))
        now = datetime.now(UTC)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return max(60.0, (target - now).total_seconds())

    def health_snapshot(self) -> dict:
        trip = self.kill.active_trip()
        return {
            "healthy": trip is None and self.market_feed.age_s() < 60,
            "mode": self.cfg.mode,
            "ws_market_age_s": round(self.market_feed.age_s(), 2),
            "ws_connected": self.market_feed.connected,
            "open_orders": self.registry.open_count(),
            "watched_markets": len(self.ctx.markets),
            "equity": round(self.ctx.equity(), 2),
            "free_usdc": round(self.ctx.portfolio.free_usdc, 2),
            "kill_switch": trip.reason if trip else None,
            "api_errors_per_min": self.kill.error_rate_per_min(),
        }

    # ---- supervision ------------------------------------------------------

    async def _loop(self, coro, interval: float) -> None:
        """Run `coro` forever. A crashing task must not take the bot down
        silently — it logs, alerts, and keeps the cadence.
        """
        while True:
            try:
                await coro()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("task %s failed", getattr(coro, "__name__", coro))
                self.kill.record_api_error()
                self.alerter.send(f"task {getattr(coro, '__name__', coro)} failed: {exc}",
                                  dedupe_key=f"task:{getattr(coro, '__name__', coro)}")
            await asyncio.sleep(interval)

    async def run_forever(self) -> None:
        await self.start()
        await asyncio.gather(*self._tasks)

    async def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        log.info("shutting down: cancelling resting orders")
        with contextlib.suppress(Exception):
            await self.engine.shutdown()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.market_feed.stop()
        await self.binance.stop()
        if self.user_feed:
            await self.user_feed.stop()
        await self.health.stop()
        await self.alerter.stop()
        await self.db.close()
        await self.http.aclose()
        log.info("shutdown complete")
