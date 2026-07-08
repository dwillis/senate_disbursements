"""Assemble parsed blocks into the published CSV schema.

Columns 1-17 match the legacy `clean_csv()` in
process_senate_disbursements.py (source_doc, senator_flag, senator_name,
bioguide_id, raw_office, funding_year, fiscal_year, congress_number,
reference_page, document_number, date_posted, start_date, end_date,
description, salary_flag, amount, payee), so older and modern-format
reports can sit in the same `data/all_years/` file. Columns 18-19 are
validation metadata the legacy pipeline can't produce: `validation_status`
says what happened when this row's segment was checked against the
report's own printed subtotal ('ok'/'warn'/'fail'/'unchecked'), and
`category` is that subtotal's label (e.g. "TRAVEL AND TRANSPORTATION OF
PERSONS"). Legacy rows should be backfilled with 'unvalidated' when
concatenated alongside modern ones.
"""

import re

CSV_COLUMNS = [
    "source_doc",
    "senator_flag",
    "senator_name",
    "bioguide_id",
    "raw_office",
    "funding_year",
    "fiscal_year",
    "congress_number",
    "reference_page",
    "document_number",
    "date_posted",
    "start_date",
    "end_date",
    "description",
    "salary_flag",
    "amount",
    "payee",
    "validation_status",
    "category",
]

FISCAL_YEAR_RE = re.compile(r"FY\s*(\d+)")
CONGRESS_FROM_SOURCE_DOC_RE = re.compile(r"^(\d+)")


def senator_info(office: str):
    if office.strip().upper().startswith("SENATOR "):
        return True, office.strip()[len("SENATOR "):].strip()
    return False, ""


def match_senator(office: str, funding_year, matcher):
    """Resolve a block's office to (senator_flag, senator_name,
    bioguide_id, outcome). The outcome makes match failures observable:
    the previous inline try/except turned matcher exceptions into the
    same "" as a plain no-match, so a matcher regression (cache
    corruption, YAML format change) could zero out every ID unnoticed."""
    senator_flag, senator_name = senator_info(office)
    if not senator_flag or not senator_name:
        return senator_flag, senator_name, "", "not_senator"
    if matcher is None:
        return senator_flag, senator_name, "", "no_matcher"
    if not funding_year:
        return senator_flag, senator_name, "", "no_year"
    try:
        bioguide_id = matcher.get_bioguide_id(senator_name, funding_year) or ""
    except Exception:
        return senator_flag, senator_name, "", "error"
    return senator_flag, senator_name, bioguide_id, "matched" if bioguide_id else "unmatched"


def block_rows(block, result, source_doc: str, page_offset: int = 0, bioguide_matcher=None) -> list:
    funding_year = block.header.funding_year or ""
    senator_flag, senator_name, bioguide_id, _ = match_senator(
        block.header.office, funding_year, bioguide_matcher
    )

    fy_match = FISCAL_YEAR_RE.search(block.header.account or "")
    fiscal_year = int(fy_match.group(1)) if fy_match else ""

    congress_match = CONGRESS_FROM_SOURCE_DOC_RE.match(source_doc)
    congress_number = int(congress_match.group(1)) if congress_match else ""

    rows = []
    for rec in result.records:
        rows.append(
            {
                "source_doc": source_doc,
                "senator_flag": 1 if senator_flag else 0,
                "senator_name": senator_name,
                "bioguide_id": bioguide_id,
                "raw_office": block.header.office,
                "funding_year": funding_year,
                "fiscal_year": fiscal_year,
                "congress_number": congress_number,
                "reference_page": rec.page + page_offset,
                "document_number": rec.document_number,
                "date_posted": rec.date_posted,
                "start_date": rec.start_date,
                "end_date": rec.end_date,
                "description": rec.description,
                "salary_flag": 1 if rec.record_type == "salary" else 0,
                "amount": rec.amount,
                "payee": rec.payee,
                "validation_status": rec.validation_status,
                "category": rec.category,
            }
        )
    return rows
