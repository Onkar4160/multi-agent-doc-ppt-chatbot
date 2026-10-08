"""XLSX Generator Agent – generates structured WorkbookModel from topic and evidence."""

from __future__ import annotations

import logging
from typing import Any

from app.llm.client import get_llm_client, LLMClient
from app.models.workbook_model import CellModel, ChartModel, SheetModel, TableModel, WorkbookModel
from app.services.artifact_nodes import ensure_node_ids
from app.services.evidence_pack import format_evidence_pack

logger = logging.getLogger(__name__)

XLSX_GENERATOR_PROMPT = """You are a financial modeler and data analyst agent.
Generate a structured, professional multi-table spreadsheet WorkbookModel based on the topic and evidence below.

TOPIC:
"{topic}"

EVIDENCE PACK:
{evidence_text}

REQUIREMENTS:
1. Return a valid WorkbookModel object with sheets, tables, and rows.
2. Include at least 1-2 worksheets (e.g. 'Summary', 'Detailed_Breakdown').
3. Include proper numeric data, formatted column headers, and Excel formulas (e.g. '=SUM(B2:B5)', '=AVERAGE(C2:C5)').
4. Ground metrics in the EVIDENCE PACK where relevant numbers exist.
"""


def generate_workbook_model(
    topic: str,
    context_summary: str = "",
    evidence_pack: list[Any] | None = None,
    llm_client: LLMClient | None = None,
) -> WorkbookModel:
    """Generate a structured WorkbookModel using LLM with deterministic fallback."""
    if llm_client is None:
        llm_client = get_llm_client()

    ev_text = format_evidence_pack(evidence_pack) if evidence_pack else "(No external evidence)"
    prompt = XLSX_GENERATOR_PROMPT.format(topic=topic, evidence_text=ev_text)

    try:
        model = llm_client.generate_json(
            prompt=prompt,
            schema=WorkbookModel,
            system="You are an enterprise financial modeler producing structured Excel workbooks.",
        )
        ensure_node_ids(model)
        logger.info("Successfully generated WorkbookModel with %d sheets", len(model.sheets))
        return model
    except Exception as exc:
        logger.warning("LLM workbook generation failed (%s). Generating fallback WorkbookModel.", exc)
        return _build_fallback_workbook(topic)


def _build_fallback_workbook(topic: str) -> WorkbookModel:
    """Build a deterministic, well-structured financial/analytical workbook model."""
    sheet1_table = TableModel(
        name="Financial Overview",
        range="A1:E5",
        headers=["Category", "Q1 Actual ($k)", "Q2 Actual ($k)", "Q3 Projected ($k)", "Q4 Projected ($k)"],
        rows=[
            ["Enterprise License Revenue", 450, 520, 610, 750],
            ["Professional Services", 120, 140, 150, 160],
            ["Cloud Infrastructure Costs", 85, 95, 110, 125],
            ["Net Operating Margin", "=B2+B3-B4", "=C2+C3-C4", "=D2+D3-D4", "=E2+E3-E4"],
        ],
    )

    sheet1 = SheetModel(
        name="Executive_Summary",
        tables=[sheet1_table],
        cells=[
            CellModel(coordinate="A7", value="Annual Total License", data_type="string"),
            CellModel(coordinate="B7", formula="=SUM(B2:E2)", data_type="formula"),
        ],
        charts=[
            ChartModel(
                title=f"{topic} - Revenue Trends",
                chart_type="column",
                data_range="Sheet1!$B$2:$E$2",
            )
        ],
    )

    wb = WorkbookModel(
        title=f"{topic} Analysis & Model",
        sheets=[sheet1],
    )
    ensure_node_ids(wb)
    return wb
