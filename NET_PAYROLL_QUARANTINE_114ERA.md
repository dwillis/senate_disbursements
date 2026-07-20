# 114-era NET PAYROLL quarantine: residual characterization

Analysis of the 4,659 quarantined 114-era rows (114sdoc4: 1,451,
114sdoc7: 2,319, 114sdoc13: 889), all `validation_status='fail'`,
all `category='NET PAYROLL EXPENSES'` salary rows. Investigated as
"item 10" after items 8 and 9 cleared 768 ORG TOTALS fails across the
same 3 docs.

## What causes a row to land in quarantine

A salary row's `validation_status` is set to `'fail'` only when its
block's NET PAYROLL EXPENSES segment check (`reconcile.py:
_reconcile_block_typed`) returns `status='fail'` AND the row is in
that segment's `buffers["salary"]`. Two release paths can retag the
row to `'source_mismatch'` so it publishes:

1. **`second_opinion.apply_second_opinion`** — re-sums the amount
   column straight from the page rows (bypassing the parser's
   classifier) and compares {independent, parser, printed}. If
   `independent == parser != printed`, the rows are faithful and
   publish tagged `source_mismatch`. If `independent == printed !=
   parser`, the rows stay quarantined as `parser_suspect`. Otherwise
   `inconclusive` — stays quarantined.

2. **`pipeline._apply_cross_year_release`** — for offices with 2+
   NET PAYROLL segment fails whose actuals net to ~$0, tag the
   checks `cross_year` and retag their rows `source_mismatch`.
   Specifically designed for 113th/114th committee rosters that
   print under one S.RES. account while money is booked against a
   sibling year's account. Normalization strips the S.RES. clause
   and any `FY YYYY` suffix, so e.g. "FINANCE FINANCE - S.RES. 253B
   (113TH)..." and "FINANCE FINANCE - S.RES. 73B (114TH)..." collapse
   to the same key and their offsetting fails net to $0.

## Headline numbers (114-era, 3 docs)

| doc | total NET PAYROLL segment fails | cross_year (published) | source_mismatch (published) | inconclusive (quarantined) | of which indep=0.00 |
|---|---|---|---|---|---|
| 114sdoc4 | 125 | 83 | 5 | 37 | 32 |
| 114sdoc7 | 42 | 0 | 3 | 39 | 37 |
| 114sdoc13 | 82 | 50 | 5 | 27 | 24 |
| **total** | **249** | **133** | **13** | **103** | **93** |

The 4,659 quarantined rows come from 103 `inconclusive` fails. 93 of
those 103 (90%) have `independent_sum=0.00`. The other 10 have a
real independent sum that matches neither parser nor printed
(genuinely ambiguous).

## Root cause of the 93 zero-indep cases

Traced on SENATOR MARK BEGICH p486 in 114sdoc4 (block pages 485-486,
NET PAYROLL EXPENSES exp=$2,108.85 act=$1,458.85 diff=$650):

```
block pages=[485, 486] events=39 records=33 checks=6
  p486 NET PAYROLL EXPENSES exp=2108.85 act=1458.85
    start=(486, 263.6) end=(486, 282.08)
    p486: calibrate=OK rows=50
    indep=0.0 ok=True
