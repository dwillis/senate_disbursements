#!/usr/bin/env python3
"""
Senate Disbursements Parser

This script processes Senate disbursement PDFs and extracts structured expense data
to CSV files, identifying:
- Office/Senator name
- Purpose of spending
- Amount
- Timeframe
- Other metadata

Supports both older (113-114 Congress) and newer (118+ Congress) document formats:
- Older format: Strict column spacing with required fields
- Newer format: Flexible spacing with optional dates/amounts

The parser automatically detects and handles both formats using a fallback pattern
matching approach.

Pages are always processed in ascending numeric order (1, 2, 3, ...) rather than
lexicographic order (1, 19, 100, 200, ...) to ensure correct data sequencing.

Usage:
    python3 process_senate_disbursements.py <pdf_file> --start <start_page> --end <end_page>
    python3 process_senate_disbursements.py GPO-CDOC-114sdoc13.pdf --start 18 --end 2264
    python3 process_senate_disbursements.py GPO-CDOC-118sdoc13.pdf --start 24 --end 591

Or use the interactive mode:
    python3 process_senate_disbursements.py <pdf_file>
"""

import os
import sys
import argparse
import subprocess
import re
import csv
import json
from pathlib import Path

# Import bioguide matcher for adding bioguide IDs to senator records
try:
    from bioguide_matcher import BioguideIdMatcher
except ImportError:
    print("Warning: bioguide_matcher.py not found. Bioguide IDs will not be added.")
    BioguideIdMatcher = None


# Regular expressions for parsing
header_end = re.compile(r"\s+START\s+END\s+")

# Original patterns for older format documents (113-114 Congress)
five_data_re = re.compile(r"\s*([\w\d]+)\s+(\d\d/\d\d/\d\d\d\d)\s+(.*?)\s+(\d\d/\d\d/\d\d\d\d)\s+(\d\d/\d\d/\d\d\d\d)\s*(.+?)\s+([\d\.\-\,]+)\s*\Z")
five_data_missing_date = re.compile(r"\s*([\w\d]+)\s+(\d\d/\d\d/\d\d\d\d)\s+(.*?)\s{10,}(.*?)\s+([\d\.\-\,]+)\s*\Z")
three_data_re = re.compile(r"\s+(\w[\w\,\s\.\-\']+?)\s{10,}(\w.*?)\s{4,}([\d\.\-\,]+)\s*")

# Flexible patterns for newer format documents (118 Congress)
# Expense record with optional dates and amount (more lenient spacing)
expense_record_flexible = re.compile(
    r'^\s*([A-Z0-9]{8,12})\s+'  # Document number
    r'(\d\d/\d\d/\d\d\d\d)\s+'  # Date posted
    r'(.+?)\s{2,}'              # Payee name
    r'(?:(\d\d/\d\d/\d\d\d\d)\s+)?'  # Start date (optional)
    r'(?:(\d\d/\d\d/\d\d\d\d)\s+)?'  # End date (optional)
    r'(.+?)'                    # Description
    r'(?:\s+\$?([\d\,\.]+))?\s*'  # Amount (optional)
    r'(?:B-\d+)?\s*$'           # Page reference (optional)
)

# Salary record with amount
salary_with_amount_flexible = re.compile(
    r'^\s+([A-Z][A-Z\s\,\.\-\']+?)\s{2,}'  # Name
    r'(.+?)\s{2,}'                          # Position/title
    r'\$?([\d\,\.]+)\s*'                    # Amount
    r'(?:B-\d+)?\s*$'                       # Page reference (optional)
)

# Salary record without amount
salary_no_amount_flexible = re.compile(
    r'^\s+([A-Z][A-Z\s\,\.\-\']+?)\s{2,}'  # Name
    r'([A-Z][A-Z\s\,\.\-\/]+?)\s*'          # Position/title
    r'(?:B-\d+)?\s*$'                       # Page reference (optional)
)

