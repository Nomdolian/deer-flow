"""Telegram alerting. Free, instant, and on your phone.

Silence is the signal that matters: the heartbeat exists so that a dead
process is visible, and the daily summary exists so that a slowly bleeding
strategy is visible. Alerts are deduplicated — a bot that cries wolf every
30 seconds gets muted, and then you have no monitoring at all.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

log = logging.getLogger(__name__)

DEDUPE_WINDOW_S = 300.0


class TelegramAlerter:
    def __init__(self, token: str, chat_id: str, enabled: bool = True,
                 client: httpx.AsyncClient | None = None):
        self.enabled = enabled and bool(token and chat_id)
        self.token = token
        self.chat_id = chat_id
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None
        self._recent: dict[str, float] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self._task: asyncio.Task | None = None
        if not self.enabled:
            log.info("telegram: disabled (no token/chat configured)")

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self._drain(), name="telegram")
        return self._task

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._owns_client:
            await self._client.aclose()

    def send(self, text: str, dedupe_key: str | None = None) -> None:
        """Fire-and-forget from sync call sites (kill switches, fee drift)."""
        if not self.enabled:
            log.info("alert: %s", text)
            return
        if dedupe_key:
            now = time.time()
            last = self._recent.get(dedupe_key, 0.0)
            if now - last < DEDUPE_WINDOW_S:
                return
            self._recent[dedupe_key] = now
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            log.warning("telegram: queue full, dropping alert")

    async def _drain(self) -> None:
        while True:
            text = await self._queue.get()
            try:
                await self._client.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                )
            except httpx.HTTPError as exc:
                log.warning("telegram send failed: %s", exc)
            await asyncio.sleep(0.5)  # Bot API tolerates ~30 msg/s; stay far below

    # ---- canned messages --------------------------------------------------

    def fill(self, fill) -> None:
        self.send(
            f"FILL {fill.side} {fill.size:.2f} @ {fill.price:.4f} "
            f"({fill.strategy}, {'maker' if fill.is_maker else 'taker'}, fee {fill.fee:.4f})"
        )

    def veto(self, signal, reason: str) -> None:
        self.send(
            f"VETO {signal.strategy} {signal.side} {signal.token_id[:10]} @ {signal.price:.4f}: {reason}",
            dedupe_key=f"veto:{signal.strategy}:{reason}",
        )

    def kill_switch(self, reason: str) -> None:
        self.send(f"KILL SWITCH TRIPPED: {reason}", dedupe_key=f"kill:{reason}")

    def fee_change(self, category: str, old: float, new: float) -> None:
        self.send(f"FEE RATE CHANGED: {category} {old} -> {new}", dedupe_key=f"fee:{category}")

    def heartbeat(self, equity: float, open_orders: int, book_age_s: float) -> None:
        self.send(
            f"heartbeat: equity ${equity:.2f} | {open_orders} open orders | "
            f"book age {book_age_s:.1f}s"
        )

    def daily_summary(self, stats, equity: float, fees_paid: float) -> None:
        lines = [f"<b>Daily summary</b> — equity ${equity:.2f}, fees ${fees_paid:.2f}"]
        for s in stats:
            honesty = s.edge_honesty
            lines.append(
                f"{s.strategy}: {s.trades} trades, {s.win_rate * 100:.0f}% win, "
                f"pnl ${s.pnl:.2f}, weight {s.weight:.2f}"
                + (f", edge realised/predicted {honesty:.2f}" if honesty is not None else "")
            )
        self.send("\n".join(lines))
