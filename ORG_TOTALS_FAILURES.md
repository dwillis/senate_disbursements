# BANNER ORGANIZATION TOTALS reconciliation failures

Analysis of the `BANNER ORGANIZATION TOTALS` `fail` checks across the 10
processed Senate disbursement reports (114_sdoc4/7/13, 117sdoc8,
118sdoc2/11/13, 119sdoc3/5/6), measured after the item 6 banner-check
noise-reduction pass.

## What the check compares

For each block, `banner_checks` (senate_parser/reconcile.py) compares two
independent figures for the same funding period:

- **expected** = the banner page's printed "ORGANIZATION TOTALS / NET
  EXPENDITURES FOR THE PERIOD" figure (from `BannerSummary.organization_totals`,
  extracted by `segment.parse_banner_summary`). This is the report's own
  statement of the block's total spend.
- **actual** = `ReconcileResult.parsed_grand_total` — every itemized
  record's dollars plus the printed lump-sum subtotals that itemize no
  rows (records.LUMP_SUM_LABELS), summed across the whole block.

Both are magnitudes (banner figures print negated because they're
expenditures against the authorization). A `fail` means the two
disagree by more than the WARN_TOLERANCE of $1.00.

## Headline numbers

- **Total: 3,621 `fail` checks** across 10 docs (2,566 modern-era + 1,055
  114-era).
- Per doc (range 268–438), no single report dominates.

## Direction is the headline signal

| direction | count | share |
|---|---|---|
| actual < expected (parser **under-counts**) | 3,314 | 91% |
| actual > expected (parser **over-counts**) | 307 | 8% |
| actual == 0 (parser extracted **nothing**) | 692 | 19% of total |

The typical failure is "the banner says X, the parser found less than X" —
dollars are being *missed*, not invented. Only 8% of cases over-count.

## Magnitude distribution

| diff range | count | share |
|---|---|---|
| $0 – $10 | 14 | <1% (float-drift noise — item 7's integer-cents accumulation would clear these) |
| $10 – $100 | 148 | 4% |
| $100 – $1K | 323 | 9% |
| $1K – $10K | 683 | 19% |
| $10K – $100K | 1,982 | 55% (the bulk) |
| $100K – $1M | 333 | 9% |
| >$1M | 138 | 4% (structural failures) |

- median diff: $25,672
- mean diff: $172,041
- max diff: $15,416,333 (119sdoc5, SERGEANT AT ARMS, FY2025)

The bulk (55%) are $10K–$100K off — likely a few missed subtotals or
lump-sum lines per block, not whole-block gaps.

## The zero-actual tail (692 checks)

692 fails have `actual == 0`: the banner printed a real figure (median
$2,157, max $7.5M — the EMERGENCY APPROPRIATION P.L. 109-13 block in
117sdoc8) but the parser extracted no dollars at all. These are
whole-block parsing gaps — typically expense-only committee blocks
(e.g. CHAIRMAN MAJORITY/MINORITY CONFERENCE COMMITTEE, CHAIRMAN MAJORITY
POLICY COMMITTEE) where the body rows didn't get attributed to the
block. These are the most actionable single class: a $0 body total is
unambiguous and points at a specific block's record extraction.

## The biggest fails cluster on one office

7 of the top 10 diffs are **SERGEANT AT ARMS AND DOORKEEPER OF THE
SENATE**, across every congress:

| doc | FY | banner expected | parser actual | diff |
|---|---|---|---|---|
| 119sdoc5 | 2025 | $45,268,763 | $29,852,430 | $15,416,333 |
| 117sdoc8 | 2021 | $38,859,328 | $24,083,963 | $14,775,365 |
| 118sdoc13 | 2024 | $42,456,092 | $29,921,968 | $12,534,124 |
| 119sdoc3 | 2025 | $29,159,807 | $17,596,832 | $11,562,974 |
| 118sdoc11 | 2024 | $28,447,862 | $17,511,834 | $10,936,028 |
| 118sdoc2 | 2023 | $25,838,595 | $15,281,559 | $10,557,036 |
| 119sdoc6 | 2026 | $27,770,790 | $19,726,833 | $8,043,958 |

The Sgt at Arms is the largest office by dollar volume, with many
sub-offices (CAPITOL DIVISION, TECHNOLOGY DEVELOPMENT SERVICES, STAFF
OFFICES, CENTRAL OPERATIONS, etc.). The banner's ORGANIZATION TOTALS
line aggregates all of them; the parser's `parsed_grand_total` is
consistently ~$10M short. This is systematic, not per-row — the gap
repeats across reports and congresses, which suggests a structural
issue in how the Sgt at Arms block's dollars are accumulated (e.g.
sub-office blocks segmenting as separate blocks, or a class of
expense rows not reaching `parsed_grand_total`).

