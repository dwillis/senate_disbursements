#!/usr/bin/env python3
"""
Download Senate Disbursement Reports

Downloads full Senate disbursement reports from govinfo.gov
and organizes them using the existing directory convention (e.g., 114_sdoc13/).

The reports are available at: https://www.govinfo.gov/app/collection/cdoc

Requirements:
    - wget (recommended) or Python requests library
    - Internet connection without restrictive proxies

Usage:
    # Download specific report by doc ID
    python3 download_reports.py --doc 118sdoc13

    # Download multiple reports
    python3 download_reports.py --doc 118sdoc13 118sdoc11 117sdoc10

    # Download from a list file
    python3 download_reports.py --list-file report_ids.txt

    # Dry run (show what would be downloaded)
    python3 download_reports.py --doc 118sdoc13 --dry-run

    # Generate wget commands for manual download
    python3 download_reports.py --doc 118sdoc13 --generate-commands

Known Report IDs:
    118sdoc13, 118sdoc11, 118sdoc2  (118th Congress - 2023-2025)
    117sdoc10, 117sdoc2             (117th Congress - 2021-2023)
    116sdoc19, 116sdoc10, 116sdoc2  (116th Congress - 2019-2021)
    115sdoc20, 115sdoc7             (115th Congress - 2017-2019)
    114sdoc13, 114sdoc7, 114sdoc4   (114th Congress - 2015-2017)
    113sdoc25, 113sdoc22, 113sdoc17, 113sdoc2  (113th Congress - 2013-2015)
    112sdoc10, 112sdoc7, 112sdoc4   (112th Congress - 2011-2013)

Note: govinfo.gov may block automated downloads. If you encounter 403 errors,
      use --generate-commands to create wget commands for manual execution.
"""

import os
import sys
import argparse
import requests
import time
from pathlib import Path


# Base URL for govinfo.gov
GOVINFO_BASE = "https://www.govinfo.gov/content/pkg"

# Headers to avoid 403 errors
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/pdf,application/octet-stream,*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.govinfo.gov/'
}


def get_pdf_urls(doc_id):
    """
    Generate possible PDF URLs for a given document ID.
    Some reports are split into multiple parts (-1.pdf, -2.pdf, etc.)

    Args:
        doc_id (str): Document ID (e.g., '118sdoc13')

    Returns:
        list: List of possible PDF URLs
    """
    # GovInfo URLs use lowercase doc_id in path but may vary in filename
    doc_id_lower = doc_id.lower()
    base_url = f"{GOVINFO_BASE}/GPO-CDOC-{doc_id_lower}/pdf"

    # Try different patterns - govinfo.gov uses lowercase in filenames
    urls = [
        f"{base_url}/GPO-CDOC-{doc_id_lower}.pdf",      # Single file
        f"{base_url}/GPO-CDOC-{doc_id_lower}-1.pdf",    # Part 1
        f"{base_url}/GPO-CDOC-{doc_id_lower}-2.pdf",    # Part 2
        f"{base_url}/GPO-CDOC-{doc_id_lower}-3.pdf",    # Part 3 (some reports have 3 parts)
    ]

    return urls


def check_url_exists(url):
    """
    Check if a URL exists without downloading the full file.

    Args:
        url (str): URL to check

    Returns:
        tuple: (exists: bool, size: int or None)
    """
    try:
        response = requests.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if response.status_code == 200:
            size = int(response.headers.get('content-length', 0))
            return True, size
        return False, None
    except requests.RequestException:
        return False, None


