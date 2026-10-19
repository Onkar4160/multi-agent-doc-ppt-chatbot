"""Request-understanding evaluation (PLAN Stage 4): labelled messages in context → interpretation accuracy.

Cases live in ``eval/conversation/cases.jsonl`` (split ``dev`` for tuning, ``test`` held out) with artifact
fixtures in ``fixtures.json``. Each case is run through the real planner (``parse_plan``: the LLM when
configured, offline inference under the mock) and the interpreter, then scored field by field.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.agents.interpreter import interpret
from app.agents.supervisor import parse_plan
from app.models.context import ConversationContext
from app.models.deck_model import DeckModel
from app.models.document_model import DocumentModel, TableBlock
from app.models.interpretation import Interpretation
from app.models.workbook_model import WorkbookModel
from app.services.artifact_nodes import ensure_node_ids

CASES = Path("eval/conversation/cases.jsonl")
FIXTURES = Path("eval/conversation/fixtures.json")
_MODELS = {"pptx": DeckModel, "docx": DocumentModel, "xlsx": WorkbookModel}


@dataclass
class CaseResult:
    case_id: str
    split: str
    message: str
    checks: dict[str, bool] = field(default_factory=dict)
    got: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(self.checks.values())


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _model(fixture: dict[str, Any]):
    model = _MODELS[fixture["kind"]].model_validate(fixture["model"])
    ensure_node_ids(model)
    return model


def _node_for(label: str, model: Any) -> str | None:
    kind, _, value = label.partition(":")
    if kind == "slide":
        return model.slides[int(value) - 1].node_id
    if kind == "section":
        return next((s.node_id for s in model.sections if s.heading == value), None)
    if kind == "table":
        tables = [b for s in model.sections for b in s.blocks if isinstance(b, TableBlock)]
        return tables[int(value) - 1].node_id
    if kind == "sheet":
        return next((s.node_id for s in model.sheets if s.name == value), None)
    return None


class OfflinePlanner:
    """Stands in for the LLM so ``parse_plan`` uses its deterministic inference (no API key, no quota)."""

    _mock_mode = True


def run_case(case: dict[str, Any], fixtures: dict[str, Any], llm_client: Any = None) -> tuple[Interpretation, Any]:
    ctx_in = case.get("context") or {}
    model = None
    context = ConversationContext()
    if ctx_in.get("artifact"):
        fx = fixtures[ctx_in["artifact"]]
        model = _model(fx)
        art = {"artifact_id": 1, "kind": fx["kind"], "title": fx["title"], "latest_version": 1, "active": True}
        context = ConversationContext(active_artifact=art, artifact_history=[art], active_version=1)
    plan = parse_plan(case["message"], session_context=context, llm_client=llm_client)
    interp = interpret(
        case["message"], plan=plan, context=context, active_model=model,
        pending_clarification=ctx_in.get("pending_clarification"),
    )
    return interp, model


def score(case: dict[str, Any], interp: Interpretation, model: Any) -> CaseResult:
    exp = case["expect"]
    res = CaseResult(case["id"], case["split"], case["message"])
    spec = interp.output_spec
    change = interp.change
    res.got = {
        "kind": interp.kind,
        "clarify": interp.needs_clarification,
        "format": spec.format if spec else None,
        "operation": change.operation if change else None,
        "targets": sorted(t.node_id for t in change.scope.targets) if change else [],
    }
    res.checks["kind"] = interp.kind == exp["kind"]
    res.checks["clarify"] = interp.needs_clarification == exp.get("clarify", False)
    if "format" in exp:
        res.checks["format"] = bool(spec) and spec.format == exp["format"]
    if "length" in exp:
        unit, value = exp["length"]
        res.checks["length"] = bool(spec) and spec.length.explicit and (spec.length.unit, spec.length.value) == (unit, value)
    for key, attr in (("audience", "audience"), ("tone", "tone"), ("detail", "detail_level"),
                      ("citation_style", "citation_style"), ("language", "language")):
        if key in exp:
            res.checks[key] = bool(spec) and str(getattr(spec, attr)).lower() == str(exp[key]).lower()
    if "operation" in exp:
        res.checks["operation"] = bool(change) and change.operation == exp["operation"]
    if "targets" in exp:
        if interp.kind == "question":
            got_targets = _question_targets(case["message"], model)
        else:
            got_targets = set(t.node_id for t in change.scope.targets) if change else set()
        wanted = {_node_for(label, model) for label in exp["targets"]}
        res.checks["targets"] = got_targets == wanted
    if "style" in exp:
        got_style = {c.path: c.value for c in (change.style_changes if change else [])}
        res.checks["style"] = got_style == exp["style"]
    return res


def _question_targets(message: str, model: Any) -> set[str]:
    from app.agents.interpreter import resolve_targets

    targets, _ = resolve_targets(message, model)
    return {t.node_id for t in targets}


def evaluate(split: str | None = None, *, live: bool = False) -> dict[str, Any]:
    """Score the labelled set. ``live=True`` uses the configured Gemini planner (about one call per case)."""
    planner = None if live else OfflinePlanner()
    fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
    results = []
    for case in load_cases():
        if split and case["split"] != split:
            continue
        interp, model = run_case(case, fixtures, planner)
        results.append(score(case, interp, model))

    def accuracy(check: str) -> float | None:
        relevant = [r for r in results if check in r.checks]
        return round(100 * sum(r.checks[check] for r in relevant) / len(relevant), 1) if relevant else None

    clar_expected = [r for r, c in zip(results, [c for c in load_cases() if not split or c["split"] == split]) if c["expect"].get("clarify")]
    return {
        "cases": len(results),
        "fully_correct": sum(r.passed for r in results),
        "kind_accuracy": accuracy("kind"),
        "target_accuracy": accuracy("targets"),
        "clarification_accuracy": accuracy("clarify"),
        "clarification_recall": round(100 * sum(r.got["clarify"] for r in clar_expected) / len(clar_expected), 1) if clar_expected else None,
        "spec_accuracy": {k: accuracy(k) for k in ("format", "length", "audience", "tone", "detail", "citation_style", "language")},
        "change_accuracy": {k: accuracy(k) for k in ("operation", "style")},
        "failures": [{"id": r.case_id, "message": r.message, "failed": [k for k, ok in r.checks.items() if not ok], "got": r.got}
                     for r in results if not r.passed],
    }
