# Data audit — collector_hmrc_uk

- Audit seed: `20260918`
- Source version: live capture on 2026-09-18
- Test result: **96 passed**
- Execution: **PASS**
- Overall: **PARTIAL** — Valores e metadados validados; frequência trimestral de produção corrigida. Edições anteriores não capturadas exigem arquivos históricos.
- Output: 5,922 observations, 58 series, 1991-01-01 to 2026-07-01.
- Sample: 31; values matched: 31; failures: 0; not verifiable: 0.

## Observation evidence

| # | Series | Period | Collector | Official source | Unit/frequency evidence | Result |
|---:|---|---|---:|---:|---|---|
| 1 | `HMRC_TOBACCO_CLEARANCES_HRT` | 1991-01-01 | 278.0 | 278.0 | '000 kilograms; monthly; Table_2_clearances!row 77 col 4 | **PASS** |
| 2 | `HMRC_TOBACCO_RECEIPTS_CIGARS` | 2026-07-01 | 16.22791248 | 16.22791248 | GBP million; monthly; Table_1_receipts!row 504 col 3 | **PASS** |
| 3 | `HMRC_TOBACCO_RECEIPTS_OTHER` | 1991-11-01 | 8.612547 | 8.612547 | GBP million; monthly; Table_1_receipts!row 88 col 5 | **PASS** |
| 4 | `HMRC_TOBACCO_RECEIPTS_OTHER` | 1998-05-01 | 1.930966 | 1.930966 | GBP million; monthly; Table_1_receipts!row 166 col 5 | **PASS** |
| 5 | `HMRC_TOBACCO_RECEIPTS_CIGARS` | 1997-01-01 | 1.591196 | 1.591196 | GBP million; monthly; Table_1_receipts!row 150 col 3 | **PASS** |
| 6 | `HMRC_TOBACCO_CLEARANCES_CIGARS` | 1996-08-01 | 122.879840472028 | 122.879840472028 | '000 kilograms; monthly; Table_2_clearances!row 144 col 3 | **PASS** |
| 7 | `HMRC_TOBACCO_RECEIPTS_CIGARETTES` | 2014-02-01 | 541.03109589 | 541.03109589 | GBP million; monthly; Table_1_receipts!row 355 col 2 | **PASS** |
| 8 | `HMRC_TOBACCO_RECEIPTS_CIGARETTES` | 2005-07-01 | 688.19649483 | 688.19649483 | GBP million; monthly; Table_1_receipts!row 252 col 2 | **PASS** |
| 9 | `HMRC_TOBACCO_RECEIPTS_OTHER` | 2008-12-01 | 2.04416012 | 2.04416012 | GBP million; monthly; Table_1_receipts!row 293 col 5 | **PASS** |
| 10 | `HMRC_TOBACCO_RECEIPTS_OVERALLTOTAL` | 2004-09-01 | 643.000056596 | 643.000056596 | GBP million; monthly; Table_1_receipts!row 242 col 6 | **PASS** |
| 11 | `HMRC_TOBACCO_CLEARANCES_CIGARETTES` | 2026-05-01 | 1000.19438712091 | 1000.19438712091 | million sticks; monthly; Table_2_clearances!row 501 col 2 | **PASS** |
| 12 | `HMRC_TOBACCO_RECEIPTS_CIGARETTES` | 2026-05-01 | 430.06529259 | 430.06529259 | GBP million; monthly; Table_1_receipts!row 502 col 2 | **PASS** |
| 13 | `HMRC_TOBACCO_RECEIPTS_HRT` | 2026-02-01 | 70.71359562 | 70.71359562 | GBP million; monthly; Table_1_receipts!row 499 col 4 | **PASS** |
| 14 | `HMRC_TOBACCO_RECEIPTS_CIGARETTES` | 2025-11-01 | 436.69377882 | 436.69377882 | GBP million; monthly; Table_1_receipts!row 496 col 2 | **PASS** |
| 15 | `HMRC_TOBACCO_CLEARANCES_OTHER` | 2001-01-01 | 70.1910606842015 | 70.1910606842015 | '000 kilograms; monthly; Table_2_clearances!row 197 col 5 | **PASS** |
| 16 | `HMRC_ALCOHOL_CIDER_CLEARANCES_NORELIEF` | 2023-08-01 | 1051597.47020939 | 1051597.47020939 | litres of alcohol; monthly; Cider!row 22 col 2 | **PASS** |
| 17 | `HMRC_ALCOHOL_BEER_RECEIPTS` | 2026-07-01 | 298.0 | 298.0 | pounds million; monthly; Beer!row 59 col 15 | **PASS** |
| 18 | `HMRC_ALCOHOL_SPIRITS_CLEARANCES_35TO85` | 2024-02-01 | 281064.480407081 | 281064.480407081 | litres of alcohol; monthly; Spirits!row 29 col 4 | **PASS** |
| 19 | `HMRC_ALCOHOL_OFP_CLEARANCES_EXSHIP` | 2023-08-01 | 222095.080426485 | 222095.080426485 | litres of alcohol; monthly; Other_Fermented_Products!row 24 col 7 | **PASS** |
| 20 | `HMRC_ALCOHOL_OFP_CLEARANCES_EXWAREHOUSE` | 2024-02-01 | 463747.205296108 | 463747.205296108 | litres of alcohol; monthly; Other_Fermented_Products!row 30 col 6 | **PASS** |
| 21 | `HMRC_ALCOHOL_OFP_CLEARANCES_UKREGISTERED` | 2023-12-01 | 109493.244567808 | 109493.244567808 | litres of alcohol; monthly; Other_Fermented_Products!row 28 col 8 | **PASS** |
| 22 | `HMRC_ALCOHOL_CIDER_CLEARANCES_DRAUGHT` | 2025-03-01 | 551489.490785672 | 551489.490785672 | litres of alcohol; monthly; Cider!row 41 col 3 | **PASS** |
| 23 | `HMRC_ALCOHOL_SPIRITS_CLEARANCES_EXWAREHOUSEUKREG` | 2025-02-01 | 4377766.91293566 | 4377766.91293566 | litres of alcohol; monthly; Spirits!row 41 col 7 | **PASS** |
| 24 | `HMRC_ALCOHOL_BEER_CLEARANCES_EXSHIP` | 2024-11-01 | 555740.262981772 | 555740.262981772 | litres of alcohol; monthly; Beer!row 39 col 12 | **PASS** |
| 25 | `HMRC_ALCOHOL_CIDER_CLEARANCES_TOTAL` | 2025-03-01 | 2074679.57387602 | 2074679.57387602 | litres of alcohol; monthly; Cider!row 41 col 9 | **PASS** |
| 26 | `HMRC_ALCOHOL_WINE_CLEARANCES_LT85` | 2026-06-01 | 241801.831674084 | 241801.831674084 | litres of alcohol; monthly; Wine!row 56 col 2 | **PASS** |
| 27 | `HMRC_ALCOHOL_ALL_RECEIPTS` | 2026-06-01 | 947.0 | 947.0 | pounds million; monthly; Beer!row 58 col 16 | **PASS** |
| 28 | `HMRC_ALCOHOL_BEER_CLEARANCES_SPR` | 2025-11-01 | 288512.596971747 | 288512.596971747 | litres of alcohol; monthly; Beer!row 51 col 9 | **PASS** |
| 29 | `HMRC_ALCOHOL_WINE_CLEARANCES_LT85` | 2026-03-01 | 184293.677058848 | 184293.677058848 | litres of alcohol; monthly; Wine!row 53 col 2 | **PASS** |
| 30 | `HMRC_ALCOHOL_CIDER_CLEARANCES_UKREGISTERED` | 2023-09-01 | 1049804.57512478 | 1049804.57512478 | litres of alcohol; monthly; Cider!row 23 col 8 | **PASS** |
| 31 | `HMRC_ALCOHOL_SPIRITS_PRODUCTION_LAL` | 2026-06-01 | 111710394.4 | 111710394.4 | litres of alcohol; quarterly; Spirits!row 57 col 2 | **PASS** |

## Filtering and metadata


The audit read the captured official artifact independently of the collector parser. It checked identifier linkage, published labels, units, frequency and first/latest boundaries. Source artifacts are identified by SHA-256 in the audit evidence.

## Point-in-time and revisions

Predictor as-of queries filter availability before ranking vintages. `inferred` and `unknown` remain excluded by default. Current mutable-file backfills are recorded at `first_seen`; later observed revisions create later vintages and do not inherit an original release timestamp. Actual pre-collection historical editions remain `NOT_VERIFIABLE` unless an archived source file exists.

## Corrections

- Mesma correção PIT para os boletins.
- Produção de destilados passou de monthly para quarterly.
- Rótulos de produto incompatíveis com a worksheet agora falham.

## Result

**PARTIAL** — Valores e metadados validados; frequência trimestral de produção corrigida. Edições anteriores não capturadas exigem arquivos históricos.
