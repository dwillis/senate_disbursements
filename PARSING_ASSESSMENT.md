# Assessment: Work Remaining to Make Senate Disbursements Parsing Successful

## TL;DR

- **118sdoc13 looks done by row count, but the cleaned CSV is not yet
  analytically useful** — every row's `raw_office` is a top-matter dump, so
  senator names, bioguide IDs, and office grouping are all broken.
- **`salary_flag` is inverted** (1 means expense today). Trivial fix, big
  impact.
- **~26k of the 28k "missing" lines are recoverable continuations** (job
  titles, orphan amounts) once the merge logic spans pages and salary
  records.
- **Coverage is patchy across the corpus.** 114sdoc7, 114sdoc13 have raw
  CSVs but no cleaned output and aren't in `data/all_years/`. 115/116/117
  reports haven't been parsed at all.
- **No amount-vs-PDF reconciliation exists**; all "improvement" claims so
  far are based on row counts. Numbers could be wildly wrong without
  anyone noticing.
- **Estimate:** ~3–5 focused days to fix items 1–5 below and produce a
  trustworthy 118sdoc13 dataset; another ~2–4 days to backfill the
  missing reports and consolidate `data/all_years/`.

---

## Context

This repo parses U.S. Senate disbursement PDFs (govinfo.gov) into CSVs. Older
formats (113–114 Congress) parse fine and have ~46k rows of mostly clean data.
Recent work focused on the 118sdoc13 (118 Congress) report, which uses a new
PDF layout. Multiple incremental fixes have been merged (header detection,
encoding fallback, flexible regexes, format auto-detection, multi-line
assembly, page ordering, bioguide ID matching).

The branch reports a "78–91% capture rate" (54,719 raw rows; 28,788 missing
items still in `missing_data.json`). However, **a closer look at
`data/118sdoc13/senate_data_cleaned.csv` shows the dataset is not yet useful
for analysis**. This document inventories what is broken and what is left to
do before the data can be published.

The intent of this task is *assessment, not implementation*: the user asked
for a read-out of the remaining work, not for the work itself.

---

## Current state (118sdoc13)

| Artifact | Status |
|---|---|
| `senate_data.csv` | 54,719 raw rows |
| `senate_data_cleaned.csv` | 54,719 rows + 2 header lines |
| `missing_data.json` | 2,189 page-groups / 28,788 unparsed items |
| `senate_data_recovered.csv` | 8,446 rows (older recovery script — superseded) |

## Coverage across the corpus

The README advertises 22 known report IDs across 112–118 Congress. Actual
state on this branch:

| Report | Raw CSV | Cleaned CSV | In `data/all_years/` | Notes |
|---|---|---|---|---|
| 112sdoc4 | ✅ | ✅ | ✅ | older format, OK |
| 112sdoc7 | ✅ | ✅ | ✅ | older format, OK |
| 112sdoc10 | ✅ | ✅ | ✅ | older format, OK |
| 113sdoc2 | ✅ | ✅ | ✅ | older format, OK |
| 113sdoc17 | ✅ | ✅ | ✅ | older format, OK |
| 113sdoc22 | ✅ | ✅ | ✅ | older format, OK |
| 113sdoc25 | ✅ | ✅ | ✅ | older format, OK |
| 114sdoc4 | ✅ | ✅ | ✅ | older format, OK |
| 114sdoc7 | ✅ | ❌ | ❌ | raw only; never cleaned |
| 114sdoc13 | ✅ (53k) | ❌ | ❌ | raw only; never cleaned |
| 115sdoc7 / 115sdoc20 | ❌ | ❌ | ❌ | not downloaded |
| 116sdoc2 / 10 / 19 | ❌ | ❌ | ❌ | not downloaded |
| 117sdoc2 / 10 | ❌ | ❌ | ❌ | not downloaded |
| 118sdoc2 / 11 | ❌ | ❌ | ❌ | not downloaded |
| **118sdoc13** | ✅ (54.7k) | ⚠️ generated but unusable | ❌ | this branch's focus |

So out of 22 advertised reports, **8 are fully processed** (112–113 + 114sdoc4),
**2 are half-done** (114sdoc7/13: raw CSV only, no cleaning), **11 haven't
been downloaded**, and **1 (118sdoc13) has data that needs the fixes below
to be trustworthy**.

`data/all_years/headerlist.csv` is the unioned list of distinct office
strings across the cleaned files — useful for QA but stale once new
reports are added.

---

## Critical defects (block "useful data")

These are the issues that make the cleaned CSV unfit for analysis today.

