"""Audit regressions: a page timestamp is not evidence for current-file values."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from main import _availability_rows
from scripts.availability import attribute_release, get_series_as_of, upsert_availability
from scripts.time_series import Observation, upsert_time_series


def test_page_history_is_only_inferred():
    release = datetime(2020, 1, 7, 9, tzinfo=UTC)
    _, basis, released = attribute_release(date(2020, 1, 6), [release], 0, 31, 1)
    assert basis == "inferred"
    assert released is None


def test_current_file_backfill_and_legacy_rows_cannot_leak(engine):
    ref = date(2020, 1, 6)
    release = datetime(2020, 1, 7, 9, tzinfo=UTC)
    fetched = datetime(2026, 9, 18, 12, tzinfo=UTC)
    obs = Observation("AUDIT_SERIES", ref, 999.0, "current-file")
    data = SimpleNamespace(
        observations=[obs], releases=[release], min_lag_days=0, max_lag_days=31, inferred_lag_days=1
    )
    with engine.begin() as conn:
        result = upsert_time_series(conn, [obs], fetched)
        rows = _availability_rows(data, result, fetched)
        assert rows[0]["availability_basis"] == "first_seen"
        assert rows[0]["available_at"] == fetched
        assert rows[0]["release_date"] is None
        # Simulate a row written by the old implementation, without rewriting it.
        rows[0].update(
            availability_basis="official_timestamp",
            available_at=release,
            release_date=release.date(),
        )
        upsert_availability(conn, rows, fetched)
    for t in (
        release - timedelta(days=1),
        release,
        release + timedelta(days=1),
        fetched - timedelta(seconds=1),
    ):
        assert get_series_as_of(engine, "AUDIT_SERIES", t) == []
    assert get_series_as_of(engine, "AUDIT_SERIES", fetched)[0]["value"] == 999.0
