"""Real end-to-end integration health check script.

Tests all external connections (Gemini, Pinecone, Tavily/DuckDuckGo) and runs
a minimal complete generation cycle, conversational edit, and grounded Q&A.
This script is intended to be executed manually when spending real API quota.
"""

from __future__ import annotations

import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# ── 1. Refuse to run in mock mode ──────────────────────────────────────────
def _check_mock_mode() -> None:
    env_file = ROOT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("MOCK_LLM") and "=" in line:
                val = line.split("=", 1)[1].strip().strip('"').strip("'").lower()
                if val in ("true", "1", "yes"):
                    print("This check needs real API keys, not mock mode")
                    sys.exit(1)
    if os.environ.get("MOCK_LLM", "").lower() in ("true", "1", "yes"):
        print("This check needs real API keys, not mock mode")
        sys.exit(1)


_check_mock_mode()

import logging
logging.basicConfig(level=logging.WARNING)

from docx import Document
from pptx import Presentation

from app.agents.editor import edit_artifact
from app.agents.graph import _get_sync_session, graph_app
from app.agents.state import GraphState
from app.core.config import get_settings
from app.core.database import Base
from app.llm.client import get_llm_client
from app.models.artifact import Artifact, ArtifactVersion


@dataclass
class StageResult:
    name: str
    status: str  # "PASS", "FAIL", "QUOTA-LIMITED"
    llm_calls: int
    duration_s: float
    details: str = ""


def is_quota_error(exc: BaseException) -> bool:
    err_str = str(exc).lower()
    return any(k in err_str for k in ("429", "resource_exhausted", "quota", "quota-limited"))


def format_duration(seconds: float) -> str:
    return f"{seconds:.2f}s"


# ── Stage 1: Connectivity Only ─────────────────────────────────────────────
def run_stage_1(client: Any) -> StageResult:
    print("\n" + "=" * 60)
    print("STAGE 1: External Services Connectivity Checks")
    print("=" * 60)
    start_time = time.perf_counter()
    calls_start = client.stats.real_calls
    settings = get_settings()

    gemini_status = "FAIL"
    pinecone_status = "FAIL"
    search_status = "FAIL"

    # 1. Gemini minimal call
    try:
        if client._mock_mode or client._client is None:
            raise RuntimeError("LLM client not configured for live API calls.")
        # Minimal single call with temperature 0
        response = client._client.models.generate_content(
            model=client._primary,
            contents="reply with the word OK",
        )
        client.stats.real_calls += 1
        resp_text = (response.text or "").strip()
        print(f"  [PASS] Gemini Connectivity OK (model: {client._primary}, reply: '{resp_text}')")
        gemini_status = "PASS"
    except Exception as exc:
        err = str(exc)
        if is_quota_error(exc):
            print(f"  [QUOTA-LIMITED] Gemini: quota-limited, not a bug ({err[:120]})")
            gemini_status = "QUOTA-LIMITED"
        elif any(k in err.lower() for k in ("401", "403", "api key", "permission_denied", "invalid_argument")):
            print(f"  [FAIL] Gemini Authentication / API key error: {err[:150]}")
        elif any(k in err.lower() for k in ("404", "not found")):
            print(f"  [FAIL] Gemini Model Not Found error: {err[:150]}")
        elif any(k in err.lower() for k in ("timeout", "connection", "socket", "dns")):
            print(f"  [FAIL] Gemini Network connection error: {err[:150]}")
        else:
            print(f"  [FAIL] Gemini general error: {err[:150]}")

    # 2. Pinecone index stats
    try:
        api_key = settings.pinecone_api_key.strip()
        if not api_key or api_key in ("mock-pinecone-key", "EX"):
            raise RuntimeError("PINECONE_API_KEY is not configured or placeholder.")
        from pinecone import Pinecone
        pc = Pinecone(api_key=api_key)
        idx = pc.Index(settings.pinecone_index)
        stats = idx.describe_index_stats()
        dim = stats.get("dimension") if isinstance(stats, dict) else getattr(stats, "dimension", "unknown")
        count = stats.get("total_vector_count") if isinstance(stats, dict) else getattr(stats, "total_vector_count", 0)
        print(f"  [PASS] Pinecone OK (index: '{settings.pinecone_index}', dimension: {dim}, vectors: {count})")
        pinecone_status = "PASS"
    except Exception as exc:
        print(f"  [FAIL] Pinecone check failed: {exc}")

    # 3. Web Search (Tavily or DuckDuckGo fallback)
    try:
        tavily_key = settings.tavily_api_key.strip()
        search_provider = None
        results_count = 0
        if tavily_key and tavily_key not in ("EX", "mock-tavily-key"):
            try:
                from tavily import TavilyClient
                tav = TavilyClient(api_key=tavily_key)
                res = tav.search(query="test query 2026", search_depth="basic", max_results=3)
                results_count = len(res.get("results", []))
                search_provider = "Tavily"
            except Exception as t_err:
                print(f"  [INFO] Tavily search failed ({t_err}); testing DuckDuckGo fallback...")
        if search_provider is None:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                ddg_res = list(ddgs.text("test query 2026", max_results=3))
                results_count = len(ddg_res)
                search_provider = "DuckDuckGo"
        print(f"  [PASS] Web Search OK (provider: {search_provider}, results: {results_count})")
        search_status = "PASS"
    except Exception as exc:
        print(f"  [FAIL] Web search check failed: {exc}")

    duration = time.perf_counter() - start_time
    used_calls = client.stats.real_calls - calls_start

    if gemini_status == "QUOTA-LIMITED":
        overall = "QUOTA-LIMITED"
    elif gemini_status == "PASS" and pinecone_status == "PASS" and search_status == "PASS":
        overall = "PASS"
    else:
        overall = "FAIL"

    return StageResult(
        name="STAGE 1: Connectivity Checks",
        status=overall,
        llm_calls=used_calls,
        duration_s=duration,
        details=f"Gemini: {gemini_status}, Pinecone: {pinecone_status}, Search: {search_status}",
    )