top_matter_end_re = re.compile(r"\s+DOCUMENT\s+NO\.\s+DATE\s+PAYEE")
funding_year_re = re.compile(r"\s*Funding\s+Year\s+(\d+)")
blank_line_re = re.compile(r"\s+\Z")
# Support both old format (\w-\d+, \w-\d-\d+) and new format (B-\d+)
page_number_re = re.compile(r"\s+B\s*\-\s*\d+\s*")
page_number_alt_re = re.compile(r"\s+\w\-\d\-\d+")
page_number_old_re = re.compile(r"\s+\w\-\d+")
continuation_with_amount_re = re.compile(r"\s*(.+?)\s{10,}([\d\.\-\,]+)\s+\Z")

# Subtotal patterns
SUBTOTAL_PATTERNS = [
    re.compile(r"\s+TRAVEL\s+AND\s+TRANSPORTATION\s+OF\s+PERSONS\s+"),
    re.compile(r"\s+INTERDEPARTMENTAL\s+TRANSPORTATION\s+"),
    re.compile(r"\s+OTHER\s+CONTRACTUAL\s+SERVICES\s+"),
    re.compile(r"\s+ACQUISITION\s+OF\s+ASSETS\s+"),
    re.compile(r"\s+PERSONNEL\s+BENEFITS\s+"),
    re.compile(r"\s+NET\s+PAYROLL\s+EXPENSES\s+"),
    re.compile(r"\s+PERSONNEL COMP. FULL-TIME PERMANENT\s+"),
    re.compile(r"\s+OTHER PERSONNEL COMPENSATION\s+"),
    re.compile(r"\s+RE-EMPLOYED ANNUITANTS\s+"),
    re.compile(r"\s+BENEFITS FOR NON SENATE/FORMER PERSONNEL\s+"),
]

# Cleaning patterns
FUNDING_YEAR_RE = re.compile(r'(Funding Year) (\d+)')
FISCAL_YEAR_RE = re.compile(r'(FY) (\d+)')
CONGRESS_NUMBER = re.compile(r'\((\d+)TH\)')


def is_subtotal(line):
    """Check if a line is a subtotal line."""
    return any(pattern.match(line) for pattern in SUBTOTAL_PATTERNS)


def extract_pages(pdf_file, start_page, end_page, output_dir="pages"):
    """Extract individual pages from PDF using pdftotext with layout preservation."""
    print(f"\n=== Extracting pages {start_page} to {end_page} from {pdf_file} ===")

    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    for page_number in range(start_page, end_page + 1):
        output_filename = os.path.join(output_dir, f"layout_{page_number}.txt")
        layout_cmd = ["pdftotext", "-f", str(page_number), "-l", str(page_number),
                      "-layout", pdf_file, output_filename]

        if page_number % 100 == 0 or page_number == start_page:
            print(f"Extracting page {page_number}...")

        try:
            result = subprocess.run(layout_cmd, capture_output=True, text=True, check=True)
            if result.stderr:
                print(f"Warning on page {page_number}: {result.stderr}")
        except subprocess.CalledProcessError as e:
            print(f"Error extracting page {page_number}: {e}")
            if e.stderr:
                print(e.stderr)

    print(f"Extraction complete! Pages saved to {output_dir}/")


def process_top_matter(page_num, top_matter):
    """Extract office/expense description from the top matter of a page."""
    top_matter_top_left_column_delimiter = 48

    expense_description = ''
    for whole_line in top_matter:
        if top_matter_end_re.match(whole_line):
            break
        line = whole_line[:top_matter_top_left_column_delimiter]
        if blank_line_re.match(line):
            continue

        line_stripped = line.strip()
        if line_stripped:
            expense_description += ' ' + line_stripped + ' '

    expense_description = re.sub(r'\s+', ' ', expense_description).strip()
    return expense_description


