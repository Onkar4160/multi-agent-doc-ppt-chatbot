"""Smoke test script for LLMClient wrapper."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure root directory is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from pydantic import BaseModel

from app.core.config import get_settings
from app.llm.client import get_llm_client, reset_llm_client

logging.basicConfig(level=logging.INFO)


class TinyResponse(BaseModel):
    """Tiny schema for testing structured output."""
    message: str
    status: str


def main():
    """Run smoke tests for generate_text and generate_json."""
    settings = get_settings()
    print(f"Primary model: {settings.gemini_model}")
    print(f"Fallback model: {settings.gemini_fallback_model}")
    print(f"API key configured: {'Yes' if settings.gemini_api_key and settings.gemini_api_key != 'mock-gemini-key' else 'No (Mock/Default)'}")

    client = get_llm_client()

    print("\n--- Testing generate_text ---")
    try:
        text_res = client.generate_text(
            prompt="Hello, reply with 'Gemini system check ok'.",
            system="You are a helpful assistant.",
            use_cache=False,
        )
        print(f"Response: {text_res}")
    except Exception as exc:
        print(f"generate_text executed (result/error: {exc})")

    print("\n--- Testing generate_json ---")
    try:
        json_res = client.generate_json(
            prompt="Return status 'ok' and message 'all systems operational'.",
            schema=TinyResponse,
            system="You return valid JSON adhering strictly to the schema.",
            use_cache=False,
        )
        print(f"Response (Pydantic model): {json_res}")
    except Exception as exc:
        print(f"generate_json executed (result/error: {exc})")


if __name__ == "__main__":
    main()
