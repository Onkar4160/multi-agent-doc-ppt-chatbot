# Quality Model

What "good" means for each deliverable, how it is checked, the thresholds that gate delivery, and how results are reported. Implements PRD §5.8 and ADR-009. Markers: **[now]** implemented · **[Sn]** planned stage.

Related: [PRD](PRD.md) · [DATA_MODELS](DATA_MODELS.md) · [TESTING](TESTING.md)

## 1. Quality dimensions

| Dimension | Question | Primary checker |
|---|---|---|
| Content | Accurate, relevant, deep enough, non-repetitive, on-tone? | Plan checks, Reviewer agent, human rubric |
| Evidence | Factual claims traceable and honestly labelled? | Evidence checks |
| Structure | Coherent storyline / hierarchy / workbook model? | Plan and structural checks |
| Layout | Elements fit, align, stay in bounds, no unintended overlap? | Geometric checks, visual checks |
| Typography | Consistent, readable fonts and hierarchy? | Style checks |
| Data | Numbers, formulas, ranges and types correct? | Formula evaluator, workbook checks |
| Template fidelity | Identity preserved, sample residue removed? | Template checks |
| Accessibility | Headings, alt text, contrast? | Accessibility checks |
| Validity | File opens and converts? | Re-open, LibreOffice conversion |

## 2. Check catalogue

Tiers: 1 structural · 2 geometric / text-metric · 3 computation · 4 visual (renderer needed) · 5 LLM review. Severity is the default for a failure. "Where" column names the current implementation.

### 2.1 Common

| Check ID | Tier | Rule | Sev | State |
|---|---|---|---|---|
| `common.reopen` | 1 | File re-opens with its library. | critical | PPTX now (`validate_pptx_file`); DOCX/XLSX S9/S10 |
| `common.convert` | 4 | LibreOffice converts to PDF. | critical | S12 (not performed when absent) |
| `common.length` | 1 | Explicit length honoured: slides/sheets exact, pages/words ±15 %. | major | slides now (`validate_outputs`); pages S9 |
| `content.placeholder_text` | 1 | No "Lorem ipsum", "Click to add", "sample text", "[placeholder]". | critical | now (model + PPTX file) |
| `content.empty_units` | 1 | No empty slide, section, sheet or text box. | critical | now (renderer skips empties; file check for empty boxes) |
| `content.duplicates` | 1 | No two units with near-identical headline/key message (similarity ≥ 0.9). | major | S7 |
| `content.density` | 2 | Slide body ≤ 6 bullets per box and ≤ 110 words. | minor | now (model warning) |
| `content.review` | 5 | Reviewer findings: shallow, repetitive, off-tone, unsupported. | minor–major | S12 |
| `evidence.citations_resolve` | 1 | Every cited ID resolves to a registered source; citation numbers are source IDs. | critical | now (`valid_source_ids`) |
| `evidence.uncited_figures` | 1 | Numbers, percentages, dates in content carry a source or claim. | major | now (`NUMBER_OR_PERCENT_REGEX`) |
| `evidence.coverage` | 1 | ≥ 95 % factual claims have evidence or a non-fact status. | major | dossier metric now (`ResearchDossier.coverage_pct`); gate in the quality report S8 |
| `evidence.unsupported_flagged` | 1 | Unsupported claims labelled or removed, never stated as fact. | major | dossier marks them and writers are told not to state them as fact (now); content check S7 |
| `a11y.alt_text` | 1 | Pictures and charts carry alt text. | minor | photos now; charts S8 |
| `a11y.contrast` | 2 | Text vs real backdrop ≥ 4.5:1 (≥ 3:1 for large/bold). | major (reported as warning today) | now (PPTX) |

### 2.2 PPTX

| Check ID | Tier | Rule | Sev | State |
|---|---|---|---|---|
| `pptx.text_overflow` | 2 | Measured text height ≤ box height (×1.02); no word wider than the box. | critical | now |
| `pptx.font_floor` | 2 | Title ≥ 24 pt, headline ≥ 20 pt, body ≥ 14 pt, references ≥ 10 pt, all × slide height / 7.5 in. | major | now |
| `pptx.out_of_bounds` | 2 | Our own shapes (`content:`, `decor:`, `footer:`) inside the slide; template artwork may bleed. | critical | now |
| `pptx.empty_placeholder` | 1 | No empty placeholders or empty `content:` boxes. | critical | now |
| `pptx.sample_residue` | 1 | No template sample sentence remains. | critical | now |
| `pptx.overlap` | 2 | Content shapes overlap ≤ 2 % of the smaller area. | major | S8 |
| `pptx.template_fonts` | 1 | Only template/theme fonts when a template is used. | major | S8 |
| `pptx.template_palette` | 1 | Colours from the template palette or tints. | major | S8 |
| `pptx.chart_data` | 1 | Native chart series lengths equal category count; no NaN. | critical | S8 |
| `pptx.visual_clipping` | 4 | Rendered image shows no clipped text. | major | S12 |

### 2.3 DOCX