def test_carryover_line(line_offset, line):
    """Check if a line is a continuation of a previous line."""
    line_start = line[:line_offset]
    if blank_line_re.match(line_start):
        line_end = line[line_offset:]
        if not blank_line_re.match(line_end):
            return True
    return False


def process_data_lines(page_num, data_lines):
    """Process data lines from a page and extract expense records."""
    missing_data = []
    return_data = []
    return_data_index = 0
    one_part_continuation_register = []
    last_line_data_index = None

    for data_line in data_lines:
        if blank_line_re.match(data_line):
            continue

        if page_number_re.match(data_line) or page_number_old_re.match(data_line):
            continue

        if is_subtotal(data_line):
            last_line_data_index = None
            continue

        # Try original strict patterns first (for backward compatibility)
        found_data = five_data_re.match(data_line)
        if found_data:
            return_data.append(['five data line', False, page_num] + list(found_data.groups()))
            return_data_index += 1
            last_line_data_index = str(found_data.start(6))
        else:
            found_data2 = three_data_re.match(data_line)
            found_data_missing_date = five_data_missing_date.match(data_line)

            if found_data2:
                results = list(found_data2.groups())
                result_formatted = ['three data line', False, page_num, '', '', results[0], '', '', results[1], results[2]]
                return_data.append(result_formatted)
                return_data_index += 1
                last_line_data_index = None

            elif found_data_missing_date:
                print("**found missing date line")
                results = list(found_data_missing_date.groups())
                result_formatted = ['missing date line', False, page_num, results[0], results[1], results[2], '', '', results[3], results[4]]
                return_data.append(result_formatted)
                return_data_index += 1
                last_line_data_index = None

            else:
                # Try flexible patterns for newer format documents
                expense_flex = expense_record_flexible.match(data_line)
                if expense_flex:
                    doc_num, date_posted, payee, start_date, end_date, description, amount = expense_flex.groups()
                    result_formatted = ['five data line', False, page_num,
                                      doc_num, date_posted, payee,
                                      start_date or '', end_date or '',
                                      description, amount or '']
                    return_data.append(result_formatted)
                    return_data_index += 1
                    last_line_data_index = None
                    continue

                # Try flexible salary patterns
                salary_flex_amount = salary_with_amount_flexible.match(data_line)
                if salary_flex_amount:
                    name, position, amount = salary_flex_amount.groups()
                    # Filter out non-salary lines
                    if not any(word in position.upper() for word in
                              ['NET PAYROLL', 'ORGANIZATION', 'UNEXPENDED', 'TOTAL', 'AUTHORIZATION']):
                        result_formatted = ['three data line', False, page_num, '', '', name, '', '', position, amount]
                        return_data.append(result_formatted)
                        return_data_index += 1
                        last_line_data_index = None
                        continue

                salary_flex_no_amount = salary_no_amount_flexible.match(data_line)
                if salary_flex_no_amount:
                    name, position = salary_flex_no_amount.groups()
                    # Filter out non-salary lines
                    if not any(word in position.upper() for word in
                              ['NET PAYROLL', 'ORGANIZATION', 'UNEXPENDED', 'TOTAL', 'AUTHORIZATION']):
                        result_formatted = ['three data line', False, page_num, '', '', name, '', '', position, '']
                        return_data.append(result_formatted)
                        return_data_index += 1
                        last_line_data_index = None
                        continue

                # Check if it's a page number
                is_page_num = page_number_re.match(data_line)
                is_page_num_alt = page_number_alt_re.match(data_line)
                is_page_num_old = page_number_old_re.match(data_line)
                if is_page_num or is_page_num_alt or is_page_num_old:
                    continue

                # Check for continuation lines
                if last_line_data_index:
                    carryover_found = test_carryover_line(int(last_line_data_index), data_line)

                    if carryover_found:
                        continuation_data = continuation_with_amount_re.match(data_line)

                        if continuation_data:
                            previous_result = return_data[return_data_index-1]
                            result_formatted = ['continuation_data', True, previous_result[2], previous_result[3],
                                              previous_result[4], previous_result[5], previous_result[6], previous_result[7],
                                              continuation_data.group(1), continuation_data.group(2)]
                            return_data.append(result_formatted)
                            return_data_index += 1
                        else:
                            description = data_line.strip()
                            register_data = {'array_index': return_data_index, 'data': description}
                            one_part_continuation_register.append(register_data)
                else:
                    # Still couldn't parse - add to missing data
                    print("missing <" + data_line + ">")
                    missing_data.append({'data': data_line, 'offset': return_data_index, 'page_num': page_num})

    return {'data': return_data, 'register': one_part_continuation_register, 'missing_data': missing_data}