# ── Stage 2: Minimal Full Generation Cycle ──────────────────────────────────
def run_stage_2(client: Any) -> tuple[StageResult, dict[str, Any]]:
    print("\n" + "=" * 60)
    print("STAGE 2: Minimal Real Full Generation Cycle")
    print("=" * 60)
    start_time = time.perf_counter()
    calls_start = client.stats.real_calls
    created_artifacts: dict[str, Any] = {}

    brief = "Give a short update on renewable energy adoption in India (3 slides)"
    run_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    initial_state: GraphState = {
        "run_id": run_id,
        "session_id": session_id,
        "user_message": brief,
        "file_ids": [],
        "artifacts": [],
        "errors": [],
        "retry_count": 0,
        "findings": [],
        "template_profiles": {},
    }

    try:
        final_state = graph_app.invoke(initial_state)

        # 1. Assert both artifact entries created
        artifacts = final_state.get("artifacts", [])
        docx_info = next((a for a in artifacts if a.get("kind") == "docx"), None)
        pptx_info = next((a for a in artifacts if a.get("kind") == "pptx"), None)
        assert docx_info is not None, "DOCX artifact entry missing in final state"
        assert pptx_info is not None, "PPTX artifact entry missing in final state"

        created_artifacts["docx"] = docx_info
        created_artifacts["pptx"] = pptx_info

        # 2. Assert both physical files exist
        docx_path = Path(docx_info["file_path"])
        pptx_path = Path(pptx_info["file_path"])
        assert docx_path.exists(), f"DOCX file does not exist on disk: {docx_path}"
        assert pptx_path.exists(), f"PPTX file does not exist on disk: {pptx_path}"
        print(f"  [OK] Both files exist on disk: {docx_path.name}, {pptx_path.name}")

        # 3. Assert PPTX has exactly 3 content slides + 1 sources slide = 4 total slides
        prs = Presentation(str(pptx_path))
        total_slides = len(prs.slides)
        deck_model = final_state.get("deck_model", {})
        deck_content_slides = len(deck_model.get("slides", []))
        assert total_slides == 4, (
            f"PPTX slide count mismatch: expected 4 slides (3 content + 1 sources), found {total_slides}"
        )
        assert deck_content_slides == 3, (
            f"DeckModel content slides mismatch: expected 3, found {deck_content_slides}"
        )
        print(f"  [OK] PPTX structure validated: {deck_content_slides} content slides + 1 sources slide (total: {total_slides})")

        # 4. Assert DOCX has at least 1 section
        doc = Document(str(docx_path))
        doc_model = final_state.get("doc_model", {})
        model_sections = doc_model.get("sections", [])
        assert len(model_sections) >= 1, f"Expected >= 1 section in DocumentModel, found {len(model_sections)}"
        assert len(doc.sections) >= 1 or len(doc.paragraphs) > 0, "DOCX file has no paragraphs or sections"
        print(f"  [OK] DOCX structure validated: {len(model_sections)} section(s) generated")

        # 5. Assert ArtifactVersion.file_type in DB is correct for each
        db_sess = _get_sync_session()
        assert db_sess is not None, "Sync DB session unavailable"
        try:
            docx_ver = (
                db_sess.query(ArtifactVersion)
                .filter(ArtifactVersion.artifact_id == docx_info["artifact_id"])
                .order_by(ArtifactVersion.version_no.desc())
                .first()
            )
            pptx_ver = (
                db_sess.query(ArtifactVersion)
                .filter(ArtifactVersion.artifact_id == pptx_info["artifact_id"])
                .order_by(ArtifactVersion.version_no.desc())
                .first()
            )
            assert docx_ver is not None, f"No ArtifactVersion found for DOCX artifact {docx_info['artifact_id']}"
            assert pptx_ver is not None, f"No ArtifactVersion found for PPTX artifact {pptx_info['artifact_id']}"
            assert docx_ver.file_type == "docx", f"Expected file_type 'docx', got '{docx_ver.file_type}'"
            assert pptx_ver.file_type == "pptx", f"Expected file_type 'pptx', got '{pptx_ver.file_type}'"
            print(f"  [OK] ArtifactVersion.file_type verified: docx={docx_ver.file_type}, pptx={pptx_ver.file_type}")
        finally:
            db_sess.close()

        # 6. Assert or explain validation passed status
        val = final_state.get("validation", {})
        passed = val.get("passed", False)
        score = val.get("score", 0.0)
        issues = val.get("issues", [])
        if passed:
            print(f"  [OK] Validation passed: score {score:.1f}/100")
        else:
            issue_summary = "; ".join(f"[{i.get('where')}]: {i.get('message')}" for i in issues)
            print(f"  [WARN] Validation did not pass 100%: score {score:.1f}, issues: {issue_summary}")

        duration = time.perf_counter() - start_time
        used_calls = client.stats.real_calls - calls_start
        return (
            StageResult(
                name="STAGE 2: Minimal Full Generation Cycle",
                status="PASS",
                llm_calls=used_calls,
                duration_s=duration,
                details=f"DOCX & PPTX generated ({total_slides} slides, validation_passed={passed})",
            ),
            created_artifacts,
        )

    except Exception as exc:
        duration = time.perf_counter() - start_time
        used_calls = client.stats.real_calls - calls_start
        status = "QUOTA-LIMITED" if is_quota_error(exc) else "FAIL"
        print(f"  [{status}] Stage 2 failed: {exc}")
        return (
            StageResult(
                name="STAGE 2: Minimal Full Generation Cycle",
                status=status,
                llm_calls=used_calls,
                duration_s=duration,
                details=str(exc)[:150],
            ),
            created_artifacts,
        )


