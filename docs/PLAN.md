# Implementation Plan

Progressive stages from specification to delivery. One stage is worked at a time; a stage is **verified** only when its verification steps pass and the evidence is recorded in [STATUS](STATUS.md). Requirement IDs refer to the [PRD](PRD.md).

Status values: `planned` · `active` · `done` (code exists) · `verified` (behaviour checked against requirements) · `blocked`.

## Stage overview

| # | Stage | Depends on | Status |
|---|---|---|---|
| 1 | Requirements and product definition | — | verified |
| 2 | Architecture and system design | 1 | verified |
| 3 | Repository and implementation foundation | 2 | verified |
| 4 | Request understanding and conversation context | 3 | done |
| 5 | Research and evidence management | 3 | done |
| 6 | Template analysis and reusable design representation | 3 | done |
| 7 | Content planning and synthesis | 4, 5, 6 | done |
| 8 | PowerPoint generation | 6, 7 | done |
| 9 | Word document generation | 6, 7 | done |
| 10 | Excel workbook generation | 6, 7 | done |
| 11 | Cross-format orchestration and artifact editing | 8, 9, 10 | done |
| 12 | Quality assurance and evaluation | 11 | done |
| 13 | Application integration and delivery | 12 | done |

Stages 4–6 can proceed in any order after 3. Each stage ends with: full test suite green, stage tests added, docs updated (STATUS change log, and DECISIONS for any design choice).

## Stage details

### Stage 1 — Requirements and product definition
- **Purpose:** fix scope, scenarios, requirements and acceptance criteria.
- **Deliverables:** [PRD](PRD.md) (scenarios S1–S13, FR-*, NFR-*, AC-1…AC-11, question register), this plan, [STATUS](STATUS.md) with a baseline requirement-coverage register.
- **Verification:** `tests/test_docs_consistency.py` — unique requirement IDs, every requirement has a coverage entry in STATUS, acceptance criteria reference existing scenarios, plan lists 13 stages.

### Stage 2 — Architecture and system design
- **Purpose:** define components, LangGraph flows, agent responsibilities, shared contracts, persistence, error handling, security boundaries and artifact lifecycle within the fixed stack.
- **Scope:** the typed content model (IR) for deck/document/workbook with claim links and typed blocks; evidence model; template manifest; quality report model; edit patch model; how the IR relates to today's `DeckModel`/`DocumentModel`/`WorkbookModel` (PRD Q8).
- **Deliverables:** `docs/architecture/ARCHITECTURE.md` (component and sequence diagrams), `docs/architecture/DECISIONS.md` (ADRs), `docs/DATA_MODELS.md`, `docs/QUALITY.md` (dimensions, thresholds, rubric), `docs/TESTING.md`, `AGENTS.md`.
- **Verification:** every M requirement mapped to an owning component; every data contract named in ARCHITECTURE defined in DATA_MODELS; docs consistency test extended.

### Stage 3 — Repository and implementation foundation
- **Purpose:** runnable, configurable, observable base matching the architecture.
- **Scope:** configuration and `.env` documentation, structured logging with run IDs, error taxonomy, upload safety (magic bytes, zip-bomb limits, macro rejection), owner-scoped data, LLM gateway hardening (budgets, usage logging, mock/replay), CLI entry point, IR schemas from Stage 2 in code.
- **Deliverables:** foundation code, `docs/DEPLOYMENT.md` (environment), baseline tests.
- **Verification:** suite offline green; schema round-trip tests; upload-safety tests; CLI smoke test.

### Stage 4 — Request understanding and conversation context
- **Purpose:** interpret intent and scope reliably across turns (FR-CONV).
- **Scope:** message classification (new task, continuation, modification, regeneration, scope change, clarification answer, question, retry); output specification with audience/tone/length; reference resolution to artifacts and elements; change-scope representation; confidence-based clarification.
- **Deliverables:** interpreter component, conversation-state model, change-scope model, labelled evaluation set of ≥ 60 messages.
- **Verification:** ≥ 90 % classification accuracy and ≥ 90 % target-resolution accuracy on the labelled set (mocked + one live pass); ambiguous cases ask.

