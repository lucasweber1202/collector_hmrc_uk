"""Adversarial cover for the per-data-set 5.1 floors.

The Alcohol Bulletin's monthly tables begin in August 2023, when the new
Alcohol Duty regime replaced the old one. A single five-year floor drops every
alcohol series until 2028 even though each is current and near-complete, so the
floor is declared per data set. These tests hold both halves of that decision:
a legitimate young regime survives, and the things a short floor could let
through -- stubs, dead series, half-parsed sheets -- still do not.
"""

from __future__ import annotations

from datetime import date

import pytest

from scripts.extract import (
    SOURCE_THRESHOLDS,
    SourceThresholds,
    assess_series,
    filter_usable_series,
    thresholds_for,
)

TODAY = date(2026, 9, 25)
REGIME_START = date(2023, 8, 1)
ALCOHOL = "hmrc_alcohol_bulletin"
TOBACCO = "hmrc_tobacco_bulletin"


def _months(start: date, count: int, step: int = 1) -> list[date]:
    """Return ``count`` monthly reference dates from ``start``."""
    out = []
    for index in range(0, count * step, step):
        year = start.year + (start.month - 1 + index) // 12
        month = (start.month - 1 + index) % 12 + 1
        out.append(date(year, month, 1))
    return out


class _Observation:
    def __init__(self, series_id: str, reference_date: date, value: float | None) -> None:
        self.series_id = series_id
        self.reference_date = reference_date
        self.value = value


# -- the regime break itself ----------------------------------------------


def test_a_full_new_regime_alcohol_series_is_kept() -> None:
    """36 dense months from the reform date is the real shape of the source."""
    dates = _months(REGIME_START, 36)
    assert assess_series(dates, TODAY, thresholds_for(ALCOHOL)) == "keep"


def test_the_same_series_is_dropped_by_the_tobacco_floor() -> None:
    """Proves the floors actually differ, so this test fails if they are merged."""
    dates = _months(REGIME_START, 36)
    assert assess_series(dates, TODAY, thresholds_for(TOBACCO)) == "short_history"


def test_the_tobacco_floor_still_demands_five_years() -> None:
    dates = _months(date(2023, 1, 1), 44)
    assert assess_series(dates, TODAY, thresholds_for(TOBACCO)) == "short_history"
    assert assess_series(_months(date(1991, 1, 1), 426), TODAY, thresholds_for(TOBACCO)) == "keep"


# -- what the shorter floor must still refuse ------------------------------


def test_a_two_print_stub_is_not_rescued_by_the_short_floor() -> None:
    """A stub spanning the regime but holding two prints is still unusable."""
    dates = [REGIME_START, date(2026, 7, 1)]
    assert assess_series(dates, TODAY, thresholds_for(ALCOHOL), "monthly") == "sparse"


def test_a_half_parsed_sheet_is_sparse_not_kept() -> None:
    """Every other month present: the shape a broken row scan produces."""
    dates = _months(REGIME_START, 18, step=2)
    assert assess_series(dates, TODAY, thresholds_for(ALCOHOL), "monthly") == "sparse"


def test_a_discontinued_alcohol_series_is_still_stale() -> None:
    """A long, dense series that stopped publishing is dropped on recency."""
    dates = _months(REGIME_START, 24)
    assert assess_series(dates, TODAY, thresholds_for(ALCOHOL)) == "stale"


def test_an_empty_series_is_still_empty() -> None:
    assert assess_series([], TODAY, thresholds_for(ALCOHOL)) == "empty"


def test_a_series_shorter_than_the_alcohol_floor_is_dropped() -> None:
    """17 dense months is below the 1.5-year floor."""
    dates = _months(date(2025, 5, 1), 17)
    assert assess_series(dates, TODAY, thresholds_for(ALCOHOL)) == "short_history"


# -- wiring ----------------------------------------------------------------


def test_every_declared_data_set_has_thresholds() -> None:
    from scripts.extract import DATASETS

    assert set(SOURCE_THRESHOLDS) == set(DATASETS)


def test_an_undeclared_data_set_fails_loudly() -> None:
    """A new bulletin must not silently inherit another one's floors."""
    with pytest.raises(ValueError, match="No 5.1 thresholds declared"):
        thresholds_for("hmrc_something_new")


def test_filter_resolves_thresholds_from_the_source_id() -> None:
    """The end-to-end path main.py uses: source_id in, alcohol floors applied."""
    dates = _months(REGIME_START, 36)
    observations = [_Observation("HMRC_ALCOHOL_ALL_RECEIPTS", d, 1.0) for d in dates]
    catalog: dict[str, dict[str, object]] = {"HMRC_ALCOHOL_ALL_RECEIPTS": {}}

    kept, kept_catalog, report = filter_usable_series(observations, catalog, TODAY, ALCOHOL)
    assert report.kept == ("HMRC_ALCOHOL_ALL_RECEIPTS",)
    assert len(kept) == 36
    assert kept_catalog == catalog

    _, _, tobacco_report = filter_usable_series(observations, catalog, TODAY, TOBACCO)
    assert tobacco_report.short_history == ("HMRC_ALCOHOL_ALL_RECEIPTS",)


def test_nulls_do_not_make_a_dead_series_look_dense() -> None:
    """Blank recent cells must not count toward density or recency."""
    live = _months(REGIME_START, 24)
    observations = [_Observation("S", d, 1.0) for d in live]
    observations += [_Observation("S", d, None) for d in _months(date(2025, 8, 1), 14)]
    _, _, report = filter_usable_series(observations, {"S": {}}, TODAY, ALCOHOL)
    assert report.stale == ("S",)


def test_a_quarterly_series_is_not_mistaken_for_a_sparse_monthly_one() -> None:
    """UK potable spirits production is quarterly inside a monthly workbook.

    Judged on a monthly calendar it looks 2/3 empty; judged at its declared
    frequency it is complete. A regression here silently drops a real series.
    """
    dates = _months(date(2023, 9, 1), 12, step=3)
    assert assess_series(dates, TODAY, thresholds_for(ALCOHOL), "quarterly") == "keep"
    assert assess_series(dates, TODAY, thresholds_for(ALCOHOL), "monthly") == "sparse"


def test_the_filter_reads_the_declared_frequency_from_the_catalog() -> None:
    quarterly = _months(date(2023, 9, 1), 12, step=3)
    observations = [_Observation("SPIRITS", d, 1.0) for d in quarterly]
    catalog: dict[str, dict[str, object]] = {"SPIRITS": {"frequency": "quarterly"}}
    _, _, report = filter_usable_series(observations, catalog, TODAY, ALCOHOL)
    assert report.kept == ("SPIRITS",)

    mislabelled: dict[str, dict[str, object]] = {"SPIRITS": {"frequency": "monthly"}}
    _, _, broken = filter_usable_series(observations, mislabelled, TODAY, ALCOHOL)
    assert broken.sparse == ("SPIRITS",)


def test_an_unknown_frequency_fails_loudly() -> None:
    with pytest.raises(ValueError, match="Unknown frequency"):
        assess_series(_months(REGIME_START, 36), TODAY, thresholds_for(ALCOHOL), "fortnightly")


def test_defaults_are_the_strict_floors() -> None:
    """An unnamed call must not accidentally get the permissive alcohol floors."""
    assert thresholds_for(None) == SourceThresholds()
    assert SOURCE_THRESHOLDS[TOBACCO] == SourceThresholds()
    assert SOURCE_THRESHOLDS[ALCOHOL].min_history_years < SourceThresholds().min_history_years
