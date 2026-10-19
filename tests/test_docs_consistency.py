"""The Markdown documentation is one consistent project definition (PLAN Stage 1 verification)."""

import re
from pathlib import Path

DOCS = Path("docs")
PRD = (DOCS / "PRD.md").read_text(encoding="utf-8")
PLAN = (DOCS / "PLAN.md").read_text(encoding="utf-8")
STATUS = (DOCS / "STATUS.md").read_text(encoding="utf-8")

ROW_ID = re.compile(r"^\| ((?:FR-[A-Z]+|NFR)-\d+)\b", re.MULTILINE)


def _requirements() -> list[str]:
    return ROW_ID.findall(PRD.split("## 5. Functional requirements")[1])


def test_requirement_ids_are_unique_and_counted():
    ids = _requirements()
    assert len(ids) == len(set(ids)), "duplicate requirement IDs in the PRD"
    functional = [i for i in ids if i.startswith("FR-")]
    non_functional = [i for i in ids if i.startswith("NFR-")]
    claim = re.search(r"(\d+) functional and (\d+) non-functional requirements", STATUS)
    assert claim, "STATUS must state the requirement counts"
    assert (int(claim.group(1)), int(claim.group(2))) == (len(functional), len(non_functional))


def test_every_functional_requirement_has_one_coverage_entry():
    functional = {i for i in _requirements() if i.startswith("FR-")}
    register = STATUS.split("## Requirement coverage register")[1].split("\n## ")[0]
    rows = re.findall(r"^\| (FR-[A-Z]+-\d+) \| (met|partial|missing) \|", register, re.MULTILINE)
    covered = [r[0] for r in rows]
    assert len(covered) == len(set(covered)), "a requirement appears twice in the coverage register"
    assert set(covered) == functional, f"missing: {sorted(functional - set(covered))}, unknown: {sorted(set(covered) - functional)}"


def test_acceptance_criteria_reference_existing_scenarios():
    scenarios = set(re.findall(r"^\| (S\d+) \|", PRD, re.MULTILINE))
    criteria = PRD.split("## 8. Acceptance criteria")[1].split("\n## ")[0]
    for ref in re.findall(r"\bS(\d+)\b", criteria):
        assert f"S{ref}" in scenarios, f"AC references unknown scenario S{ref}"
    ranges = re.findall(r"S(\d+)–S(\d+)", criteria)
    for lo, hi in ranges:
        assert all(f"S{n}" in scenarios for n in range(int(lo), int(hi) + 1))


def test_plan_and_status_list_the_same_thirteen_stages():
    plan_rows = re.findall(r"^\| (\d+) \| ([^|]+?) \| [^|]+ \| (\w+) \|$", PLAN, re.MULTILINE)
    status_rows = re.findall(r"^\| (\d+) \| [^|]+ \| (\w+) \|", STATUS, re.MULTILINE)
    assert [int(n) for n, _, _ in plan_rows] == list(range(1, 14))
    assert {n: s for n, _, s in plan_rows} == {n: s for n, s in status_rows}, "PLAN and STATUS disagree on stage status"
    for n in range(1, 14):
        assert f"### Stage {n} —" in PLAN, f"Stage {n} has no detail section in PLAN"


def test_documents_link_to_each_other():
    for doc, links in ((PRD, ["PLAN.md", "STATUS.md"]), (PLAN, ["PRD.md", "STATUS.md"]), (STATUS, ["PRD.md", "PLAN.md"])):
        for link in links:
            assert f"]({link})" in doc


# ── Stage 2: architecture set ──────────────────────────────────────────────

ARCH = (DOCS / "architecture" / "ARCHITECTURE.md").read_text(encoding="utf-8")
DECISIONS = (DOCS / "architecture" / "DECISIONS.md").read_text(encoding="utf-8")
DATA_MODELS = (DOCS / "DATA_MODELS.md").read_text(encoding="utf-8")
QUALITY = (DOCS / "QUALITY.md").read_text(encoding="utf-8")
TESTING = (DOCS / "TESTING.md").read_text(encoding="utf-8")
AGENTS = Path("AGENTS.md").read_text(encoding="utf-8")
APP_SOURCE = "\n".join(p.read_text(encoding="utf-8") for p in Path("app").rglob("*.py"))


def test_every_requirement_has_an_owning_component():
    ownership = ARCH.split("## 13. Requirement ownership")[1].split("\n## ")[0]
    owned = set(re.findall(r"FR-[A-Z]+-\d+", ownership))
    functional = {i for i in _requirements() if i.startswith("FR-")}
    assert functional <= owned, f"requirements without an owner: {sorted(functional - owned)}"


def test_contracts_named_in_architecture_are_defined_in_data_models():
    contracts = set(re.findall(r"`([A-Z][A-Za-z]+(?:Model|Plan|Report|Profile|Design|Base|State|Context|Interpretation|Dossier|Request|Asset|Version|Run|Trace))`", ARCH))
    assert contracts, "expected contract names in ARCHITECTURE"
    missing = sorted(c for c in contracts if c not in DATA_MODELS)
    assert not missing, f"contracts not described in DATA_MODELS: {missing}"


def test_adr_ids_are_sequential_and_references_resolve():
    ids = [int(n) for n in re.findall(r"^## ADR-(\d+) ", DECISIONS, re.MULTILINE)]
    assert ids == list(range(1, len(ids) + 1)), "ADR numbers must be unique and sequential"
    for name, doc in (("ARCHITECTURE", ARCH), ("DATA_MODELS", DATA_MODELS), ("QUALITY", QUALITY), ("TESTING", TESTING), ("AGENTS", AGENTS)):
        for ref in re.findall(r"ADR-(\d+)", doc):
            assert int(ref) in ids, f"{name} references unknown ADR-{ref}"
    assert "resolves PRD Q8" in DECISIONS  # the content-model decision the PRD deferred to Stage 2


def test_code_paths_and_identifiers_in_docs_exist():
    planned = {"app/cli.py"}
    found = set(re.findall(r"`((?:app|ui|tests|agents|services|api|core|llm|models)/[\w/]+\.py)`", ARCH + DATA_MODELS + TESTING + AGENTS))
    assert len(found) > 30, "expected many code references in the docs"
    for path in found:
        full = path if path.startswith(("app/", "ui/", "tests/")) else f"app/{path}"
        assert full in planned or Path(full).exists(), f"docs reference missing file {path}"
    # Functions and settings the quality catalogue says exist today must exist in the code.
    for name in set(re.findall(r"`([a-z_][a-z0-9_]{3,})`", QUALITY)):
        if "_" in name and "." not in name:
            assert name in APP_SOURCE, f"QUALITY names `{name}`, which is not in app/"


def test_architecture_docs_link_back():
    for name, doc in (("ARCHITECTURE", ARCH), ("DATA_MODELS", DATA_MODELS), ("QUALITY", QUALITY), ("TESTING", TESTING)):
        assert "PRD.md" in doc or "DECISIONS.md" in doc or "PLAN.md" in doc, f"{name} has no cross-links"
