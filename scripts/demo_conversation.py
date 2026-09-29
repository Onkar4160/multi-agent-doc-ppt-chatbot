"""Demo conversation script running a 5-turn flow through the real pipeline API.

Turn 1: Generate a short report + deck
Turn 2: Ask what the report says about one subtopic (Document Q&A)
Turn 3: "add a section on <subtopic>"
Turn 4: "make the presentation shorter"
Turn 5: Print session artifacts and versions

Exit codes:
  0 = All turns succeeded
  1 = Genuinely failed (code bug, assertion error)
  2 = Quota limited / 429 ResourceExhausted
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Ensure real API keys are present
def _check_env() -> None:
    if os.environ.get("MOCK_LLM", "").lower() in ("true", "1", "yes"):
        print("This demo conversation requires real API calls, not mock mode.")
        sys.exit(1)

_check_env()

import logging
logging.basicConfig(level=logging.WARNING)

from app.agents.graph import _get_sync_session, graph_app
from app.agents.state import GraphState
from app.llm.client import get_llm_client
from app.models.artifact import Artifact, ArtifactVersion


def is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "quota" in msg or "resourceexhausted" in msg or "rate limit" in msg


def run_turn(session_id: str, turn_num: int, user_message: str) -> tuple[dict[str, Any], int]:
    llm = get_llm_client()
    calls_before = llm.stats.real_calls

    run_id = f"demo_turn_{turn_num}_{uuid.uuid4().hex[:6]}"
    state: GraphState = {
        "run_id": run_id,
        "session_id": session_id,
        "user_message": user_message,
        "file_ids": [],
        "artifacts": [],
        "errors": [],
        "retry_count": 0,
    }

    try:
        final_state = graph_app.invoke(state)
    except Exception as exc:
        if is_quota_error(exc):
            print(f"[Turn {turn_num}] QUOTA ERROR: {exc}")
            sys.exit(2)
        print(f"[Turn {turn_num}] FAILED: {exc}")
        sys.exit(1)

    calls_used = llm.stats.real_calls - calls_before
    plan = final_state.get("plan", {})
    action = plan.get("action", "unknown")
    artifacts = final_state.get("artifacts", [])

    print(f"\n--- Turn {turn_num}: '{user_message}' ---")
    print(f"Action: {action}")
    print(f"LLM Calls Used: {calls_used}")
    print(f"Artifacts: {[(a.get('kind'), a.get('artifact_id'), a.get('version')) for a in artifacts]}")
    if final_state.get("reply"):
        print(f"Reply Preview: {final_state['reply'][:180]}...")

    return final_state, calls_used


def main() -> None:
    session_id = f"demo_sess_{uuid.uuid4().hex[:8]}"
    print(f"Starting Demo Conversation for Session: {session_id}")

    try:
        # Turn 1: generate short report + deck
        run_turn(session_id, 1, "Create a short 6-slide presentation and document on Quantum Computing applications")

        # Turn 2: document Q&A
        run_turn(session_id, 2, "What does the report say about cryptography?")

        # Turn 3: conversational edit (add section)
        run_turn(session_id, 3, "add a section on post-quantum cryptography standards")

        # Turn 4: condense presentation
        run_turn(session_id, 4, "make the presentation shorter")

        # Turn 5: print session artifacts and versions
        print(f"\n--- Turn 5: Inspect Session Artifacts and Versions ---")
        db = _get_sync_session()
        if not db:
            print("Database session unavailable.")
            sys.exit(1)

        arts = db.query(Artifact).filter(Artifact.session_id == session_id).order_by(Artifact.id.asc()).all()
        print(f"Total Artifacts in Session: {len(arts)}")
        for a in arts:
            vers = db.query(ArtifactVersion).filter(ArtifactVersion.artifact_id == a.id).order_by(ArtifactVersion.version_no.asc()).all()
            print(f"Artifact {a.id} [{a.artifact_type.upper()}] '{a.title}':")
            for v in vers:
                print(f"  - v{v.version_no}: {v.change_summary or 'No summary'} ({v.file_path})")
        db.close()

        print("\n[SUCCESS] 5-turn demo conversation completed successfully.")
        sys.exit(0)

    except Exception as exc:
        if is_quota_error(exc):
            print(f"[QUOTA ERROR] {exc}")
            sys.exit(2)
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