# ── Stage 3: Minimal Real Conversational Edit ──────────────────────────────
def run_stage_3(client: Any, artifacts: dict[str, Any]) -> StageResult:
    print("\n" + "=" * 60)
    print("STAGE 3: Minimal Real Edit (DOCX Conclusion Section)")
    print("=" * 60)
    start_time = time.perf_counter()
    calls_start = client.stats.real_calls

    docx_info = artifacts.get("docx")
    if not docx_info:
        return StageResult(
            name="STAGE 3: Minimal Artifact Edit",
            status="FAIL",
            llm_calls=0,
            duration_s=0.0,
            details="Skipped: STAGE 2 did not produce a DOCX artifact to edit.",
        )

    artifact_id = docx_info["artifact_id"]
    db_sess = _get_sync_session()
    if not db_sess:
        return StageResult(
            name="STAGE 3: Minimal Artifact Edit",
            status="FAIL",
            llm_calls=0,
            duration_s=0.0,
            details="Database session unavailable.",
        )

    try:
        # Snapshot v1 state
        v1 = (
            db_sess.query(ArtifactVersion)
            .filter(
                ArtifactVersion.artifact_id == artifact_id,
                ArtifactVersion.version_no == 1,
            )
            .first()
        )
        assert v1 is not None, "Version 1 artifact record not found in database"
        v1_path = Path(v1.file_path)
        assert v1_path.exists(), f"Version 1 file not found on disk: {v1_path}"
        v1_size = v1_path.stat().st_size
        v1_mtime = v1_path.stat().st_mtime

        # Execute minimal edit (adding conclusion section is cheaper than condensing full deck)
        instruction = "add a short conclusion section"
        res = edit_artifact(artifact_id=artifact_id, instruction=instruction, db_session=db_sess)

        # Assert new version created
        assert res.new_version_no == 2, f"Expected new version 2, got {res.new_version_no}"
        v2_path = Path(res.file_path)
        assert v2_path.exists(), f"Edited version 2 file does not exist on disk: {v2_path}"
        assert v2_path.resolve() != v1_path.resolve(), "v2 must be saved to a distinct path from v1"

        # Assert old version is untouched
        assert v1_path.exists(), "Original v1 file was removed or displaced"
        assert v1_path.stat().st_size == v1_size, "Original v1 file size was modified"
        assert v1_path.stat().st_mtime == v1_mtime, "Original v1 file timestamp changed"

        print(f"  [OK] New version v{res.new_version_no} created ({v2_path.name})")
        print(f"  [OK] Old version v1 remains intact ({v1_path.name}, {v1_size} bytes)")

        duration = time.perf_counter() - start_time
        used_calls = client.stats.real_calls - calls_start
        return StageResult(
            name="STAGE 3: Minimal Artifact Edit",
            status="PASS",
            llm_calls=used_calls,
            duration_s=duration,
            details=f"Added conclusion section: v1 intact, v2 created ({res.summary[:60]})",
        )

    except Exception as exc:
        duration = time.perf_counter() - start_time
        used_calls = client.stats.real_calls - calls_start
        status = "QUOTA-LIMITED" if is_quota_error(exc) else "FAIL"
        print(f"  [{status}] Stage 3 edit failed: {exc}")
        return StageResult(
            name="STAGE 3: Minimal Artifact Edit",
            status=status,
            llm_calls=used_calls,
            duration_s=duration,
            details=str(exc)[:150],
        )
    finally:
        db_sess.close()


