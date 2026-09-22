"""Centralised Gemini LLM client with structured output, retry, fallback, latency logging, and disk cache."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

def get_cache_dir() -> Path:
    """Return cache directory for LLM calls, creating it if missing."""
    p = Path(get_settings().storage_dir) / "llm_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_search_cache_dir() -> Path:
    """Return cache directory for web search results, creating it if missing."""
    p = Path(get_settings().storage_dir) / "search_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


_get_cache_dir = get_cache_dir


@dataclass
class LLMCallStats:
    """Tracks LLM call statistics for a session."""

    real_calls: int = 0
    cache_hits: int = 0
    mock_calls: int = 0

    @property
    def total_calls(self) -> int:
        return self.real_calls + self.mock_calls

    @property
    def call_count(self) -> int:
        return self.total_calls

    def summary(self) -> str:
        """Return a one-line summary string."""
        return (
            f"real_calls={self.real_calls}, "
            f"cache_hits={self.cache_hits}, "
            f"mock_calls={self.mock_calls}"
        )


@dataclass
class LLMCallRecord:
    """Metadata for a single LLM call, suitable for logging into AgentTrace."""

    model: str
    cache_hit: bool
    latency_ms: float
    mock: bool = False


class LLMClient:
    """Wrapper around the google-genai SDK with safety-net features."""

    def __init__(self) -> None:
        settings = get_settings()
        self._mock_mode = settings.mock_llm
        self._primary = settings.gemini_model
        self._fallback = settings.gemini_fallback_model
        self._max_retries = settings.llm_max_retries
        self._stats = LLMCallStats()
        self._last_record: LLMCallRecord | None = None

        if self._mock_mode and "pytest" not in sys.modules:
            sys.exit(
                "MOCK_LLM is only for tests. Remove it from .env"
            )

        if self._mock_mode:
            _warn_mock_mode()
            self._client = None
        else:
            api_key = settings.gemini_api_key
            if not api_key or api_key.strip() in ("", "EX", "mock-gemini-key", "MOCK_KEY"):
                raise RuntimeError(
                    "GEMINI_API_KEY missing or placeholder. "
                    "Add a valid key to .env, or set MOCK_LLM=true for testing."
                )
            self._client = genai.Client(api_key=api_key)

        get_cache_dir()

    @property
    def stats(self) -> LLMCallStats:
        """Return call statistics for this client session."""
        return self._stats

    @property
    def last_record(self) -> LLMCallRecord | None:
        """Return the last LLM call record (for AgentTrace logging)."""
        return self._last_record

    # ── Public methods ───────────────────────────────────

    def generate_text(
        self,
        prompt: str,
        system: str | None = None,
        *,
        use_cache: bool = True,
        temperature: float = 0.7,
    ) -> str:
        """Generate plain text output from Gemini."""
        cache_key = self._cache_key(self._primary, system, prompt, "text")
        if use_cache:
            cached = self._read_cache(cache_key)
            if cached is not None:
                logger.info("LLM cache hit for text query %s", cache_key[:12])
                self._stats.cache_hits += 1
                self._last_record = LLMCallRecord(
                    model=self._primary, cache_hit=True, latency_ms=0.0,
                )
                return cached

        if self._mock_mode:
            return self._mock_text_response(prompt)

        raw = self._call_with_retry(
            prompt,
            system=system,
            temperature=temperature,
        )

        if use_cache:
            self._write_cache(cache_key, raw)
        return raw

    def generate_json(
        self,
        prompt: str,
        schema: type[T],
        system: str | None = None,
        *,
        use_cache: bool = True,
        temperature: float = 0.3,
    ) -> T:
        """Generate structured JSON output validated against a Pydantic schema."""
        cache_key = self._cache_key(self._primary, system, prompt, schema.__name__)
        if use_cache:
            cached = self._read_cache(cache_key)
            if cached is not None:
                logger.info(
                    "LLM cache hit for JSON schema %s (%s)",
                    schema.__name__, cache_key[:12],
                )
                self._stats.cache_hits += 1
                self._last_record = LLMCallRecord(
                    model=self._primary, cache_hit=True, latency_ms=0.0,
                )
                return schema.model_validate_json(cached)

        if self._mock_mode:
            return self._mock_json_response(schema)

        raw = self._call_with_retry(
            prompt,
            system=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=temperature,
        )
        result = schema.model_validate_json(raw)

        if use_cache:
            self._write_cache(cache_key, raw)
        return result

    def generate_with_image(
        self,
        prompt: str,
        image_bytes: bytes,
        mime_type: str = "image/png",
        system: str | None = None,
        *,
        temperature: float = 0.2,
    ) -> str:
        """Send an image + prompt to Gemini vision and return response text."""
        if self._mock_mode:
            return self._mock_text_response(prompt)

        return self._call_with_retry(
            prompt,
            system=system,
            temperature=temperature,
            image_bytes=image_bytes,
            image_mime=mime_type,
        )

    # ── Mock helpers ─────────────────────────────────────

    def _mock_text_response(self, prompt: str) -> str:
        """Return a canned text response in mock mode."""
        self._stats.mock_calls += 1
        self._last_record = LLMCallRecord(
            model="[MOCK]", cache_hit=False, latency_ms=0.0, mock=True,
        )
        logger.warning("[MOCK] Returning canned text response (prompt: %.60s…)", prompt)
        return "[MOCK] Canned response for testing purposes."

    def _mock_json_response(self, schema: type[T]) -> T:
        """Return a minimal valid instance of schema in mock mode."""
        self._stats.mock_calls += 1
        self._last_record = LLMCallRecord(
            model="[MOCK]", cache_hit=False, latency_ms=0.0, mock=True,
        )
        logger.warning("[MOCK] Returning canned JSON response for schema %s", schema.__name__)
        try:
            return schema.model_validate({})
        except Exception:
            s_name = schema.__name__
            if s_name == "DocumentModel":
                return schema.model_validate({
                    "title": "[MOCK] Sample Proposal Document",
                    "subtitle": "Sample Engagement Architecture",
                    "client_name": "Sample Enterprise Client",
                    "date": "September 2026",
                    "sections": [
                        {"heading": "1. Executive Summary", "level": 1, "blocks": [{"type": "paragraph", "text": "Sample mock paragraph content with citation source.", "source_ids": [1]}]},
                        {"heading": "2. Problem Statement", "level": 1, "blocks": [{"type": "paragraph", "text": "Sample problem statement.", "source_ids": [1]}]},
                        {"heading": "3. Proposed Solution", "level": 1, "blocks": [{"type": "paragraph", "text": "Sample solution description.", "source_ids": [1]}]},
                        {"heading": "4. Methodology", "level": 1, "blocks": [{"type": "paragraph", "text": "Sample methodology.", "source_ids": [1]}]},
                        {"heading": "5. Commercials & Timeline", "level": 1, "blocks": [{"type": "paragraph", "text": "Sample commercials.", "source_ids": [1]}]},
                        {"heading": "6. Sources & References", "level": 1, "blocks": [{"type": "paragraph", "text": "Sample references.", "source_ids": [1]}]},
                    ],
                })
            elif s_name == "DeckModel":
                return schema.model_validate({
                    "title": "[MOCK] Sample Presentation Deck",
                    "slides": [
                        {"role": "title", "title": "Mock Title Slide", "subtitle": "Mock Subtitle", "source_ids": [1]},
                        {"role": "section_header", "title": "Mock Section Header", "subtitle": "Mock Subtitle", "source_ids": [1]},
                        {"role": "title_content", "title": "Mock Content Slide 1", "bullets": [{"text": "Mock bullet text item 1.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 2.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 3.", "level": 0, "source_ids": [1]}], "notes": "Mock notes 1.", "source_ids": [1]},
                        {"role": "two_content", "title": "Mock Comparison Slide", "left": [{"text": "Left bullet item 1.", "level": 0, "source_ids": [1]}, {"text": "Left bullet item 2.", "level": 0, "source_ids": [1]}, {"text": "Left bullet item 3.", "level": 0, "source_ids": [1]}], "right": [{"text": "Right bullet item 1.", "level": 0, "source_ids": [1]}, {"text": "Right bullet item 2.", "level": 0, "source_ids": [1]}, {"text": "Right bullet item 3.", "level": 0, "source_ids": [1]}], "notes": "Mock notes 2.", "source_ids": [1]},
                        {"role": "title_content", "title": "Mock Content Slide 2", "bullets": [{"text": "Mock bullet text item 4.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 5.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 6.", "level": 0, "source_ids": [1]}], "notes": "Mock notes 3.", "source_ids": [1]},
                        {"role": "title_content", "title": "Mock Content Slide 3", "bullets": [{"text": "Mock bullet text item 7.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 8.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 9.", "level": 0, "source_ids": [1]}], "notes": "Mock notes 4.", "source_ids": [1]},
                        {"role": "title_content", "title": "Mock Content Slide 4", "bullets": [{"text": "Mock bullet text item 10.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 11.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 12.", "level": 0, "source_ids": [1]}], "notes": "Mock notes 5.", "source_ids": [1]},
                        {"role": "title_content", "title": "Mock Content Slide 5", "bullets": [{"text": "Mock bullet text item 13.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 14.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 15.", "level": 0, "source_ids": [1]}], "notes": "Mock notes 6.", "source_ids": [1]},
                        {"role": "title_content", "title": "Mock Content Slide 6", "bullets": [{"text": "Mock bullet text item 16.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 17.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 18.", "level": 0, "source_ids": [1]}], "notes": "Mock notes 7.", "source_ids": [1]},
                        {"role": "title_content", "title": "Mock Content Slide 7", "bullets": [{"text": "Mock bullet text item 19.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 20.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 21.", "level": 0, "source_ids": [1]}], "notes": "Mock notes 8.", "source_ids": [1]},
                        {"role": "title_content", "title": "Mock Content Slide 8", "bullets": [{"text": "Mock bullet text item 22.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 23.", "level": 0, "source_ids": [1]}, {"text": "Mock bullet text item 24.", "level": 0, "source_ids": [1]}], "notes": "Mock notes 9.", "source_ids": [1]},
                        {"role": "title_only", "title": "Mock Conclusion Slide", "subtitle": "Mock Subtitle", "source_ids": [1]},
                    ],
                })
            elif s_name == "SearchQueriesSchema":
                return schema.model_validate({"queries": ["Generative AI trends 2026", "Enterprise RAG adoption 2026"]})
            elif s_name == "ResearchFindingsSchema":
                return schema.model_validate({
                    "findings": [
                        {"text": "72% of mid-size enterprises plan agentic workflow adoption.", "source_ids": [1]},
                        {"text": "Automated document creation reduces proposal SLA by 80%.", "source_ids": [1]},
                    ]
                })
            elif s_name == "Plan":
                return schema.model_validate({
                    "action": "generate",
                    "outputs": ["docx", "pptx"],
                    "topic": "Sample Topic",
                    "slide_count": 12,
                })
            elif s_name == "EditPlan":
                return schema.model_validate({
                    "target": "docx",
                    "ops": [
                        {
                            "type": "add_section",
                            "after_heading": "1. Executive Summary",
                            "section": {
                                "heading": "New Section",
                                "level": 1,
                                "blocks": [{"type": "paragraph", "text": "Mock added section text.", "source_ids": [1]}],
                            },
                        }
                    ],
                    "summary": "Mock edit plan operation",
                })
            dummy_data = {}
            for fname, ffield in schema.model_fields.items():
                if ffield.is_required():
                    dummy_data[fname] = f"Mock {fname}"
            return schema.model_validate(dummy_data)

    # ── Internal helpers ─────────────────────────────────

    def _call_with_retry(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_mime_type: str | None = None,
        response_schema: type[BaseModel] | None = None,
        temperature: float = 0.7,
        image_bytes: bytes | None = None,
        image_mime: str = "image/png",
    ) -> str:
        """Call Gemini with exponential backoff retry and model fallback."""
        models = [self._primary, self._fallback]

        for model_name in models:
            for attempt in range(1, self._max_retries + 1):
                try:
                    start_time = time.perf_counter()
                    res = self._single_call(
                        model_name,
                        prompt,
                        system=system,
                        response_mime_type=response_mime_type,
                        response_schema=response_schema,
                        temperature=temperature,
                        image_bytes=image_bytes,
                        image_mime=image_mime,
                    )
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    self._stats.real_calls += 1
                    self._last_record = LLMCallRecord(
                        model=model_name, cache_hit=False, latency_ms=elapsed_ms,
                    )
                    logger.info(
                        "LLM call to model '%s' completed in %.0f ms",
                        model_name,
                        elapsed_ms,
                    )
                    return res
                except Exception as exc:
                    err_str = str(exc)
                    is_retryable = any(
                        code in err_str for code in ("429", "500", "502", "503", "504")
                    )
                    logger.warning(
                        "LLM call failed (model=%s attempt=%d/%d): %s",
                        model_name,
                        attempt,
                        self._max_retries,
                        err_str[:200],
                    )
                    if is_retryable and attempt < self._max_retries:
                        wait = 2 ** attempt
                        logger.info("Retrying in %ds…", wait)
                        time.sleep(wait)
                    elif not is_retryable:
                        break  # non-retryable error -> try fallback model
                    else:
                        break  # exhausted retries -> try fallback model

            logger.info("Falling back from '%s' to next model…", model_name)

        raise RuntimeError("All LLM models exhausted after retries")

    def _single_call(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None,
        response_mime_type: str | None,
        response_schema: type[BaseModel] | None,
        temperature: float,
        image_bytes: bytes | None,
        image_mime: str,
    ) -> str:
        """Make a single Gemini API call."""
        assert self._client is not None, "Cannot make API call: client is None (mock mode?)"

        config_kwargs: dict = {"temperature": temperature}
        if system:
            config_kwargs["system_instruction"] = system
        if response_mime_type:
            config_kwargs["response_mime_type"] = response_mime_type
        if response_schema:
            config_kwargs["response_schema"] = response_schema

        config = types.GenerateContentConfig(**config_kwargs)

        # Build contents
        if image_bytes:
            contents = [
                types.Part.from_text(text=prompt),
                types.Part.from_bytes(data=image_bytes, mime_type=image_mime),
            ]
        else:
            contents = prompt

        response = self._client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        return response.text or ""

    # ── Disk cache ───────────────────────────────────────

    @staticmethod
    def _cache_key(model: str, system: str | None, prompt: str, suffix: str) -> str:
        """Compute a SHA-256 cache key from model + system + prompt + suffix."""
        raw_key = f"{model}::{system or ''}::{prompt}::{suffix}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @staticmethod
    def _read_cache(key: str) -> str | None:
        """Read cached response from disk."""
        path = get_cache_dir() / f"{key}.json"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    @staticmethod
    def _write_cache(key: str, data: str) -> None:
        """Write response to disk cache."""
        path = get_cache_dir() / f"{key}.json"
        path.write_text(data, encoding="utf-8")


def _warn_mock_mode() -> None:
    """Emit a loud warning when mock mode is active."""
    msg = (
        "\n" + "=" * 70 + "\n"
        "  WARNING: MOCK_LLM=true — All LLM calls return canned data.\n"
        "  This mode is intended for pytest ONLY.\n"
        + "=" * 70 + "\n"
    )
    warnings.warn(msg, stacklevel=3)
    logger.warning(msg)


# ── Singleton accessor ───────────────────────────────────

_instance: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Return a cached LLMClient singleton."""
    global _instance
    if _instance is None:
        _instance = LLMClient()
    return _instance


def reset_llm_client() -> None:
    """Reset the singleton (used by tests)."""
    global _instance
    _instance = None
