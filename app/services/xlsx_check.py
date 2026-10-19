"""Independent recalculation of a saved workbook (FR-XLSX-6, AC-4; ADR-031).

Re-opens the file with openpyxl and evaluates every formula with a small evaluator (arithmetic, cell and range
references across sheets, defined names, SUM/AVERAGE/MIN/MAX/COUNT/SUMIF/AVERAGEIF/ROUND/IF/ABS). It reports
``#REF!`` (unknown sheet), ``#NAME?`` (unknown function or name), ``#DIV/0!``, circular references, formulas it cannot
evaluate (reported as not checked, never as passing) and the workbook's expected-value checks.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter
from pydantic import BaseModel, Field

SHEET = r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_. ]*?))!"
CELL = r"\$?([A-Z]{1,3})\$?(\d+)"
RANGE_RE = re.compile(rf"(?<!['\w])(?:{SHEET})?{CELL}:{CELL}")
REF_RE = re.compile(rf"(?<!['\w$])(?:{SHEET})?{CELL}(?![\w(])")
FUNC_RE = re.compile(r"\b([A-Z][A-Z0-9.]*)\(")
STRING_RE = re.compile(r'"[^"]*"')


class XlsxError(Exception):
    def __init__(self, code: str, where: str = "") -> None:
        super().__init__(code)
        self.code, self.where = code, where


class CheckResult(BaseModel):
    ref: str
    expected: float
    actual: float | None = None
    ok: bool = False
    description: str = ""


class RecalcReport(BaseModel):
    formulas: int = 0
    evaluated: int = 0
    errors: list[str] = Field(default_factory=list)
    not_checked: list[str] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and all(c.ok for c in self.checks)


def _nums(v: Any) -> list[float]:
    if isinstance(v, list):
        return [x for item in v for x in _nums(item)]
    return [float(v)] if isinstance(v, (int, float)) and not isinstance(v, bool) else []


def _match(value: Any, criterion: Any) -> bool:
    if isinstance(criterion, str):
        m = re.match(r"^(<=|>=|<>|<|>|=)(.*)$", criterion)
        if m:
            op, rhs = m.groups()
            try:
                a, b = float(value), float(rhs)
            except (TypeError, ValueError):
                a, b = str(value), rhs
            return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b, "=": a == b, "<>": a != b}[op]
        return str(value).lower() == criterion.lower()
    return value == criterion


def _flat(v: Any) -> list[Any]:
    return [x for row in v for x in row] if v and isinstance(v[0], list) else list(v)


def _div(a: float, b: float) -> float:
    if b == 0:
        raise XlsxError("#DIV/0!")
    return a / b


FUNCS = {
    "SUM": lambda *a: sum(x for v in a for x in _nums(v)),
    "AVERAGE": lambda *a: _div(sum(x for v in a for x in _nums(v)), len([x for v in a for x in _nums(v)])),
    "MIN": lambda *a: min(x for v in a for x in _nums(v)),
    "MAX": lambda *a: max(x for v in a for x in _nums(v)),
    "COUNT": lambda *a: len([x for v in a for x in _nums(v)]),
    "ROUND": lambda v, n=0: round(v, int(n)),
    "ABS": abs,
    "IF": lambda c, a, b=0: a if c else b,
    "SUMIF": lambda rng, crit, s=None: sum(float(y) for x, y in zip(_flat(rng), _flat(s if s is not None else rng))
                                          if _match(x, crit) and _nums(y)),
    "AVERAGEIF": lambda rng, crit, s=None: _div(*(lambda ys: (sum(ys), len(ys)))(
        [float(y) for x, y in zip(_flat(rng), _flat(s if s is not None else rng)) if _match(x, crit) and _nums(y)])),
}


class Evaluator:
    def __init__(self, wb: Any) -> None:
        self.wb = wb
        self.cache: dict[tuple[str, str], Any] = {}
        self.stack: set[tuple[str, str]] = set()
        self.names = {n: dn.attr_text for n, dn in wb.defined_names.items()}

    def _sheet(self, name: str | None, current: str) -> Any:
        title = name or current
        if title not in self.wb.sheetnames:
            raise XlsxError("#REF!", f"sheet '{title}'")
        return self.wb[title]

    def cell(self, sheet: str, coord: str) -> Any:
        key = (sheet, coord)
        if key in self.cache:
            return self.cache[key]
        if key in self.stack:
            raise XlsxError("circular", f"{sheet}!{coord}")
        value = self._sheet(sheet, sheet)[coord].value
        if isinstance(value, str) and value.startswith("="):
            self.stack.add(key)
            try:
                value = self.formula(value[1:], sheet)
            finally:
                self.stack.discard(key)
        self.cache[key] = value
        return value

    def rng(self, sheet: str, a: str, b: str) -> list[list[Any]]:
        ws = self._sheet(sheet, sheet)
        c1, r1 = re.match(r"([A-Z]+)(\d+)", a).groups()
        c2, r2 = re.match(r"([A-Z]+)(\d+)", b).groups()
        r2 = min(int(r2), ws.max_row)
        return [[self.cell(ws.title, f"{get_column_letter(c)}{r}")
                 for c in range(column_index_from_string(c1), column_index_from_string(c2) + 1)]
                for r in range(int(r1), r2 + 1)]

    def formula(self, text: str, sheet: str) -> Any:
        strings: list[str] = []

        def keep(m: re.Match) -> str:
            strings.append(m.group(0)[1:-1])
            return f"__s[{len(strings) - 1}]"

        expr = STRING_RE.sub(keep, text)
        for fn in FUNC_RE.findall(expr):
            if fn not in FUNCS:
                raise XlsxError("#NAME?", fn)
        expr = RANGE_RE.sub(lambda m: f"__rng({(m.group(1) or m.group(2) or sheet)!r}, '{m.group(3)}{m.group(4)}', '{m.group(5)}{m.group(6)}')", expr)
        expr = REF_RE.sub(lambda m: f"__ref({(m.group(1) or m.group(2) or sheet)!r}, '{m.group(3)}{m.group(4)}')", expr)
        expr = FUNC_RE.sub(lambda m: f"__f['{m.group(1)}'](", expr)

        def name(m: re.Match) -> str:
            word = m.group(0)
            if word in self.names:
                target = self.names[word]
                s, _, c = target.rpartition("!")
                return f"__ref({s.strip(chr(39))!r}, '{c.replace('$', '')}')"
            raise XlsxError("#NAME?", word)

        expr = re.sub(r"(?<![\w'\"\[])(?!__)[A-Za-z_][A-Za-z0-9_]*(?![\w'(\[])", name, expr)
        expr = expr.replace("^", "**").replace("<>", "!=")
        expr = re.sub(r"(?<![<>!=])=(?!=)", "==", expr)
        env = {"__s": strings, "__f": FUNCS, "__rng": self.rng, "__ref": self.cell}
        try:
            return eval(expr, {"__builtins__": {}}, env)  # noqa: S307 — expression built from parsed tokens only
        except ZeroDivisionError:
            raise XlsxError("#DIV/0!") from None
        except XlsxError:
            raise
        except Exception as exc:
            raise XlsxError("unsupported", f"{text} ({exc.__class__.__name__})") from None


def recalculate(path: str | Path, checks: list[Any] | None = None) -> RecalcReport:
    wb = openpyxl.load_workbook(str(path), data_only=False)
    ev = Evaluator(wb)
    report = RecalcReport()
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    report.formulas += 1
                    where = f"{ws.title}!{c.coordinate}"
                    try:
                        ev.cell(ws.title, c.coordinate)
                        report.evaluated += 1
                    except XlsxError as err:
                        if err.code == "unsupported":
                            report.not_checked.append(f"{where}: {err.where}")
                        else:
                            report.errors.append(f"{where}: {err.code} {err.where}".strip())
    for chk in checks or []:
        ref = chk.ref if hasattr(chk, "ref") else chk["ref"]
        expected = float(chk.expected if hasattr(chk, "expected") else chk["expected"])
        tol = float(getattr(chk, "tolerance", None) or (chk.get("tolerance", 0.01) if isinstance(chk, dict) else 0.01))
        sheet, _, coord = ref.rpartition("!")
        res = CheckResult(ref=ref, expected=expected, description=getattr(chk, "description", ""))
        try:
            v = ev.cell(sheet.strip("'"), coord.replace("$", ""))
            res.actual = float(v) if _nums(v) else None
            res.ok = res.actual is not None and abs(res.actual - expected) <= max(tol, abs(expected) * 1e-9)
        except XlsxError as err:
            report.errors.append(f"check {ref}: {err.code}")
        report.checks.append(res)
    return report
