from dataclasses import dataclass

from app.db.models import Direction
from app.llm_analyst.client import LLMAnalyst
from app.signals.base import Signal

_SYSTEM = """You are the analyst layer of a trading system. You never place or size \
trades — you only synthesize context for a human trader and for the trade log. \
Be concise and concrete. Do not invent confluences that were not provided."""


@dataclass(frozen=True, slots=True)
class ThesisResult:
    instrument: str
    agreement: bool
    thesis: str
    contradictions: list[str]


def synthesize_thesis(analyst: LLMAnalyst, signals: list[Signal]) -> ThesisResult | None:
    """Phase 6, item 1a/1b: when several engines agree on an instrument, write a
    thesis; when they disagree, flag the contradiction for display — resolution
    is a hard-coded precedence rule elsewhere or requires manual input, never
    this function's job."""
    if not signals:
        return None
    instrument = signals[0].instrument
    directions = {s.direction for s in signals}
    agreement = len(directions) == 1

    contradictions = []
    if not agreement:
        by_direction: dict[Direction, list[str]] = {}
        for s in signals:
            by_direction.setdefault(s.direction, []).append(s.strategy_id)
        contradictions = [f"{d.value}: {', '.join(ids)}" for d, ids in by_direction.items()]
        thesis = (
            f"Engines disagree on {instrument}: " + "; ".join(contradictions) + ". No consensus thesis generated."
        )
        return ThesisResult(instrument=instrument, agreement=False, thesis=thesis, contradictions=contradictions)

    user = (
        f"Instrument: {instrument}\nDirection: {signals[0].direction.value}\n\n"
        "Engine signals (each independently computed, deterministic):\n"
        + "\n".join(
            f"- {s.strategy_id} v{s.strategy_version}: confidence={s.confidence_score:.2f}, "
            f"entry={s.entry}, sl={s.stop_loss}, tp={s.take_profit}, confluences={s.confluences}"
            for s in signals
        )
        + "\n\nWrite a 3-5 sentence trade thesis synthesizing why these engines agree, referencing "
        "only the confluences listed above. This is informational context for the trader and journal, "
        "not a trade decision."
    )
    text = analyst.complete_text(system=_SYSTEM, user=user, max_tokens=400)
    return ThesisResult(instrument=instrument, agreement=True, thesis=text, contradictions=[])