### Stage 5 — Research and evidence management
- **Purpose:** research that is relevant, traceable and honest (FR-RES).
- **Scope:** research need/depth decision, research plan (questions), web + scholarly + file retrieval, page fetch, source evaluation (authority, recency), atomic evidence extraction with locators, claims, conflicts, gaps, coverage report, injection boundary.
- **Deliverables:** research pipeline, evidence/source models persisted per task, coverage report in replies.
- **Verification:** recorded-fixture tests for each provider; coverage computed on fixtures; every claim resolves to evidence or a non-fact status; one live research run recorded.

### Stage 6 — Template analysis and reusable design representation
- **Purpose:** structured understanding of PPTX, DOCX and XLSX templates (FR-TPL).
- **Scope:** element inventories and roles; layout archetypes; designed-sample-slide bases (exists); DOCX style/section/header manifest; XLSX sheet/table/formula/chart manifest; design tokens; manifest cache by hash + analyser version.
- **Deliverables:** template manifests for all three formats, fixtures for the sample templates.
- **Verification:** manifest snapshot tests on every sample template; role classification spot-checked against hand labels.

### Stage 7 — Content planning and synthesis
- **Purpose:** plan the information architecture before rendering (FR-PLAN).
- **Scope:** deck plan with archetypes and claim links; document outline with section purposes, written section by section; workbook plan with sheet roles, schemas, formulas and expected values; length rules; plan validation with one revision round.
- **Deliverables:** planners and writers producing the IR; plan validators.
- **Verification:** plan-validation tests (duplicates, empty units, citation coverage, length compliance, archetype fit); AC-5 and AC-7 measured on fixtures.

### Stage 8 — PowerPoint generation
- **Purpose:** polished, editable decks with or without templates (FR-PPTX).
- **Scope:** archetype library (charts, tables, timelines, process, comparison, KPI, quote, image+text); generated design system for no-template decks; template layout path and designed-slide clone path; text fitting; photos; render → check → repair loop.
- **Deliverables:** renderer and component library, repair workflow, visual regression fixtures (preview PNGs).
- **Verification:** AC-3 and AC-8 on all sample templates and the no-template path; visual regression comparison; live S1/S2 run.

### Stage 9 — Word document generation
- **Purpose:** substantive, well-formatted documents (FR-DOCX).
- **Scope:** title page, summary, TOC, headings, lists, tables, figures, captions, citation styles, headers/footers/page numbers, template styles, style-level edits, page-flow checks and repair.
- **Deliverables:** DOCX pipeline, style model, document checks.
- **Verification:** structure and style tests; S3/S4/S7 cases; live S3 run.

### Stage 10 — Excel workbook generation
- **Purpose:** correct, usable analytical workbooks (FR-XLSX).
- **Scope:** dataset ingestion with profiling; sheet roles; tables, formats, named ranges, real formulas, charts, conditional formatting, validation; independent recalculation and expected-value checks; one corrective round.
- **Deliverables:** XLSX pipeline, workbook checks.
- **Verification:** AC-4; S5/S6 cases; live S5 run.

### Stage 11 — Cross-format orchestration and artifact editing
- **Purpose:** one LangGraph workflow for create / edit / convert with safe, targeted changes (FR-EDIT).
- **Scope:** IR patches confined to resolved targets; regression guard; versioning with diffs; format conversion inheriting evidence; background runs with progress.
- **Deliverables:** integrated workflow, edit regression set.
- **Verification:** AC-6, AC-9, AC-11; cross-format regression tests; live S8 run.

### Stage 12 — Quality assurance and evaluation
- **Purpose:** evidence that the product meets the acceptance criteria.
- **Scope:** evaluation dataset (S1–S13 cases), automated suites, visual regression fixtures, LLM content review, human rubric scoring, quality reports.
- **Deliverables:** `eval/` cases and runner, results report, documented limitations.
- **Verification:** AC-1 … AC-11 measured and recorded in STATUS.

### Stage 13 — Application integration and delivery
- **Purpose:** a deliverable application.
- **Scope:** Streamlit UI completeness (FR-UX), API, run status, downloads, configuration, deployment (local and container), monitoring, user guide.
- **Deliverables:** `docs/DEPLOYMENT.md`, user guide, final acceptance report.
- **Verification:** clean-machine setup following the docs; full suite + evaluation green; acceptance report signed off.
