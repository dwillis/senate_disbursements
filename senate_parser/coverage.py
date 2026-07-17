"""Coverage findings and the fail-closed release gate.

Reconciliation can only validate rows that reached the parser.  Coverage
checks therefore live alongside it and answer the earlier question: did every
requested PDF data page reach a block and produce something classifiable?
"""

import json
from pathlib import Path

from .audit import is_hard_audit_violation


PAGE_LEDGER_FIELDS = [
    "source_doc",
    "source_pdf",
    "source_page",
    "reference_page",
    "classification",
    "assigned_to_block",
    "block_start_page",
    "block_reference_start",
    "office",
    "funding_year",
    "word_count",
    "visual_row_count",
    "reason",
]

COVERAGE_FINDING_FIELDS = [
    "severity",
    "reason",
    "source_doc",
    "source_pdf",
    "reference_page",
    "office",
    "funding_year",
    "label",
    "expected",
    "detail",
    "exception_key",
]


def exception_key(finding: dict) -> str:
    """Return a stable, reviewable identifier for a coverage exception."""
    parts = (
        finding.get("source_doc", ""),
        finding.get("reason", ""),
        finding.get("reference_page", ""),
        finding.get("office", ""),
        finding.get("funding_year", ""),
        finding.get("label", ""),
        finding.get("expected", ""),
    )
    return "|".join("" if value is None else str(value).strip() for value in parts)


def finalize_finding(finding: dict) -> dict:
    row = {field: finding.get(field, "") for field in COVERAGE_FINDING_FIELDS}
    row["exception_key"] = exception_key(row)
    return row


def hard_audit_findings(violations: list[dict], source_pdf: str) -> list[dict]:
    """Translate hard row-audit violations into release-gate findings.

    ``label`` and ``expected`` carry row identifiers so reviewed exceptions
    remain narrow even when several bad rows share a page and office.
    """
    findings = []
    for violation in violations:
        if not is_hard_audit_violation(violation):
            continue
        findings.append(
            finalize_finding(
                {
                    "severity": "error",
                    "reason": f"audit_{violation['reason']}",
                    "source_doc": violation.get("source_doc", ""),
                    "source_pdf": source_pdf,
                    "reference_page": violation.get("reference_page", ""),
                    "office": violation.get("raw_office", ""),
                    "label": violation.get("payee", ""),
                    "expected": violation.get("amount", ""),
                    "detail": " | ".join(
                        part
                        for part in (
                            violation.get("description", ""),
                            violation.get("detail", ""),
                        )
                        if part
                    ),
                }
            )
        )
    return findings


def unparsed_item_findings(items: list[dict], source_doc: str, source_pdf: str) -> list[dict]:
    """Make every unparsed parser item an exact, release-blocking finding."""
    return [
        finalize_finding(
            {
                "severity": "error",
                "reason": f"unparsed_{item.get('reason', 'unknown')}",
                "source_doc": source_doc,
                "source_pdf": source_pdf,
                "reference_page": item.get("page", item.get("start_page", "")),
                "office": item.get("office", ""),
                "funding_year": item.get("funding_year", ""),
                # The raw text makes two failures on the same page separately
                # reviewable, instead of allowing one exception to mask both.
                "label": item.get("text", ""),
                "expected": item.get("start_page", ""),
                "detail": item.get("detail", ""),
            }
        )
        for item in items
    ]


def banner_check_findings(
    checks: list,
    source_doc: str,
    source_pdf: str,
    office,
    funding_year,
    page_offset: int = 0,
) -> list[dict]:
    """Surface non-OK banner cross-checks without treating them as parser proof.

    Banner tables are an independent, useful signal, but historical reports
    contain legitimate source-side residuals and absent components.  These
    warnings are intentionally reviewable rather than release-blocking; the
    inline subtotal and second-opinion gates remain the authoritative checks.
    """
    findings = []
    for check in checks:
        if check.basis != "banner" or check.status in ("ok", "not_applicable"):
            continue
        missing = check.status == "banner_missing"
        findings.append(
            finalize_finding(
                {
                    "severity": "warning",
                    "reason": "banner_check_missing" if missing else "banner_check_discrepancy",
                    "source_doc": source_doc,
                    "source_pdf": source_pdf,
                    "reference_page": check.page + page_offset,
                    "office": office,
                    "funding_year": funding_year,
                    "label": check.label,
                    "expected": check.expected,
                    "detail": (
                        f"status={check.status}; banner={check.expected}; "
                        f"parsed_body={check.actual}. Banner checks are advisory; "
                        "review against the source before treating this as a parser defect."
                    ),
                }
            )
        )
    return findings


def load_approved_exceptions(path) -> set[str]:
    """Load exact exception keys from a reviewed JSON file.

    Accepted formats are ``{"approved": ["key", ...]}`` and
    ``{"approved": [{"key": "...", "reviewer": "..."}, ...]}``.
    Metadata is encouraged, but only the exact key affects the gate.
    """
    if path is None:
        return set()
    data = json.loads(Path(path).read_text())
    approved = data.get("approved", []) if isinstance(data, dict) else data
    keys = set()
    for item in approved:
        key = item.get("key") if isinstance(item, dict) else item
        if key:
            keys.add(str(key))
    return keys


def unapproved_findings(findings: list[dict], approved: set[str]) -> list[dict]:
    """Return release-blocking findings not covered by exact exceptions."""
    return [
        finding
        for finding in findings
        if finding.get("severity") == "error"
        and finding.get("exception_key") not in approved
    ]