def download_file(url, filepath, dry_run=False):
    """
    Download a file from URL to filepath using wget (more reliable for govinfo.gov).

    Args:
        url (str): URL to download from
        filepath (str): Local file path to save to
        dry_run (bool): If True, only check if file exists

    Returns:
        bool: True if successful, False otherwise
    """
    if dry_run:
        # In dry run, try a simple HEAD request to check existence
        exists, size = check_url_exists(url)
        if exists:
            size_mb = size / (1024 * 1024) if size else 0
            print(f"    [DRY RUN] Would download: {os.path.basename(url)} ({size_mb:.1f} MB)")
            return True
        else:
            # Even if HEAD fails, the file might exist - report optimistically
            print(f"    [DRY RUN] Attempting: {os.path.basename(url)} (HEAD check failed, but file may exist)")
            return True

    print(f"    ⬇ Downloading: {os.path.basename(url)}")
    print(f"       URL: {url}")

    # Use wget which is more reliable for govinfo.gov than Python requests
    import subprocess

    try:
        # wget options:
        # -O: output file
        # -q: quiet (no wget output)
        # --show-progress: show progress bar
        # --timeout=120: timeout after 120 seconds
        # --tries=3: try up to 3 times
        result = subprocess.run(
            ['wget', '-O', filepath, '-q', '--show-progress', '--timeout=120', '--tries=3', url],
            capture_output=True,
            text=True,
            timeout=180
        )

        if result.returncode == 0 and os.path.exists(filepath):
            file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
            print(f"       ✓ Downloaded: {file_size_mb:.1f} MB")
            return True
        else:
            # File doesn't exist or download failed
            if os.path.exists(filepath):
                os.remove(filepath)  # Clean up partial download
            # Silently fail for non-existent parts (404 is expected for part 2, 3, etc.)
            if result.returncode == 8:  # wget error code 8 is 404
                return False
            # For other errors, report them
            if result.stderr:
                print(f"       ✗ Error: {result.stderr.strip()}")
            return False

    except subprocess.TimeoutExpired:
        print(f"       ✗ Error: Download timed out")
        if os.path.exists(filepath):
            os.remove(filepath)
        return False
    except FileNotFoundError:
        # wget not installed, fall back to requests
        print(f"       Note: wget not found, using Python requests (may fail due to bot protection)")
        return download_file_with_requests(url, filepath)
    except Exception as e:
        print(f"       ✗ Error: {e}")
        if os.path.exists(filepath):
            os.remove(filepath)
        return False


