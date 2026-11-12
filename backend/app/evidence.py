"""Deterministic source-table evidence for extracted 10-K metrics.

The LLM returns a source snippet, but a reviewer needs more context: the table
header row, the complete source row, the current-period column, and a filing
link.  This module reconstructs that context from the parsed filing tables and
also performs the cross-statement check used by confidence scoring.

No XBRL values are used here.  Every result comes from the filing tables
already supplied to the workflow.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from app.confidence import metric_role, metric_terms
from app.schemas import ExtractedMetric

__all__ = [
    "attach_evidence_context",
    "attach_cross_statement",
    "parse_markdown_table",
]

_SEPARATOR_RE = re.compile(r"^:?-{2,}:?$")
_NUMBER_RE = re.compile(r"^\(?[-+]?\$?\d[\d,]*(?:\.\d+)?\)?$")
_EXPECTED_CATEGORIES = {
    "Revenue": {"income"},
    "Net Income": {"income"},
    "Total Assets": {"balance"},
    "Total Liabilities": {"balance"},
    "Shareholders' Equity": {"balance"},
    "Operating Cash Flow": {"cashflow"},
    "Cash and Cash Equivalents": {"balance"},
    "R&D / Operating Income": {"income"},
}


def _clean_number(text: str) -> float | None:
    raw = str(text or "").strip().replace("$", "").replace("−", "-")
    if not raw or not _NUMBER_RE.fullmatch(raw):
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()").replace(",", "")
    try:
        number = float(raw)
    except ValueError:
        return None
    return -number if negative else number


def _digits(value: float) -> str:
    text = str(int(value)) if float(value).is_integer() else f"{value:g}"
    return re.sub(r"\D", "", text)


def _cells_from_line(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_markdown_table(markdown: str) -> tuple[list[str], list[list[str]]]:
    """Return the first header row and all non-separator data rows."""
    header: list[str] = []
    rows: list[list[str]] = []
    for line in str(markdown or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = _cells_from_line(stripped)
        if cells and all(_SEPARATOR_RE.fullmatch(cell or "") for cell in cells):
            continue
        if not header:
            header = cells
            continue
        rows.append(cells)
    return header, rows


def _period_years(text: Any) -> set[str]:
    return set(re.findall(r"(?:19|20)\d{2}", str(text or "")))


def _expected_years(metadata: dict[str, Any]) -> set[str]:
    years = _period_years(metadata.get("fiscal_year"))
    years |= _period_years(metadata.get("report_date"))
    return years


def _cell_matches_value(cell: str, value: float) -> bool:
    parsed = _clean_number(cell)
    if parsed is None:
        return False
    if abs(parsed - float(value)) <= 1e-9:
        return True
    return bool(_digits(parsed)) and _digits(parsed) == _digits(float(value))


def _row_text(cells: Iterable[str]) -> str:
    return " | ".join(str(cell) for cell in cells)


def _row_score(
    metric: ExtractedMetric,
    table: dict[str, Any],
    cells: list[str],
    header: list[str],
) -> float:
    role = metric_role(metric.metric_name)
    terms = metric_terms(role)
    label = (cells[0] if cells else "").lower()
    label_match = any(term in label for term in terms)
    value_matches = [
        index
        for index, cell in enumerate(cells[1:], start=1)
        if _cell_matches_value(cell, metric.value)
    ]
    snippet = _row_text(cells).lower()
    source = str(metric.source_snippet or "").lower()
    snippet_match = bool(source and (source in snippet or snippet in source))
    category = str(table.get("category") or "")
    category_match = category in _EXPECTED_CATEGORIES.get(role, {category})
    score = 0.0
    if label_match:
        score += 4.0
    if value_matches:
        score += 3.0
    if snippet_match:
        score += 2.0
    if category_match:
        score += 1.0
    if header and any(_period_years(cell) for cell in header[1:]):
        score += 0.5
    return score


def _value_column(
    cells: list[str],
    header: list[str],
    value: float,
    expected_years: set[str],
) -> tuple[int | None, bool | None]:
    candidates = [
        index
        for index, cell in enumerate(cells)
        if index > 0 and _cell_matches_value(cell, value)
    ]
    if not candidates:
        return None, None
    if expected_years:
        for index in candidates:
            header_years = _period_years(header[index]) if index < len(header) else set()
            if header_years & expected_years:
                return index, True
        for index in candidates:
            header_years = _period_years(header[index]) if index < len(header) else set()
            if header_years and not (header_years & expected_years):
                return index, False
    return candidates[0], None


def _best_row(
    metric: ExtractedMetric,
    tables: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], list[str], list[str], int] | None:
    best: tuple[float, dict[str, Any], list[str], list[str], int] | None = None
    for table_index, table in enumerate(tables or [], start=1):
        if not isinstance(table, dict):
            continue
        header, rows = parse_markdown_table(str(table.get("markdown") or ""))
        for row in rows:
            score = _row_score(metric, table, row, header)
            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, table, header, row, table_index)
    if best is None or best[0] < 3.0:
        return None
    _, table, header, row, table_index = best
    return table, header, row, table_index


def attach_evidence_context(
    metrics: list[ExtractedMetric],
    tables: list[dict[str, Any]] | None,
    filing_metadata: dict[str, Any] | None = None,
) -> None:
    """Attach header, row, column, and period context to each metric."""
    metadata = filing_metadata or {}
    expected_years = _expected_years(metadata)
    for metric in metrics:
        found = _best_row(metric, tables)
        if found is None:
            metric.source_header = metric.source_header or []
            metric.source_row = metric.source_row or []
            metric.source_row_text = metric.source_row_text or metric.source_snippet
            metric.period_column_matched = None
            continue

        table, header, row, table_index = found
        column_index, period_match = _value_column(
            row, header, metric.value, expected_years
        )
        metric.source_table_index = table_index
        metric.source_table_title = str(table.get("title") or f"Table {table_index}")
        metric.source_table_category = str(table.get("category") or "")
        metric.source_header = list(header)
        metric.source_row = list(row)
        metric.source_row_text = _row_text(row)
        metric.value_column_index = column_index
        metric.value_column_header = (
            header[column_index]
            if column_index is not None and column_index < len(header)
            else None
        )
        metric.period_column_matched = period_match


def _cashflow_net_income(
    tables: list[dict[str, Any]] | None,
    expected_years: set[str],
) -> tuple[float | None, str | None]:
    for table in tables or []:
        if not isinstance(table, dict):
            continue
        category = str(table.get("category") or "").lower()
        title = str(table.get("title") or "").lower()
        if category != "cashflow" and "cash flow" not in title:
            continue
        header, rows = parse_markdown_table(str(table.get("markdown") or ""))
        for row in rows:
            label = (row[0] if row else "").lower()
            if not any(
                term in label for term in ("net income", "net earnings", "net loss")
            ):
                continue
            column_index = None
            if expected_years:
                for index, cell in enumerate(header):
                    if index > 0 and _period_years(cell) & expected_years:
                        column_index = index
                        break
            if column_index is None:
                column_index = 1 if len(row) > 1 else None
            if column_index is None:
                continue
            parsed = _clean_number(row[column_index])
            if parsed is not None:
                return parsed, f"{table.get('title') or 'Cash-flow statement'} · {row[0]}"
    return None, None


def attach_cross_statement(
    metrics: list[ExtractedMetric],
    tables: list[dict[str, Any]] | None,
    filing_metadata: dict[str, Any] | None = None,
) -> None:
    """Check Net Income against the cash-flow statement's opening line."""
    expected_years = _expected_years(filing_metadata or {})
    for metric in metrics:
        metric.cross_statement_match = None
        metric.cross_statement_note = None
        if metric_role(metric.metric_name) != "Net Income":
            continue
        cash_value, source = _cashflow_net_income(tables, expected_years)
        if cash_value is None:
            metric.cross_statement_note = (
                "No cash-flow statement net-income line was found; the "
                "cross-statement check is not applicable."
            )
            continue
        tolerance = max(abs(float(metric.value)) * 0.005, 1e-9)
        matches = abs(cash_value - float(metric.value)) <= tolerance
        metric.cross_statement_match = matches
        metric.cross_statement_note = (
            f"{source}: {cash_value:g} vs income-statement {metric.value:g} "
            f"(tolerance ±0.5%)."
        )
