# Predictor collector methodology

This repository belongs to the UK inflation predictor fleet. It collects raw explanatory variables (X) only. Official forecast targets (Y), CPI weights and bottom-up reconciliation remain owned by `collector_ons_cpi` / `collector_ons_ex_cpi`.

## Collector contract

Each repository owns one publisher/source family and writes five tables in a schema whose name equals the repository name:

1. `metadata`
2. `time_series`
3. `availability`
4. `source_snapshots`
5. `logs`

Only raw published levels are stored. MoM, YoY, MTD, rolling averages, monthly aggregation, diffusion and model features are downstream research transformations.

## Source isolation

Source-specific download, parsing and validation live in `scripts/extract.py` or source-forced helper modules. There are no imports from other collector repositories, no shared Python package, no `BaseCollector`, ORM layer or migration framework. Template code is copied into each repository so every collector remains independently deployable and auditable.

## Validation

Before persistence the source module should validate at least source schema, duplicate keys, units, frequency/cadence, plausible values, expected history boundaries where defensible and non-empty output. Source changes that undermine an invariant fail loudly.

## Idempotency and revisions

An unchanged rerun writes no `time_series`, `availability`, `source_snapshots` or `metadata` rows. A later-day historical change creates a new vintage and keeps the old vintage. Point-in-time revision handling follows `POINT_IN_TIME.md`.

## Raw snapshots

Every parsed artifact is hashed with SHA-256. A changed upstream file becomes a new `source_snapshots` row rather than replacing the previous snapshot. Raw bytes stay outside Git in a gitignored location.

## HMRC bulletin workbook layout

Both bulletins use the same workbook convention, described once in
`scripts/hmrc_tables.py`. That module is a description of one publisher's file
format — it owns no HTTP, no identifiers, no validation policy and no database
access, and nothing inherits from it.

Each data sheet stacks three blocks: `Table Na` by financial year, `Table Nb` by
calendar year and `Table Nc` monthly. **Only the monthly block is collected.**
The two annual blocks are HMRC's own aggregations of exactly the same monthly
figures, so storing them would duplicate the same information under a second
frequency and invite double counting; the research layer can rebuild either
aggregation from the monthly series.

HMRC's workbooks also carry stale external-link entries whose names look like
`'file://.../BOARDNEW.XLS'#Sheet1`. They are link targets left by the producing
spreadsheet, not sheets, and are dropped when the workbook is read.

Missing values are published as markers (`[X]` in tobacco, `[No Data]` in
alcohol) rather than blanks, and are skipped rather than stored as zero. A
period label may carry a `[provisional]` or `[revised]` suffix; that is a
publication status, not part of the period, so it is stripped before parsing.

## Receipts and clearances are different quantities

Receipts are duty actually received, net of repayments. Clearances are the
quantity released for consumption, the point at which duty becomes payable.
They move together but are not the same series and are never mixed under one
identifier. Receipts may be negative; a physical quantity may not.

## Neither bulletin is a duty rate

A bulletin is periodic activity data. A duty *rate* is a tax parameter, usually
announced ahead of the date it takes effect. They are different economic
objects with different point-in-time semantics and are not collected together.

## The two bulletins do not share a history depth

The Tobacco Bulletin publishes a continuous monthly series from 1991. The
Alcohol Bulletin does not: its monthly tables begin in August 2023.

That start date is the source's, not this collector's. HMRC states on the
workbook cover that "all clearances statistics in the tables now relate only to
the new Alcohol Duty regime introduced in August 2023", and Table 1c on every
product sheet begins at August 2023 for production and receipts as well. The
page carries a single data attachment, which is downloaded and parsed in full,
so the window is neither a parser nor a download limitation. Pre-reform figures
survive only in archived bulletins at the National Archives and UK Trade Info,
on the old basis of taxation — litres of product rather than litres of pure
alcohol, and for beer clearances in hectolitres rather than litres. Chaining
those onto the current series would fabricate a level break, so the guideline's
stitching rule does not apply: there is no compatible historical workbook to
stitch.

A single five-year floor therefore removed all 48 alcohol series — every one of
them current and near-complete — and would have kept doing so until 2028. The
floor is declared per data set in `scripts/config.py` instead: five years for
tobacco, eighteen months for alcohol.

Depth is not the only test, because a shorter floor on its own would also admit
a stub or a half-parsed sheet. A series must additionally be dense: it has to
cover at least 80% of the periods in its own span. Density is measured at the
frequency the catalog declares for that series, not on a monthly calendar,
because the bulletin publishes UK potable spirits production quarterly inside
an otherwise monthly workbook; judged monthly, that complete series reads as
two-thirds empty and is dropped. Staleness is unchanged, so a discontinued
alcohol series is still removed on recency.

`tests/test_alcohol_regime_thresholds.py` holds both halves of this decision:
a full new-regime series and the quarterly series survive, while a two-print
stub, an every-other-month parse, a mislabelled frequency, a discontinued
series and an undeclared data set are all refused.
