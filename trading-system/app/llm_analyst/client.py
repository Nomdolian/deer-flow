import json

from anthropic import Anthropic

from app.config import settings

_MODEL = "claude-sonnet-5"


class LLMAnalyst:
    """Thin wrapper around the Claude API. This is the ONLY place in the system
    that calls an LLM, and it is never on the execution critical path (Phase 6,
    item 3) — it runs on signal formation (for thesis synthesis, informational
    only) and on trade close / weekly schedule (for review), never in a tight
    per-tick loop across asset classes.
    """

    def __init__(self, api_key: str | None = None):
        key = api_key or settings.anthropic_api_key
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured — the LLM analyst layer is disabled")
        self._client = Anthropic(api_key=key)

    def complete_json(self, *, system: str, user: str, max_tokens: int = 1024) -> dict:
        """Ask the model for a single JSON object and parse it. Callers must
        treat a parse failure as "no result" (fail closed), never as a reason to
        retry against live capital logic."""
        response = self._client.messages.create(
            model=_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            start, end = text.index("{"), text.rindex("}") + 1
            return json.loads(text[start:end])
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"LLM analyst did not return parseable JSON: {text!r}") from exc

    def complete_text(self, *, system: str, user: str, max_tokens: int = 1024) -> str:
        response = self._client.messages.create(
            model=_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if block.type == "text")
