"""HMRC extractor entrypoint: orchestrates this repository's datasets.

This module stays deliberately small. It owns no parsing and no HTTP beyond the
one shared client: each HMRC data set is a self-contained ``extract_hmrc_*``
module returning its own ``SourceData``, and adding a data set means adding a
module and one entry to ``DATASETS``.

Datasets are collected one at a time so that a layout change at one HMRC page
fails that data set alone. ``main.py`` persists each one in its own transaction
and reports the failures at the end of the run.

Not implemented: duty rates
---------------------------
`hmrc_tobacco_duty_rates` and `hmrc_alcohol_duty_rates` are deliberately absent.
Verified on 2026-09-16, HMRC publishes both rate histories only as HTML tables
(`/government/statistics/tobacco-bulletin/historical-tobacco-duty-rates` and
`/government/statistics/alcohol-bulletin/alcohol-bulletin-historic-duty-rates`)
with no CSV, ODS or XLSX attachment on either page. Building an HTML scraper for
a tax parameter is out of scope, so no module exists rather than a stub that
pretends the source is handled. See `POINT_IN_TIME.md` for why a duty rate also
needs announcement-date semantics that differ from a bulletin's.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from scripts.config import (
    ALCOHOL_MIN_HISTORY_YEARS,
    MAX_STALE_MONTHS,
    MIN_DENSITY,
    MIN_HISTORY_YEARS,
)
from scripts.extract_hmrc_alcohol_bulletin import collect as _collect_alcohol_bulletin
from scripts.extract_hmrc_tobacco_bulletin import collect as _collect_tobacco_bulletin
from scripts.govuk import SourceData, build_client

logger = logging.getLogger(__name__)


# -- series_id contract (GUIDELINES.md 4, 9.2) ---------------------------
# This repository owns two HMRC bulletins, each with its own grammar, so the
# canonical entry point dispatches to the extractor that owns the id. Both
# parsers return the raw underscore segments, so rejoining them is the
# exact inverse and build(*parse(sid)) == sid.
from scripts.extract_hmrc_alcohol_bulletin import (
    parse_series_id as _parse_alcohol,
)
from scripts.extract_hmrc_tobacco_bulletin import (
    parse_series_id as _parse_tobacco,
)

_SERIES_ID_PARSERS = (_parse_alcohol, _parse_tobacco)


def parse_series_id(series_id: str) -> tuple[str, ...]:
    """Decode an HMRC series_id using whichever bulletin owns its grammar."""
    for parser in _SERIES_ID_PARSERS:
        try:
            return tuple(parser(series_id))
        except ValueError:
            continue
    raise ValueError(f"Unknown HMRC series_id: {series_id}")


def build_series_id(*components: str) -> str:
    """Rejoin the tuple parse_series_id returned into the original id."""
    if not components:
        raise ValueError("series_id needs at least one component")
    if any(not c or c != c.upper() for c in components):
        raise ValueError(f"invalid series_id components: {components!r}")
    return "_".join(components)


# -- 5.1 usable-series filtering ------------------------------------------


@dataclass(frozen=True)
class SourceThresholds:
    """The 5.1 floors one data set is judged against.

    Held per data set because this repository owns two bulletins whose
    published history depths differ by three decades. See scripts/config.py for
    why the alcohol floor is not the tobacco one.
    """

    max_stale_months: int = MAX_STALE_MONTHS
    min_history_years: float = MIN_HISTORY_YEARS
    min_density: float = MIN_DENSITY


SOURCE_THRESHOLDS: dict[str, SourceThresholds] = {
    "hmrc_tobacco_bulletin": SourceThresholds(),
    "hmrc_alcohol_bulletin": SourceThresholds(min_history_years=ALCOHOL_MIN_HISTORY_YEARS),
}


def thresholds_for(source_id: str | None) -> SourceThresholds:
    """Return the floors for ``source_id``, defaulting to the strict ones."""
    if source_id is None:
        return SourceThresholds()
    if source_id not in SOURCE_THRESHOLDS:
        raise ValueError(
            f"No 5.1 thresholds declared for {source_id!r}; known: {sorted(SOURCE_THRESHOLDS)}"
        )
    return SOURCE_THRESHOLDS[source_id]


@dataclass(frozen=True)
class UsabilityReport:
    """What the filter removed, for logging and for tests to assert on."""

    kept: tuple[str, ...]
    stale: tuple[str, ...]
    short_history: tuple[str, ...]
    empty: tuple[str, ...]
    sparse: tuple[str, ...] = ()

    @property
    def dropped(self) -> tuple[str, ...]:
        return tuple(
            sorted(set(self.stale) | set(self.short_history) | set(self.empty) | set(self.sparse))
        )


def _months_between(earlier: date, later: date) -> int:
    """Whole months from ``earlier`` to ``later``, day-of-month aware."""
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    if later.day < earlier.day:
        months -= 1
    return months


def _is_valid(value: Any) -> bool:
    """A real observation: present, numeric and finite."""
    if value is None:
        return False
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric)


PERIOD_MONTHS: dict[str, int] = {
    "monthly": 1,
    "quarterly": 3,
    "annual": 12,
}


def period_months(frequency: str | None) -> int:
    """Return the month step of ``frequency``, defaulting to monthly."""
    if frequency is None:
        return 1
    step = PERIOD_MONTHS.get(frequency)
    if step is None:
        raise ValueError(f"Unknown frequency {frequency!r}; known: {sorted(PERIOD_MONTHS)}")
    return step


def assess_series(
    reference_dates: list[date],
    today: date,
    thresholds: SourceThresholds | None = None,
    frequency: str | None = None,
) -> str:
    """Classify one series from the reference dates of its valid observations.

    Returns ``"keep"``, ``"empty"``, ``"stale"``, ``"short_history"`` or
    ``"sparse"``. Recency is judged at the period end and over non-null values
    only: a source that keeps listing a discontinued series with empty recent
    cells must not look live because of those blanks.

    Depth alone cannot separate a series that is young because its statistical
    regime is young from a stub or a half-parsed sheet, so a series must also be
    dense: it has to cover nearly every period of its own span. Density is
    measured at the frequency the catalog declares, because this source
    publishes one quarterly series inside an otherwise monthly workbook.
    """
    limits = thresholds or SourceThresholds()
    if not reference_dates:
        return "empty"
    first, last = min(reference_dates), max(reference_dates)
    if _months_between(last, today) > limits.max_stale_months:
        return "stale"
    span_months = _months_between(first, last)
    if span_months < round(limits.min_history_years * 12):
        return "short_history"
    step = period_months(frequency)
    expected = span_months // step + 1
    if len(reference_dates) < limits.min_density * expected:
        return "sparse"
    return "keep"


def filter_usable_series(
    observations: list[Any],
    catalog: dict[str, dict[str, Any]],
    today: date,
    source_id: str | None = None,
    thresholds: SourceThresholds | None = None,
) -> tuple[list[Any], dict[str, dict[str, Any]], UsabilityReport]:
    """Drop obsolete and history-less series before anything is persisted.

    Runs after parsing and before the time_series / metadata upsert, so the
    standardized tables never carry a dead or stub series, and prunes the
    catalog alongside the observations so metadata can never describe a series
    the database does not hold (GUIDELINES.md 5.1).
    """
    limits = thresholds or thresholds_for(source_id)
    valid_dates: dict[str, list[date]] = {}
    for observation in observations:
        if _is_valid(observation.value):
            valid_dates.setdefault(observation.series_id, []).append(observation.reference_date)

    verdicts: dict[str, str] = {}
    for series_id in set(catalog) | {o.series_id for o in observations}:
        verdicts[series_id] = assess_series(
            valid_dates.get(series_id, []),
            today,
            limits,
            catalog.get(series_id, {}).get("frequency"),
        )

    keep = {series_id for series_id, verdict in verdicts.items() if verdict == "keep"}
    report = UsabilityReport(
        kept=tuple(sorted(keep)),
        stale=tuple(sorted(s for s, v in verdicts.items() if v == "stale")),
        short_history=tuple(sorted(s for s, v in verdicts.items() if v == "short_history")),
        empty=tuple(sorted(s for s, v in verdicts.items() if v == "empty")),
        sparse=tuple(sorted(s for s, v in verdicts.items() if v == "sparse")),
    )

    if report.dropped:
        logger.info(
            "Usable-series filter for %s: kept %d, dropped %d (stale=%d short_history=%d "
            "sparse=%d empty=%d; max_stale_months=%d min_history_years=%s min_density=%s)",
            source_id or "<unnamed>",
            len(report.kept),
            len(report.dropped),
            len(report.stale),
            len(report.short_history),
            len(report.sparse),
            len(report.empty),
            limits.max_stale_months,
            limits.min_history_years,
            limits.min_density,
        )
        for series_id in report.stale:
            logger.info(
                "Dropped %s: last valid observation older than %d months",
                series_id,
                limits.max_stale_months,
            )
        for series_id in report.short_history:
            logger.info(
                "Dropped %s: valid history shorter than %s years",
                series_id,
                limits.min_history_years,
            )
        for series_id in report.sparse:
            logger.info(
                "Dropped %s: below %s coverage of its own span at its declared frequency",
                series_id,
                limits.min_density,
            )
        for series_id in report.empty:
            logger.info("Dropped %s: no valid observations", series_id)
    else:
        logger.info("Usable-series filter: all %d series usable", len(report.kept))

    kept_observations = [o for o in observations if o.series_id in keep]
    kept_catalog = {sid: fields for sid, fields in catalog.items() if sid in keep}
    return kept_observations, kept_catalog, report


# source_id -> collector.
DATASETS: dict[str, Callable[[httpx.Client], SourceData]] = {
    "hmrc_tobacco_bulletin": _collect_tobacco_bulletin,
    "hmrc_alcohol_bulletin": _collect_alcohol_bulletin,
}


def resolve(source_ids: list[str] | None) -> list[str]:
    """Return the data sets to run, rejecting an unknown name loudly."""
    selected = list(DATASETS) if source_ids is None else list(source_ids)
    unknown = [source_id for source_id in selected if source_id not in DATASETS]
    if unknown:
        raise ValueError(f"Unknown HMRC data set(s) {unknown}; known: {sorted(DATASETS)}")
    return selected


@contextmanager
def open_client() -> Iterator[httpx.Client]:
    """Yield the single managed HTTP client shared by one run."""
    with build_client() as client:
        yield client


def collect_one(client: httpx.Client, source_id: str) -> SourceData:
    """Collect exactly one data set with an already-open client."""
    return DATASETS[resolve([source_id])[0]](client)


def collect(source_ids: list[str] | None = None) -> list[SourceData]:
    """Collect the named data sets, or all of them, failing on the first error."""
    with open_client() as client:
        return [collect_one(client, source_id) for source_id in resolve(source_ids)]
