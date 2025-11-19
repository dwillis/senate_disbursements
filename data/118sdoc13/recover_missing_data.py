#!/usr/bin/env python3
"""
Recovery script for 118sdoc13 missing data.

This script parses the missing_data.json file and extracts expense and salary
records that weren't captured by the standard parser due to formatting differences.
"""

import json
import re
import csv
import sys
from pathlib import Path

# Regex patterns for parsing different record types
EXPENSE_RECORD = re.compile(
    r'^\s*([A-Z0-9]{8,12})\s+'  # Document number
    r'(\d\d/\d\d/\d\d\d\d)\s+'  # Date posted
    r'(.+?)\s{2,}'              # Payee name
    r'(?:(\d\d/\d\d/\d\d\d\d)\s+)?'  # Start date (optional)
    r'(?:(\d\d/\d\d/\d\d\d\d)\s+)?'  # End date (optional)
    r'(.+?)'                    # Description
    r'(?:\s+\$?([\d\,\.]+))?\s*'  # Amount (optional)
    r'(?:B-\d+)?\s*$'           # Page reference (optional)
)

SALARY_WITH_AMOUNT = re.compile(
    r'^\s+([A-Z][A-Z\s\,\.\-\']+?)\s{2,}'  # Name
    r'(.+?)\s{2,}'                          # Position/title
    r'\$?([\d\,\.]+)\s*'                    # Amount
    r'(?:B-\d+)?\s*$'                       # Page reference (optional)
)

SALARY_NO_AMOUNT = re.compile(
    r'^\s+([A-Z][A-Z\s\,\.\-\']+?)\s{2,}'  # Name
    r'([A-Z][A-Z\s\,\.\-\/]+?)\s*'          # Position/title
    r'(?:B-\d+)?\s*$'                       # Page reference (optional)
)

DATE_DESCRIPTION = re.compile(
    r'^\s+(\d\d/\d\d/\d\d\d\d)\s+'  # Date
    r'(.+)$'                         # Description
)

DESCRIPTION_ONLY = re.compile(
    r'^\s{20,}(.+)$'  # Heavily indented description
)

# Lines to skip
SKIP_PATTERNS = [
    re.compile(r'^\s*(TRAVEL AND TRANSPORTATION|INTERDEPARTMENTAL|OTHER CONTRACTUAL|'
               r'ACQUISITION OF|PERSONNEL|NET PAYROLL|FURNISHINGS|ORGANIZATION TOTAL|'
               r'UNEXPENDED BALANCE)'),
    re.compile(r'^\s*\$?[\d\,\.]+\s*$'),  # Just an amount
    re.compile(r'^\s*$'),  # Blank lines
    re.compile(r'^\s*B-\d+\s*$'),  # Just page numbers
]


def should_skip(line):
    """Check if a line should be skipped."""
    return any(pattern.match(line) for pattern in SKIP_PATTERNS)


def parse_expense_record(line):
    """Parse an expense record line."""
    match = EXPENSE_RECORD.match(line)
    if match:
        doc_num, date_posted, payee, start_date, end_date, description, amount = match.groups()
        return {
            'type': 'expense',
            'doc_num': doc_num.strip(),
            'date_posted': date_posted.strip(),
            'payee': payee.strip(),
            'start_date': start_date.strip() if start_date else '',
            'end_date': end_date.strip() if end_date else '',
            'description': description.strip(),
            'amount': amount.strip() if amount else '',
        }
    return None


def parse_salary_record(line):
    """Parse a salary record line."""
    # Try with amount first
    match = SALARY_WITH_AMOUNT.match(line)
    if match:
        name, position, amount = match.groups()
        return {
            'type': 'salary',
            'name': name.strip(),
            'position': position.strip(),
            'amount': amount.strip(),
        }

    # Try without amount
    match = SALARY_NO_AMOUNT.match(line)
    if match:
        name, position = match.groups()
        # Filter out non-salary lines
        if any(word in position.upper() for word in
               ['NET PAYROLL', 'ORGANIZATION', 'UNEXPENDED', 'TOTAL', 'AUTHORIZATION']):
            return None
        return {
            'type': 'salary',
            'name': name.strip(),
            'position': position.strip(),
            'amount': '',
        }

    return None


