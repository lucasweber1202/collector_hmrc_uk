"""HMRC Tobacco Bulletin parsing, measures, negative receipts and gates."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

import pytest

from scripts.extract_hmrc_tobacco_bulletin import (
    EXPECTED_FIRST_OBSERVATION,
    MIN_EXPECTED_DATES,
    PRODUCT_NAMES,
    SHEETS,
    _build_catalog,
    make_series_id,
    parse_ods,
    parse_series_id,
    strip_unit,
    validate,
)
from scripts.metadata import validate_catalog
from scripts.time_series import Observation
from tests._hmrc_ods import build

RECEIPT_COLS = ("Cigarettes", "Cigars", "HRT", "Other", "Overall total")
CLEARANCE_COLS = (
    "Cigarettes (million sticks)",
    "Cigars ('000 kilograms)",
    "HRT ('000 kilograms)",
    "Other ('000 kilograms)",
    "Non-cigarette tobacco total ('000 kilograms)",
)


def _workbook(
    receipt_rows: Sequence[tuple[str, Sequence[str]]] = (
        ("January 1991", ("1.0", "2.0", "3.0", "4.0", "10.0")),
    ),
    clearance_rows: Sequence[tuple[str, Sequence[str]]] = (
        ("January 1991", ("5.0", "6.0", "7.0", "8.0", "21.0")),
    ),
    receipt_cols: Sequence[str] = RECEIPT_COLS,
    clearance_cols: Sequence[str] = CLEARANCE_COLS,
    sheets: tuple[str, str] = ("Table_1_receipts", "Table_2_clearances"),
) -> bytes:
    return build(
        [
            {
                "name": sheets[0],
                "title": "Tobacco receipts",
                "header": receipt_cols,
                "rows": list(receipt_rows),
                "annual_rows": [("1990 to 1991", ("9",) * len(receipt_cols))],
                "table_number": 1,
            },
            {
                "name": sheets[1],
                "title": "Tobacco clearances",
                "header": clearance_cols,
                "rows": list(clearance_rows),
                "table_number": 2,
            },
        ]
    )


def _month(index: int) -> date:
    total = (EXPECTED_FIRST_OBSERVATION.month - 1) + index
    return date(EXPECTED_FIRST_OBSERVATION.year + total // 12, total % 12 + 1, 1)


def _panel(
    dates: int = MIN_EXPECTED_DATES,
) -> tuple[list[Observation], dict[str, dict[str, str]]]:
    observations: list[Observation] = []
    natives: dict[str, dict[str, str]] = {}
    for sheet, (measure, _unit) in SHEETS.items():
        for product in ("CIGARETTES", "CIGARS", "HRT", "OTHER", "OVERALLTOTAL"):
            series_id = make_series_id(measure, product)
            natives[series_id] = {
                "measure": measure,
                "product": product,
                "sheet": sheet,
                "published_unit": "GBP million",
            }
            for step in range(dates):
                observations.append(
                    Observation(series_id, _month(step), 100.0, "snapshot")
                )
    return observations, natives


def test_parses_receipts_and_clearances_as_separate_measures() -> None:
    observations, natives = parse_ods(_workbook(), "t://t", "snap")
    assert len(natives) == 10
    assert make_series_id("RECEIPTS", "CIGARETTES") in natives
    assert make_series_id("CLEARANCES", "CIGARETTES") in natives
    values = {(o.series_id, o.reference_date): o.value for o in observations}
    assert values[(make_series_id("RECEIPTS", "CIGARETTES"), date(1991, 1, 1))] == 1.0
    assert values[(make_series_id("CLEARANCES", "CIGARETTES"), date(1991, 1, 1))] == 5.0


def test_the_published_unit_is_captured_per_column() -> None:
    _observations, natives = parse_ods(_workbook(), "t://t", "snap")
    assert natives[make_series_id("CLEARANCES", "CIGARETTES")]["published_unit"] == "million sticks"
    assert natives[make_series_id("CLEARANCES", "CIGARS")]["published_unit"] == "'000 kilograms"
    assert natives[make_series_id("RECEIPTS", "CIGARETTES")]["published_unit"] == "GBP million"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Cigarettes", ("Cigarettes", None)),
        ("Cigars ('000 kilograms)", ("Cigars", "'000 kilograms")),
        ("Cigarettes (million sticks)", ("Cigarettes", "million sticks")),
    ],
)
def test_the_unit_parenthetical_is_split_off(label: str, expected: tuple) -> None:
    assert strip_unit(label) == expected


def test_an_unrecognised_product_column_fails_loudly() -> None:
    cols = (*RECEIPT_COLS[:-1], "Heated tobacco")
    with pytest.raises(ValueError, match="unrecognised column"):
        parse_ods(_workbook(receipt_cols=cols), "t://t", "snap")


def test_a_missing_sheet_fails_loudly() -> None:
    with pytest.raises(ValueError, match="missing sheet"):
        parse_ods(_workbook(sheets=("Table_1_receipts", "Table_9_other")), "t://t", "snap")


def test_a_published_missing_marker_is_skipped_not_zeroed() -> None:
    rows = (("January 1991", ("[X]", "2.0", "3.0", "4.0", "10.0")),)
    observations, _natives = parse_ods(_workbook(receipt_rows=rows), "t://t", "snap")
    receipts = [o for o in observations if o.series_id == make_series_id("RECEIPTS", "CIGARETTES")]
    assert receipts == []


def test_the_annual_blocks_are_not_collected() -> None:
    """Only the monthly block is stored; HMRC's annual totals aggregate it."""
    observations, _natives = parse_ods(_workbook(), "t://t", "snap")
    assert {o.reference_date for o in observations} == {date(1991, 1, 1)}


