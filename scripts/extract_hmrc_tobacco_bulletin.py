"""HMRC Tobacco Bulletin: monthly tobacco duty receipts and clearances.

Official page:
https://www.gov.uk/government/statistics/tobacco-bulletin

HMRC publishes one ODS workbook with two data sheets, each carrying an annual
financial-year block, an annual calendar-year block and a monthly block. Only
the monthly blocks are collected; see `scripts/hmrc_tables.py` for why.

``Table_1_receipts``
    Tobacco duty receipts in GBP million, by product.
``Table_2_clearances``
    Tobacco clearances — the quantity released for consumption, and therefore
    the point at which duty becomes payable — in million sticks for cigarettes
    and thousand kilograms for everything else.

Receipts and clearances are different economic quantities and are stored as
separate series under a measure token, never mixed.

This bulletin is activity data. It is not the tobacco duty *rate*, which is a
tax parameter published elsewhere and announced ahead of taking effect. The two
must not be conflated: see `POINT_IN_TIME.md`.

Nothing is aggregated here. HMRC's own financial-year and calendar-year totals
are reconstructible in the research layer from the monthly series.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from itertools import pairwise
from typing import Any

import httpx

from scripts.govuk import SourceData, download, fetch_page, find_attachment, release_timestamps
from scripts.hmrc_tables import monthly_block, parse_value, read_sheets
from scripts.snapshots import build_snapshot
from scripts.time_series import Observation

logger = logging.getLogger(__name__)

SOURCE_ID = "hmrc_tobacco_bulletin"
PAGE_URL = "https://www.gov.uk/government/statistics/tobacco-bulletin"

# The published name carries the latest reference month, so only the stable
# stem is matched.
ODS_PATTERN = r"/Tobacco[^/]*\.ods"

# sheet name -> (measure token, fleet unit). Verified against the live workbook
# on 2026-09-16.
SHEETS: dict[str, tuple[str, str]] = {
    "Table_1_receipts": ("RECEIPTS", "currency"),
    "Table_2_clearances": ("CLEARANCES", "other"),
}

# Published column label (unit parenthetical stripped) -> product token. An
# unrecognised column stops collection: a silently ignored column is how a
# published product disappears without anything failing.
PRODUCTS: dict[str, str] = {
    "cigarettes": "CIGARETTES",
    "cigars": "CIGARS",
    "hrt": "HRT",
    "other": "OTHER",
    "overall total": "OVERALLTOTAL",
    "non-cigarette tobacco total": "NONCIGARETTETOTAL",
}

# Readable names for the catalog, keyed by product token.
PRODUCT_NAMES: dict[str, str] = {
    "CIGARETTES": "cigarettes",
    "CIGARS": "cigars",
    "HRT": "hand-rolling tobacco",
    "OTHER": "other tobacco products",
    "OVERALLTOTAL": "all tobacco products",
    "NONCIGARETTETOTAL": "non-cigarette tobacco products",
}

# The unit HMRC publishes for each measure and product, kept for the catalog
# because the fleet `unit` vocabulary cannot express "million sticks".
_UNIT_PATTERN = re.compile(r"\(([^)]*)\)\s*$")
DEFAULT_RECEIPTS_UNIT = "GBP million"

# Observed release schedule. The bulletin is published quarterly even though
# its data is monthly, and one release carries several months at once: the
# 2026-08-28 release added receipts for February to July 2026. The latest month
# in a release is consistently about 58 days after that month began (2026-07 on
# 2026-08-28; 2025-07 on 2025-08-29; 2025-04 on 2025-05-30), so the minimum lag
# is set just below that. A month missed by one release waits for the next, so
# the maximum is wide enough to span a skipped quarter.
MIN_LAG_DAYS = 55
MAX_LAG_DAYS = 260
INFERRED_LAG_DAYS = 100

EXPECTED_FIRST_OBSERVATION = date(1991, 1, 1)
MIN_EXPECTED_SERIES = 10
MIN_EXPECTED_DATES = 400
# Monthly cadence with no published break.
MAX_GAP_DAYS = 40

# Plausibility envelope, per measure, because the two behave differently.
#
# Receipts CAN be negative: they are duty actually received net of repayments,
# so a month in which refunds exceed payments for a small product publishes a
# negative figure. Verified against the live workbook, which publishes
# -4.920903 for "Other" in January 2020 — 3 negatives in 4,260 observations,
# all small and all in receipts. Rejecting those would discard real published
# data. Clearances are a physical quantity released for consumption and cannot
# be negative, so that floor is kept and is a genuine parse guard.
MIN_PLAUSIBLE_RECEIPTS = -1_000.0
MIN_PLAUSIBLE_CLEARANCES = 0.0
MAX_PLAUSIBLE_VALUE = 1_000_000.0


def _token(raw: str) -> str:
    """Normalize one native label into an uppercase identifier token."""
    token = re.sub(r"[^A-Z0-9]+", "", raw.strip().upper())
    if not token:
        raise ValueError(f"HMRC tobacco label {raw!r} normalizes to an empty identifier token")
    return token


def strip_unit(label: str) -> tuple[str, str | None]:
    """Split a column label into its product name and its published unit."""
    match = _UNIT_PATTERN.search(label)
    if not match:
        return label.strip(), None
    return label[: match.start()].strip(), match.group(1).strip()


def make_series_id(measure: str, product: str) -> str:
    """Build ``HMRC_TOBACCO_{MEASURE}_{PRODUCT}``."""
    series_id = f"HMRC_TOBACCO_{measure}_{product}"
    if len(series_id) > 200:
        raise ValueError(f"series_id exceeds 200 characters: {series_id}")
    return series_id


def parse_series_id(series_id: str) -> tuple[str, str, str, str]:
    """Decode ``HMRC_TOBACCO_{MEASURE}_{PRODUCT}`` into its four parts."""
    parts = series_id.split("_", 3)
    if len(parts) != 4 or parts[0] != "HMRC" or parts[1] != "TOBACCO":
        raise ValueError(f"Invalid HMRC tobacco series_id: {series_id}")
    source, dataset, measure, product = parts
    if measure not in {token for token, _ in SHEETS.values()}:
        raise ValueError(f"Unknown measure in HMRC tobacco series_id: {series_id}")
    if product not in PRODUCT_NAMES:
        raise ValueError(f"Unknown product in HMRC tobacco series_id: {series_id}")
    return source, dataset, measure, product


def parse_ods(
    body: bytes, url: str, snapshot_id: str
) -> tuple[list[Observation], dict[str, dict[str, str]]]:
    """Parse both monthly blocks into observations and their native labels."""
    sheets = read_sheets(body, url)
    missing = set(SHEETS) - set(sheets)
    if missing:
        raise ValueError(
            f"HMRC tobacco workbook {url} is missing sheet(s) {sorted(missing)}; found "
            f"{sorted(sheets)}. The published layout changed and must be re-verified."
        )

    observations: list[Observation] = []
    natives: dict[str, dict[str, str]] = {}
    for sheet, (measure, _unit) in SHEETS.items():
        block = monthly_block(sheets[sheet], sheet, url)
        for label, column in block.columns.items():
            product_label, published_unit = strip_unit(label)
            product = PRODUCTS.get(product_label.lower())
            if product is None:
                raise ValueError(
                    f"HMRC tobacco sheet {sheet} publishes unrecognised column {label!r}; known "
                    f"products are {sorted(PRODUCTS)}. The published layout changed and must be "
                    "re-verified against the official source."
                )
            series_id = make_series_id(measure, product)
            natives.setdefault(
                series_id,
                {
                    "measure": measure,
                    "product": product,
                    "sheet": sheet,
                    "published_unit": published_unit or DEFAULT_RECEIPTS_UNIT,
                },
            )
            for reference_date, _provisional, row in block.rows:
                if column >= len(row):
                    continue
                value = parse_value(
                    row[column],
                    sheet=sheet,
                    label=label,
                    reference_date=reference_date,
                    url=url,
                )
                if value is None:
                    continue
                observations.append(
                    Observation(
                        series_id=series_id,
                        reference_date=reference_date,
                        value=value,
                        snapshot_id=snapshot_id,
                    )
                )
    return observations, natives


def _build_catalog(
    natives: dict[str, dict[str, str]], last_publish_date: date | None
) -> dict[str, dict[str, Any]]:
    """Describe every collected series from its verified native labels."""
    catalog: dict[str, dict[str, Any]] = {}
    for series_id, fields in natives.items():
        product = PRODUCT_NAMES[fields["product"]]
        unit = fields["published_unit"]
        if fields["measure"] == "RECEIPTS":
            name = f"UK tobacco duty receipts: {product} ({unit})"
            description = (
                f"Monthly UK tobacco duty receipts from {product}, in {unit}, as published by "
                "HM Revenue & Customs in the Tobacco Bulletin. Receipts are duty actually "
                "received, an activity measure, not the duty rate."
            )
            fleet_unit = "currency"
        else:
            name = f"UK tobacco clearances: {product} ({unit})"
            description = (
                f"Monthly UK tobacco clearances of {product}, in {unit}, as published by HM "
                "Revenue & Customs in the Tobacco Bulletin. Clearances are the quantity "
                "released for consumption, the point at which duty becomes payable."
            )
            fleet_unit = "other"
        catalog[series_id] = {
            "source_id": SOURCE_ID,
            "name": name,
            "description": (
                description + " Stored exactly as published at monthly frequency with no derived "
                "transformation; HMRC's own financial-year and calendar-year totals are not "
                "collected because they are aggregations of these same months."
            ),
            "frequency": "monthly",
            "unit": fleet_unit,
            "eco_group": "public_finance",
            "source_url": PAGE_URL,
            "last_publish_date": last_publish_date,
        }
    return catalog


def validate(observations: list[Observation], natives: dict[str, dict[str, str]]) -> None:
    """Gate the parsed panel before anything reaches the database."""
    if not observations:
        raise ValueError("HMRC tobacco collection produced no observations")
    if len(natives) < MIN_EXPECTED_SERIES:
        raise ValueError(
            f"HMRC tobacco returned only {len(natives)} series, below the "
            f"{MIN_EXPECTED_SERIES} floor; a sheet or a product is missing"
        )

    present = {fields["measure"] for fields in natives.values()}
    expected = {token for token, _ in SHEETS.values()}
    if present != expected:
        raise ValueError(
            f"HMRC tobacco returned measures {sorted(present)}, expected {sorted(expected)}"
        )

    keys = [(observation.series_id, observation.reference_date) for observation in observations]
    if len(keys) != len(set(keys)):
        counts: dict[tuple[str, date], int] = {}
        for key in keys:
            counts[key] = counts.get(key, 0) + 1
        duplicates = sorted(key for key, count in counts.items() if count > 1)[:5]
        raise ValueError(f"HMRC tobacco published duplicate observations, for example {duplicates}")

    dates = sorted({observation.reference_date for observation in observations})
    if dates[0] != EXPECTED_FIRST_OBSERVATION:
        raise ValueError(
            f"HMRC tobacco history starts at {dates[0]}, expected {EXPECTED_FIRST_OBSERVATION}; "
            "the published file changed and must be re-verified"
        )
    if len(dates) < MIN_EXPECTED_DATES:
        raise ValueError(
            f"HMRC tobacco returned only {len(dates)} reference months, below the "
            f"{MIN_EXPECTED_DATES} floor; the download was probably truncated"
        )
    if dates[-1] > datetime.now(UTC).date():
        raise ValueError(f"HMRC tobacco published a future reference date {dates[-1]}")

    off_month = [day for day in dates if day.day != 1]
    if off_month:
        raise ValueError(f"HMRC tobacco published non-month-start reference dates {off_month[:5]}")

    long_gaps = [
        (a, b) for a, b in pairwise(dates) if (b - a).days > MAX_GAP_DAYS
    ]
    if long_gaps:
        raise ValueError(
            f"HMRC tobacco monthly cadence broken by gaps longer than {MAX_GAP_DAYS} days at "
            f"{long_gaps[:5]}"
        )

    floors = {"RECEIPTS": MIN_PLAUSIBLE_RECEIPTS, "CLEARANCES": MIN_PLAUSIBLE_CLEARANCES}
    implausible = []
    for observation in observations:
        floor = floors[natives[observation.series_id]["measure"]]
        if not floor <= observation.value <= MAX_PLAUSIBLE_VALUE:
            implausible.append((observation.series_id, observation.reference_date, observation.value))
    if implausible:
        raise ValueError(
            f"HMRC tobacco published values outside their plausible envelope, for example "
            f"{implausible[:5]}; receipts may be negative (net of repayments) but clearances "
            "may not, so check for a unit or sign change at source"
        )

    logger.info(
        "HMRC tobacco validation passed: %d series, %d reference months, %s to %s",
        len(natives),
        len(dates),
        dates[0],
        dates[-1],
    )


def collect(client: httpx.Client) -> SourceData:
    """Download, parse and validate the full HMRC Tobacco Bulletin history."""
    page = fetch_page(client, PAGE_URL)
    releases = release_timestamps(page)
    if not releases:
        raise ValueError(f"No publication history found on {PAGE_URL}; the page layout changed")
    last_publish_date = releases[-1].astimezone(UTC).date()
    logger.info(
        "HMRC tobacco page carries %d official release timestamps, %s to %s",
        len(releases),
        releases[0].astimezone(UTC).date(),
        last_publish_date,
    )

    ods_url = find_attachment(page, PAGE_URL, ODS_PATTERN)
    ods_body, ods_digest, ods_etag, ods_last_modified = download(client, ods_url)
    snapshot = build_snapshot(
        source_id=SOURCE_ID,
        source_url=ods_url,
        filename=ods_url.rsplit("/", 1)[-1],
        body=ods_body,
        digest=ods_digest,
        etag=ods_etag,
        last_modified=ods_last_modified,
        fetched_at=datetime.now(UTC),
        source_published_date=last_publish_date,
    )
    observations, natives = parse_ods(ods_body, ods_url, ods_digest)
    validate(observations, natives)
    return SourceData(
        source_id=SOURCE_ID,
        source_url=PAGE_URL,
        catalog=_build_catalog(natives, last_publish_date),
        observations=observations,
        releases=releases,
        snapshots=[snapshot],
        min_lag_days=MIN_LAG_DAYS,
        max_lag_days=MAX_LAG_DAYS,
        inferred_lag_days=INFERRED_LAG_DAYS,
        last_publish_date=last_publish_date,
    )
