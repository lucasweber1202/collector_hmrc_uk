"""HMRC Alcohol Bulletin: monthly alcohol duty receipts, clearances and production.

Official page:
https://www.gov.uk/government/statistics/alcohol-bulletin

HMRC publishes one ODS workbook with five product sheets — Beer, Cider, Wine,
Other_Fermented_Products and Spirits — each carrying a financial-year block, a
calendar-year block and a monthly block. Only the monthly blocks are collected;
see `scripts/hmrc_tables.py` for why.

History starts in August 2023
-----------------------------
The monthly series begin at 2023-08, when the reformed Alcohol Duty regime took
effect and HMRC restructured the bulletin around ABV bands and the draught and
small-producer reliefs. Figures published under the previous regime are not on
a comparable basis and are not chained on here: doing so would splice two
different definitions of the same name. Extending history across the reform is
a research-layer decision with an explicit method, not a collection one.

This is a short history — about three years of monthly observations — and that
limits what can be asked of it econometrically. It is collected because it is
genuine point-in-time activity data upstream of CPI alcohol, not because the
sample is comfortable.

Shared totals are stored once
-----------------------------
Two published columns are not product-specific:

``Total Alcohol Duty receipts (pounds million)``
    the all-alcohol total, repeated identically on all five sheets;
``Total Alcohol Duty receipts from wine and other fermented products``
    a combined total, repeated on the Wine and Other_Fermented_Products sheets.

Writing those under a per-product identifier would store the same figure five
times and invite double counting. Each is stored once under a product-neutral
identifier, and the repeated copies are cross-checked: if two sheets ever
disagree about the same shared total, collection fails rather than silently
choosing one.

This bulletin is activity data, not the alcohol duty *rate*, which is a tax
parameter published elsewhere and announced ahead of taking effect.
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

SOURCE_ID = "hmrc_alcohol_bulletin"
PAGE_URL = "https://www.gov.uk/government/statistics/alcohol-bulletin"

ODS_PATTERN = r"/Alcohol[^/]*\.ods"

# sheet name -> product token. Verified against the live workbook on 2026-09-16.
PRODUCT_SHEETS: dict[str, str] = {
    "Beer": "BEER",
    "Cider": "CIDER",
    "Wine": "WINE",
    "Other_Fermented_Products": "OFP",
    "Spirits": "SPIRITS",
}

PRODUCT_NAMES: dict[str, str] = {
    "BEER": "beer",
    "CIDER": "cider",
    "WINE": "wine",
    "OFP": "other fermented products",
    "SPIRITS": "spirits",
    "ALL": "all alcohol",
    "WINEOFP": "wine and other fermented products",
}

# Columns published identically on more than one sheet. They are stored once,
# under the product token given here, and cross-checked across the sheets that
# publish them.
SHARED_COLUMNS: dict[str, tuple[str, str]] = {
    "total alcohol duty receipts": ("ALL", "RECEIPTS"),
    "total alcohol duty receipts from wine and other fermented products": (
        "WINEOFP",
        "RECEIPTS",
    ),
}

# Published column label (lowercased, unit parenthetical stripped) -> measure
# token. An unrecognised column stops collection rather than being dropped.
MEASURES: dict[str, str] = {
    "uk beer production": "PRODUCTION",
    "uk potable spirits production": "PRODUCTION",
    "clearances less than 3.5% abv": "CLEARANCES_LT35",
    "clearances less than 8.5% abv": "CLEARANCES_LT85",
    "clearances less than 8.5% abv including sparkling cider from 5.5% to 8.5% abv": (
        "CLEARANCES_LT85"
    ),
    "clearances at least 3.5% abv but less than 8.5% abv": "CLEARANCES_35TO85",
    "clearances at least 8.5% abv but less than 22% abv": "CLEARANCES_85TO22",
    "clearances at least 8.5% abv": "CLEARANCES_GE85",
    "clearances at least 22% abv": "CLEARANCES_GE22",
    "clearances claiming no relief": "CLEARANCES_NORELIEF",
    "clearances claiming draught relief only": "CLEARANCES_DRAUGHT",
    "clearances claiming small producer relief only": "CLEARANCES_SPR",
    "clearances claiming draught relief and small producer relief": "CLEARANCES_DRAUGHTSPR",
    "clearances claiming draught relief and/or small producer relief": "CLEARANCES_DRAUGHTORSPR",
    "ex-warehouse clearances": "CLEARANCES_EXWAREHOUSE",
    "ex-ship clearances": "CLEARANCES_EXSHIP",
    "uk registered clearances": "CLEARANCES_UKREGISTERED",
    "ex-warehouse and uk-registered clearances": "CLEARANCES_EXWAREHOUSEUKREG",
}

# "Total beer clearances", "Total spirits clearances", ... all mean the same
# measure on their own sheet.
_TOTAL_CLEARANCES = re.compile(r"^total .+ clearances$")
# "Total Alcohol Duty receipts from beer|cider|spirits" — product-specific.
_PRODUCT_RECEIPTS = re.compile(r"^total alcohol duty receipts from (beer|cider|spirits)$")

# Two distinct beer production columns share one label stem, so the published
# unit disambiguates them.
_PRODUCTION_UNITS = {"litres": "_LITRES", "litres of alcohol": "_LAL"}

_UNIT_PATTERN = re.compile(r"\(([^)]*)\)\s*$")

# Observed release schedule, identical in shape to the tobacco bulletin:
# published quarterly, several months at a time, with the latest month in a
# release about 58 days after that month began.
MIN_LAG_DAYS = 55
MAX_LAG_DAYS = 260
INFERRED_LAG_DAYS = 100

EXPECTED_FIRST_OBSERVATION = date(2023, 8, 1)
MIN_EXPECTED_SERIES = 40
MIN_EXPECTED_DATES = 30
MAX_GAP_DAYS = 40

# Receipts are net of repayments and can be negative; a physical clearance or
# production quantity cannot.
MIN_PLAUSIBLE_RECEIPTS = -10_000.0
MIN_PLAUSIBLE_QUANTITY = 0.0
MAX_PLAUSIBLE_VALUE = 1e12


def strip_unit(label: str) -> tuple[str, str | None]:
    """Split a column label into its measure name and its published unit."""
    match = _UNIT_PATTERN.search(label)
    if not match:
        return label.strip(), None
    return label[: match.start()].strip(), match.group(1).strip()


def classify_column(label: str, sheet_product: str) -> tuple[str, str, str | None]:
    """Map one published column to ``(product, measure, published_unit)``.

    A shared total resolves to a product-neutral product token so it is stored
    once; everything else belongs to the sheet's own product.
    """
    stem, unit = strip_unit(label)
    key = re.sub(r"\s+", " ", stem).strip().lower()

    shared = SHARED_COLUMNS.get(key)
    if shared is not None:
        product, measure = shared
        return product, measure, unit

    if _PRODUCT_RECEIPTS.match(key):
        if key != f"total alcohol duty receipts from {PRODUCT_NAMES[sheet_product]}":
            raise ValueError(f"Receipt product does not match sheet {sheet_product}: {label!r}")
        return sheet_product, "RECEIPTS", unit
    if _TOTAL_CLEARANCES.match(key):
        if key != f"total {PRODUCT_NAMES[sheet_product]} clearances":
            raise ValueError(f"Clearance product does not match sheet {sheet_product}: {label!r}")
        return sheet_product, "CLEARANCES_TOTAL", unit

    known = MEASURES.get(key)
    if known is None:
        raise ValueError(
            f"HMRC alcohol sheet {sheet_product} publishes unrecognised column {label!r}. The "
            "published layout changed and must be re-verified against the official source."
        )
    measure = known
    if measure == "PRODUCTION":
        suffix = _PRODUCTION_UNITS.get((unit or "").lower())
        if suffix is None:
            raise ValueError(
                f"HMRC alcohol sheet {sheet_product} publishes production column {label!r} in "
                f"unrecognised unit {unit!r}; the two production measures are distinguished by "
                "their unit and must be re-verified"
            )
        measure += suffix
    return sheet_product, measure, unit


def make_series_id(product: str, measure: str) -> str:
    """Build ``HMRC_ALCOHOL_{PRODUCT}_{MEASURE}``."""
    series_id = f"HMRC_ALCOHOL_{product}_{measure}"
    if len(series_id) > 200:
        raise ValueError(f"series_id exceeds 200 characters: {series_id}")
    return series_id


def parse_series_id(series_id: str) -> tuple[str, str, str, str]:
    """Decode ``HMRC_ALCOHOL_{PRODUCT}_{MEASURE}`` into its four parts."""
    parts = series_id.split("_", 3)
    if len(parts) != 4 or parts[0] != "HMRC" or parts[1] != "ALCOHOL":
        raise ValueError(f"Invalid HMRC alcohol series_id: {series_id}")
    source, dataset, product, measure = parts
    if product not in PRODUCT_NAMES:
        raise ValueError(f"Unknown product in HMRC alcohol series_id: {series_id}")
    if not measure:
        raise ValueError(f"Incomplete HMRC alcohol series_id: {series_id}")
    return source, dataset, product, measure


def parse_ods(
    body: bytes, url: str, snapshot_id: str
) -> tuple[list[Observation], dict[str, dict[str, str]]]:
    """Parse all five monthly blocks, storing each shared total exactly once."""
    sheets = read_sheets(body, url)
    missing = set(PRODUCT_SHEETS) - set(sheets)
    if missing:
        raise ValueError(
            f"HMRC alcohol workbook {url} is missing sheet(s) {sorted(missing)}; found "
            f"{sorted(sheets)}. The published layout changed and must be re-verified."
        )

    observations: list[Observation] = []
    natives: dict[str, dict[str, str]] = {}
    # (series_id, reference_date) -> (value, sheet), used to prove the repeated
    # copies of a shared total agree before one of them is stored.
    seen: dict[tuple[str, date], tuple[float, str]] = {}

    for sheet, sheet_product in PRODUCT_SHEETS.items():
        block = monthly_block(sheets[sheet], sheet, url)
        for label, column in block.columns.items():
            product, measure, published_unit = classify_column(label, sheet_product)
            series_id = make_series_id(product, measure)
            natives.setdefault(
                series_id,
                {
                    "product": product,
                    "measure": measure,
                    "sheet": sheet,
                    "published_unit": published_unit or "",
                    "label": label,
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
                key = (series_id, reference_date)
                previous = seen.get(key)
                if previous is not None:
                    earlier_value, earlier_sheet = previous
                    if earlier_value != value:
                        raise ValueError(
                            f"HMRC alcohol workbook {url} publishes conflicting values for the "
                            f"shared column {label!r} at {reference_date}: {earlier_sheet} says "
                            f"{earlier_value} and {sheet} says {value}. A shared total must "
                            "agree across the sheets that repeat it."
                        )
                    # Identical repeat of a shared total; already stored once.
                    continue
                seen[key] = (value, sheet)
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
        unit = fields["published_unit"] or "as published"
        receipts = fields["measure"] == "RECEIPTS"
        published_label = fields["label"]
        # HMRC's Spirits worksheet explicitly reports production quarterly,
        # even though its values appear inside the monthly block.
        frequency = (
            "quarterly"
            if fields["product"] == "SPIRITS" and fields["measure"].startswith("PRODUCTION")
            else "monthly"
        )
        catalog[series_id] = {
            "source_id": SOURCE_ID,
            "name": f"UK alcohol duty, {product}: {published_label}",
            "description": (
                f"{frequency.capitalize()} UK figure for {product}, published by HM Revenue & Customs in the "
                f"Alcohol Bulletin as {published_label!r}, in {unit}. "
                + (
                    "Receipts are duty actually received net of repayments and can be negative "
                    "in a month where refunds exceed payments. "
                    if receipts
                    else ""
                )
                + "History begins 2023-08 with the reformed Alcohol Duty regime; earlier "
                "figures are on a different basis and are not chained on. Stored exactly as "
                "published with no derived transformation; HMRC's own financial-year and "
                "calendar-year totals are not collected because they aggregate these months."
            ),
            "frequency": frequency,
            "unit": "currency" if receipts else "other",
            "eco_group": "public_finance",
            "source_url": PAGE_URL,
            "last_publish_date": last_publish_date,
        }
    return catalog


def validate(observations: list[Observation], natives: dict[str, dict[str, str]]) -> None:
    """Gate the parsed panel before anything reaches the database."""
    if not observations:
        raise ValueError("HMRC alcohol collection produced no observations")
    if len(natives) < MIN_EXPECTED_SERIES:
        raise ValueError(
            f"HMRC alcohol returned only {len(natives)} series, below the "
            f"{MIN_EXPECTED_SERIES} floor; a sheet or a column is missing"
        )

    present = {fields["product"] for fields in natives.values()}
    expected = set(PRODUCT_SHEETS.values())
    if not expected <= present:
        raise ValueError(f"HMRC alcohol is missing published products {sorted(expected - present)}")

    keys = [(observation.series_id, observation.reference_date) for observation in observations]
    if len(keys) != len(set(keys)):
        counts: dict[tuple[str, date], int] = {}
        for key in keys:
            counts[key] = counts.get(key, 0) + 1
        duplicates = sorted(key for key, count in counts.items() if count > 1)[:5]
        raise ValueError(
            f"HMRC alcohol produced duplicate observations, for example {duplicates}; a shared "
            "total may have been stored more than once"
        )

    dates = sorted({observation.reference_date for observation in observations})
    if dates[0] != EXPECTED_FIRST_OBSERVATION:
        raise ValueError(
            f"HMRC alcohol history starts at {dates[0]}, expected {EXPECTED_FIRST_OBSERVATION}; "
            "the published file changed — check for a further duty reform — and must be "
            "re-verified"
        )
    if len(dates) < MIN_EXPECTED_DATES:
        raise ValueError(
            f"HMRC alcohol returned only {len(dates)} reference months, below the "
            f"{MIN_EXPECTED_DATES} floor; the download was probably truncated"
        )
    if dates[-1] > datetime.now(UTC).date():
        raise ValueError(f"HMRC alcohol published a future reference date {dates[-1]}")

    off_month = [day for day in dates if day.day != 1]
    if off_month:
        raise ValueError(f"HMRC alcohol published non-month-start reference dates {off_month[:5]}")

    long_gaps = [(a, b) for a, b in pairwise(dates) if (b - a).days > MAX_GAP_DAYS]
    if long_gaps:
        raise ValueError(
            f"HMRC alcohol monthly cadence broken by gaps longer than {MAX_GAP_DAYS} days at "
            f"{long_gaps[:5]}"
        )

    implausible = []
    for observation in observations:
        receipts = natives[observation.series_id]["measure"] == "RECEIPTS"
        floor = MIN_PLAUSIBLE_RECEIPTS if receipts else MIN_PLAUSIBLE_QUANTITY
        if not floor <= observation.value <= MAX_PLAUSIBLE_VALUE:
            implausible.append(
                (observation.series_id, observation.reference_date, observation.value)
            )
    if implausible:
        raise ValueError(
            f"HMRC alcohol published values outside their plausible envelope, for example "
            f"{implausible[:5]}; receipts may be negative (net of repayments) but a clearance "
            "or production quantity may not"
        )

    logger.info(
        "HMRC alcohol validation passed: %d series, %d reference months, %s to %s",
        len(natives),
        len(dates),
        dates[0],
        dates[-1],
    )


def collect(client: httpx.Client) -> SourceData:
    """Download, parse and validate the full HMRC Alcohol Bulletin history."""
    page = fetch_page(client, PAGE_URL)
    releases = release_timestamps(page)
    if not releases:
        raise ValueError(f"No publication history found on {PAGE_URL}; the page layout changed")
    last_publish_date = releases[-1].astimezone(UTC).date()
    logger.info(
        "HMRC alcohol page carries %d official release timestamps, %s to %s",
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