def test_series_ids_round_trip() -> None:
    for measure, _unit in SHEETS.values():
        for product in PRODUCT_NAMES:
            series_id = make_series_id(measure, product)
            _s, _d, parsed_measure, parsed_product = parse_series_id(series_id)
            assert make_series_id(parsed_measure, parsed_product) == series_id


@pytest.mark.parametrize(
    "series_id",
    ["HMRC_TOBACCO_RECEIPTS", "HMRC_TOBACCO_SALES_CIGARETTES", "HMRC_TOBACCO_RECEIPTS_VAPES"],
)
def test_a_malformed_series_id_is_refused(series_id: str) -> None:
    with pytest.raises(ValueError):
        parse_series_id(series_id)


# --- validation gates ----------------------------------------------------


def test_validation_accepts_a_well_formed_panel() -> None:
    validate(*_panel())


def test_negative_receipts_are_accepted_as_published() -> None:
    """HMRC publishes negative receipts where repayments exceed payments."""
    observations, natives = _panel()
    negative = Observation(make_series_id("RECEIPTS", "OTHER"), _month(0), -4.920903, "snapshot")
    kept = [o for o in observations if (o.series_id, o.reference_date) != (negative.series_id, negative.reference_date)]
    validate([negative, *kept], natives)


def test_negative_clearances_are_refused() -> None:
    """A physical quantity released for consumption cannot be negative."""
    observations, natives = _panel()
    broken = Observation(make_series_id("CLEARANCES", "CIGARETTES"), _month(0), -5.0, "snapshot")
    kept = [o for o in observations if (o.series_id, o.reference_date) != (broken.series_id, broken.reference_date)]
    with pytest.raises(ValueError, match="plausible envelope"):
        validate([broken, *kept], natives)


def test_losing_a_whole_measure_is_refused() -> None:
    observations, natives = _panel()
    kept = {s for s, f in natives.items() if f["measure"] == "RECEIPTS"}
    with pytest.raises(ValueError, match="returned measures|below the .* floor"):
        validate(
            [o for o in observations if o.series_id in kept],
            {s: f for s, f in natives.items() if s in kept},
        )


def test_too_few_months_is_refused() -> None:
    observations, natives = _panel(dates=MIN_EXPECTED_DATES - 1)
    with pytest.raises(ValueError, match="reference months, below"):
        validate(observations, natives)


def test_a_shifted_first_observation_is_refused() -> None:
    observations, natives = _panel(dates=MIN_EXPECTED_DATES + 1)
    shifted = [o for o in observations if o.reference_date != EXPECTED_FIRST_OBSERVATION]
    with pytest.raises(ValueError, match="history starts at"):
        validate(shifted, natives)


def test_a_monthly_cadence_gap_is_refused() -> None:
    observations, natives = _panel(dates=MIN_EXPECTED_DATES + 2)
    gapped = [o for o in observations if o.reference_date not in {_month(1), _month(2)}]
    with pytest.raises(ValueError, match="cadence broken by gaps"):
        validate(gapped, natives)


def test_a_duplicate_observation_is_refused() -> None:
    observations, natives = _panel()
    with pytest.raises(ValueError, match="duplicate observations"):
        validate([*observations, observations[0]], natives)


def test_a_future_reference_date_is_refused() -> None:
    observations, natives = _panel()
    future = Observation(
        observations[0].series_id,
        datetime.now(UTC).date().replace(day=1) + timedelta(days=400),
        1.0,
        "snapshot",
    )
    with pytest.raises(ValueError, match="future reference date"):
        validate([*observations, future], natives)


def test_an_empty_panel_is_refused() -> None:
    with pytest.raises(ValueError, match="no observations"):
        validate([], {})


def test_the_catalog_satisfies_the_metadata_vocabularies() -> None:
    _observations, natives = parse_ods(_workbook(), "t://t", "snap")
    catalog = _build_catalog(natives, date(2026, 8, 28))
    validate_catalog(catalog)
    receipts = catalog[make_series_id("RECEIPTS", "CIGARETTES")]
    clearances = catalog[make_series_id("CLEARANCES", "CIGARETTES")]
    assert receipts["unit"] == "currency"
    assert clearances["unit"] == "other"
    assert all(entry["frequency"] == "monthly" for entry in catalog.values())
    # The published physical unit must survive into the catalog, since the
    # fleet vocabulary cannot express "million sticks".
    assert "million sticks" in clearances["name"]
    assert "not the duty rate" in receipts["description"]