### 1. `raw_office` is the entire top-matter dump, not an office name

**Symptom (every row in 118sdoc13):**
```
"DETAILED AND SUMMARY STATEMENT OF PRESIDENT PRO TEMPORE (D) DESCRIPTION
 Funding Year 2024 EXPENSE ALLOWANCES OF THE VICE PRESIDENT, Authorization
 PRESIDENT PRO TEMPORE, MAJORITY AND Supplementals MINORITY LEADERS,
 MAJORITY AND MINORITY Transfers WHIPS … DOCUMENT NO. DATE PAYEE NAME
 OBLIGATION/SERVICE POSTED DATES START"
```
- `process_top_matter()` (`process_senate_disbursements.py:181`) takes the
  left 80 chars of every line above the `DOCUMENT NO. DATE PAYEE …` header
  and concatenates it. In the 118 layout the *right* column of the top
  matter (descriptions of the org, summary totals, column legend) bleeds
  into the same 80-char window, producing the giant blob.
- All downstream fields derived from the office string break:
  - `senator_name` becomes `"DETAILED AND SUMMARY STATEMENT  AMY KLOBUCHAR DESCRIPTION"`
    (44,264 rows are flagged senator=1 but the names are mangled).
  - `funding_year` / `fiscal_year` / `congress_number` are picked up by
    regex sometimes, but the `raw_office` field itself is unusable for
    grouping or display.

### 2. Bioguide matching collapses because senator names are garbled

`add_bioguide_ids` matches against the (mangled) `senator_name`, so almost no
118sdoc13 senator rows get a bioguide ID even though 44k rows are flagged.
Older reports (112–114) match successfully because their `raw_office`
extraction is clean.

### 3. `salary_flag` semantics are wrong

`process_senate_disbursements.py:602`:
```python
salary_flag = 1 if start_date != '' or end_date != '' else 0
```
The README says `salary_flag = 1 if salary-related, 0 otherwise`. Salary
records *don't* have start/end dates, so this logic is inverted — every
salary row currently shows `salary_flag=0`, every expense row `salary_flag=1`.
Comparison with the 113/114 cleaned files confirms the inversion.

### 4. Senate Page program rows are mis-classified

Pages like `pages/layout_125.txt` (Senate Page program) have lines:
```
  PHIFER, RUSSELL MILES                  PAGE TO JUN. 7        $7,253.72
```
These are salary-style, but the parser fits `expense_record_flexible` against
them, putting the name in `payee`, `"PAGE FROM JUN. 10 TO JUN. 21"` in
`description`, and tagging them `five data line`. This affects thousands of
rows under the Sergeant at Arms / Senate Pages organization.

### 5. ~28,800 unparsed items in `missing_data.json`

Categorized:
- **~26,200 ALL-CAPS continuation lines** — position titles for the line
  above, e.g. `STAFF ASSISTANT FROM JUN. 3`, `PROFESSIONAL STAFF MEMBER FROM SEP. 3`,
  `POLICY ADVISOR`. These should be merged into the prior salary record's
  `description` (job title) and date fields.
- **~1,070 amount-only lines** — orphaned `$X,XXX.XX` continuations whose
  parent record is missing an amount.
- **~970 date-first lines** — e.g. `06/06/2024 FURNISHINGS MAINT`; expense
  continuations.
