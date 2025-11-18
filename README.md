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

# 2. Process the PDF (specify page range where itemizations appear)
python3 process_senate_disbursements.py data/118sdoc13/GPO-CDOC-118sdoc13-1.pdf --start 20 --end 2500

# 3. Check the output
head data/118sdoc13/senate_data_cleaned.csv
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

### Basic Processing

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

## Understanding the Data

### Expense Types

The parser identifies different expense record formats:

1. **Five data line** - Full itemized expenses with document number, dates, payee, description, and amount
2. **Three data line** - Salary/personnel entries with name, position, and amount
3. **Missing date line** - Expenses with incomplete date information

### Continuation Lines

Some expense descriptions span multiple lines. The parser automatically detects and appends continuation lines (marked with " + " in the output).

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