## Funding-year spread

Every year 2013–2026 is represented:

| FY | count |
|---|---|
| (empty — S.RES. committee inquiry accounts + NO-YEAR accounts) | 713 |
| 2024 | 548 |
| 2025 | 495 |
| 2023 | 397 |
| 2015 | 410 |
| 2026 | 194 |
| 2022 | 192 |
| 2014 | 168 |
| 2016 | 128 |
| 2020 | 90 |
| 2021 | 229 |
| 2019 | 32 |
| 2013 | 19 |
| 2017/2018 | 6 |

No single funding year dominates, so this is **not a template-era
bug**. The empty-FY bucket (713) is the committee inquiry / NO-YEAR
offices — a distinct structural class worth separating out.

## Net characterization

These fails are **real**: the parser's body total for the block doesn't
match what the printed banner says. Unlike the BANNER NET PAYROLL
fails — where 79% are explained by FY-boundary cross-year bookings and
are tagged `fy_boundary_pattern` (item 6b) — the ORGANIZATION TOTALS
fails don't have a single structural alibi. They're the genuine review
queue, falling into three classes:

1. **Whole-block parsing gaps (692, ~19%)** — `actual == 0`. The most
   actionable: a $0 body total unambiguously points at a specific block
   whose records didn't extract. Concentrated in expense-only committee
   blocks.
2. **Systematic Sgt at Arms undercounts (~7 blocks, ~$10M each)** —
   the largest dollar failures, repeating across every congress. A
   structural segmentation/aggregation issue on the biggest office.
3. **Mid-range drift ($1K–$100K, ~74%)** — the bulk. Likely a mix of
   lump-sum lines not reaching `parsed_grand_total`, subtotals whose
   rows fell outside the segment window, and float rounding at the
   <1% fringe. Each is small enough to need per-block inspection.

Item 6 left these untouched deliberately: the ORGANIZATION TOTALS fails
are the signal worth investigating, not the noise. The next step is
splitting class 1 (zero-actual gaps) from class 3 (mid-range drift) in
the reconciliation report, then drilling into the Sgt at Arms block
(class 2) to find where the ~$10M goes unattributed.

## Update (item 8): banner-only categories explain most of class 1 and all of class 2

Drilling into the Sgt at Arms FY2025 block in 119sdoc5 (start_page 404)
revealed the structural alibi for class 2: the block's banner summary
table prints 9 expense categories, but the office only itemized 4 of
them in the body. The other 5 (Transportation of Things, Rent/
Communications/Utilities, Printing and Reproduction, Supplies and
Materials, Land and Structures) appear **only** on the banner summary
table — they have zero itemized rows in the block's 81 data pages.
Their banner-period values sum to $15,416,333.45, which is exactly the
ORG TOTALS gap ($45,268,763.29 expected vs $29,852,429.84 actual).