# ── Stage 4: Minimal Real Q&A ("answer" action) ────────────────────────────
def run_stage_4(client: Any) -> StageResult:
    print("\n" + "=" * 60)
    print("STAGE 4: Minimal Grounded Q&A ('answer' action)")
    print("=" * 60)
    start_time = time.perf_counter()
    calls_start = client.stats.real_calls

    qa_prompt = "What is India's target for renewable energy capacity by 2030? In short, just answer, no need for a file."
    run_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    initial_state: GraphState = {
        "run_id": run_id,
        "session_id": session_id,
        "user_message": qa_prompt,
        "file_ids": [],
        "artifacts": [],
        "errors": [],
        "retry_count": 0,
        "findings": [],
        "template_profiles": {},
    }

    try:
        final_state = graph_app.invoke(initial_state)

        # 1. Assert no file/artifact was created
        artifacts = final_state.get("artifacts", [])
        assert len(artifacts) == 0, f"Expected 0 artifacts for 'answer' action, but found {len(artifacts)}"
        print(f"  [OK] No file created (artifacts count: {len(artifacts)})")

        # 2. Assert reply exists and contains citation
        reply = final_state.get("reply", "")
        assert reply and len(reply.strip()) > 10, "Reply text is empty or too short"

        has_bracket_citation = bool(re.search(r"\[\d+\]", reply))
        has_registry = bool(final_state.get("registry"))
        assert has_bracket_citation or has_registry, (
            f"Expected at least 1 citation in Q&A reply, none detected. Reply: '{reply[:120]}...'"
        )
        print(f"  [OK] Reply verified with grounded citation(s): '{reply[:100]}...'")

        duration = time.perf_counter() - start_time
        used_calls = client.stats.real_calls - calls_start
        return StageResult(
            name="STAGE 4: Grounded Q&A (No File)",
            status="PASS",
            llm_calls=used_calls,
            duration_s=duration,
            details="Grounded answer produced with citation, 0 artifacts created",
        )

    except Exception as exc:
        duration = time.perf_counter() - start_time
        used_calls = client.stats.real_calls - calls_start
        status = "QUOTA-LIMITED" if is_quota_error(exc) else "FAIL"
        print(f"  [{status}] Stage 4 Q&A failed: {exc}")
        return StageResult(
            name="STAGE 4: Grounded Q&A (No File)",
            status=status,
            llm_calls=used_calls,
            duration_s=duration,
            details=str(exc)[:150],
        )


