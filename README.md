# Senate Disbursements Parser

A Python 3 tool for parsing and analyzing U.S. Senate disbursement reports. This project converts PDF reports from the Senate Clerk's office into structured CSV data for analysis.

**Background:** The U.S. Senate publishes quarterly disbursement reports showing how Senate offices spend taxpayer money. These reports are only available as PDFs, making bulk analysis difficult. This tool automates the extraction and parsing of these reports into machine-readable CSV format.

For more about this project, see the [original blog post](https://sunlightfoundation.com/blog/2014/08/05/now-its-easier-to-account-for-how-the-senate-spends-your-money/).

## Quick Start

### Prerequisites

- **Python 3.7+**
- **poppler-utils** (provides `pdftotext`)

```bash
# Ubuntu/Debian
sudo apt-get install poppler-utils

# macOS (Homebrew)
brew install poppler

# Verify installation
pdftotext -v
```

### Basic Usage

```bash
# 1. Download a Senate disbursement report
python3 download_reports.py --doc 118sdoc13

# 2. Process and verify the complete report (all volumes and pages)
python3 process_report.py 118sdoc13 --output-dir output/118sdoc13

# 3. Check the output
head output/118sdoc13/senate_data_cleaned.csv
```

## Features

- **Automated Download**: Fetch reports directly from govinfo.gov
- **PDF Parsing**: Extract and parse individual pages from PDF reports
- **Data Cleaning**: Standardize and format expense records
- **Multiple Report Formats**: Handles variations across different Congressional sessions
- **Python 3**: Modern, maintained codebase

## Repository Structure

```
senate_disbursements/
├── download_reports.py              # Download reports from govinfo.gov
├── process_senate_disbursements.py  # Main processing script (PDF → CSV)
├── data/                            # All report data and outputs
│   ├── 112_sdoc10/                  # Individual report directories
│   ├── 113_sdoc2/                   # (organized by Congress and doc number)
│   ├── 114_sdoc13/
│   ├── ...
│   └── all_years/                   # Consolidated CSVs across all reports
└── scripts/                         # Utility scripts
    ├── clean_files.py               # CSV cleaning utilities
    ├── parse_office_names.py        # Office name standardization
    └── get_all_headers.py           # Header extraction tools
```

## Download Reports

### Automated Download (Recommended)

```bash
# Download a specific report
python3 download_reports.py --doc 118sdoc13

# Download multiple reports
python3 download_reports.py --doc 118sdoc13 117sdoc10 114sdoc4

# Generate wget commands for manual download
python3 download_reports.py --doc 118sdoc13 --generate-commands
```

**Known Report IDs** (as of 2025):
- **118th Congress**: 118sdoc13, 118sdoc11, 118sdoc2
- **117th Congress**: 117sdoc10, 117sdoc2
- **116th Congress**: 116sdoc19, 116sdoc10, 116sdoc2
- **115th Congress**: 115sdoc20, 115sdoc7
- **114th Congress**: 114sdoc13, 114sdoc7, 114sdoc4
- **113th Congress**: 113sdoc25, 113sdoc22, 113sdoc17, 113sdoc2
- **112th Congress**: 112sdoc10, 112sdoc7, 112sdoc4

### Manual Download

Visit the [Senate Disbursement Reports](http://www.senate.gov/legislative/common/generic/report_secsen.htm) or search [govinfo.gov](https://www.govinfo.gov/app/collection/cdoc) for "Senate" disbursements.

## Process Reports

### Release-grade report processing (recommended)

```bash
python3 process_report.py 118sdoc13 --output-dir output/118sdoc13
```

This command discovers valid PDF volumes, reads their page counts from the
files, assigns cumulative `reference_page` offsets, runs the coordinate parser,
merges every output artifact, and writes a top-level provenance manifest. It
builds in a staging directory and renames that directory into place only after
the coverage gate passes; it refuses to overwrite an existing release.

The gate blocks publication when a requested page was not consumed, a data
page was not attached to a banner block, column calibration failed, a multi-page
data block produced nothing, or a normally itemized subtotal unexpectedly has
zero records. It also blocks on every unparsed line and hard row-audit
violation. Failed-run diagnostics are preserved in a hidden
`.REPORT.failed-TIMESTAMP` directory beside the requested output.

Known source exceptions must be approved by their exact key from
`coverage_report.csv`; broad reason-level suppressions are not supported:

```json
{
  "approved": [
    {
      "key": "118sdoc13|unexpected_zero_records|123|SENATOR EXAMPLE|2024|TRAVEL|100.0",
      "reviewer": "initials",
      "reason": "Compared with the printed page; legitimate lump-sum adjustment"
    }
  ]
}
```

Pass that reviewed file with `--exceptions path/to/exceptions.json`. Senator
bioguide matching is required by default; use `--no-bioguide` only as an
explicit, manifest-visible opt-out.

### Single-PDF processing (advanced/debugging)

```bash
python3 process_senate_disbursements.py <pdf_file> --start <start_page> --end <end_page>
```

**Example:**
```bash
python3 process_senate_disbursements.py data/114sdoc13/GPO-CDOC-114sdoc13.pdf --start 18 --end 2264
```

### Finding the Page Range

1. Open the downloaded PDF
2. Find where **itemized expenses** begin (detailed line items with document numbers, dates, payees)
3. Find where they end (usually before summary sections)
4. Use these page numbers for `--start` and `--end`

**Tip:** Itemizations typically start around page 15-20 and end hundreds or thousands of pages later.

### Advanced Options

```bash
# Skip page extraction (if already done)
python3 process_senate_disbursements.py file.pdf --start 18 --end 2264 --skip-extract

# Skip CSV cleaning
python3 process_senate_disbursements.py file.pdf --start 18 --end 2264 --skip-clean

# Custom output directory
python3 process_senate_disbursements.py file.pdf --start 18 --end 2264 --output-dir my_output
```

## Output Files

The processing script generates three files:

1. **senate_data.csv** - Raw parsed data with all extracted fields
2. **senate_data_cleaned.csv** - Cleaned and formatted data with standardized headers:
   - `source_doc` - Document identifier (e.g., "114sdoc13")
   - `senator_flag` - 1 if senator's office, 0 otherwise
   - `senator_name` - Senator name (if applicable)
   - `bioguide_id` - Bioguide ID for senators (matched from congress-legislators data)
   - `raw_office` - Full office description
   - `funding_year`, `fiscal_year`, `congress_number` - Temporal identifiers
   - `reference_page` - PDF page number
   - `document_number` - Transaction document number
   - `date_posted`, `start_date`, `end_date` - Transaction dates
   - `description` - Expense description/purpose
   - `salary_flag` - 1 if salary-related, 0 otherwise
   - `amount` - Dollar amount
   - `payee` - Payee name
3. **missing_data.json** - Lines that couldn't be parsed (usually wrapped text or formatting issues)

## Modern Format Reports (115th Congress and later)

Starting around the 115th Congress, the Senate's PDF layout changed in a
way that breaks `pdftotext -layout`: it can desynchronize columns, so a
line's amount can actually belong to a *different* row's payee (verified
on 118sdoc13 -- see `senate_parser/records.py` for details). The
`--format legacy` pipeline described above should only be used for
112th-114th Congress reports; for 115th and later, use:

```bash
python3 process_senate_disbursements.py data/118sdoc13/GPO-CDOC-118sdoc13-1.pdf \
  --start 1 --end 1495 --format modern
```

This routes to `senate_parser/`, which extracts words with their PDF
coordinates (via [Natural PDF](https://github.com/jsoma/natural-pdf)) and
reconstructs table rows geometrically instead of trusting a reflowed text
layout. It also:

- Segments the page stream into office/account blocks at each banner page
  before parsing any rows, rather than parsing line-by-line -- this is
  what makes cross-page continuations and office/senator identification
  reliable.
- **Reconciles every block's itemized rows against the report's own
  printed subtotals** (e.g. "OTHER CONTRACTUAL SERVICES $80,325.38")
  before publishing them. Rows in a segment that doesn't sum to its
  printed figure are written to `quarantine.csv` instead of
  `senate_data_cleaned.csv` (per segment -- a failing category no longer
  holds back the block's other, reconciled categories), with the
  discrepancy recorded in `reconciliation_report.csv`.
- **Gets a second opinion on every failing segment** before quarantining:
  an independent re-sum of the amount column straight from the page
  geometry, bypassing the record classifier. When the independent sum
  matches the parsed rows but not the printed subtotal, the discrepancy
  is in the source document itself -- the report's own itemization
  doesn't add up to its own printed total (verified real: the INTERN
  COMPENSATION - BLACKBURN block in 118sdoc13 prints 14 rows summing to
  $24,774.31 under a printed subtotal of $24,745.43). Those rows publish
  tagged `source_mismatch` rather than being quarantined. When the
  independent sum instead sides with the printed figure, the rows stay
  quarantined and the run's `audit_report.csv` flags a likely parser bug
  (`second_opinion_disagrees`).
- **Cross-checks each block against its banner page** (advisory): the
  banner's summary table prints the period's Net Payroll Expenses and
  ORGANIZATION TOTALS independently of the inline listing; both are
  compared (basis `banner` in `reconciliation_report.csv`, per-block
  `banner_status` in `block_summaries.csv`). Every missing or discrepant
  banner check is also emitted as a clearly labeled warning in
  `coverage_report.csv`, with an exact key for review. These warnings never
  gate publishing: historical reports have legitimate source-side residuals
  and absent banner components, so inline subtotals and the second-opinion
  process remain the release authority.

For release output, do not concatenate multi-volume reports manually. Use
`process_report.py`, which records the source volume on every page-ledger row
and makes `reference_page` report-wide by applying measured cumulative page
offsets.

Outputs, in `--output-dir`:

- **senate_data_cleaned.csv** -- the legacy 17-column schema (see below)
  plus two validation columns: `validation_status` says what happened
  when this row's segment was checked against the report's own printed
  subtotal (`ok` reconciled within $0.01 / `warn` within $1 / `unchecked`
  no covering subtotal exists -- e.g. rows after a block's final
  subtotal / `source_mismatch` the segment doesn't sum to its printed
  subtotal but an independent re-sum confirms the rows are faithful
  transcriptions, i.e. the source's own itemization disagrees with its
  own printed total), and `category` is that subtotal's label (e.g.
  "TRAVEL AND TRANSPORTATION OF PERSONS"). When concatenating with
  legacy 112-114 files, backfill their missing columns with
  `unvalidated`.
- **quarantine.csv** -- same schema, for rows of segments that failed
  reconciliation *and* couldn't be cleared by the second opinion.
  Review before deciding whether to hand-fix or drop.
- **reconciliation_report.csv** -- one row per printed subtotal checked:
  office, funding year, label, expected vs. actual amount, status, and
  for failing segments the `second_opinion` verdict (`source_mismatch` /
  `parser_suspect` / `inconclusive`) with its `independent_sum`.
  `no_records` means the subtotal is an always-lump-sum budget figure
  with no itemized rows behind it (e.g. "PERSONNEL BENEFITS") -- expected,
  not a failure, and its printed amount is folded into the NET PAYROLL
  EXPENSES rollup check's basis. `zero_records` is the same situation on
  a normally itemized label; every historical case is a
  verified-legitimate lump-summed adjustment (e.g. a post-departure
  payroll correction), but it's kept distinct so a row-loss regression
  is countable. `unchecked` (basis `trailing`) reports rows after the
  block's final subtotal that no check covers. Basis `banner` rows are
  the advisory banner-page cross-checks.
- **unparsed.jsonl** -- lines that didn't classify as a record, subtotal,
  or continuation. Any entry blocks release pending a parser fix or an exact
  reviewed exception.
- **block_summaries.csv** -- per office/funding-year block: status,
  banner-check status, record count, rows quarantined, dollars checked
  vs. unchecked, amount-parse failures, page/subtotal counts, pages skipped.
- **page_ledger.csv** -- one row for every requested PDF page, including its
  classification, source volume/page, report-wide page number, and block
  assignment. This proves that pages discarded as cover/TOC material were
  seen rather than silently skipped.
- **coverage_report.csv** -- release-gating completeness findings, including
  every unparsed line and hard row-audit violation, with exact exception keys.
- **unmatched_senators.csv** -- senator-office blocks whose name didn't
  resolve to a bioguide ID, with the failure mode (`unmatched` / `error` /
  `no_year`). The pipeline warns loudly if the row-weighted match rate
  falls below 90% (clean reports run 93-96%).
- **audit_report.csv** -- row-level field checks. Malformed field failures
  (unparseable amounts/dates, contaminated office names, and bad funding
  years) block release; duplicate-row notices remain advisory. Nothing is
  dropped; the report is still a review queue.
- **manifest.json** -- per-volume runs record source-PDF SHA-256, page range,
  parser git commit/dirty state, timestamp, tolerances, coverage counts, and
  run counts. The report-level manifest additionally records all volumes,
  measured page counts/offsets, rejected invalid PDF candidates, dependency
  lock hash, reviewed exception file hash, and hashes of merged outputs.

### Regression testing

`uv run pytest` runs the fast fixture suite (committed golden pages, no
PDFs needed; also runs in CI). `uv run pytest -m slow` re-parses every
locally available report end-to-end and diffs block/record/dollar/check
statistics against committed snapshots in `tests/snapshots/` -- run this
before publishing regenerated data. After an intentional parser change,
regenerate with `UPDATE_SNAPSHOTS=1 uv run pytest -m slow` and review the
snapshot diff.

### Known source-data caveat: `source_mismatch` rows

Some segments' printed itemization genuinely does not add up to the
report's own printed subtotal. Two verified classes:

- **Small payroll residuals**: e.g. the INTERN COMPENSATION - BLACKBURN
  block in 118sdoc13 prints 14 rows summing to $24,774.31 under a
  printed subtotal of $24,745.43 -- and the banner page agrees with the
  subtotal, not the rows. The Sergeant at Arms' ~$53M payroll segment in
  118sdoc11 is off by $101.20 the same way. The likely mechanism is
  adjustments processed against the total but not printed as rows.
- **Fiscal-year-boundary Travel gaps**: reports whose period starts at a
  federal fiscal-year boundary (e.g. 118sdoc11, 10/01/2023-03/31/2024)
  show `TRAVEL AND TRANSPORTATION OF PERSONS` rows summing to far more
  than the printed subtotal across many offices (verified: Senator Mike
  Lee's 2023 block sums to $16,783.35 against a printed $1,172.69). Many
  affected rows carry prior-fiscal-year service dates -- obligated
  against the old year's authorization, listed but not counted toward
  this period's net expenditure. 118sdoc13 (mid-fiscal-year) shows zero
  Travel failures, which fits.

In both classes the individual rows are faithful transcriptions of what
the Senate printed; it's the source's category total that disagrees with
its own itemization. When the pipeline's independent second-opinion
re-sum confirms this (it matches the parsed rows, not the printed
subtotal), the rows are published with
`validation_status = source_mismatch` and the residual stays documented
in `reconciliation_report.csv`. Sum such a category yourself and you'll
match the rows, not necessarily the Senate's printed total -- cite
accordingly.

### Report templates: modern vs. old (112th-114th)

`senate_parser/records.py` calibrates columns per page, anchored on that
page's own header row. Two modes, selected automatically from the
congress number in the document name (`pipeline.run`'s `template`
parameter overrides):

- **modern** (115th Congress onward, verified on 117sdoc8 through
  119sdoc6): boundaries are fixed deltas from the DESCRIPTION header,
  identical relative geometry across page sizes.
- **anchor** (112th-114th): the older table generator's relative
  geometry differs, and a single document mixes two header layouts
  (committee pages shift PAYEE/DESCRIPTION further right), so every
  boundary derives from that page's own seven header anchors with
  measured header-to-data offsets.

The old era also *reports* differently, so its reconciliation is
type-aware (`reconcile._reconcile_block_typed`), all verified penny-exact
against real blocks:

- All of a block's subtotals print at the END of the listing (a
  committee's TRAVEL subtotal prints before its payroll subtotals), so
  salary and expense records accumulate in separate streams.
- The payroll category lines (PERSONNEL COMP. FULL-TIME PERMANENT /
  OTHER PERSONNEL COMPENSATION) *partition* the roster's total rather
  than covering distinct row runs -- they're recorded as non-gating
  `component` checks (basis `payroll_component`), and the roster
  validates as a whole against NET PAYROLL EXPENSES (plus the true
  lump-sums, PERSONNEL BENEFITS and RE-EMPLOYED ANNUITANTS).
- Payroll adjustments are sometimes *counted* against one funding year
  while the rows print in a sibling year's roster (verified: Cantwell's
  FY2015 block prints OPC $102,388.98 with no rows; her FY2016 roster
  exceeds its own printed NET by exactly that amount). When an office's
  failing NET PAYROLL residuals cancel across its blocks, the rows
  publish as `source_mismatch` with the distinct `cross_year` verdict in
  the reconciliation report. Residuals that pair with a *different
  report's* period can't be verified from one document and stay
  quarantined.

### Known issue: spurious `-3.pdf` volume in some downloads

Some multi-part downloads include a `<doc>-3.pdf` file that is not a PDF
at all -- it's an HTML error page saved with a `.pdf` extension (verified
byte-identical, via `md5`, across 117sdoc8, 118sdoc11, and 119sdoc3).
Ignore it; process only `-1.pdf` and `-2.pdf` (and not the combined
`<doc>.pdf`, which reintroduces the cross-volume TOC/page-numbering
problem the per-volume approach avoids). Likely a `download_reports.py`
bug worth fixing separately.

## Understanding the Data

### Expense Types

The parser identifies different expense record formats:

1. **Five data line** - Full itemized expenses with document number, dates, payee, description, and amount
2. **Three data line** - Salary/personnel entries with name, position, and amount
3. **Missing date line** - Expenses with incomplete date information

### Continuation Lines

Some expense descriptions span multiple lines. The parser automatically detects and appends continuation lines (marked with " + " in the output).

## Bioguide ID Matching

The parser automatically adds **bioguide IDs** to senator records by matching senator names and years against the [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators) database.

### Automatic Matching (New Reports)

When processing new reports, bioguide IDs are automatically added during the cleaning step:

```bash
python3 process_senate_disbursements.py file.pdf --start 18 --end 2264
# Bioguide IDs are automatically added to senate_data_cleaned.csv
```

### Adding Bioguide IDs to Existing Files

For existing cleaned CSV files, use the `add_bioguide_ids.py` utility:

```bash
# Install required dependencies
pip3 install -r requirements.txt

# Add bioguide IDs to a single file
python3 add_bioguide_ids.py data/114_sdoc13/senate_data_cleaned.csv

# Add bioguide IDs to all files in data/all_years/
python3 add_bioguide_ids.py --all

# Add bioguide IDs to files matching a pattern
python3 add_bioguide_ids.py --pattern "data/*/senate_data_cleaned.csv"
```

The script will:
- Download legislator data from congress-legislators (cached for 7 days)
- Match senator names to bioguide IDs based on name and year
- Update the CSV files in-place with a new `bioguide_id` column
- Display match statistics and warnings for unmatched senators

### How Bioguide Matching Works

1. **Name Matching**: Senator names are normalized and matched against official names, nicknames, and name variations
2. **Time Period Matching**: The funding year or fiscal year is used to ensure the senator was serving during that time
3. **Caching**: Legislator data is cached locally for 7 days to speed up repeated operations

### Example Output

```bash
python3 add_bioguide_ids.py data/114_sdoc13/senate_data_cleaned.csv

Initializing bioguide matcher...
Downloading legislators-current.yaml...
Downloading legislators-historical.yaml...
Loaded 2017 senators

Processing: data/114_sdoc13/senate_data_cleaned.csv
  Total rows: 50000
  Senator rows: 12500
  Matched: 12450
  Unmatched: 50
  Match rate: 99.6%
```

## Troubleshooting

### "Expected 1 header, found 0"

This usually means the page range includes non-itemization pages (cover pages, summaries, or blank pages). Adjust your `--start` and `--end` values.

### Missing Data

Check `missing_data.json` to see unparsed lines. Common causes:
- Text wrapping issues in the PDF
- Non-standard formatting
- Special characters

Most missing data are continuation lines that don't affect core expense records.

### PDF Extraction Issues

```bash
# Verify pdftotext is installed
which pdftotext

# Test manually
pdftotext -f 1 -l 1 -layout yourfile.pdf test.txt

# Check PDF integrity
pdfinfo yourfile.pdf
```

## Complete Workflow Example

```bash
# 1. Download report
python3 download_reports.py --doc 118sdoc13

# 2. Open PDF and identify itemization page range
# (Look for pages with detailed expenses, document numbers, dates)

# 3. Process the PDF
python3 process_senate_disbursements.py data/118sdoc13/GPO-CDOC-118sdoc13-1.pdf --start 20 --end 2500

# 4. Verify output
wc -l data/118sdoc13/senate_data_cleaned.csv
head -20 data/118sdoc13/senate_data_cleaned.csv

# 5. Review any parsing issues
less data/118sdoc13/missing_data.json
```

## Programmatic Usage

Import and use functions in your own scripts:

```python
from process_senate_disbursements import extract_pages, parse_pages, clean_csv

# Extract pages
extract_pages('my_file.pdf', 18, 2264, output_dir='pages')

# Parse pages
parse_pages(18, 2264, pages_dir='pages',
            out_file='senate_data.csv',
            missing_file='missing_data.json')

# Clean CSV
clean_csv('114sdoc13', 'senate_data.csv', 'senate_data_cleaned.csv')
```

## Legacy Process (Manual)

For historical reference, the original manual process involved:
1. Downloading PDFs manually
2. Creating individual directories per report
3. Copying `rip_pages.py` and `read_pages.py` to each directory
4. Manually editing page ranges in each script
5. Running scripts individually

The modernized tools (`download_reports.py` and `process_senate_disbursements.py`) automate this entire workflow.

## Data Analysis

The `data/all_years/` directory contains:
- Consolidated CSVs combining multiple reports
- Cleaned versions with standardized formatting
- Utility scripts for cross-report analysis

Use these for analyzing spending trends across Congressional sessions.

## Contributing

This project parses Senate disbursement PDFs that can vary in format across different time periods. If you encounter parsing errors:

1. Check `missing_data.json` for specific issues
2. Verify the page range excludes non-itemization pages
3. Report persistent issues with the specific report ID and error details

## Changes from Original Version

- ✅ **Python 3** - Modern, maintained Python
- ✅ **Automated downloads** - No manual PDF hunting
- ✅ **Unified processing** - Single command instead of per-directory scripts
- ✅ **Better error handling** - Clear messages and progress indicators
- ✅ **Organized structure** - Separate data and scripts
- ✅ **Preserved compatibility** - Same parsing logic and CSV output format

## License

See [LICENSE](LICENSE) file for details.

## Resources

- [Senate Disbursement Reports](http://www.senate.gov/legislative/common/generic/report_secsen.htm)
- [govinfo.gov Congressional Documents](https://www.govinfo.gov/app/collection/cdoc)
- [Original Sunlight Foundation Blog Post](https://sunlightfoundation.com/blog/2014/08/05/now-its-easier-to-account-for-how-the-senate-spends-your-money/)