def parse_continuation(line):
    """Parse a continuation line (date + description or just description)."""
    # Try date + description
    match = DATE_DESCRIPTION.match(line)
    if match:
        date, description = match.groups()
        return {
            'type': 'continuation',
            'subtype': 'date_description',
            'date': date.strip(),
            'description': description.strip(),
        }

    # Try description only
    match = DESCRIPTION_ONLY.match(line)
    if match:
        description = match.group(1)
        # Skip category headers
        if any(word in description.upper() for word in
               ['TRAVEL AND TRANSPORTATION', 'CONTRACTUAL SERVICES', 'NET PAYROLL']):
            return None
        return {
            'type': 'continuation',
            'subtype': 'description_only',
            'description': description.strip(),
        }

    return None


def get_current_office(page_num, pages_dir, office_cache):
    """Extract the office name from the page header.

    Args:
        page_num: Page number to extract office from
        pages_dir: Directory containing page files
        office_cache: Dict to cache office names by page number

    Returns:
        Office name string
    """
    # Check cache first
    if page_num in office_cache:
        return office_cache[page_num]

    # Check if we can use a nearby cached office (within 5 pages)
    for nearby_page in range(page_num - 1, max(page_num - 6, 0), -1):
        if nearby_page in office_cache:
            office_cache[page_num] = office_cache[nearby_page]
            return office_cache[nearby_page]

    page_file = Path(pages_dir) / f"layout_{page_num}.txt"
    if not page_file.exists():
        return ''

    with open(page_file, 'r') as f:
        lines = f.readlines()

    office_name = ''

    # Strategy 1: Look for "Funding Year" pattern (most reliable)
    for i, line in enumerate(lines[:15]):
        if 'Funding Year' in line:
            # Office name is in the line 1-2 lines above "Funding Year"
            # It's the left-most text before the "DESCRIPTION" column header
            for j in range(max(0, i-3), i):
                candidate_line = lines[j]

                # Skip header line
                if 'DETAILED AND SUMMARY' in candidate_line:
                    continue

                # Extract left column before DESCRIPTION/NET FUNDS
                if 'DESCRIPTION' in candidate_line:
                    candidate = candidate_line.split('DESCRIPTION')[0]
                elif 'NET FUNDS' in candidate_line:
                    candidate = candidate_line.split('NET FUNDS')[0]
                else:
                    candidate = candidate_line

                candidate = candidate.strip()

                # Clean up and validate
                if candidate and len(candidate) < 200:  # Reasonable length
                    # Remove common header artifacts
                    candidate = candidate.replace('AVAILABLE AS', '')
                    candidate = candidate.replace('THE PERIOD OF', '')
                    candidate = candidate.replace('YTD', '')
                    candidate = candidate.strip()

                    # Must be substantive
                    if len(candidate) > 3:
                        office_name = candidate
                        break
            if office_name:
                break

    # Strategy 2: Look for party affiliation markers (R) or (D)
    if not office_name:
        for line in lines[:12]:
            # Look for lines ending with (R) or (D)
            if line.strip().endswith('(R)') or line.strip().endswith('(D)'):
                parts = line.split('DESCRIPTION')
                if parts:
                    candidate = parts[0].strip()
                    if len(candidate) < 200:  # Reasonable length
                        office_name = candidate
                        break

    # Strategy 3: Look for committee names
    if not office_name:
        committee_names = ['APPROPRIATIONS', 'AGRICULTURE', 'ARMED SERVICES', 'BANKING',
                          'BUDGET', 'COMMERCE', 'ENERGY', 'FINANCE', 'FOREIGN RELATIONS',
                          'HEALTH', 'JUDICIARY', 'RULES', 'VETERANS', 'INTELLIGENCE',
                          'HOMELAND SECURITY', 'ENVIRONMENT', 'SMALL BUSINESS', 'ETHICS']

        for line in lines[:15]:
            for committee in committee_names:
                if committee in line and 'SALARIES' not in line and 'Authorization' not in line:
                    parts = line.split('Funding')[0].split('DESCRIPTION')[0].split('NET FUNDS')[0]
                    candidate = parts.strip()
                    if len(candidate) < 200:
                        office_name = candidate
                        break
            if office_name:
                break

    # Cache the result
    office_cache[page_num] = office_name
    return office_name


