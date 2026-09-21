"""Pytest configuration for test suite."""

import os
import shutil
import tempfile
from pathlib import Path
import pytest

# Initialize temporary directory immediately so module-level imports use temp storage/db
_temp_dir = tempfile.mkdtemp(prefix="test_isolation_")
_temp_db_path = Path(_temp_dir) / "test.db"
_temp_storage = Path(_temp_dir) / "storage"
_temp_storage.mkdir(parents=True, exist_ok=True)

os.environ["MOCK_LLM"] = "true"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_temp_db_path.as_posix()}"
os.environ["STORAGE_DIR"] = str(_temp_storage)

from app.core.config import get_settings, reset_settings
from app.core.database import reset_db
from app.llm.client import reset_llm_client

reset_settings()
reset_db()
reset_llm_client()


@pytest.fixture(autouse=True, scope="session")
def setup_test_env():
    """Ensure pytest runs with MOCK_LLM=true, temporary SQLite file, and temporary storage."""
    os.environ["MOCK_LLM"] = "true"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_temp_db_path.as_posix()}"
    os.environ["STORAGE_DIR"] = str(_temp_storage)

    reset_settings()
    reset_db()
    reset_llm_client()

    yield

    reset_settings()
    reset_db()
    reset_llm_client()
    shutil.rmtree(_temp_dir, ignore_errors=True)
