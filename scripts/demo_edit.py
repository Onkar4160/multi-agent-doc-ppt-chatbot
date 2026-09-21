"""Demo script demonstrating Step 8: Conversational Editing, Version History, and Document <-> Presentation Conversion."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.converter import convert_artifact
from app.agents.doc_analyzer import analyze_document
from app.agents.doc_generator import generate_document_model
from app.agents.editor import edit_artifact
from app.agents.ppt_analyzer import analyze_presentation
from app.agents.ppt_generator import generate_deck_model
from app.core.database import get_sync_session
from app.models.artifact import Artifact, ArtifactVersion
from app.services.docx_renderer import render_docx
from app.services.pptx_renderer import render_pptx
from app.services.versioning import add_version

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("demo_edit")

DEFAULT_DOCX_TEMPLATE = Path("data/sample_templates/Company_Proposal.docx")
DEFAULT_PPTX_TEMPLATE = Path("data/sample_templates/Company_Template.pptx")


def ensure_initial_artifacts(session) -> tuple[Artifact, Artifact]:
    """Ensure at least one DOCX and one PPTX artifact exist in DB, generating them if missing."""
    docx_art = (
        session.query(Artifact)
        .filter(Artifact.artifact_type == "docx")
        .order_by(Artifact.id.desc())
        .first()
    )
    pptx_art = (
        session.query(Artifact)
        .filter(Artifact.artifact_type == "pptx")
        .order_by(Artifact.id.desc())
        .first()
    )

    out_dir = Path("data/outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    if not docx_art:
        logger.info("No DOCX artifact found. Generating initial DOCX artifact...")
        profile = analyze_document(DEFAULT_DOCX_TEMPLATE)
        doc_model = generate_document_model(
            brief="AI Cloud Infrastructure Strategy and Roadmap",
            profile=profile,
        )
        out_file = out_dir / "Initial_Proposal.docx"
        render_docx(doc_model, DEFAULT_DOCX_TEMPLATE, profile, out_file)

        docx_art = Artifact(title=doc_model.title, artifact_type="docx")
        session.add(docx_art)
        session.flush()

        v1 = ArtifactVersion(
            artifact_id=docx_art.id,
            version_no=1,
            model_json=doc_model.model_dump_json(),
            file_path=str(out_file),
            file_type="docx",
            change_summary="Initial DOCX generation",
        )
        session.add(v1)
        session.commit()
        logger.info(f"Created DOCX Artifact ID: {docx_art.id}")

    if not pptx_art:
        logger.info("No PPTX artifact found. Generating initial PPTX artifact...")
        profile = analyze_presentation(DEFAULT_PPTX_TEMPLATE)
        deck_model = generate_deck_model(
            brief="AI Cloud Infrastructure Executive Overview",
            profile=profile,
            slide_count=6,
        )
        out_file = out_dir / "Initial_Deck.pptx"
        render_pptx(deck_model, DEFAULT_PPTX_TEMPLATE, profile, out_file)

        pptx_art = Artifact(title=deck_model.title, artifact_type="pptx")
        session.add(pptx_art)
        session.flush()

        v1 = ArtifactVersion(
            artifact_id=pptx_art.id,
            version_no=1,
            model_json=deck_model.model_dump_json(),
            file_path=str(out_file),
            file_type="pptx",
            change_summary="Initial PPTX generation",
        )
        session.add(v1)
        session.commit()
        logger.info(f"Created PPTX Artifact ID: {pptx_art.id}")

    return docx_art, pptx_art


def _refuse_mock(artifact, step_name: str, failures: list) -> bool:
    """Return True (and record failure) if the artifact title starts with [MOCK]."""
    if artifact.title.startswith("[MOCK]"):
        msg = f"{step_name}: refused – artifact title starts with '[MOCK]' ({artifact.title!r})"
        print(f"FAILED: {msg}")
        failures.append(msg)
        return True
    return False


def main():
    print("=" * 70)
    print("       STEP 8: CONVERSATIONAL EDITING & CONVERSION DEMO       ")
    print("=" * 70)

    session = get_sync_session()
    failures: list[str] = []

    try:
        docx_art, pptx_art = ensure_initial_artifacts(session)

        # ---------------------------------------------------------------------
        # a) "Add an executive summary." (docx)
        # ---------------------------------------------------------------------
        print("\n--- [Step a] Edit DOCX: 'Add an executive summary.' ---")
        if _refuse_mock(docx_art, "Step a", failures):
            pass
        else:
            try:
                res_a = edit_artifact(
                    artifact_id=docx_art.id,
                    instruction="Add an executive summary.",
                    db_session=session,
                )
                print(f"New Version:  v{res_a.new_version_no}")
                print(f"Summary:      {res_a.summary}")
                print(f"Diff:         {json.dumps(res_a.diff, indent=2)}")
                print(f"LLM Calls:    {res_a.llm_calls}")
            except Exception as exc:
                msg = f"Step a: {exc}"
                print(f"FAILED: {msg}")
                failures.append(msg)

        # ---------------------------------------------------------------------
        # b) "Make the presentation more concise." (pptx)
        # ---------------------------------------------------------------------
        print("\n--- [Step b] Edit PPTX: 'Make the presentation more concise.' ---")
        if _refuse_mock(pptx_art, "Step b", failures):
            pass
        else:
            try:
                res_b = edit_artifact(
                    artifact_id=pptx_art.id,
                    instruction="Make the presentation more concise.",
                    db_session=session,
                )
                print(f"New Version:  v{res_b.new_version_no}")
                print(f"Summary:      {res_b.summary}")
                print(f"Diff:         {json.dumps(res_b.diff, indent=2)}")
                print(f"LLM Calls:    {res_b.llm_calls}")
            except Exception as exc:
                msg = f"Step b: {exc}"
                print(f"FAILED: {msg}")
                failures.append(msg)

        # ---------------------------------------------------------------------
        # c) "Add a competitive analysis section." (docx & matching slide to pptx)
        # ---------------------------------------------------------------------
        print("\n--- [Step c] Edit DOCX & PPTX: 'Add a competitive analysis section.' ---")
        if _refuse_mock(docx_art, "Step c (docx)", failures):
            pass
        else:
            try:
                res_c_doc = edit_artifact(
                    artifact_id=docx_art.id,
                    instruction="Add a competitive analysis section.",
                    db_session=session,
                )
                print(f"DOCX New Version: v{res_c_doc.new_version_no}")
                print(f"DOCX Summary:     {res_c_doc.summary}")
                print(f"DOCX Diff:        {json.dumps(res_c_doc.diff, indent=2)}")
            except Exception as exc:
                msg = f"Step c (docx): {exc}"
                print(f"FAILED: {msg}")
                failures.append(msg)

        if _refuse_mock(pptx_art, "Step c (pptx)", failures):
            pass
        else:
            try:
                res_c_ppt = edit_artifact(
                    artifact_id=pptx_art.id,
                    instruction="Add a competitive analysis slide.",
                    db_session=session,
                )
                print(f"PPTX New Version: v{res_c_ppt.new_version_no}")
                print(f"PPTX Summary:     {res_c_ppt.summary}")
                print(f"PPTX Diff:        {json.dumps(res_c_ppt.diff, indent=2)}")
            except Exception as exc:
                msg = f"Step c (pptx): {exc}"
                print(f"FAILED: {msg}")
                failures.append(msg)

        # ---------------------------------------------------------------------
        # d) "Update the report using the latest web information." (docx)
        # ---------------------------------------------------------------------
        print("\n--- [Step d] Edit DOCX: 'Update the report using the latest web information.' ---")
        if _refuse_mock(docx_art, "Step d", failures):
            pass
        else:
            try:
                res_d = edit_artifact(
                    artifact_id=docx_art.id,
                    instruction="Update the report using the latest web information.",
                    db_session=session,
                )
                print(f"New Version:  v{res_d.new_version_no}")
                print(f"Summary:      {res_d.summary}")
                print(f"Diff:         {json.dumps(res_d.diff, indent=2)}")
                print(f"LLM Calls:    {res_d.llm_calls}")
            except Exception as exc:
                msg = f"Step d: {exc}"
                print(f"FAILED: {msg}")
                failures.append(msg)

        # ---------------------------------------------------------------------
        # e) Convert DOCX to PPTX
        # ---------------------------------------------------------------------
        print("\n--- [Conversion] Convert DOCX -> PPTX ---")
        if _refuse_mock(docx_art, "Conversion", failures):
            pass
        else:
            try:
                conv_res = convert_artifact(
                    artifact_id=docx_art.id,
                    target_kind="pptx",
                    slide_count=8,
                    db_session=session,
                )
                print(f"New PPTX Artifact ID: {conv_res.new_artifact_id}")
                print(f"Title:                {conv_res.title}")
                print(f"File Path:            {conv_res.file_path}")
            except Exception as exc:
                msg = f"Conversion: {exc}"
                print(f"FAILED: {msg}")
                failures.append(msg)

        # ---------------------------------------------------------------------
        # f) Revert DOCX to Version 1
        # ---------------------------------------------------------------------
        print("\n--- [Revert] Revert DOCX to Version 1 ---")
        try:
            v1_rec = (
                session.query(ArtifactVersion)
                .filter(
                    ArtifactVersion.artifact_id == docx_art.id,
                    ArtifactVersion.version_no == 1,
                )
                .first()
            )
            latest_rec = (
                session.query(ArtifactVersion)
                .filter(ArtifactVersion.artifact_id == docx_art.id)
                .order_by(ArtifactVersion.version_no.desc())
                .first()
            )

            if v1_rec and latest_rec:
                next_v_no = latest_rec.version_no + 1
                out_file = Path("data/outputs") / f"Proposal_{docx_art.id}_v{next_v_no}.docx"
                if Path(v1_rec.file_path).exists():
                    import shutil
                    shutil.copy2(v1_rec.file_path, out_file)
                else:
                    out_file = Path(v1_rec.file_path)

                new_v = ArtifactVersion(
                    artifact_id=docx_art.id,
                    version_no=next_v_no,
                    model_json=v1_rec.model_json,
                    file_path=str(out_file),
                    file_type="docx",
                    parent_version_id=latest_rec.id,
                    change_summary="Reverted to version 1",
                    source_ids_json=v1_rec.source_ids_json,
                    diff_json=json.dumps({"action": "revert", "target_version": 1}),
                )
                session.add(new_v)
                session.commit()
                print(f"Revert completed. Created NEW Version v{new_v.version_no} copying Version 1 snapshot at '{out_file}'.")
            else:
                msg = "Revert: could not find Version 1 record"
                print(f"FAILED: {msg}")
                failures.append(msg)
        except Exception as exc:
            msg = f"Revert: {exc}"
            print(f"FAILED: {msg}")
            failures.append(msg)

    finally:
        session.close()

    print("\n" + "=" * 70)
    if failures:
        print(f"  PARTIAL – {len(failures)} step(s) failed:")
        for f in failures:
            print(f"    • {f}")
        print("=" * 70)
        sys.exit(1)
    else:
        print("                   COMPLETED SUCCESSFULLY                 ")
        print("=" * 70)


if __name__ == "__main__":
    main()
