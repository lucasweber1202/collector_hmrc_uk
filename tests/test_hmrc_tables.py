"""The HMRC workbook layout helper: blocks, periods and missing markers."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from scripts.hmrc_tables import monthly_block, parse_period, parse_value, read_sheets
from tests._hmrc_ods import build

HEADER = ("Cigarettes", "Cigars")
MONTHLY = [("January 1991", ("1.0", "2.0")), ("February 1991", ("3.0", "4.0"))]
ANNUAL = [("1990 to 1991", ("10.0", "20.0")), ("1991", ("11.0", "21.0"))]


def _workbook(**overrides: Any) -> bytes:
    spec: dict[str, Any] = {
        "name": "Table_1_receipts",
        "title": "Tobacco receipts",
        "header": HEADER,
        "rows": MONTHLY,
        "annual_rows": ANNUAL,
    }
    spec.update(overrides)
    return build([spec])


def test_only_the_monthly_block_is_read() -> None:
    """The financial-year and calendar-year blocks are HMRC's own aggregations."""
    sheets = read_sheets(_workbook(), "t://x")
    block = monthly_block(sheets["Table_1_receipts"], "Table_1_receipts", "t://x")
    assert [row[0] for row in block.rows] == [date(1991, 1, 1), date(1991, 2, 1)]
    assert list(block.columns) == list(HEADER)


def test_a_workbook_with_no_monthly_block_fails_loudly() -> None:
    sheets = read_sheets(_workbook(block_letter="b", annual_rows=()), "t://x")
    with pytest.raises(ValueError, match="no monthly"):
        monthly_block(sheets["Table_1_receipts"], "Table_1_receipts", "t://x")


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("January 1991", (date(1991, 1, 1), False)),
        ("July 2026 [provisional]", (date(2026, 7, 1), True)),
        ("March 2020 [revised]", (date(2020, 3, 1), True)),
    ],
)
def test_month_labels_parse_with_their_status(label: str, expected: tuple) -> None:
    assert parse_period(label) == expected


@pytest.mark.parametrize("label", ["2023 to 2024", "2024", "", "End of worksheet", "Notes"])
def test_a_non_month_label_is_not_a_period(label: str) -> None:
    assert parse_period(label) is None


@pytest.mark.parametrize("marker", ["[X]", "[No Data]", "[x]", "", "..", "-"])
def test_a_published_missing_marker_is_not_a_zero(marker: str) -> None:
    assert parse_value(marker, sheet="s", label="l", reference_date=date(2020, 1, 1), url="u") is None


def test_an_unparseable_value_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unparseable value"):
        parse_value("abc", sheet="s", label="l", reference_date=date(2020, 1, 1), url="u")


def test_a_negative_value_is_parsed_not_rejected() -> None:
    """HMRC publishes negative receipts when repayments exceed payments."""
    value = parse_value("-4.92", sheet="s", label="l", reference_date=date(2020, 1, 1), url="u")
    assert value == pytest.approx(-4.92)


def test_stale_external_link_sheets_are_not_sheets() -> None:
    sheets = read_sheets(_workbook(), "t://x")
    assert all(not name.startswith("'file://") for name in sheets)