| Check ID | Tier | Rule | Sev | State |
|---|---|---|---|---|
| `docx.heading_hierarchy` | 1 | One title; no skipped heading levels. | major | S9 |
| `docx.styles_used` | 1 | Headings and body use named styles, not direct overrides. | minor | S9 |
| `docx.toc_present` | 1 | TOC field present when requested. | minor | S9 |
| `docx.references` | 1 | Every cited source listed once in the selected style. | major | S9 |
| `docx.tables` | 1 | Header row, consistent column count. | major | S9 |
| `docx.page_numbers` | 1 | Page-number field in footer. | minor | S9 |
| `docx.template_styles` | 1 | Template style names used for mapped roles. | major | S9 |
| `docx.length` | 1 | Section count ≥ 3 and word count proportional to requested depth. | warning | now (model check) |
| `docx.page_flow` | 4 | No near-empty pages from breaks; no heading orphaned at page bottom. | minor | S12 |

### 2.4 XLSX

| Check ID | Tier | Rule | Sev | State |
|---|---|---|---|---|
| `xlsx.formula_errors` | 3 | No computed `#REF!`, `#NAME?`, `#VALUE!`, `#DIV/0!`, `#N/A` unless declared. | critical | S10 |
| `xlsx.expected_values` | 3 | Every plan check within tolerance. | critical | S10 |
| `xlsx.circular` | 3 | No undeclared circular references. | critical | S10 |
| `xlsx.refs_resolve` | 1 | All sheet / cell / name references exist. | critical | S10 |
| `xlsx.formula_syntax` | 1 | Formulas start with `=`. | warning | now (model check) |
| `xlsx.number_formats` | 1 | Typed columns carry formats. | minor | S10 |
| `xlsx.chart_ranges` | 1 | Chart ranges inside populated data. | major | S10 |
| `xlsx.hardcoded_calcs` | 1 | Calculated columns contain formulas, not pasted values. | major | S10 |
| `xlsx.sources_sheet` | 1 | Sources/assumptions sheet when external figures are used. | major | S10 |
| `xlsx.sheet_names` | 1 | Unique, ≤ 31 characters, valid characters, non-empty. | critical | now (non-empty) / S10 |

### 2.5 Editing

| Check ID | Tier | Rule | Sev | State |
|---|---|---|---|---|
| `edit.scope` | 1 | Changed node IDs ⊆ planned targets. | critical | now (`validate_scope`) |
| `edit.regression` | 1 | Out-of-scope units identical in IR; equivalent in file. | critical | S11 |
| `edit.requested_change_present` | 1 | The requested change is observable in the new version. | major | S11 |

## 3. Repairs

| Finding | Deterministic repair | LLM repair | State |
|---|---|---|---|
| Text overflow | shrink to floor; grow template box downward; widen within region | one "shorten" re-plan with per-slide instructions | now |
| Overflow persists | split slide into a continuation (only when length is not explicit) | — | S8 |
| Empty placeholder | remove | — | now (render time) |
| Sample residue | delete shape | — | now (clone path removes sample shapes) |
| Off-slide / overlap | snap into content region | — | S8 |
| Low contrast | switch to readable theme/neutral colour | — | now (title slots, `_readable`) |
| Formula error | fix reference from plan; recompute | one corrective workbook round | S10 |
| Unsupported claim | relabel as assumption / add limitation note | rewrite with available evidence | S5/S7 |

Loop: check → repair → re-render → re-check, up to `MAX_REPAIR_ITERATIONS` (default 2).

## 4. Gating

Every version is delivered (best effort) with a verdict:
- **pass** — no unresolved findings above `info`, and every applicable check was performed.
- **pass_with_warnings** — unresolved minor/major findings, or any check not performed (e.g. visual checks without a renderer).
- **fail** — any unresolved critical finding. The reply says what is wrong in plain language; the UI shows it first.

## 5. Human evaluation rubric (1–5)

| Criterion | 1 | 3 | 5 |
|---|---|---|---|
| Relevance | Misses the request | Mostly on target | Fulfils purpose and audience precisely |
| Accuracy & evidence | Errors / unsupported | Minor gaps | Accurate, sourced, limitations stated |
| Structure & storyline | Disorganised | Logical but flat | Clear arc / hierarchy |
| Depth | Superficial | Adequate | Specific, quantified, insightful |
| Visual design | Broken / cluttered | Clean but generic | Polished, purposeful visuals |
| Template fidelity | Identity lost | Recognisable | Looks made in-house |
| Edit precision | Collateral damage | Small side effects | Exactly the requested change |

Target: mean ≥ 4.0, no criterion mean < 3.5 on the evaluation set (Stage 12).

## 6. Reporting

Each version stores a `QualityReport` (DATA_MODELS §9.2) shown in the API and UI:
1. Verdict and counts: detected / corrected / unresolved / not performed.
2. Unresolved findings with location and plain-language message.
3. Corrected findings with the repair taken.
4. Checks not performed, with reason.
5. Evidence summary: sources used, coverage, unsupported claims, gaps.

Today the UI shows the `ValidationReport` (score, issues grouped by slide/section); the four-way split arrives with the `QualityReport` in Stage 8.
