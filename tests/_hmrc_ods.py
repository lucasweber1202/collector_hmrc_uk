"""Shared test fixture builder for HMRC-shaped ODS workbooks."""

from __future__ import annotations

import io
from collections.abc import Sequence

from odf.opendocument import OpenDocumentSpreadsheet
from odf.table import Table, TableCell, TableRow
from odf.text import P


def cell(text: str) -> TableCell:
    node = TableCell()
    node.addElement(P(text=text))
    return node


def add_sheet(
    document: OpenDocumentSpreadsheet,
    name: str,
    title: str,
    header: Sequence[str],
    rows: Sequence[tuple[str, Sequence[str]]],
    *,
    annual_rows: Sequence[tuple[str, Sequence[str]]] = (),
    block_letter: str = "c",
    table_number: int = 1,
) -> None:
    """Add one HMRC-shaped sheet: an annual block, then the monthly block."""
    table = Table(name=name)
    intro = TableRow()
    intro.addElement(cell(title))
    table.addElement(intro)

    if annual_rows:
        head = TableRow()
        head.addElement(cell(f"Table {table_number}a. {title}"))
        for label in header:
            head.addElement(cell(label))
        table.addElement(head)
        for period, values in annual_rows:
            row = TableRow()
            row.addElement(cell(period))
            for value in values:
                row.addElement(cell(value))
            table.addElement(row)

    head = TableRow()
    head.addElement(cell(f"Table {table_number}{block_letter}. {title}"))
    for label in header:
        head.addElement(cell(label))
    table.addElement(head)
    for period, values in rows:
        row = TableRow()
        row.addElement(cell(period))
        for value in values:
            row.addElement(cell(value))
        table.addElement(row)

    end = TableRow()
    end.addElement(cell("End of worksheet"))
    table.addElement(end)
    document.spreadsheet.addElement(table)


def build(sheets: Sequence[dict]) -> bytes:
    """Render a workbook from a list of add_sheet keyword dicts."""
    document = OpenDocumentSpreadsheet()
    for spec in sheets:
        add_sheet(document, **spec)
    buffer = io.BytesIO()
    document.write(buffer)
    return buffer.getvalue()
