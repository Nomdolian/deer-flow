"""Runs the "learning loop" as a scheduled background process: post-trade LLM
classification (frequent), weekly mechanical strategy re-weighting (hard-
coded, not LLM judgment — Phase 5, item 4), and the weekly LLM mistake-
pattern report. Without this process running, app/journal/classification.py,
app/journal/stats.py, and app/llm_analyst/mistake_report.py are just callable
functions that nothing ever invokes.

Runs as a SEPARATE process from the trading loop (scripts/run_watchlist.py /
run_live_paper.py) — the LLM calls here are slow and must never sit on the
execution path (non-negotiable constraint: no LLM call sits on the real-time
execution critical path).

    python -m scripts.run_scheduler --classification-interval-minutes 15

Requires ANTHROPIC_API_KEY in .env — a job that can't build an LLMAnalyst
logs the failure and skips that run rather than crashing the process.
"""

import argparse

from apscheduler.schedulers.blocking import BlockingScheduler

from app.db.base import SessionLocal, init_db
from app.learning.jobs import run_classification_sweep, run_mistake_report, run_weekly_review
from app.llm_analyst.client import LLMAnalyst


def _classification_job() -> None:
    session = SessionLocal()
    try:
        analyst = LLMAnalyst()
        count = run_classification_sweep(session, analyst)
        print(f"[classification] attempted {count} trade(s)")
    except Exception as exc:  # noqa: BLE001 - background job must not crash the scheduler over one bad run
        print(f"[classification] skipped: {exc}")
    finally:
        session.close()


def _weekly_review_job() -> None:
    session = SessionLocal()
    try:
        actions = run_weekly_review(session)
        print(f"[weekly review] {len(actions)} strategy/instrument combination(s) reviewed")
        for line in actions:
            print(f"  {line}")
    finally:
        session.close()


def _mistake_report_job() -> None:
    session = SessionLocal()
    try:
        analyst = LLMAnalyst()
        report = run_mistake_report(session, analyst)
        print(f"[mistake report]\n{report}")
    except Exception as exc:  # noqa: BLE001 - same as _classification_job
        print(f"[mistake report] skipped: {exc}")
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--classification-interval-minutes", type=int, default=15)
    parser.add_argument("--weekly-day", default="mon", help="cron day_of_week for the weekly jobs")
    args = parser.parse_args()

    init_db()
    scheduler = BlockingScheduler()
    scheduler.add_job(
        _classification_job, "interval", minutes=args.classification_interval_minutes, id="classification_sweep"
    )
    scheduler.add_job(_weekly_review_job, "cron", day_of_week=args.weekly_day, hour=0, minute=0, id="weekly_review")
    scheduler.add_job(_mistake_report_job, "cron", day_of_week=args.weekly_day, hour=0, minute=5, id="mistake_report")

    print(
        f"scheduler started — classification every {args.classification_interval_minutes}m, "
        f"weekly review + mistake report {args.weekly_day} 00:00/00:05 UTC"
    )
    scheduler.start()


if __name__ == "__main__":
    main()