def get_page_numbers_from_directory(pages_dir):
    """
    Extract page numbers from layout files in a directory and return them sorted numerically.

    Args:
        pages_dir: Directory containing layout_*.txt files

    Returns:
        List of page numbers sorted in ascending numeric order (1, 2, 3, ... not 1, 19, 100, 200)
    """
    import glob
    page_files = glob.glob(os.path.join(pages_dir, "layout_*.txt"))
    page_numbers = []

    for filepath in page_files:
        # Extract number from filename like "layout_123.txt"
        filename = os.path.basename(filepath)
        match = re.search(r'layout_(\d+)\.txt', filename)
        if match:
            page_numbers.append(int(match.group(1)))

    # Sort numerically (not lexicographically) to ensure proper page order
    return sorted(page_numbers)


def find_header_index(line_array):
    """Find the index of the header line in a page."""
    matches = 0
    header_index = None
    for index, line in enumerate(line_array):
        r = header_end.search(line)
        if r:
            matches += 1
            header_index = index

    # Return None if no header found (e.g., blank pages or summary pages)
    if matches == 0:
        return None

    # Break if we don't find exactly one occurrence of this per page
    assert matches == 1, f"Expected 1 header, found {matches}"
    return header_index


def parse_pages(start_page, end_page, pages_dir="pages", out_file='senate_data.csv', missing_file='missing_data.json'):
    """Parse extracted pages and create CSV output."""
    print(f"\n=== Parsing pages {start_page} to {end_page} ===")

    page_file_unfilled = os.path.join(pages_dir, "layout_%s.txt")
    header_index_hash = {}

    # Generate page numbers in ascending numeric order (1, 2, 3, ... not 1, 19, 100, 200)
    # Using range() ensures proper numeric ordering
    page_numbers = list(range(start_page, end_page + 1))

    with open(out_file, 'w', newline='') as csvfile:
        datawriter = csv.writer(csvfile)
        current_description = None
        description = None

        with open(missing_file, 'w') as missing_data_file:
            missing_data_file.write('[\n')

            for page in page_numbers:
                if page % 100 == 0 or page == start_page:
                    print(f"Processing page {page}")

                filename = page_file_unfilled % (page)
                try:
                    with open(filename, 'r', encoding='utf-8') as fh:
                        page_array = fh.readlines()
                except UnicodeDecodeError:
                    # Fall back to latin-1 encoding for pages with special characters
                    with open(filename, 'r', encoding='latin-1') as fh:
                        page_array = fh.readlines()

                header_index = find_header_index(page_array)

                # Skip pages without headers (blank pages, summary pages, etc.)
                if header_index is None:
                    if page % 100 == 0 or page == start_page:
                        print(f"  Skipping page {page} (no header found)")
                    continue

                # Keep stats on where we find the index
                header_index_hash[header_index] = header_index_hash.get(header_index, 0) + 1

                # Extract top matter if present
                if header_index > 6:
                    the_top_matter = page_array[:header_index+1]
                    description = process_top_matter(page, the_top_matter)

                current_description = description

                # Process data lines
                data_lines = page_array[header_index+1:]
                data_found = process_data_lines(page, data_lines)
                data_lines = data_found['data']
                one_line_continuation_register = data_found['register']

                # Append continuation lines to the right places
                for cl in one_line_continuation_register:
                    all_related_lines_found = False
                    current_line_position = cl['array_index'] - 1

                    while not all_related_lines_found:
                        data_lines[current_line_position][8] = data_lines[current_line_position][8] + " + " + cl['data']
                        if data_lines[current_line_position][0] != 'continuation_data':
                            all_related_lines_found = True
                        else:
                            current_line_position -= 1

                # Write data
                for data in data_lines:
                    datawriter.writerow([current_description] + data)

                # Write missing data
                if data_found['missing_data']:
                    missing_data_file.write(json.dumps(data_found['missing_data'], indent=4, separators=(',', ': ')))
                    missing_data_file.write(',')

            missing_data_file.write(']\n')

    print(f"\nParsing complete!")
    print(f"Data written to: {out_file}")
    print(f"Missing data written to: {missing_file}")
    print(f"\nHeader index statistics:")
    for k, v in sorted(header_index_hash.items()):
        print(f"  {k}: {v}")


