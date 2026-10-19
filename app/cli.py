"""Command-line entry point for batch generation, template inspection and environment checks (FR-UX-5).

    python -m app.cli health
    python -m app.cli analyze-template data/sample_templates/Company_Template.pptx
    python -m app.cli generate "Make a 6-slide deck on heat pumps" --template my.pptx --out out/

``generate`` runs the same LangGraph workflow as ``POST /chat`` (same persistence, reply, quality report and
usage accounting), as user ``cli``, and copies the produced files to ``--out``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

CLI_USER = "cli"


def _bootstrap() -> None:
    from app.core.database import create_tables, get_session_factory
    from app.services.workspace_init import init_default_workspace

    async def run() -> None:
        await create_tables()
        async with get_session_factory()() as session:
            await init_default_workspace(session)

    asyncio.run(run())


def cmd_health(_: argparse.Namespace) -> int:
    """Configuration and dependency status; exit code 1 when something required is missing."""
    from app.core.config import get_settings

    s = get_settings()

    def is_set(value: str | None) -> bool:
        return bool(value and value.strip() and not value.startswith("your-"))

    report = {
        "gemini_api_key": is_set(s.gemini_api_key),
        "model_chain": [s.gemini_model, *s.fallback_models],
        "tavily_api_key": is_set(s.tavily_api_key),
        "pinecone_api_key": is_set(getattr(s, "pinecone_api_key", "")),
        "pexels_api_key": is_set(s.pexels_api_key),
        "academic_search": s.academic_search,
        "images_enabled": s.images_enabled,
        "default_docx_template": Path(s.default_docx_template_path).exists(),
        "default_pptx_template": Path(s.default_pptx_template_path).exists(),
        "libreoffice": bool(shutil.which("soffice") or shutil.which("libreoffice")),
        "database_url": s.database_url.split("///")[0] + "///…",
        "mock_llm": s.mock_llm,
    }
    print(json.dumps(report, indent=2))
    required = report["gemini_api_key"] or report["mock_llm"]
    return 0 if required and report["default_pptx_template"] and report["default_docx_template"] else 1


def cmd_analyze_template(args: argparse.Namespace) -> int:
    """Summary of what the analysers understand about a template."""
    path = Path(args.path)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 2
    from app.services.analyzers.summary import NoLLM, manifest_summary

    suffix = path.suffix.lower()
    if suffix in (".pptx", ".potx"):
        from app.agents.ppt_analyzer import analyze_presentation

        profile = analyze_presentation(path, llm_client=NoLLM())
    elif suffix in (".docx", ".dotx"):
        from app.agents.doc_analyzer import analyze_document

        profile = analyze_document(path, llm_client=NoLLM())
    elif suffix in (".xlsx", ".xltx"):
        from app.agents.xlsx_analyzer import analyze_workbook

        profile = analyze_workbook(path)
    else:
        print(f"Unsupported template type '{suffix}' (pptx, potx, docx, dotx, xlsx, xltx)", file=sys.stderr)
        return 2
    summary = manifest_summary(profile)
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    """Run one request through the workflow and copy the produced files to --out."""
    from app.api.chat import _run_graph_background
    from app.core.database import get_sync_session
    from app.models.chat import ChatMessage, ChatSession
    from app.models.file import UploadedFile
    from app.models.run import AgentRun

    _bootstrap()
    from app.core.config import get_settings

    settings = get_settings()
    if args.budget:
        settings.llm_max_calls_per_run = args.budget
    run_id = f"run_{uuid.uuid4().hex[:10]}"
    session_id = f"sess_cli_{uuid.uuid4().hex[:8]}"
    db = get_sync_session()
    try:
        file_ids: list[int] = []
        if args.template:
            src = Path(args.template)
            if not src.exists():
                print(f"Template not found: {src}", file=sys.stderr)
                return 2
            dest_dir = Path(settings.storage_dir) / "uploads" / uuid.uuid4().hex
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src.name
            shutil.copyfile(src, dest)
            rec = UploadedFile(
                filename=src.name, file_type=src.suffix.lstrip(".").lower(), file_size=dest.stat().st_size,
                stored_path=str(dest), session_id=session_id, purpose="TEMPLATE", owner=CLI_USER,
            )
            db.add(rec)
            db.flush()
            file_ids.append(rec.id)
        db.add(ChatSession(session_id=session_id, title=args.prompt[:50], owner=CLI_USER))
        db.add(ChatMessage(session_id=session_id, role="user", content=args.prompt, run_id=run_id))
        db.add(AgentRun(run_id=run_id, session_id=session_id, status="running"))
        db.commit()
    finally:
        db.close()

    _run_graph_background(run_id=run_id, session_id=session_id, user_message=args.prompt, file_ids=file_ids)

    db = get_sync_session()
    try:
        run = db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
        result = json.loads(run.result_json or "{}") if run else {}
        status = run.status if run else "failed"
    finally:
        db.close()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for art in result.get("artifacts", []):
        path = Path(art.get("file_path") or "")
        if path.exists():
            target = out_dir / f"{art.get('kind', 'file')}_{art.get('artifact_id')}_v{art.get('version')}{path.suffix}"
            shutil.copyfile(path, target)
            copied.append(str(target))
    print(json.dumps({
        "status": status,
        "reply": result.get("reply", ""),
        "files": copied,
        "validation": (result.get("validation") or {}).get("score"),
        "usage": result.get("usage"),
        "session_id": session_id,
        "run_id": run_id,
    }, indent=2))
    return 0 if status == "done" and copied else 1


def cmd_eval(args: argparse.Namespace) -> int:
    """Run an evaluation suite and print its metrics (Stage 4: conversation understanding)."""
    if args.suite == "acceptance":
        from app.eval.acceptance import evaluate as acceptance

        report = acceptance(Path(args.out) if args.out else None)
        print(json.dumps({k: v for k, v in report.items() if k != "cases"} | {"cases": [
            {k: c[k] for k in ("id", "scenario", "format", "length_ok", "quality")} for c in report["cases"]]}, indent=2))
        return 0 if report["passed"] else 1
    from app.eval.conversation import evaluate

    report = evaluate(args.split, live=args.live)
    print(json.dumps(report, indent=2))
    ok = (report["kind_accuracy"] or 0) >= 90 and (report["target_accuracy"] or 100) >= 90
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Document & presentation platform CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Show configuration and dependency status").set_defaults(func=cmd_health)

    p_tpl = sub.add_parser("analyze-template", help="Summarise how a PPTX/DOCX template is understood")
    p_tpl.add_argument("path")
    p_tpl.set_defaults(func=cmd_analyze_template)

    p_gen = sub.add_parser("generate", help="Generate files from a request")
    p_gen.add_argument("prompt")
    p_gen.add_argument("--template", help="PPTX or DOCX template to follow")
    p_gen.add_argument("--out", default="out", help="Directory for the produced files")
    p_gen.add_argument("--budget", type=int, help="Maximum AI calls for this run")
    p_gen.set_defaults(func=cmd_generate)

    p_eval = sub.add_parser("eval", help="Run an evaluation suite (offline by default)")
    p_eval.add_argument("suite", choices=["conversation", "acceptance"])
    p_eval.add_argument("--out", help="acceptance: folder for the generated files")
    p_eval.add_argument("--split", choices=["dev", "test", "test2"], help="Only one split of the labelled set")
    p_eval.add_argument("--live", action="store_true", help="Use the real Gemini planner (about one call per case)")
    p_eval.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    from app.core.config import get_settings
    from app.core.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
