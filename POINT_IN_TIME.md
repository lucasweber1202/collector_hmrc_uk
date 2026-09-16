# Point-in-time contract

This collector stores predictor data for forecasting. A historical backtest must never see information that was unavailable at the simulated forecast instant.

## Stored dates

- `reference_date`: period the observation describes.
- `vintage_date`: UTC date on which this collector stored that version.
- `release_date`: source publication date when explicitly supported.
- `available_at`: earliest defensible instant at which this stored vintage could have been known.
- `collected_at`: timestamp of this pipeline run.

`availability_basis` is one of `official_timestamp`, `official_date`, `archived_release`, `first_seen`, `inferred`, `unknown`.

By default `get_series_as_of()` accepts only `official_timestamp`, `official_date`, `archived_release`, and `first_seen`. `inferred` and `unknown` require explicit opt-in.

## Historical revisions

A revised value for an already stored `(series_id, reference_date)` must not reuse the original publication timestamp. Unless the source exposes explicit evidence for the revision release, the revised vintage is stamped:

- `available_at = collected_at`
- `availability_basis = first_seen`
- `release_date = NULL`

This prevents a 2026 revision of a 2024 observation from appearing in a 2024 backtest.

## Same-day revisions

The fleet schema uses `vintage_date DATE`. Two different intraday revisions therefore cannot be represented without overwriting one information set. Predictor collectors fail closed when an already stored vintage changes again on the same UTC date. Retry after the UTC date changes rather than rewriting history.

## As-of guarantee

`get_series_as_of(series_id, as_of)` filters on `available_at <= as_of` before ranking vintages. A later revision therefore cannot mask the vintage that was actually current at the historical instant.

## HMRC bulletin release attribution

### Published quarterly, even though the data is monthly

Both bulletins are released about every three months and each release adds
several months of monthly data at once. The 2026-08-28 tobacco release, for
example, added provisional receipts for February to July 2026.

That matters for attribution. The naive rule — attribute a month to the first
release after it — would claim February 2026 was knowable on 2026-02-27,
because a release happened that day. It was not: that release carried August
2025 to January 2026, and February 2026 first appeared six months later.

The minimum lag is therefore set from evidence. Across releases, the **latest**
month in a release is consistently about 58 days after that month began:

| Release | Latest month carried | Lag from month start |
| --- | --- | --- |
| 2026-08-28 | July 2026 | 58 days |
| 2025-08-29 | July 2025 | 59 days |
| 2025-05-30 | April 2025 | 59 days |
| 2025-02-28 | January 2025 | 58 days |

`MIN_LAG_DAYS = 55` is just below that floor, so a month can never be
attributed to a release that predates its publication. `MAX_LAG_DAYS = 260` is
wide enough that a month missed by one release is still attributed to the next
even when a quarter is skipped.

### Coverage

Both change histories begin in February 2016.

- **Alcohol**: data starts 2023-08, well inside the change history, so every
  observation is attributable to an official timestamp.
- **Tobacco**: data starts 1991-01, long before it, so months before 2016 carry
  `availability_basis = inferred` and are excluded from `get_series_as_of()` by
  default.

On the verified 2026-09-16 build across both data sets: **2,987 observations
carry `official_timestamp`, 2,935 carry `inferred`, none `unknown`.**

## Why duty rates are not collected here

A duty rate is a tax parameter, not activity data, and it carries three
distinct dates that must never be conflated:

| Date | Meaning |
| --- | --- |
| `announcement_date` | when the rate was announced, usually at a fiscal event |
| `effective_date` | when the rate begins to apply |
| `reference_date` | the period an observation describes |

A rate is *knowable* from the announcement, which is typically weeks or months
before it takes effect. Using the effective date as `available_at` would
understate the information set a contemporary forecaster had; using the
reference date would be worse still.

Getting that right needs the announcement dates, and HMRC publishes the rate
histories only as HTML tables with no machine-readable artifact (verified
2026-09-16). Rather than scrape a tax parameter and guess at its announcement
semantics, both duty-rate data sets stay blocked and unimplemented.
