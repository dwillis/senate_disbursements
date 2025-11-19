#!/usr/bin/env python3
"""
Add Bioguide IDs to Existing Senate Disbursement CSV Files

This script reads existing cleaned senate_data CSV files and adds bioguide IDs
to senator records by matching names and years against the congress-legislators
database.

Usage:
    # Add bioguide IDs to a single file
    python3 add_bioguide_ids.py senate_data_cleaned.csv

    # Process all cleaned CSV files in data/all_years/
    python3 add_bioguide_ids.py --all

    # Process all files matching a pattern
    python3 add_bioguide_ids.py --pattern "data/*/senate_data_cleaned.csv"
"""

import os
import sys
import csv
import argparse
import glob
from pathlib import Path
from bioguide_matcher import BioguideIdMatcher


def add_bioguide_ids_to_csv(input_file, output_file=None, matcher=None):
    """
    Add bioguide IDs to an existing cleaned CSV file.

    Args:
        input_file: Path to input CSV file
        output_file: Path to output CSV file (default: overwrites input)
        matcher: BioguideIdMatcher instance (creates new one if None)

    Returns:
        Dictionary with statistics about the operation
    """
    if output_file is None:
        output_file = input_file

    # Initialize matcher if not provided
    if matcher is None:
        matcher = BioguideIdMatcher()

    # Read the input file
    rows = []
    header_row_1 = None
    header_row_2 = None

    with open(input_file, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)

        # Read first two rows (citation and headers)
        try:
            header_row_1 = next(reader)
            header_row_2 = next(reader)
        except StopIteration:
            print(f"Error: {input_file} appears to be empty or malformed")
            return None

        # Read all data rows
        rows = list(reader)

    # Check if bioguide_id column already exists
    has_bioguide = 'bioguide_id' in header_row_2
    if has_bioguide:
        bioguide_idx = header_row_2.index('bioguide_id')
    else:
        # Add bioguide_id column after senator_name
        try:
            senator_name_idx = header_row_2.index('senator_name')
            bioguide_idx = senator_name_idx + 1
            header_row_2.insert(bioguide_idx, 'bioguide_id')
        except ValueError:
            print(f"Error: Could not find 'senator_name' column in {input_file}")
            return None

    # Get column indices
    try:
        senator_flag_idx = header_row_2.index('senator_flag')
        senator_name_idx = header_row_2.index('senator_name')
        funding_year_idx = header_row_2.index('funding_year')
        fiscal_year_idx = header_row_2.index('fiscal_year')
    except ValueError as e:
        print(f"Error: Missing required column in {input_file}: {e}")
        return None

    # Process rows and add bioguide IDs
    stats = {
        'total_rows': len(rows),
        'senator_rows': 0,
        'matched': 0,
        'unmatched': 0,
        'already_had_id': 0
    }

    for row in rows:
        # Ensure row has enough columns
        while len(row) <= max(bioguide_idx, len(header_row_2) - 1):
            row.append('')

        # Check if this is a senator row
        try:
            senator_flag = int(row[senator_flag_idx]) if row[senator_flag_idx] else 0
        except (ValueError, IndexError):
            senator_flag = 0

        if senator_flag:
            stats['senator_rows'] += 1

            # Check if already has a bioguide ID
            if has_bioguide and row[bioguide_idx]:
                stats['already_had_id'] += 1
                continue

            # Get senator info
            senator_name = row[senator_name_idx] if len(row) > senator_name_idx else ''

            try:
                funding_year = int(row[funding_year_idx]) if row[funding_year_idx] else None
            except (ValueError, IndexError):
                funding_year = None

            try:
                fiscal_year = int(row[fiscal_year_idx]) if row[fiscal_year_idx] else None
            except (ValueError, IndexError):
                fiscal_year = None

            # Use funding_year if available, otherwise fiscal_year
            year = funding_year if funding_year else fiscal_year

            # Get bioguide ID
            if senator_name:
                bioguide_id = matcher.get_bioguide_id(senator_name, year)

                if bioguide_id:
                    stats['matched'] += 1
                    if not has_bioguide:
                        row.insert(bioguide_idx, bioguide_id)
                    else:
                        row[bioguide_idx] = bioguide_id
                else:
                    stats['unmatched'] += 1
                    if not has_bioguide:
                        row.insert(bioguide_idx, '')
                    print(f"  Warning: Could not match senator '{senator_name}' (year: {year})")
            else:
                if not has_bioguide:
                    row.insert(bioguide_idx, '')
        else:
            # Not a senator row, just add empty bioguide_id if needed
            if not has_bioguide:
                row.insert(bioguide_idx, '')

    # Write output file
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(header_row_1)
        writer.writerow(header_row_2)
        writer.writerows(rows)

    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Add bioguide IDs to existing senate disbursement CSV files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Add bioguide IDs to a single file (in-place)
  python3 add_bioguide_ids.py data/114_sdoc13/senate_data_cleaned.csv

  # Process all cleaned CSV files in data/all_years/
  python3 add_bioguide_ids.py --all

  # Process files matching a pattern
  python3 add_bioguide_ids.py --pattern "data/*/senate_data_cleaned.csv"
        """
    )

    parser.add_argument('input_file', nargs='?', help='Input CSV file to process')
    parser.add_argument('--output', '-o', help='Output CSV file (default: overwrites input)')
    parser.add_argument('--all', action='store_true', help='Process all *_cleaned.csv files in data/all_years/')
    parser.add_argument('--pattern', help='Process all files matching this glob pattern')

    args = parser.parse_args()

    # Determine which files to process
    files_to_process = []

    if args.all:
        pattern = 'data/all_years/*_cleaned.csv'
        files_to_process = glob.glob(pattern)
        print(f"Found {len(files_to_process)} files matching pattern: {pattern}")
    elif args.pattern:
        files_to_process = glob.glob(args.pattern)
        print(f"Found {len(files_to_process)} files matching pattern: {args.pattern}")
    elif args.input_file:
        if not os.path.exists(args.input_file):
            print(f"Error: File not found: {args.input_file}")
            sys.exit(1)
        files_to_process = [args.input_file]
    else:
        parser.print_help()
        sys.exit(1)

    if not files_to_process:
        print("No files to process!")
        sys.exit(1)

    # Initialize matcher once for all files
    print("\nInitializing bioguide matcher...")
    matcher = BioguideIdMatcher()

    # Process each file
    total_stats = {
        'files_processed': 0,
        'files_failed': 0,
        'total_rows': 0,
        'senator_rows': 0,
        'matched': 0,
        'unmatched': 0,
        'already_had_id': 0
    }

    for input_file in files_to_process:
        print(f"\nProcessing: {input_file}")

        output_file = args.output if args.output and len(files_to_process) == 1 else None

        try:
            stats = add_bioguide_ids_to_csv(input_file, output_file, matcher)

            if stats:
                total_stats['files_processed'] += 1
                total_stats['total_rows'] += stats['total_rows']
                total_stats['senator_rows'] += stats['senator_rows']
                total_stats['matched'] += stats['matched']
                total_stats['unmatched'] += stats['unmatched']
                total_stats['already_had_id'] += stats['already_had_id']

                print(f"  Total rows: {stats['total_rows']}")
                print(f"  Senator rows: {stats['senator_rows']}")
                print(f"  Matched: {stats['matched']}")
                print(f"  Unmatched: {stats['unmatched']}")
                if stats['already_had_id']:
                    print(f"  Already had ID: {stats['already_had_id']}")
            else:
                total_stats['files_failed'] += 1

        except Exception as e:
            print(f"  Error processing file: {e}")
            total_stats['files_failed'] += 1
            import traceback
            traceback.print_exc()

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Files processed: {total_stats['files_processed']}")
    if total_stats['files_failed']:
        print(f"Files failed: {total_stats['files_failed']}")
    print(f"Total rows: {total_stats['total_rows']}")
    print(f"Senator rows: {total_stats['senator_rows']}")
    print(f"Bioguide IDs matched: {total_stats['matched']}")
    print(f"Unmatched senators: {total_stats['unmatched']}")
    if total_stats['already_had_id']:
        print(f"Already had bioguide ID: {total_stats['already_had_id']}")

    if total_stats['matched'] > 0:
        match_rate = (total_stats['matched'] / total_stats['senator_rows']) * 100
        print(f"Match rate: {match_rate:.1f}%")


if __name__ == '__main__':
    main()
