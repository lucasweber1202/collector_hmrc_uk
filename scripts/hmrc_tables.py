"""Reading HMRC's bulletin workbook layout.

Both HMRC bulletins in this repository publish the same workbook convention, so
the convention is described once here rather than twice in the two
``extract_hmrc_*`` modules. This is a description of one publisher's file
format, not a collection framework: it owns no HTTP, no series identifiers, no
validation policy and no database access, and nothing inherits from it.

Layout
------
Each data sheet carries several stacked blocks introduced by a title cell in
column A:

``Table Na.``
    annual totals by financial year ("2023 to 2024")
``Table Nb.``
    annual totals by calendar year ("2024")
``Table Nc.``
    the monthly series ("August 2023", "July 2026 [provisional]")

Only the monthly block is collected by this repository. The financial-year and
calendar-year blocks are HMRC's own aggregations of exactly the same monthly
figures, so storing them too would duplicate the same information under a
second frequency and invite double counting in the research layer, which can
rebuild either aggregation from the monthly series.

Cells
-----
Missing values are published as markers rather than blanks: ``[X]`` in the
tobacco bulletin and ``[No Data]`` in the alcohol bulletin. A month label may
carry a ``[provisional]`` or ``[revised]`` suffix, which is a publication
status rather than part of the period, so it is stripped before parsing and
reported separately.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from datetime import date

from odf import teletype
from odf.opendocument import load
from odf.table import CoveredTableCell, Table, TableCell, TableRow

logger = logging.getLogger(__name__)

# A generous ceiling on a repeated-cell run, so a malformed workbook cannot
# expand into an unbounded row.
MAX_COLUMNS = 40
_CELL_QNAMES = frozenset({TableCell().qname, CoveredTableCell().qname})

# Values HMRC publishes in place of a number.
MISSING_VALUES = frozenset({"", "[x]", "[no data]", "[z]", "[c]", "[low]", ":", "-", "..", "n/a"})

# A status suffix on a period label, for example "July 2026 [provisional]".
_STATUS_SUFFIX = re.compile(r"\[(provisional|revised|revision|r|p)\]", re.IGNORECASE)

_MONTHS = {
    month: index
    for index, month in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        start=1,
    )
}

# "Table 1c." / "Table_1c:" / "Table 12c -" all introduce the monthly block.
_MONTHLY_BLOCK = re.compile(r"^table[\s_]*\d+c\b", re.IGNORECASE)
_ANY_BLOCK = re.compile(r"^table[\s_]*\d+[abc]\b", re.IGNORECASE)
_END_OF_SHEET = re.compile(r"^end of (worksheet|table)", re.IGNORECASE)


@dataclass(frozen=True)
class MonthlyBlock:
    """One sheet's monthly block: its column labels and its dated rows."""

    sheet: str
    title: str
    # Column label -> column index, for the data columns only (column 0 is the
    # period label and is excluded).
    columns: dict[str, int]
    # (reference_date, provisional, row cells)
    rows: list[tuple[date, bool, list[str]]]


def _cell_values(row: TableRow) -> list[str]:
    """Expand one ODS row into a flat list of cell strings, honouring repeats."""
    expanded: list[str] = []
    for cell in row.childNodes:
        if cell.qname not in _CELL_QNAMES:
            continue
        repeat = int(cell.getAttribute("numbercolumnsrepeated") or 1)
        value = cell.getAttribute("value")
        text = value if value is not None else teletype.extractText(cell).strip()
        if repeat > MAX_COLUMNS:
            repeat = 1
        remaining = MAX_COLUMNS - len(expanded)
        if remaining <= 0:
            break
        expanded.extend([text] * min(repeat, remaining))
    return expanded


def read_sheets(body: bytes, url: str) -> dict[str, list[list[str]]]:
    """Return every real sheet in the workbook as a grid of cell strings.

    HMRC's workbooks carry stale external-link entries whose names look like
    ``'file://.../BOARDNEW.XLS'#Sheet1``. They are link targets left behind by
    the producing spreadsheet, not sheets, and are dropped here so a caller
    never has to know they exist.
    """
    document = load(io.BytesIO(body))
    sheets: dict[str, list[list[str]]] = {}
    for table in document.getElementsByType(Table):
        name = str(table.getAttribute("name"))
        if name.startswith("'file://"):
            continue
        sheets[name] = [_cell_values(row) for row in table.getElementsByType(TableRow)]
    if not sheets:
        raise ValueError(f"HMRC workbook {url} contains no readable sheet")
    return sheets


def parse_period(raw: str) -> tuple[date, bool] | None:
    """Parse a monthly period label into its first-of-month date.

    Returns ``(reference_date, provisional)``, or ``None`` when the label is not
    a month at all — a financial year ("2023 to 2024"), a calendar year
    ("2024"), a footnote or a blank.
    """
    label = raw.strip()
    if not label:
        return None
    provisional = bool(_STATUS_SUFFIX.search(label))
    cleaned = _STATUS_SUFFIX.sub("", label).strip().rstrip(",.")
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", cleaned)
    if not match:
        return None
    month = _MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    return date(int(match.group(2)), month, 1), provisional


def monthly_block(grid: list[list[str]], sheet: str, url: str) -> MonthlyBlock:
    """Extract the monthly block from one sheet grid.

    The block runs from its ``Table Nc`` title row to the next block title or
    the end-of-worksheet marker, so a future block appended below it cannot be
    silently swept into the monthly series.
    """
    start = next(
        (
            index
            for index, row in enumerate(grid)
            if row and _MONTHLY_BLOCK.match(row[0].strip())
        ),
        None,
    )
    if start is None:
        raise ValueError(
            f"HMRC workbook {url} sheet {sheet} has no monthly (…c) block; the published "
            "layout changed and must be re-verified against the official source"
        )
    header = grid[start]
    columns: dict[str, int] = {}
    for index, raw in enumerate(header[1:], start=1):
        label = re.sub(r"\s+", " ", raw).strip()
        if not label:
            continue
        if label in columns:
            raise ValueError(
                f"HMRC workbook {url} sheet {sheet} publishes column {label!r} twice in its "
                "monthly block"
            )
        columns[label] = index
    if not columns:
        raise ValueError(f"HMRC workbook {url} sheet {sheet} monthly block has no data columns")

    rows: list[tuple[date, bool, list[str]]] = []
    for row in grid[start + 1 :]:
        if not row or not row[0].strip():
            continue
        first = row[0].strip()
        if _END_OF_SHEET.match(first):
            break
        if _ANY_BLOCK.match(first):
            # A further block starts here; the monthly one has ended.
            break
        parsed = parse_period(first)
        if parsed is None:
            continue
        reference_date, provisional = parsed
        rows.append((reference_date, provisional, row))
    if not rows:
        raise ValueError(
            f"HMRC workbook {url} sheet {sheet} monthly block carries no dated rows"
        )
    return MonthlyBlock(sheet=sheet, title=header[0].strip(), columns=columns, rows=rows)


def parse_value(raw: str, *, sheet: str, label: str, reference_date: date, url: str) -> float | None:
    """Parse one published cell, returning None for a published missing marker."""
    text = raw.strip()
    if text.lower() in MISSING_VALUES:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError as exc:
        raise ValueError(
            f"HMRC workbook {url} sheet {sheet} has unparseable value {text!r} for {label!r} "
            f"at {reference_date}"
        ) from exc