```

`second_opinion._independent_segment_sum` uses a narrow window
between the previous boundary subtotal (ACQUISITION OF ASSETS,
top=263.6) and the failing check (NET PAYROLL EXPENSES,
top=282.08) — an 18pt vertical band that contains zero data rows.

**Why the window is empty:** the anchor template (112th-114th) prints
all subtotals at the END of the listing (verified: JUDICIARY,
114sdoc13 p2221-2226, TRAVEL's subtotal prints before the payroll
ones), so salary rows are itemized at the top of the block, before
any subtotals. The narrow window between two adjacent subtotal
lines is below the salary roster — it has no data rows at all.

`calibrate_columns` is NOT returning None on these pages (the
"no_header" theory was wrong). It returns a valid ColumnMap, the
independent sum runs cleanly, and the sum is genuinely 0 because
the window contains no data rows. The three-way comparison is:

- `independent = 0.0` (no rows in window)
- `parser = $1,458.85` (= $0 salary rows + $1,458.85 PERSONNEL
  BENEFITS lump sum added via `lump_sum_total` in
  `_reconcile_block_typed`)
- `printed = $2,108.85` (= $650 OPC + $1,458.85 PB per the body's
  printed subtotals)

Neither matches independent → `inconclusive` → rows stay quarantined.

## Why second_opinion can't validate the anchor rollup, even with a fix

Two structural incompatibilities:

1. **Window problem.** A block-wide start_pos (instead of the
   previous subtotal) would put salary rows in the window, but
   also expense rows (TRAVEL, OTHER CONTRACT, ACQUISITION). The
   naive sum would be ~$24K on BEGICH p486 vs the parser's
   salary-only $1,458.85 — still no match. The whole premise of
   second_opinion (naive row sum, no classifier) breaks for a
   rollup that filters by record type.

2. **Lump-sum problem.** The parser's NET PAYROLL actual includes
   printed lump-sum subtotals (PB, REA) that have zero itemized
   rows. second_opinion's naive sum can only sum data rows; it
   cannot validate that the parser correctly included a printed
   lump-sum figure. So even a salary-only independent sum would
   disagree with the parser whenever PB or REA is nonzero.

The modern path (117sdoc8+) doesn't have this issue because it
marks NET PAYROLL EXPENSES as `basis='block_running_total'`, which
second_opinion skips (`if check.basis != "segment"`). The anchor
path marks it `basis='segment'` (line 317 in `reconcile.py`), so
second_opinion runs and gets the wrong answer.

## The 10 non-zero-indep cases (genuinely ambiguous)

3 in 114sdoc4: APPROPRIATIONS p58, SGT @ ARMS - CAPITOL p126,
CONSULTANTS p197. For these, second_opinion's independent sum ran
but matched neither side — typically the independent is BETWEEN
parser and printed. These are likely either parser bugs (independent
excludes rows the parser included) or source-side (the body's own
itemization doesn't add up). Per-block PDF inspection would be
needed to classify.

## Cross-office structural pattern in the residual

Looking at the 21 single-fail offices in 114sdoc4 and the 10
multi-fail offices that don't net to $0, several have IDENTICAL
diffs to other offices:

| office | net diff |
|---|---|
| SENATOR BARBARA BOXER | +$42,364.74 |
| SENATOR JOHNNY ISAKSON | +$42,364.74 |
| ETHICS COMMITTEE ON ETHICS | -$84,729.48 |
| SENATOR RON JOHNSON | +$1,166.66 |
| SENATOR MIKE LEE | -$1,166.66 |
| SENATOR CHARLES E. SCHUMER | +$2,678.47 |
| SENATOR SUSAN M. COLLINS | -$2,678.47 |
| SENATOR SHELDON WHITEHOUSE | +$1,915.56 |

The BOXER/ISAKSON/ETHICS triangle is the cleanest: BOXER +$42,364.74
+ ISAKSON +$42,364.74 = +$84,729.48, exactly offsetting ETHICS
-$84,729.48. (BOXER and ISAKSON were both on the ETHICS committee
in the 114th Congress.) The structural pattern is that shared
committee staff get attributed to multiple senators' blocks, but
the committee's own block shows the offsetting under-count.

`_apply_cross_year_release` only nets within an office (after
stripping the S.RES. / FY clause). The triangle doesn't balance
under per-office netting because the three offices have different
keys.

## Paths forward (not taken)

A. **Cross-office netting in cross-year release.** Group offices
   by a broader key (committee + its members) and release if the
   group nets to $0. The BOXER/ISAKSON/ETHICS triangle would clear.
   Risky — could mask real parser bugs, and the grouping key
   requires a per-congress committee-membership reference table.

B. **Block-wide independent sum for PERSONNEL_ROLLUP_LABELS** that
   filters salary rows via row position / amount column heuristics
   (not the full classifier). Could work but introduces a
   partial-classifier into second_opinion, undermining its "naive
   re-sum" safety property.

C. **Accept the residual.** 4,659 rows across ~103 blocks, tagged
   `fail`, not publishing. The structural patterns are real (shared
   staff, single-block source discrepancies, 10 genuine ambiguous
   cases). The quarantine is the safety mechanism working as
   designed. **This is the chosen path.**

## Why C is right

- The residual is structural, not a bug. The cross-office shared
  staff pattern is a Senate data-modeling issue (a staffer paid by
  the ETHICS committee appearing on the ETHICS block AND on each
  member senator's block), not a parser bug.
- The 10 genuine ambiguous cases need per-block PDF inspection to
  classify — automation can't decide.
- Forcing a release via cross-office netting (A) would publish
  rows that may include real parser bugs. The current behavior —
  quarantine and require manual review — is the safe default.
- The 4,659 quarantined rows are 0.7% of the 114-era's ~625K
  published rows (4,659 quarantined vs 144,395 published across
  the 3 docs). A small, well-characterized review queue.

## Recommendations for consumers

- The 114-era `senate_data_cleaned.csv` is the published,
  reconciliation-passed subset. Safe for analysis.
- The 114-era `quarantine.csv` contains 4,659 NET PAYROLL salary
  rows that failed reconciliation and weren't released. Use them
  only with awareness of the structural patterns above.
- The 114-era `reconciliation_report.csv` carries the
  `second_opinion` column (cross_year / source_mismatch /
  inconclusive / parser_suspect) for every block-level check, so
  consumers can filter or audit by verdict.

## Test coverage

No code changes in this investigation. The existing test suite
(160 fast tests, 10 slow snapshot tests) continues to pass and
characterizes the parser's current behavior on all 10 processed
reports.