def download_file_with_requests(url, filepath):
    """Fallback download using Python requests library."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=120, stream=True)
        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        chunk_size = 8192

        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)

        downloaded_mb = downloaded / (1024 * 1024)
        print(f"       ✓ Downloaded: {downloaded_mb:.1f} MB")
        return True

    except requests.RequestException as e:
        print(f"       ✗ Error: {e}")
        if os.path.exists(filepath):
            os.remove(filepath)
        return False


def download_report(doc_id, dry_run=False):
    """
    Download a Senate disbursement report by document ID.

    Args:
        doc_id (str): Document ID (e.g., '118sdoc13')
        dry_run (bool): If True, only check what would be downloaded

    Returns:
        int: Number of files successfully downloaded
    """
    doc_id = doc_id.lower().strip()
    directory = doc_id

    # Create directory if it doesn't exist
    if not dry_run:
        os.makedirs(directory, exist_ok=True)

    print(f"\n[{doc_id.upper()}]")

    # Get possible PDF URLs
    pdf_urls = get_pdf_urls(doc_id)

    success_count = 0
    found_any = False

    for url in pdf_urls:
        # Extract filename from URL
        filename = os.path.basename(url)
        filepath = os.path.join(directory, filename)

        # Check if already downloaded
        if os.path.exists(filepath):
            file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
            print(f"  ✓ {filename}: Already exists ({file_size_mb:.1f} MB) - skipping")
            success_count += 1
            found_any = True
            continue

        # Try to download the file
        # Note: We skip HEAD check because govinfo.gov may block HEAD requests
        # but allow GET requests. We'll just try to download and handle errors.
        if download_file(url, filepath, dry_run):
            success_count += 1
            found_any = True
        elif dry_run:
            # In dry run mode, we tried and failed - that's expected for non-existent parts
            pass
        else:
            # In actual download mode, the file doesn't exist - that's OK for optional parts
            pass

        # Be nice to the server
        time.sleep(1)

    if not found_any:
        print(f"  ✗ No files found for {doc_id.upper()}")
        print(f"     This report may not exist or may use a different naming convention")
        print(f"     Check https://www.govinfo.gov/app/collection/cdoc for available reports")

    return success_count


def read_doc_ids_from_file(filepath):
    """
    Read document IDs from a text file (one per line).

    Args:
        filepath (str): Path to file containing doc IDs

    Returns:
        list: List of doc IDs
    """
    try:
        with open(filepath, 'r') as f:
            doc_ids = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        return doc_ids
    except FileNotFoundError:
        print(f"Error: File not found: {filepath}")
        return []


def main():
    parser = argparse.ArgumentParser(
        description='Download Senate disbursement reports from govinfo.gov',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download a specific report
  python3 download_reports.py --doc 118sdoc13

  # Download multiple reports
  python3 download_reports.py --doc 118sdoc13 117sdoc10 116sdoc19

  # Download from a list file (one doc ID per line)
  python3 download_reports.py --list-file reports.txt

  # Dry run (check what would be downloaded)
  python3 download_reports.py --doc 118sdoc13 --dry-run

Known Report IDs (as of 2025):
  118sdoc13, 118sdoc11, 118sdoc2  (118th Congress, 2023-2025)
  117sdoc10, 117sdoc2             (117th Congress, 2021-2023)
  116sdoc19, 116sdoc10, 116sdoc2  (116th Congress, 2019-2021)
  115sdoc20, 115sdoc7             (115th Congress, 2017-2019)
  114sdoc13, 114sdoc7, 114sdoc4   (114th Congress, 2015-2017)
  113sdoc25, 113sdoc22, 113sdoc17, 113sdoc2
  112sdoc10, 112sdoc7, 112sdoc4

For more reports, visit: https://www.govinfo.gov/app/collection/cdoc
        """
    )

    parser.add_argument('--doc', nargs='+', metavar='DOC_ID',
                       help='Document ID(s) to download (e.g., 118sdoc13)')
    parser.add_argument('--list-file', metavar='FILE',
                       help='File containing document IDs (one per line)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be downloaded without actually downloading')
    parser.add_argument('--generate-commands', action='store_true',
                       help='Generate wget/curl commands for manual download instead of downloading')

    args = parser.parse_args()

    # Collect document IDs
    doc_ids = []

    if args.doc:
        doc_ids.extend(args.doc)

    if args.list_file:
        doc_ids.extend(read_doc_ids_from_file(args.list_file))

    if not doc_ids:
        parser.print_help()
        print("\nError: Please specify document IDs using --doc or --list-file")
        return 1

    # Remove duplicates while preserving order
    seen = set()
    doc_ids = [x for x in doc_ids if not (x.lower() in seen or seen.add(x.lower()))]

    print(f"{'='*80}")
    print(f"Senate Disbursement Report Downloader")
    print(f"{'='*80}")

    # Generate commands mode
    if args.generate_commands:
        print("\n# Copy and paste these commands to download reports:\n")
        for doc_id in doc_ids:
            doc_id_lower = doc_id.lower()
            directory = doc_id_lower
            print(f"# {doc_id.upper()}")
            print(f"mkdir -p {directory}")
            for i in ['', '-1', '-2', '-3']:
                filename = f"GPO-CDOC-{doc_id_lower}{i}.pdf"
                url = f"https://www.govinfo.gov/content/pkg/GPO-CDOC-{doc_id_lower}/pdf/{filename}"
                print(f"wget -nc -P {directory} {url}  # Use -nc to skip if already downloaded")
            print()
        print("\n# After downloading, process with:")
        print(f"# python3 process_senate_disbursements.py <doc_id>/GPO-CDOC-<DOC_ID>.pdf --start XX --end YY")
        return 0

    if args.dry_run:
        print("[DRY RUN MODE - No files will be downloaded]")
    print(f"\nProcessing {len(doc_ids)} report(s)...")

    total_files = 0
    for doc_id in doc_ids:
        files_downloaded = download_report(doc_id, dry_run=args.dry_run)
        total_files += files_downloaded

    # Summary
    print(f"\n{'='*80}")
    print(f"Summary: {total_files} file(s) downloaded across {len(doc_ids)} report(s)")
    if args.dry_run:
        print("(This was a dry run - no files were actually downloaded)")
    print(f"{'='*80}")

    print("\nNext steps:")
    print("  1. Process the PDFs using: python3 process_senate_disbursements.py")
    print("  2. Check the PDF to find the correct page range")
    print("  3. Example: python3 process_senate_disbursements.py 118sdoc13/GPO-CDOC-118SDOC13.pdf --start 20 --end 2500")

    return 0


if __name__ == '__main__':
    sys.exit(main())
