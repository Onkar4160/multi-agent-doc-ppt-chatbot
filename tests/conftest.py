"""Pytest configuration for test suite."""

import os
import pytest
from app.llm.client import reset_llm_client


@pytest.fixture(autouse=True, scope="session")
def setup_test_env():
    """Ensure pytest always runs with MOCK_LLM=true by default."""
    os.environ["MOCK_LLM"] = "true"
    reset_llm_client()
    yield
