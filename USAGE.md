# Senate Disbursements Parser - Usage Guide

## Updated Python 3 Version

This codebase has been updated to **Python 3** with improved functionality and ease of use.

## Prerequisites

### System Requirements
- Python 3.7 or later
- `poppler-utils` package (provides `pdftotext`)

### Installing poppler-utils

**Ubuntu/Debian:**
```bash
sudo apt-get install poppler-utils
```

**macOS (with Homebrew):**
```bash
brew install poppler
```

**Verify installation:**
```bash
pdftotext -v
```

## Quick Start

### Method 1: Using the Unified Script (Recommended)

The new `process_senate_disbursements.py` script provides a simple, all-in-one solution:

```bash
python3 process_senate_disbursements.py <pdf_file> --start <start_page> --end <end_page>
```

**Example:**
```bash
python3 process_senate_disbursements.py 114_sdoc13/GPO-CDOC-114sdoc13.pdf --start 18 --end 2264
```

This will:
1. Extract pages 18-2264 from the PDF
2. Parse the extracted text
3. Generate structured CSV files
4. Save everything to the PDF's directory

### Method 2: Using Legacy Per-Directory Scripts

Each directory (e.g., `114_sdoc13/`) contains individual scripts:

```bash
cd 114_sdoc13
python3 run.py
```

## Finding the Correct Page Range

1. Download the Senate disbursement "full report" PDF from [here](http://www.senate.gov/legislative/common/generic/report_secsen.htm)
2. Open the PDF and find where **itemized expenses** begin (detailed line items with document numbers, dates, payees)
3. Find where they end (usually before summary sections)
4. Note these page numbers - they are your `--start` and `--end` values

**Example from 114_sdoc13:**
- Itemizations start on page 18
- Itemizations end on page 2264

## Output Files

The script generates three files:

1. **senate_data.csv** - Raw parsed data with all fields
2. **senate_data_cleaned.csv** - Cleaned and formatted data with headers:
   - `source_doc` - Document identifier (e.g., "114sdoc13")
   - `senator_flag` - 1 if senator's office, 0 otherwise
   - `senator_name` - Senator name (if applicable)
   - `raw_office` - Full office description
   - `funding_year` - Funding year
   - `fiscal_year` - Fiscal year (if specified)
   - `congress_number` - Congress number
   - `reference_page` - PDF page number
   - `document_number` - Transaction document number
   - `date_posted` - Date transaction posted
   - `start_date` - Service/expense start date
   - `end_date` - Service/expense end date
   - `description` - Expense description/purpose
   - `salary_flag` - 1 if salary-related, 0 otherwise
   - `amount` - Dollar amount
   - `payee` - Payee name

3. **missing_data.json** - Lines that couldn't be parsed (usually wrapped text or formatting issues)

## Advanced Options

### Skip Page Extraction (if already done)
```bash
python3 process_senate_disbursements.py file.pdf --start 18 --end 2264 --skip-extract
```

### Skip CSV Cleaning
```bash
python3 process_senate_disbursements.py file.pdf --start 18 --end 2264 --skip-clean
```

### Custom Output Directory
```bash
python3 process_senate_disbursements.py file.pdf --start 18 --end 2264 --output-dir my_output
```

## Module-Level Usage

You can also import and use the functions in your own scripts:

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

## Understanding the Data

### Expense Types

The parser identifies different types of expenses:

1. **Five data line** - Full itemized expenses with:
   - Document number
   - Date posted
   - Payee
   - Start date
   - End date
   - Description
   - Amount

2. **Three data line** - Salary/personnel entries with:
   - Name
   - Position/title
   - Amount

3. **Missing date line** - Expenses with incomplete date information

### Continuation Lines

Some expense descriptions span multiple lines. The parser automatically:
- Detects continuation lines
- Appends them to the appropriate expense record
- Marks them in the output (look for " + " in descriptions)

## Troubleshooting

### "AssertionError: Expected 1 header, found 0"
This means a page doesn't have the expected header format. This can happen with:
- Summary pages
- Blank pages
- Cover pages

**Solution:** Adjust your page range to exclude these pages

### Missing Data
Check `missing_data.json` to see which lines couldn't be parsed. Common reasons:
- Text wrapping issues in the PDF
- Non-standard formatting
- Special characters

Most missing data represents continuation text that doesn't affect the core expense data.

### PDF Extraction Issues
If `pdftotext` isn't working:
1. Verify it's installed: `which pdftotext`
2. Test manually: `pdftotext -f 1 -l 1 -layout yourfile.pdf test.txt`
3. Check PDF isn't corrupted: `pdfinfo yourfile.pdf`

## Example Workflow

```bash
# 1. Download new Senate disbursement PDF
wget https://www.senate.gov/.../GPO-CDOC-115sdoc1.pdf

# 2. Create directory for it
mkdir 115_sdoc1
mv GPO-CDOC-115sdoc1.pdf 115_sdoc1/

# 3. Open PDF and identify page range (say pages 20-2500)

# 4. Process the PDF
python3 process_senate_disbursements.py 115_sdoc1/GPO-CDOC-115sdoc1.pdf --start 20 --end 2500

# 5. Check outputs
head 115_sdoc1/senate_data_cleaned.csv
wc -l 115_sdoc1/senate_data_cleaned.csv

# 6. Review any missing data
less 115_sdoc1/missing_data.json
```

## Changes from Original Version

### Python 3 Updates
- ✅ All print statements updated to functions
- ✅ File I/O uses proper text mode with `newline=''`
- ✅ f-strings for better string formatting
- ✅ subprocess module instead of os.popen
- ✅ Better error handling

### New Features
- ✅ Unified processing script
- ✅ Command-line interface with arguments
- ✅ Automatic directory creation
- ✅ Progress indicators
- ✅ Better error messages
- ✅ Modular design for reuse

### Preserved Functionality
- ✅ Same regex patterns and parsing logic
- ✅ Same CSV output format
- ✅ Compatible with existing processed data
- ✅ Legacy scripts still work (updated to Python 3)

## Support

For issues or questions:
1. Check this usage guide
2. Review the [original README](README.md)
3. Examine `missing_data.json` for parsing issues
4. Open an issue on the project repository

## License

Same as original project license.