# ── Main Orchestrator & Summary Table ──────────────────────────────────────
def main() -> None:
    # Ensure database schema is created
    db_sess = _get_sync_session()
    if db_sess:
        try:
            Base.metadata.create_all(bind=db_sess.get_bind())
        finally:
            db_sess.close()

    client = get_llm_client()
    overall_start = time.perf_counter()
    results: list[StageResult] = []

    # Run STAGE 1
    s1_res = run_stage_1(client)
    results.append(s1_res)

    # Run STAGE 2
    s2_res, artifacts = run_stage_2(client)
    results.append(s2_res)

    # Run STAGE 3
    s3_res = run_stage_3(client, artifacts)
    results.append(s3_res)

    # Run STAGE 4
    s4_res = run_stage_4(client)
    results.append(s4_res)

    total_duration = time.perf_counter() - overall_start
    total_calls = client.stats.real_calls

    # ── Summary Table ──────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print(f"{'STAGE NAME':<38} | {'STATUS':<14} | {'LLM CALLS':<10} | {'DURATION'}")
    print("-" * 78)
    for r in results:
        print(f"{r.name:<38} | {r.status:<14} | {r.llm_calls:<10} | {format_duration(r.duration_s)}")
    print("-" * 78)
    print(f"{'TOTAL REAL LLM CALLS USED':<38} | {'':<14} | {total_calls:<10} | {format_duration(total_duration)}")
    print(f"TARGET BUDGET: < 15 LLM calls ({total_calls}/15 used)")
    print("=" * 78)

    # ── Exit Code Logic ────────────────────────────────────────────────────
    # 0 = All PASS
    # 1 = Genuinely FAILED (code bug, assertion failure, auth, model error)
    # 2 = QUOTA-LIMITED (429 / resource exhausted on API)
    statuses = [r.status for r in results]
    if any(s == "FAIL" for s in statuses):
        print("\n[RESULT] Health check FAILED with genuine errors. Exit code: 1")
        sys.exit(1)
    elif any(s == "QUOTA-LIMITED" for s in statuses):
        print("\n[RESULT] Health check hit API quota limits (QUOTA-LIMITED, not a code bug). Exit code: 2")
        sys.exit(2)
    else:
        print("\n[RESULT] All health check stages PASSED successfully. Exit code: 0")
        sys.exit(0)


if __name__ == "__main__":
    main()
