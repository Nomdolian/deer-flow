"""Entrypoint: python -m pmbot

Traps SIGTERM/SIGINT so that a restart, a deploy, or a Ctrl-C always cancels
resting orders on the way out. An orphaned order is a free option for
everyone else on the book.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from pmbot.config import Secrets, load_config
from pmbot.logging_utils import setup_logging
from pmbot.orchestrator import Bot

log = logging.getLogger("pmbot")


async def main(config_path: str, mode_override: str | None) -> int:
    cfg = load_config(config_path)
    if mode_override:
        cfg.mode = mode_override  # type: ignore[assignment]
    secrets = Secrets()
    setup_logging()

    bot = Bot(cfg, secrets)
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()

    def request_stop(signame: str) -> None:
        log.warning("received %s; shutting down", signame)
        stopping.set()

    for signame in ("SIGINT", "SIGTERM"):
        with_signal = getattr(signal, signame, None)
        if with_signal is not None:
            loop.add_signal_handler(with_signal, request_stop, signame)

    runner = asyncio.create_task(bot.run_forever(), name="bot")
    stopper = asyncio.create_task(stopping.wait(), name="stop")
    done, _ = await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)

    await bot.shutdown()
    runner.cancel()
    stopper.cancel()

    for task in done:
        if task is runner and not task.cancelled():
            exc = task.exception()
            if exc is not None:
                log.error("bot exited with error: %s", exc)
                return 1
    return 0


def cli() -> int:
    parser = argparse.ArgumentParser(prog="pmbot", description="Polymarket trading bot")
    parser.add_argument("--config", default="bot.yaml")
    parser.add_argument("--mode", choices=["paper", "live"],
                        help="override bot.yaml (live requires a funded wallet)")
    args = parser.parse_args()
    try:
        return asyncio.run(main(args.config, args.mode))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(cli())
