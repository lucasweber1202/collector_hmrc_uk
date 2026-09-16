# collector_hmrc_uk

Standalone collector for HM Revenue & Customs datasets relevant to UK inflation.

The repository owns HMRC extraction and raw point-in-time persistence. It does
not map predictors to CPI targets or calculate modelling features; those tasks
belong to [`uk_inflation_predictors`](https://github.com/lucasweber1202/uk_inflation_predictors).

Schema: `collector_hmrc_uk`.

## Current coverage

| `source_id` | Dataset | Frequency | History | Series | Artifact |
| --- | --- | --- | --- | --- | --- |
| `hmrc_tobacco_bulletin` | [Tobacco Bulletin](https://www.gov.uk/government/statistics/tobacco-bulletin) | monthly | 1991-01 → | 10 | ODS |
| `hmrc_alcohol_bulletin` | [Alcohol Bulletin](https://www.gov.uk/government/statistics/alcohol-bulletin) | monthly | 2023-08 → | 48 | ODS |

Verified on 2026-09-16: 58 series and 5,922 observations from two raw artifacts.

### Tobacco Bulletin

Two sheets, stored as two measures that are never mixed:

| Measure | Content | Unit |
| --- | --- | --- |
| `RECEIPTS` | tobacco duty received, by product | GBP million |
| `CLEARANCES` | quantity released for consumption | million sticks (cigarettes) / '000 kg |

Products: cigarettes, cigars, HRT, other, plus HMRC's own overall total
(receipts) and non-cigarette total (clearances). Identifier:
`HMRC_TOBACCO_{MEASURE}_{PRODUCT}`.

**Receipts can be negative.** They are duty received net of repayments, so a
month where refunds exceed payments publishes a negative figure — verified,
HMRC publishes `-4.920903` for "Other" in January 2020. Clearances are a
physical quantity and may not be negative; that floor is enforced.

### Alcohol Bulletin

Five product sheets — beer, cider, wine, other fermented products, spirits —
each publishing production, clearances by ABV band and relief, and receipts.
Identifier: `HMRC_ALCOHOL_{PRODUCT}_{MEASURE}`.

**History starts 2023-08**, when the reformed Alcohol Duty regime took effect
and HMRC restructured the bulletin around ABV bands and the draught and
small-producer reliefs. Earlier figures are not on a comparable basis and are
not chained on. About three years of monthly observations is a short sample and
limits what can be asked of it econometrically.

**Shared totals are stored once.** `Total Alcohol Duty receipts` is repeated
identically on all five sheets, and a wine+OFP combined total on two. Each is
stored once under a product-neutral identifier (`HMRC_ALCOHOL_ALL_RECEIPTS`,
`HMRC_ALCOHOL_WINEOFP_RECEIPTS`) and the repeated copies are cross-checked; two
sheets disagreeing about the same total stops collection.

## Not implemented: duty rates

`hmrc_tobacco_duty_rates` and `hmrc_alcohol_duty_rates` are **blocked**, and no
module exists for them rather than a stub pretending otherwise.

Verified on 2026-09-16, both rate histories are published as **HTML tables
only**, with no CSV, ODS or XLSX attachment on either page:

- `/government/statistics/tobacco-bulletin/historical-tobacco-duty-rates`
  (rates back to 1978, 2 HTML tables)
- `/government/statistics/alcohol-bulletin/alcohol-bulletin-historic-duty-rates`
  (18 HTML tables)

Building an HTML scraper for a tax parameter is out of scope. A duty rate is
also semantically different from a bulletin — see `POINT_IN_TIME.md`.

## Run

```bash
python -m pip install -r requirements.txt
cp .env.example .env
python main.py                                  # every data set
python main.py --source-id hmrc_tobacco_bulletin
```
