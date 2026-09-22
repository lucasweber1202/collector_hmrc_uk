"""HMRC Alcohol Bulletin parsing, shared-total dedup and gates."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from scripts.extract_hmrc_alcohol_bulletin import (
    EXPECTED_FIRST_OBSERVATION,
    MIN_EXPECTED_DATES,
    PRODUCT_SHEETS,
    _build_catalog,
    classify_column,
    make_series_id,
    parse_ods,
    parse_series_id,
    validate,
)
from scripts.metadata import validate_catalog
from scripts.time_series import Observation
from tests._hmrc_ods import build

SHARED = "Total Alcohol Duty receipts (pounds million)"
WINE_OFP = "Total Alcohol Duty receipts from wine and other fermented products (pounds million)"

BEER_COLS = (
    "UK beer production (litres)",
    "UK beer production (litres of alcohol)",
    "Clearances claiming no relief (litres of alcohol)",
    "Total beer clearances (litres of alcohol)",
    "Total Alcohol Duty receipts from beer (pounds million)",
    SHARED,
)
SIMPLE_COLS = (
    "Clearances claiming no relief (litres of alcohol)",
    "Total cider clearances (litres of alcohol)",
    "Total Alcohol Duty receipts from cider (pounds million)",
    SHARED,
)
WINE_COLS = (
    "Clearances at least 8.5% ABV (litres of alcohol)",
    "Total wine clearances (litres of alcohol)",
    WINE_OFP,
    SHARED,
)
OFP_COLS = (
    "Clearances at least 8.5% ABV (litres of alcohol)",
    "Total other fermented products clearances (litres of alcohol)",
    WINE_OFP,
    SHARED,
)
SPIRIT_COLS = (
    "UK potable spirits production (litres of alcohol)",
    "Clearances at least 22% ABV (litres of alcohol)",
    "Total spirits clearances (litres of alcohol)",
    "Total Alcohol Duty receipts from spirits (pounds million)",
    SHARED,
)
MONTH = "August 2023"


def _workbook(shared_value: str = "1579", wine_shared: str = "500") -> bytes:
    return build(
        [
            {
                "name": "Beer",
                "title": "Beer",
                "header": BEER_COLS,
                "rows": [(MONTH, ("1", "2", "3", "4", "5", shared_value))],
                "table_number": 1,
            },
            {
                "name": "Cider",
                "title": "Cider",
                "header": SIMPLE_COLS,
                "rows": [(MONTH, ("6", "7", "8", "1579"))],
                "table_number": 2,
            },
            {
                "name": "Wine",
                "title": "Wine",
                "header": WINE_COLS,
                "rows": [(MONTH, ("9", "10", "500", "1579"))],
                "table_number": 3,
            },
            {
                "name": "Other_Fermented_Products",
                "title": "OFP",
                "header": OFP_COLS,
                "rows": [(MONTH, ("11", "12", wine_shared, "1579"))],
                "table_number": 4,
            },
            {
                "name": "Spirits",
                "title": "Spirits",
                "header": SPIRIT_COLS,
                "rows": [(MONTH, ("13", "14", "15", "16", "1579"))],
                "table_number": 5,
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
    for sheet, product in PRODUCT_SHEETS.items():
        for measure in (
            "CLEARANCES_TOTAL",
            "RECEIPTS",
            "CLEARANCES_NORELIEF",
            "CLEARANCES_GE85",
            "CLEARANCES_EXSHIP",
            "CLEARANCES_EXWAREHOUSE",
            "CLEARANCES_UKREGISTERED",
            "PRODUCTION_LAL",
            "CLEARANCES_LT85",
        ):
            series_id = make_series_id(product, measure)
            natives[series_id] = {
                "product": product,
                "measure": measure,
                "sheet": sheet,
                "published_unit": "litres of alcohol",
                "label": measure,
            }
            for step in range(dates):
                observations.append(Observation(series_id, _month(step), 100.0, "snapshot"))
    return observations, natives


def test_parses_all_five_product_sheets() -> None:
    _observations, natives = parse_ods(_workbook(), "t://a", "snap")
    products = {f["product"] for f in natives.values()}
    assert set(PRODUCT_SHEETS.values()) <= products


def test_the_all_alcohol_total_is_stored_once_not_five_times() -> None:
    observations, natives = parse_ods(_workbook(), "t://a", "snap")
    shared_id = make_series_id("ALL", "RECEIPTS")
    assert shared_id in natives
    rows = [o for o in observations if o.series_id == shared_id]
    assert len(rows) == 1
    assert rows[0].value == 1579.0


def test_the_wine_and_ofp_total_is_stored_once_not_twice() -> None:
    observations, _natives = parse_ods(_workbook(), "t://a", "snap")
    rows = [o for o in observations if o.series_id == make_series_id("WINEOFP", "RECEIPTS")]
    assert len(rows) == 1
    assert rows[0].value == 500.0


def test_a_shared_total_that_disagrees_across_sheets_fails_loudly() -> None:
    """Two sheets claiming different values for the same total is a real conflict."""
    with pytest.raises(ValueError, match="conflicting values for the shared column"):
        parse_ods(_workbook(wine_shared="999"), "t://a", "snap")


def test_no_duplicate_keys_survive_the_shared_columns() -> None:
    observations, natives = parse_ods(_workbook(), "t://a", "snap")
    keys = [(o.series_id, o.reference_date) for o in observations]
    assert len(keys) == len(set(keys))
    validate_keys = {f["product"] for f in natives.values()}
    assert "ALL" in validate_keys and "WINEOFP" in validate_keys


@pytest.mark.parametrize(
    ("label", "sheet_product", "expected"),
    [
        (SHARED, "BEER", ("ALL", "RECEIPTS")),
        (WINE_OFP, "WINE", ("WINEOFP", "RECEIPTS")),
        ("Total beer clearances (litres of alcohol)", "BEER", ("BEER", "CLEARANCES_TOTAL")),
        ("Total Alcohol Duty receipts from beer (pounds million)", "BEER", ("BEER", "RECEIPTS")),
        (
            "Clearances at least 22% ABV (litres of alcohol)",
            "SPIRITS",
            ("SPIRITS", "CLEARANCES_GE22"),
        ),
    ],
)
def test_columns_are_classified_to_the_right_product(
    label: str, sheet_product: str, expected: tuple[str, str]
) -> None:
    product, measure, _unit = classify_column(label, sheet_product)
    assert (product, measure) == expected


def test_the_two_beer_production_columns_are_distinguished_by_unit() -> None:
    litres = classify_column("UK beer production (litres)", "BEER")
    alcohol = classify_column("UK beer production (litres of alcohol)", "BEER")
    assert litres[1] == "PRODUCTION_LITRES"
    assert alcohol[1] == "PRODUCTION_LAL"
    assert litres[1] != alcohol[1]


def test_a_production_column_in_an_unknown_unit_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unrecognised unit"):
        classify_column("UK beer production (pints)", "BEER")


def test_an_unrecognised_column_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unrecognised column"):
        classify_column("Clearances of something new (litres)", "BEER")


def test_a_missing_sheet_fails_loudly() -> None:
    partial = build(
        [
            {
                "name": "Beer",
                "title": "Beer",
                "header": BEER_COLS,
                "rows": [(MONTH, ("1", "2", "3", "4", "5", "1579"))],
            }
        ]
    )
    with pytest.raises(ValueError, match="missing sheet"):
        parse_ods(partial, "t://a", "snap")


def test_series_ids_round_trip() -> None:
    for product in ("BEER", "CIDER", "WINE", "OFP", "SPIRITS", "ALL", "WINEOFP"):
        series_id = make_series_id(product, "RECEIPTS")
        _s, _d, parsed_product, parsed_measure = parse_series_id(series_id)
        assert make_series_id(parsed_product, parsed_measure) == series_id


@pytest.mark.parametrize(
    "series_id", ["HMRC_ALCOHOL_BEER", "HMRC_ALCOHOL_MEAD_RECEIPTS", "X_Y_Z_W"]
)
def test_a_malformed_series_id_is_refused(series_id: str) -> None:
    with pytest.raises(ValueError):
        parse_series_id(series_id)


# --- validation gates ----------------------------------------------------


def test_validation_accepts_a_well_formed_panel() -> None:
    validate(*_panel())


def test_too_few_months_is_refused() -> None:
    observations, natives = _panel(dates=MIN_EXPECTED_DATES - 1)
    with pytest.raises(ValueError, match="reference months, below"):
        validate(observations, natives)


def test_a_shifted_first_observation_is_refused() -> None:
    """A further duty reform would move the history start."""
    observations, natives = _panel(dates=MIN_EXPECTED_DATES + 1)
    shifted = [o for o in observations if o.reference_date != EXPECTED_FIRST_OBSERVATION]
    with pytest.raises(ValueError, match="history starts at"):
        validate(shifted, natives)


def test_a_missing_product_is_refused() -> None:
    observations, natives = _panel()
    dropped = {s for s, f in natives.items() if f["product"] == "SPIRITS"}
    with pytest.raises(ValueError, match="missing published products|below the .* floor"):
        validate(
            [o for o in observations if o.series_id not in dropped],
            {s: f for s, f in natives.items() if s not in dropped},
        )


def test_a_negative_clearance_is_refused() -> None:
    observations, natives = _panel()
    sid = make_series_id("BEER", "CLEARANCES_TOTAL")
    broken = Observation(sid, _month(0), -1.0, "snapshot")
    kept = [o for o in observations if (o.series_id, o.reference_date) != (sid, _month(0))]
    with pytest.raises(ValueError, match="plausible envelope"):
        validate([broken, *kept], natives)


def test_negative_receipts_are_accepted_as_published() -> None:
    observations, natives = _panel()
    sid = make_series_id("BEER", "RECEIPTS")
    negative = Observation(sid, _month(0), -2.5, "snapshot")
    kept = [o for o in observations if (o.series_id, o.reference_date) != (sid, _month(0))]
    validate([negative, *kept], natives)


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
    _observations, natives = parse_ods(_workbook(), "t://a", "snap")
    catalog = _build_catalog(natives, date(2026, 8, 28))
    validate_catalog(catalog)
    receipts = catalog[make_series_id("BEER", "RECEIPTS")]
    clearances = catalog[make_series_id("BEER", "CLEARANCES_TOTAL")]
    assert receipts["unit"] == "currency"
    assert clearances["unit"] == "other"
    assert all(
        e["frequency"] == ("quarterly" if s == "HMRC_ALCOHOL_SPIRITS_PRODUCTION_LAL" else "monthly")
        for s, e in catalog.items()
    )
    # The short history and its cause must be visible to a research user.
    assert "2023-08" in receipts["description"]
    assert "can be negative" in receipts["description"]