The same structural pattern explains class 1 (zero-actual whole-block
gaps). The "EMERGENCY APPROPRIATION P.L. 109-13" block in 117sdoc8 (max
$7.5M, the audit's class-1 poster child) is a 1-page banner-only block:
the banner prints one category (Land and Structures, -$7,500,000) with
no body rows. The 692 zero-actual fails aren't "blocks whose records
didn't extract" — they're summary-only blocks where the office reported
a lump-sum expenditure without itemizing any line items.

`banner_checks` (senate_parser/reconcile.py) now detects this case:
when the ORG TOTALS gap equals the sum of banner summary categories
that have no matching check in the block body, the check downgrades
from `fail` to `warn` with `context='banner_only_categories'` in the
reconciliation report. The captured categories' actuals must reconcile
to their banner values (residual within OK_TOLERANCE), so genuine
per-category parsing bugs stay `fail`.

Snapshot impact (modern era, 7 docs):

| doc | OLD fail | NEW fail | NEW warn | downgrades |
|---|---|---|---|---|
| 117sdoc8 | 413 | 187 | 226 | 226 |
| 118sdoc11 | 566 | 269 | 255 | 297 (254 ORG + 43 NET PAYROLL) |
| (others pending regen) | | | | |

The downgrades are real: the parser captured everything that was
itemized; the gap is structural (the office didn't itemize). What's
left as `fail` after the downgrade is the genuine review queue —
per-category parsing bugs where the captured categories' actuals don't
reconcile to their banner values.

A side effect of the same fix: tightening `period_value_near`'s row
tolerance from 8.0pt to 4.0pt (necessary to keep adjacent category
rows from polluting each other's period value) also fixed 43 BANNER
NET PAYROLL checks in 118sdoc11 that were previously `fail` because
the 8pt window caught the next row's amount. The Sgt at Arms block in
119sdoc5 was one of these — the BANNER NET PAYROLL check had been
comparing the body's $6,516.93 against the banner's *Travel* value
($384,191.43) instead of the banner's *Net Payroll* value.

## Update (item 9): captured categories' banner-vs-body mismatches explain the next 646

After item 8, 487 ORG TOTALS `fail` checks remained across the 7 modern
docs. The next pass addressed class 3 (mid-range drift, $1K–$100K off):
cases where the parser *did* itemize every category in the body, but
the body's printed subtotal for one or more categories disagrees with
the banner's value for the same category — a source-side internal
inconsistency in the Senate's own report (the body prints $X, the
banner prints $Y, for the same category in the same block).

The math is an identity when every captured category's body check
passes: `parsed_grand_total = Σ |body_subtotal|` for captured
categories, so

    gap = |banner_org_totals| − parsed_grand_total
        = Σ (|banner_cat| − |body_subtotal|) for captured cats
        + Σ |banner_cat| for uncaptured cats (banner-only categories)
        = captured_bvb_diff + uncaptured_sum

When the residual `|gap − uncaptured_sum − captured_bvb_diff|` is within
`OK_TOLERANCE`, the gap is fully explained by structural source-side
discrepancies (banner-only categories + body-vs-banner mismatches on
captured categories), not a parser bug. The check downgrades from
`fail` to `warn` with `context='captured_bvb_mismatch'`.

The canonical case (SENATOR MARK KELLY FY2022, 118sdoc2 p20):
banner NET PAYROLL EXPENSES = $1,072.96 but the body prints $295.56
(PERSONNEL BENEFITS lump sum, zero salary rows) — a $777.40 source-side
mismatch. Three other categories (RENT $6,555.16, PRINTING $0,
SUPPLIES $975.45) are banner-only. Gap = $8,308.01 = $777.40 captured
bvb + $7,530.61 uncaptured. Parser captured everything itemized;
downgrade to warn.

**Bail-out**: the equation `parsed_grand_total = Σ |body_subtotal|`
breaks down when any captured category's body check is `fail` or
`warn` (the itemized rows don't sum to the printed subtotal). The
failing check's `|actual − expected|` residual is an unaccounted term
that could mask a real per-category parsing bug as a structural
mismatch, so `_captured_bvb_discrepancy` returns `None` and the check
stays `fail` via the item 8 path.

**Zero_records handling**: a `zero_records` body check (normally-
itemized label, body printed a subtotal but the parser captured no
rows — a verified-legitimate lump-summed adjustment per the audit)
does NOT contribute its printed subtotal to `parsed_grand_total` (no
rows, no lump_sum added). The body_subtotal for the equation is 0,
not `|c.expected|` — otherwise captured_bvb is understated and the
check stays fail when it should downgrade.

Snapshot impact (modern era, 7 docs):

| doc | Item 8 fail | Item 9 fail | Δ fail | Item 9 warn | downgrades |
|---|---|---|---|---|---|
| 117sdoc8 | 187 | 105 | -82 | 308 | 82 |
| 118sdoc11 | 269 | 182 | -87 | 342 | 87 |
| 118sdoc13 | 172 | 107 | -65 | 248 | 65 |
| 118sdoc2 | 237 | 137 | -100 | 378 | 100 |
| 119sdoc3 | 319 | 197 | -122 | 389 | 122 |
| 119sdoc5 | 198 | 122 | -76 | 289 | 76 |
| 119sdoc6 | 306 | 192 | -114 | 368 | 114 |
| **total** | 1,688 | 942 | **-646** | — | **646** |

After items 8 and 9, 942 ORG TOTALS `fail` checks remain across the 7
modern docs (down from 3,621 total / ~2,566 modern-era at the start of
this audit). The residual queue is now genuine per-category parsing
bugs — blocks where a captured category's body check fails (parser
mis-itemized) or where the gap can't be fully explained by structural
source-side discrepancies. The S.RES. committee inquiry blocks (class
331 in the residual analysis) are the remaining structural class:
their body rows are cumulative across fiscal years while the banner
is period-specific, so neither item 8 nor item 9 applies — those
need a separate item (10) to handle the cumulative-vs-period
template distinction.