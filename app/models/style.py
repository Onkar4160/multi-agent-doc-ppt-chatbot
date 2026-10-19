"""Explicit formatting changes requested by the user (DATA_MODELS §7.4, FR-DOCX-5).

Renderers apply them to named styles (Word), text roles (PowerPoint) and header styles (Excel), never by rewriting
content, so a restyle needs no LLM call and leaves every content unit unchanged.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

NAMED_COLOURS = {"blue": "1F4E79", "navy": "13293D", "red": "C0392B", "green": "2E7D32", "orange": "E07A2E",
                 "purple": "5E35B1", "teal": "00897B", "black": "000000", "grey": "5B6770", "gray": "5B6770"}


class StyleOverrides(BaseModel):
    heading_1_size_pt: float | None = None
    heading_2_size_pt: float | None = None
    body_size_pt: float | None = None
    body_font: str | None = None
    heading_font: str | None = None
    accent_color: str | None = None
    line_spacing: float | None = None

    def apply(self, changes: list[Any]) -> list[str]:
        """Apply StyleChange items ({path, value}); returns readable descriptions of what changed."""
        done = []
        for ch in changes:
            path = ch.path if hasattr(ch, "path") else ch["path"]
            value = ch.value if hasattr(ch, "value") else ch["value"]
            field = path.replace(".", "_")
            if field == "accent_color":
                v = str(value).lstrip("#")
                value = NAMED_COLOURS.get(v.lower(), v.upper()) if not re.fullmatch(r"[0-9a-fA-F]{6}", v) else v.upper()
            if field in type(self).model_fields:
                setattr(self, field, value)
                done.append(f"{path} = {value}")
        return done
