#!/usr/bin/env python3
"""
Legacy run script for 114_sdoc13
Kept for compatibility. Use ../process_senate_disbursements.py for new processing.
"""
from rip_pages import rip_pages
from read_pages import read_pages
from format_csv import format_csv

# STEP 1: CONFIG VARIABLES
SOURCE_DOC = '114sdoc13'
FILE_NAME = "GPO-CDOC-" + SOURCE_DOC + ".pdf"
OUT_FILE = 'senate_data.csv'
MISSING_FILE = 'missing_data.json'
START_PAGE = 18
END_PAGE = 2264

if __name__ == '__main__':
    print(f"Processing {SOURCE_DOC}")
    print(f"Pages: {START_PAGE} to {END_PAGE}")

    # STEP 2: Rip text, read pages, format output
    print("\n=== Step 1: Extracting pages ===")
    rip_pages(FILE_NAME, START_PAGE, END_PAGE)

    print("\n=== Step 2: Parsing pages ===")
    read_pages(START_PAGE, END_PAGE, OUT_FILE, MISSING_FILE)

    print("\n=== Step 3: Cleaning CSV ===")
    format_csv(SOURCE_DOC, OUT_FILE)

    print("\n=== Complete! ===")
    print("Raw data: senate_data.csv")
    print("Cleaned data: senate_data_cleaned.csv")
    print("Missing data: missing_data.json")
    # STEP 4: Reconcile data in MISSING_FILE (manual step)