def clean_csv(source_doc, csv_file='senate_data.csv', cleaned_file='senate_data_cleaned.csv', add_bioguide_ids=True):
    """Clean and reformat the CSV file."""
    print(f"\n=== Cleaning CSV data ===")

    # Initialize bioguide matcher if available and requested
    bioguide_matcher = None
    if add_bioguide_ids and BioguideIdMatcher:
        try:
            bioguide_matcher = BioguideIdMatcher()
        except Exception as e:
            print(f"Warning: Could not initialize bioguide matcher: {e}")
            print("Continuing without bioguide IDs...")
    elif add_bioguide_ids:
        print("Warning: BioguideIdMatcher not available. Skipping bioguide ID matching.")

    with open(csv_file, 'r') as in_file:
        unclean_data_reader = csv.reader(in_file)

        with open(cleaned_file, 'w', newline='') as out_file:
            cleaned_data_writer = csv.writer(out_file)

            # Write header rows
            cleaned_data_writer.writerow([
                "This data was parsed on an experimental basis by the Sunlight Foundation from Senate disbursement reports. "
                "Please cite 'The Sunlight Foundation' in any usage. "
                "For more information see the readme at http://assets-reporting.s3.amazonaws.com/1.0/senate_disbursements/readme.txt."
            ])
            cleaned_data_writer.writerow([
                'source_doc', 'senator_flag', 'senator_name', 'bioguide_id', 'raw_office', 'funding_year', 'fiscal_year',
                'congress_number', 'reference_page', 'document_number', 'date_posted', 'start_date',
                'end_date', 'description', 'salary_flag', 'amount', 'payee'
            ])

            # Process data rows
            for line in unclean_data_reader:
                senator_flag = 1 if 'senator' in line[0].lower() else 0
                senator_name = line[0].split('Funding')[0].replace('SENATOR', '').strip() if senator_flag else ''
                raw_office = line[0]

                try:
                    funding_year = int(re.search(FUNDING_YEAR_RE, line[0]).group(2))
                except:
                    funding_year = ''

                try:
                    fiscal_year = int(re.search(FISCAL_YEAR_RE, line[0]).group(2))
                except:
                    fiscal_year = ''

                try:
                    congress_number = int(re.search(CONGRESS_NUMBER, line[0]).group(1))
                except:
                    congress_number = ''

                reference_page = line[3]
                document_number = line[4]
                date_posted = line[5]
                payee = line[6]
                start_date = line[7]
                end_date = line[8]
                description = line[9]
                amount = line[10]

                salary_flag = 0 if start_date == '' and end_date == '' else 1

                # Get bioguide ID for senators
                bioguide_id = ''
                if bioguide_matcher and senator_flag and senator_name:
                    # Use funding_year if available, otherwise fiscal_year
                    year = funding_year if funding_year else fiscal_year
                    bioguide_id = bioguide_matcher.get_bioguide_id(senator_name, year)

                cleaned_data_writer.writerow([
                    source_doc, senator_flag, senator_name, bioguide_id, raw_office, funding_year,
                    fiscal_year, congress_number, reference_page, document_number, date_posted,
                    start_date, end_date, description, salary_flag, amount, payee
                ])

    print(f"Cleaned data written to: {cleaned_file}")


