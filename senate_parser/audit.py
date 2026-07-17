"""Row-level output audits and the run manifest.

Reconciliation (reconcile.py) validates dollar sums against the report's
printed subtotals; the audits here catch the orthogonal class of "garbage
in the fields journalists filter and join on" -- problems verified to
have shipped before this module existed: amounts reconciliation can't
parse, and an office name contaminated with banner boilerplate
("PHOTOGRAPHIC STUDIO Funding Year X (REVO...", from a no-year account
whose 'Funding Year X' line fell through FUNDING_YEAR_RE's 4-digit
requirement).

Violations go to audit_report.csv for human review and do not themselves
change which rows are published.  The release runner treats the hard reasons
listed below as blocking coverage findings.  Duplicate detection must stay
advisory -- identical per-diem line items are routine and legitimate.

The manifest ties a run's outputs to their inputs (source-PDF SHA-256,
page range, parser git commit, tolerances) so any published number can be
re-derived and challenged.
"""

import hashlib
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone

from .reconcile import OK_TOLERANCE, WARN_TOLERANCE

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

# Stricter than reconcile.parse_amount deliberately: that one SEARCHES for
# an amount inside column-extracted text (lenient by design), so a mangled
# field like "$1,23a.45" would still "parse" via its embedded ".45". Here
# the whole field must be a well-formed amount.
STRICT_AMOUNT_RE = re.compile(r"^-?\$?[\d,]*\.\d{2}$")

# Banner/top-matter phrases that mean segmentation leaked boilerplate
# into the office name.
OFFICE_CONTAMINATION_MARKERS = (
    "FUNDING YEAR",
    "DESCRIPTION",
    "DOCUMENT NO",
    "AUTHORIZATION",
    "ORGANIZATION TOTALS",
    "UNEXPENDED BALANCE",
)

FUNDING_YEAR_RANGE = (2000, 2040)

# These are evidence of malformed published fields, not merely patterns worth
# reviewing.  Keep duplicate detection advisory: the source legitimately
# contains repeated per-diem line items.
HARD_AUDIT_REASONS = frozenset(
    {
        "unparseable_amount",
        "unparseable_date",
        "contaminated_office_name",
        "funding_year_out_of_range",
        "funding_year_not_numeric",
        "salary_row_missing_payee",
        "second_opinion_disagrees",
    }
)


def is_hard_audit_violation(violation: dict) -> bool:
    """Whether an audit violation must block an unattended release."""
    return violation.get("reason") in HARD_AUDIT_REASONS


def audit_rows(rows: list) -> list:
    """Return output-audit violations for published and quarantined rows."""
    violations = []

    def flag(row, reason, detail=""):
        violations.append(
            {
                "reason": reason,
                "source_doc": row.get("source_doc", ""),
                "reference_page": row.get("reference_page", ""),
                "raw_office": row.get("raw_office", ""),
                "payee": row.get("payee", ""),
                "description": (row.get("description", "") or "")[:80],
                "amount": row.get("amount", ""),
                "detail": detail,
            }
        )

    for row in rows:
        amount = row.get("amount", "")
        if amount and not STRICT_AMOUNT_RE.match(amount.strip()):
            flag(row, "unparseable_amount")

        for field in ("date_posted", "start_date", "end_date"):
            value = row.get(field, "")
            if value and not DATE_RE.match(value):
                flag(row, "unparseable_date", detail=f"{field}={value}")

        office = (row.get("raw_office", "") or "").upper()
        if any(marker in office for marker in OFFICE_CONTAMINATION_MARKERS):
            flag(row, "contaminated_office_name")

        funding_year = row.get("funding_year", "")
        if funding_year != "" and funding_year is not None:
            try:
                year = int(funding_year)
                if not (FUNDING_YEAR_RANGE[0] <= year <= FUNDING_YEAR_RANGE[1]):
                    flag(row, "funding_year_out_of_range", detail=str(funding_year))
            except (TypeError, ValueError):
                flag(row, "funding_year_not_numeric", detail=str(funding_year))

        if row.get("salary_flag") == 1 and not (row.get("payee") or "").strip():
            # Bare negative correction lines legitimately have no payee;
            # a positive salary row without one is suspect.
            if not (amount or "").strip().startswith("-"):
                flag(row, "salary_row_missing_payee")

    dupes = Counter(
        (
            row.get("raw_office", ""),
            row.get("funding_year", ""),
            row.get("reference_page", ""),
            row.get("document_number", ""),
            row.get("payee", ""),
            row.get("description", ""),
            row.get("amount", ""),
        )
        for row in rows
    )
    for key, count in dupes.items():
        if count > 1 and (key[6] or key[4]):  # ignore fully-empty tuples
            violations.append(
                {
                    "reason": "duplicate_rows_advisory",
                    "source_doc": rows[0].get("source_doc", "") if rows else "",
                    "reference_page": key[2],
                    "raw_office": key[0],
                    "payee": key[4],
                    "description": (key[5] or "")[:80],
                    "amount": key[6],
                    "detail": f"appears {count}x on same page",
                }
            )

    return violations


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        return ""


def git_dirty() -> bool:
    """Whether tracked files differ from HEAD (untracked release outputs excluded)."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip()) if result.returncode == 0 else True
    except Exception:
        return True


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(pdf_path: str, source_doc: str, first, last, page_offset, stats: dict) -> dict:
    return {
        "source_doc": source_doc,
        "pdf_path": pdf_path,
        "pdf_sha256": sha256_file(pdf_path),
        "page_range": [first, last],
        "page_offset": page_offset,
        "parser_git_commit": git_commit(),
        "parser_git_dirty": git_dirty(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tolerances": {"ok": OK_TOLERANCE, "warn": WARN_TOLERANCE},
        "blocks": stats.get("blocks"),
        "records_published": stats.get("records_published"),
        "records_quarantined": stats.get("records_quarantined"),
        "unparsed": stats.get("unparsed"),
        "bioguide_match_rate": stats.get("bioguide_match_rate"),
        "audit_violations": stats.get("audit_violations"),
        "hard_audit_violations": stats.get("hard_audit_violations"),
        "unparsed_release_blockers": stats.get("unparsed_release_blockers"),
        "banner_check_warnings": stats.get("banner_check_warnings"),
        "pages_read": stats.get("pages_read"),
        "page_classifications": stats.get("page_classifications"),
        "orphan_data_pages": stats.get("orphan_data_pages"),
        "coverage_findings": stats.get("coverage_findings_count"),
    }
