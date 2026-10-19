# Testing Strategy

Related: [PLAN](PLAN.md) · [QUALITY](QUALITY.md) · [DECISIONS](architecture/DECISIONS.md) (ADR-020)

## 1. Goals

- Every component verified against its requirements; tests name the PRD IDs or scenarios they cover in docstrings.
- The default run is **offline, deterministic and free**: no network, no API key.
- Live runs against Gemini, Tavily, scholarly indexes and photo providers are opt-in and budgeted.
- A stage is *verified* only with passing tests plus recorded evidence in [STATUS](STATUS.md).

## 2. Test layers

| Layer | Location | Scope | Runs |
|---|---|---|---|
| Unit | `tests/test_*.py` | Pure functions, models, analysers, layout, renderers, checks | always |
| Component | `tests/test_*.py` | One node or service with the mock LLM and mocked HTTP (e.g. research with `httpx.MockTransport`) | always |
| API | `tests/test_*.py` with FastAPI `TestClient` | Routes, background runs (graph patched or mock LLM), persistence | always |
| UI | `tests/test_ui_support.py` | Streamlit `AppTest` against a fake backend, in a separate interpreter | always |
| Docs | `tests/test_docs_consistency.py` | Requirement IDs, coverage register, stage table, cross-links, contracts | always |
| Live | `tests/live/` (`@pytest.mark.live`) | Real Gemini / scholarly / Openverse with tiny requests | `python -m pytest -m live tests/live` with keys |
| Visual | `tests/visual/` (`@pytest.mark.visual`) [S8] | Preview / LibreOffice renders compared to fixtures | when a renderer is present |
| Evaluation | `eval/` | Labelled suites scored by `app/eval/*`: `conversation` (87 messages; splits dev / test / test2) now; scenario suite S1–S13 in S12 | `python -m app.cli eval conversation [--split] [--live]` |

## 3. Test doubles

- **Mock LLM:** `tests/conftest.py` sets `MOCK_LLM=true`; `LLMClient` returns schema-valid canned objects (and the supervisor's semantic fallback for plans). `LLMClient` refuses mock mode outside pytest.
- **Scripted transport:** `LLMClient._single_call` replaced in tests to script 404 / 429 / success per model (`tests/test_reliability.py`).
- **HTTP:** `httpx.MockTransport` for OpenAlex, Semantic Scholar, arXiv, Openverse (`tests/test_research_and_photos.py`).
- **Offline web:** an autouse fixture in `tests/conftest.py` makes web search return nothing unless a test patches it; page reading is off under the mock unless a test injects a fetcher; the research pipeline accepts injected `search`, `scholarly` and `fetch` functions.
- **Scripted LLM:** objects with `generate_json` returning fixed plans and extractions (incl. invented quotes and a prompt-injection string) test verification and the untrusted-data boundary (`tests/test_research_evidence.py`).
- **Graph:** `app.api.chat.graph_app.invoke` patched to inject failures or capture state.
- **Isolation:** temporary SQLite DB and storage per session (`conftest.py`); Streamlit tests in a subprocess because another test imports the app in bare mode.

## 4. Fixtures

| Fixture | Purpose |
|---|---|
| `data/sample_templates/*.pptx` | Three real templates: Green Cream (designed sample slides, picture artwork), Company_Template (layout artwork, photo placeholders, theme-colour backgrounds), presentation_Template (grouped shapes, split backgrounds) |
| `data/sample_templates/*.docx` | Company_Proposal, proposal_Template |
| `tests/fixtures/build_fixtures.py` | Built at test time: an XLSX financial-model template (six sheet roles, table, names, formulas, conditional format, validation, chart), .potx/.dotx/.xltx copies of any regular file, a deck with a logo on the slide master |
| `tests/fixtures/manifests/*.json` | Manifest snapshots of every sample template (`manifest_summary`); `hand_labels.json`: element roles and layout archetypes labelled by hand, independently of the classifier |
| Programmatic decks/documents in tests | Every slide role / block type; overflow and contrast cases built with python-pptx |
| `eval/cases/` [S12] | Scenario inputs, expected properties, rubric sheets |

## 5. Metrics

| Metric | Definition | Target |
|---|---|---|
| Reopen rate | files reopened without error / files generated | 100 % |
| Critical findings | unresolved critical findings per artifact | 0 |
| Citation resolution | resolved cited IDs / cited IDs | 100 % |
| Evidence coverage | claims with evidence or explicit status / factual claims | ≥ 95 % |
| Formula pass rate | expected-value checks passed / checks | 100 % |
| Length compliance | artifacts meeting explicit length | 100 % slides/sheets; ±15 % pages/words |
| Edit precision | out-of-scope units unchanged / out-of-scope units | 100 % |
| Clarification recall | ambiguous cases that asked / ambiguous cases | ≥ 90 % |
| Classification accuracy | correct interpretation kind / labelled messages | ≥ 90 % |
| Template fidelity | fidelity checks passed / applicable | 100 % |
| Reply rate | messages with a stored reply / messages (incl. injected failures) | 100 % |
| Human rubric | mean of QUALITY §5 | ≥ 4.0 |

## 6. Verification procedure per stage

1. New and existing tests pass: `pytest` (offline).
2. Each requirement the stage completes has a test referencing its ID; the coverage register in STATUS moves it to *met*.
3. Generators: sample outputs produced from fixtures (and one live run when keys are present) and inspected — PowerPoint export or preview PNGs — with the observation recorded in STATUS.
4. Docs updated: STATUS (stage row, coverage, change log), DECISIONS for new choices, DATA_MODELS / ARCHITECTURE when contracts or components change.

## 7. Commands

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q                       # offline suite
python -m pytest -q tests/test_docs_consistency.py
python -m pytest -q -m live tests/live    # real services; needs GEMINI_API_KEY; uses a few requests
python -m app.cli health                  # configuration status
python -m app.cli analyze-template data/sample_templates/Company_Template.pptx   # manifest summary, no LLM
$env:UPDATE_SNAPSHOTS=1; python -m pytest -q tests/test_template_analysis.py     # rewrite manifest snapshots after a deliberate analyser change (bump ANALYZER_VERSION)
```

## 8. Environments

| Env | Python | Renderer | Network | Purpose |
|---|---|---|---|---|
| Dev (Windows) | 3.12 | PowerPoint COM available for manual visual checks; LibreOffice optional | yes | daily work, live runs |
| CI (planned) | 3.12 | LibreOffice in image | no | offline + visual |
