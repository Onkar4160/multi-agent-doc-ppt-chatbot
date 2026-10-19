"""Per-run LLM usage accounting and call budget (NFR-4, ADR-022).

``track_run(budget)`` binds a ``RunUsage`` to the code running inside it. ``LLMClient`` reports every call (cache
hits included) and asks ``charge()`` before each real request; once the budget is spent ``BudgetExceeded`` stops
the run instead of silently burning the free-tier quota. Outside a tracked run nothing is limited.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator

from app.core.errors import BudgetExceeded


@dataclass
class RunUsage:
    budget: int | None = None
    calls: int = 0              # real requests sent to the provider (retries included)
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    by_model: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["latency_ms"] = round(self.latency_ms)
        return data


_current: ContextVar[RunUsage | None] = ContextVar("llm_run_usage", default=None)


@contextmanager
def track_run(budget: int | None = None) -> Iterator[RunUsage]:
    usage = RunUsage(budget=budget)
    token = _current.set(usage)
    try:
        yield usage
    finally:
        _current.reset(token)


def current_usage() -> RunUsage | None:
    return _current.get()


def charge() -> None:
    """Called before each real request: raise when the run's budget is used up."""
    usage = _current.get()
    if usage is not None and usage.budget is not None and usage.calls >= usage.budget:
        raise BudgetExceeded(
            f"this request needed more than its budget of {usage.budget} AI calls, so it was stopped"
        )


def record_call(model: str, latency_ms: float) -> None:
    usage = _current.get()
    if usage is not None:
        usage.calls += 1
        usage.latency_ms += latency_ms
        usage.by_model[model] = usage.by_model.get(model, 0) + 1


def record_cache_hit() -> None:
    usage = _current.get()
    if usage is not None:
        usage.cache_hits += 1


def record_tokens(metadata: Any) -> None:
    """Token counts from a google-genai ``usage_metadata`` object (missing fields count as 0)."""
    usage = _current.get()
    if usage is None or metadata is None:
        return
    usage.input_tokens += int(getattr(metadata, "prompt_token_count", 0) or 0)
    usage.output_tokens += int(getattr(metadata, "candidates_token_count", 0) or 0)