def main():
    # Set up paths
    data_dir = Path(__file__).parent
    missing_file = data_dir / 'missing_data.json'
    pages_dir = data_dir / 'pages'
    output_file = data_dir / 'senate_data_recovered.csv'

    print(f"Reading missing data from: {missing_file}")

    # Load missing data
    with open(missing_file, 'r') as f:
        missing_groups = json.load(f)

    print(f"Found {len(missing_groups)} groups with {sum(len(g) for g in missing_groups)} total items")

    # Track statistics
    stats = {
        'expense_records': 0,
        'salary_records': 0,
        'continuation_lines': 0,
        'skipped': 0,
        'unparseable': 0,
    }

    # Process all groups and build records
    all_records = []
    last_office = ''
    office_cache = {}  # Cache office names by page number

    for group in missing_groups:
        for item in group:
            line = item['data'].rstrip('\n')
            page_num = item['page_num']

            # Skip blank lines and category headers
            if should_skip(line):
                stats['skipped'] += 1
                continue

            # Try to parse as expense record
            expense = parse_expense_record(line)
            if expense:
                # Get office from page
                office = get_current_office(page_num, pages_dir, office_cache)
                if office:
                    last_office = office

                expense['office'] = last_office
                expense['page_num'] = page_num
                all_records.append(expense)
                stats['expense_records'] += 1
                continue

            # Try to parse as salary record
            salary = parse_salary_record(line)
            if salary:
                # Get office from page
                office = get_current_office(page_num, pages_dir, office_cache)
                if office:
                    last_office = office

                salary['office'] = last_office
                salary['page_num'] = page_num
                all_records.append(salary)
                stats['salary_records'] += 1
                continue

            # Try to parse as continuation
            continuation = parse_continuation(line)
            if continuation:
                stats['continuation_lines'] += 1
                # Note: Continuation lines would need to be merged with previous records
                # For now, we'll skip them in the output but count them
                continue

            # Couldn't parse
            stats['unparseable'] += 1

    # Write to CSV
    print(f"\nWriting recovered data to: {output_file}")
    with open(output_file, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)

        # Write header
        writer.writerow([
            'office', 'record_type', 'page_num', 'doc_num', 'date_posted',
            'payee_or_name', 'start_date', 'end_date', 'description_or_position', 'amount'
        ])

        # Write records
        for record in all_records:
            if record['type'] == 'expense':
                writer.writerow([
                    record['office'],
                    'expense',
                    record['page_num'],
                    record['doc_num'],
                    record['date_posted'],
                    record['payee'],
                    record['start_date'],
                    record['end_date'],
                    record['description'],
                    record['amount'],
                ])
            elif record['type'] == 'salary':
                writer.writerow([
                    record['office'],
                    'salary',
                    record['page_num'],
                    '',  # no doc_num
                    '',  # no date_posted
                    record['name'],
                    '',  # no start_date
                    '',  # no end_date
                    record['position'],
                    record['amount'],
                ])

    # Print statistics
    print("\n" + "="*60)
    print("RECOVERY SUMMARY")
    print("="*60)
    print(f"Expense records recovered:     {stats['expense_records']:6,}")
    print(f"Salary records recovered:      {stats['salary_records']:6,}")
    print(f"Continuation lines found:      {stats['continuation_lines']:6,}")
    print(f"Lines skipped (categories):    {stats['skipped']:6,}")
    print(f"Lines unparseable:             {stats['unparseable']:6,}")
    print(f"-" * 60)
    print(f"Total records in output:       {len(all_records):6,}")
    print("="*60)

    return 0


if __name__ == '__main__':
    sys.exit(main())