def main():
    parser = argparse.ArgumentParser(
        description='Process Senate disbursement PDFs and extract expense data to CSV',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process with explicit page range
  python3 process_senate_disbursements.py GPO-CDOC-114sdoc13.pdf --start 18 --end 2264

  # Process from a specific directory
  python3 process_senate_disbursements.py 114_sdoc13/GPO-CDOC-114sdoc13.pdf --start 18 --end 2264 --output-dir 114_sdoc13
        """
    )

    parser.add_argument('pdf_file', help='Path to the Senate disbursement PDF file')
    parser.add_argument('--start', type=int, help='Starting page number (inclusive)')
    parser.add_argument('--end', type=int, help='Ending page number (inclusive)')
    parser.add_argument('--output-dir', default=None, help='Output directory for extracted pages and CSV files (default: same as PDF directory)')
    parser.add_argument('--skip-extract', action='store_true', help='Skip page extraction (use if pages already extracted)')
    parser.add_argument('--skip-clean', action='store_true', help='Skip CSV cleaning step')

    args = parser.parse_args()

    # Determine output directory
    if args.output_dir:
        output_dir = args.output_dir
    else:
        # Use the directory containing the PDF
        output_dir = os.path.dirname(args.pdf_file) or '.'

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Set file paths
    pages_dir = os.path.join(output_dir, 'pages')
    csv_file = os.path.join(output_dir, 'senate_data.csv')
    cleaned_file = os.path.join(output_dir, 'senate_data_cleaned.csv')
    missing_file = os.path.join(output_dir, 'missing_data.json')

    # Get page range
    if not args.start or not args.end:
        print("Page range not specified. Please provide --start and --end page numbers.")
        print("\nTo find the correct page range:")
        print("1. Open the PDF file")
        print("2. Find where the itemized expenses begin (look for detailed line items)")
        print("3. Find where they end")
        print("4. Use those page numbers with --start and --end")
        return 1

    # Extract source document name
    pdf_basename = os.path.basename(args.pdf_file)
    source_doc = pdf_basename.replace('GPO-CDOC-', '').replace('.pdf', '')

    print(f"Processing Senate Disbursements")
    print(f"PDF: {args.pdf_file}")
    print(f"Page range: {args.start} to {args.end}")
    print(f"Output directory: {output_dir}")
    print(f"Source document: {source_doc}")

    # Step 1: Extract pages (skip if they already exist)
    if args.skip_extract:
        print("\n=== Skipping page extraction (--skip-extract flag provided) ===")
    else:
        # Check if pages already exist
        pages_exist = True
        for page_num in range(args.start, args.end + 1):
            page_file = os.path.join(pages_dir, f"layout_{page_num}.txt")
            if not os.path.exists(page_file):
                pages_exist = False
                break

        if pages_exist:
            print(f"\n=== Pages {args.start}-{args.end} already extracted, skipping extraction ===")
        else:
            extract_pages(args.pdf_file, args.start, args.end, pages_dir)

    # Step 2: Parse pages
    parse_pages(args.start, args.end, pages_dir, csv_file, missing_file)

    # Step 3: Clean CSV
    if not args.skip_clean:
        clean_csv(source_doc, csv_file, cleaned_file)
    else:
        print("\n=== Skipping CSV cleaning ===")

    print(f"\n{'='*60}")
    print("Processing complete!")
    print(f"{'='*60}")
    print(f"Raw CSV: {csv_file}")
    if not args.skip_clean:
        print(f"Cleaned CSV: {cleaned_file}")
    print(f"Missing data: {missing_file}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