- **~260 "Net Payroll Expenses"** subtotal rows that should be filtered
  (they're not in the SUBTOTAL_PATTERNS list).
- ~280 other.

The `expense_with_leading_date` and `amount_only_line` patterns *are* defined
to attach these to the previous record, but the orchestration in
`process_data_lines()` (lines 217–394) only attaches them when the prior line
already produced a record on this page; cross-page continuations and
positions-on-second-line fail through to `missing_data`.

### 6. CSV duplication / `senator_data_recovered.csv` is stale

The recovery script's output (8,446 rows) and the main parser's output
(54,719 rows) coexist in `data/118sdoc13/`, with a `.backup` of each and a
parallel `recover_missing_data.py`. The main parser now subsumes the
recovery flow, so the recovered CSV is duplicate, partial data and is
likely to confuse downstream consumers.

---

## Secondary issues

- **`data/all_years/` is incomplete.** It contains 112sdoc4/7/10,
  113sdoc2/17/22/25, 114sdoc4 — but no 114sdoc7/13, no 115/116/117, and no
  118sdoc13. The README advertises consolidated cross-year data; today it's
  effectively only 112–113 plus one 114 report.
- **Schema drift.** `data/all_years/*_cleaned.csv` files lack the
  `bioguide_id` column that the new parser writes; the old-format header is
  16 columns, the new one is 17. Concatenation across years requires
  reconciliation.
- **No automated tests / validation.** Nothing checks that totals in the
  cleaned CSV reconcile with the per-office subtotals in the source PDF.
  All "improvement" claims so far are based on row counts, not amounts.
- **`process_top_matter()` heuristics are fragile.** The 80-char column
  width is a magic number and the `.replace('DE ', '')` /
  `.replace('DETAI ', 'DETAILED ')` cleanup masks rather than fixes the
  root cause. Needs a layout-aware approach (e.g. read only lines from the
  left half of the page that match office-style ALL-CAPS headers, stop on
  first column-legend keyword).
- **`missing_data.json` blows up to 6 MB** on 118sdoc13. Useful for
  debugging but a downstream-noise problem when shipped in the repo.
- **Python 2 leftovers.** `scripts/parse_office_names.py`,
  `scripts/get_all_headers.py`, and the per-report `read_pages.py` files
  (under `data/112_sdoc*/`, `data/113_sdoc*/`, `data/114_sdoc*/`) still use
  Python 2 `print "…"` syntax. They're superseded by
  `process_senate_disbursements.py` but currently sitting in the repo as
  broken code that won't run. README acknowledges them as "legacy" but
  doesn't mark them as removed.
- **Per-report directories carry duplicate scaffolding.** Each older
  `data/<report>/` dir contains its own copy of `rip_pages.py`,
  `read_pages.py`, `run.py`, `format_csv.py`, `__init__.py` — ten copies
  of effectively the same legacy code.

---

## Recommended order of work

The list below is prioritized by impact on "produce useful data". Items
1–5 should land before declaring 118sdoc13 successful.

| # | Item | Effort | Risk | Impact |
|---|---|---|---|---|
| 1 | Rewrite `process_top_matter()` | M (½–1 day) | Low — well-scoped to one function | **High** — unblocks 2, 5, and downstream analysis |
| 2 | Fix `salary_flag` inversion | XS (minutes) | Low | High — touches every row |
| 3 | Reclassify Senate Page program rows | S (½ day) | Low — narrow heuristic | Medium — thousands of rows |
| 4 | Cross-line continuation merge for ~26k position lines | M (1 day) | Medium — easy to over-merge | High — collapses missing-data noise |
| 5 | Re-run cleaning + bioguide matching | XS | Low | Validates 1–4 |
| 6 | Amount-vs-subtotal reconciliation script | S (½ day) | Low | **High** — first real correctness check |
| 7 | Backfill 114sdoc7/13, 115, 116, 117, 118sdoc13 in `all_years/` | M (1–2 days) | Medium — schema reconciliation across formats | Medium |
| 8 | Repo cleanup (legacy py2 scripts, per-report scaffolding, backups) | S (½ day) | Low | Medium — reduces confusion |

Detail per item:

1. **Rewrite `process_top_matter()`** to extract a clean office name. The
   simplest correct approach: parse the page header column-by-column rather
   than slicing first 80 chars. Look for "OFFICE OF SENATOR …",
   "<COMMITTEE NAME>", or the bold left-column header line, and stop at the
   first occurrence of `Funding Year` / `Authorization` / `DOCUMENT NO.`.
   Compare against the older reports' clean output as a reference. The
   regex in `scripts/parse_office_names.py` (`SENATOR\s+([\w\.\s\,\(\)]+)\s+Funding Year`)
   captures the older convention and is a useful starting point — though
   the script itself is Python 2 and needs porting before reuse.
   *File:* `process_senate_disbursements.py:181-204`.

2. **Fix `salary_flag` inversion.** Rederive from `record_type` (`'three data line'` → 1)
   instead of date presence.
   *File:* `process_senate_disbursements.py:602`.

3. **Disambiguate Senate Page program rows from real expense rows.**
   The signal is "no document number in the leftmost column" + "amount in
   far right" + "ALL-CAPS NAME, FIRST" pattern. Route those to a salary
   record path. *File:* `process_senate_disbursements.py:217-394`.

4. **Recover the ~26k position-only continuation lines.** Extend the
   continuation logic so that an ALL-CAPS line under the position column
   (no doc number, no amount) is appended to the previous salary record's
   `description`, and an amount-only line below it fills its `amount`.
   Allow this to span page boundaries (carry "last record on prior page"
   forward). Also add `Net Payroll Expenses` to `SUBTOTAL_PATTERNS` so
   ~260 of the missing items get filtered rather than logged. *File:* same.

5. **Re-run cleaning** on 118sdoc13 once 1–4 are in, and re-run
   `add_bioguide_ids.py` — match rate should jump from ~0% to ~95%+.

6. **Validate amounts against PDF subtotals.** Add a small sanity-check
   script: for each office on each page, sum extracted amounts and compare
   to the subtotal lines (already detected by `SUBTOTAL_PATTERNS`).
   Anything that diverges by more than a few dollars is a parsing bug.
   This is the single most important new piece of infrastructure — without
   it, "improvements" remain anecdotal.

7. **Backfill `data/all_years/` for 114sdoc7/13, 115, 116, 117, 118sdoc13.**
   Re-run the (now-fixed) parser. Harmonize columns to include
   `bioguide_id`. Decide whether `data/all_years/` should be a flat
   directory of per-report CSVs (current pattern) or a single concatenated
   CSV with a `source_doc` column — the README implies the latter but the
   filesystem reflects the former.

8. **Clean up.**
   - Delete `data/118sdoc13/recover_missing_data.py`,
     `senate_data_recovered.csv`, and the `*.backup` files.
   - Either port `scripts/parse_office_names.py` and
     `scripts/get_all_headers.py` to Python 3 or remove them.
   - Drop the per-report `read_pages.py` / `rip_pages.py` / `run.py` /
     `format_csv.py` / `__init__.py` files inside `data/<report>/`
     directories — they're orphaned legacy code in 10 copies.
   - Fold `PARSING_IMPROVEMENTS.md`, `RECOVERY_REPORT.md`, and
     `performance_report.txt` into one `CHANGELOG.md`.
   - Move `missing_data.json` out of the committed dataset or gzip it.

---

## Verification

After implementing items 1–5, the success criteria are:

- `data/118sdoc13/senate_data_cleaned.csv`:
  - `raw_office` distinct values < ~150 (one per Senate office), and a
    spot-check of 10 rows shows clean office names like `"OFFICE OF
    SENATOR JOHN THUNE"` rather than the boilerplate dump.
  - `senator_flag=1` rows all have a non-empty `bioguide_id`
    (≥95% match rate).
  - `salary_flag=1` rows are salary records (no doc number, no dates).
- `data/118sdoc13/missing_data.json` contains < ~2,000 items
  (down from 28,788), and remaining items are genuine parser failures
  rather than expected continuation lines.
- A reconciliation script confirms that summed amounts per office/page
  match PDF subtotals within rounding error.
- Re-running `python3 process_senate_disbursements.py
  data/118sdoc13/GPO-CDOC-118sdoc13-1.pdf --start 20 --end 2500
  --skip-extract` regenerates the same CSV deterministically.

### Definition of done for the project as a whole

- All 22 advertised reports either have a cleaned CSV in `data/all_years/`
  or a tracked reason they were skipped.
- Schema is uniform across years (17 columns including `bioguide_id`).
- For each report, an automated check confirms summed amounts reconcile
  with the source-PDF subtotals to within $1.
- `missing_data.json` per report is < 1% of total parsed lines.
- README's "Known Report IDs" list matches what's actually in the repo.
- No Python 2 code remains in the tree.

---

## Files of interest

- `process_senate_disbursements.py` — main parser; almost all changes go here.
- `process_senate_disbursements.py:181-204` — `process_top_matter()` (defect 1).
- `process_senate_disbursements.py:217-394` — `process_data_lines()` (defects 3, 4, 5).
- `process_senate_disbursements.py:602` — `salary_flag` logic (defect 3).
- `bioguide_matcher.py`, `add_bioguide_ids.py` — bioguide matching; will
  start matching once defect 1 is fixed.
- `data/118sdoc13/PARSING_IMPROVEMENTS.md`,
  `data/118sdoc13/RECOVERY_REPORT.md`,
  `data/118sdoc13/performance_report.txt` — prior status reports
  (consistent with this assessment but optimistic on capture rate).
- `data/118sdoc13/recover_missing_data.py` — superseded; recommend deletion.
- `data/all_years/` — incomplete consolidation; needs backfill after fixes.
- `scripts/parse_office_names.py`, `scripts/get_all_headers.py` —
  Python 2 legacy; port or delete.
- `data/<report>/{rip_pages,read_pages,run,format_csv}.py` — per-report
  legacy scaffolding (10 copies). README marks the workflow as retired
  but the files remain.
- `download_reports.py` — works; would need updates to reference 115/116/117
  download URLs when those reports get added.
