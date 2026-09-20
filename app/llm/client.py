"""Centralised Gemini LLM client with structured output, retry, fallback, latency logging, and disk cache."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_CACHE_DIR = Path("storage/llm_cache")


class LLMClient:
    """Wrapper around the google-genai SDK with safety-net features."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = genai.Client(api_key=settings.gemini_api_key or "MOCK_KEY")
        self._primary = settings.gemini_model
        self._fallback = settings.gemini_fallback_model
        self._max_retries = settings.llm_max_retries
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)

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
                logger.debug("LLM cache hit for text query %s", cache_key[:12])
                return cached

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
                logger.debug("LLM cache hit for JSON schema %s (%s)", schema.__name__, cache_key[:12])
                return schema.model_validate_json(cached)

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
        return self._call_with_retry(
            prompt,
            system=system,
            temperature=temperature,
            image_bytes=image_bytes,
            image_mime=mime_type,
        )

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
                    logger.info(
                        "LLM call to model '%s' completed successfully in %.2f ms",
                        model_name,
                        elapsed_ms,
                    )
                    return res
                except Exception as exc:
                    err_str = str(exc)
                    is_retryable = any(code in err_str for code in ("429", "500", "502", "503", "504"))
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
        path = _CACHE_DIR / f"{key}.json"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    @staticmethod
    def _write_cache(key: str, data: str) -> None:
        """Write response to disk cache."""
        path = _CACHE_DIR / f"{key}.json"
        path.write_text(data, encoding="utf-8")


# ── Singleton accessor ───────────────────────────────────

_instance: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Return a cached LLMClient singleton."""
    global _instance
    if _instance is None:
        _instance = LLMClient()
    return _instance
