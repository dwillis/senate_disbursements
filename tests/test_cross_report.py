"""Tests for the cross-report payroll-netting post-merge step.

After item 1 (within-report netting via _normalize_office_key) runs in the
pipeline, some failing NET PAYROLL EXPENSES segments still have residuals
that don't cancel within a single report but do cancel when pooled across
sibling reports (114sdoc4+7+13 — adjacent reporting periods for the same
committees). This post-merge step loads each sibling's
reconciliation_report.csv, nets residuals per normalized office across the
group, and rewrites matching rows: validation_status fail→source_mismatch
(moving the row quarantine→cleaned) and second_opinion→cross_report.
"""

import csv
import json
from pathlib import Path

from senate_parser.assemble import CSV_COLUMNS
from senate_parser.cross_report import cross_report_release

ROLLUP = "NET PAYROLL EXPENSES"
RECON_FIELDS = [
    "office", "funding_year", "account", "start_page", "label",
    "check_page", "expected", "actual", "status", "basis",
    "second_opinion", "independent_sum", "context",
]
HEADER_NOTE = "Parsed from U.S. Senate disbursement reports (govinfo.gov). See README.md for methodology and known limitations."


def _write_report(doc_dir: Path, *, office: str, residual: float,
                  quarantine_count: int, second_opinion: str = "",
                  start_page: int = 100) -> None:
    """Create a minimal synthetic report directory with one NET PAYROLL
    EXPENSES segment-fail check and `quarantine_count` matching quarantine
    rows. expected=0, actual=residual so actual-expected == residual."""
    doc_dir.mkdir(parents=True, exist_ok=True)
    expected = 0.0
    actual = residual
    with open(doc_dir / "reconciliation_report.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RECON_FIELDS)
        w.writeheader()
        w.writerow({
            "office": office, "funding_year": "", "account": "A",
            "start_page": start_page, "label": ROLLUP,
            "check_page": start_page + 1, "expected": expected,
            "actual": actual, "status": "fail", "basis": "segment",
            "second_opinion": second_opinion, "independent_sum": "",
            "context": "",
        })
    with open(doc_dir / "senate_data_cleaned.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([HEADER_NOTE])
        w.writerow(CSV_COLUMNS)
    with open(doc_dir / "quarantine.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([HEADER_NOTE])
        w.writerow(CSV_COLUMNS)
        for i in range(quarantine_count):
            w.writerow([
                doc_dir.name, "", "", "", office, "", "", "",
                start_page + i, "", "", "", "", f"row {i}", "",
                f"{abs(residual):.2f}", "PAYEE", "fail", ROLLUP,
            ])
    with open(doc_dir / "manifest.json", "w") as f:
        json.dump({
            "source_doc": doc_dir.name,
            "records_published": 0,
            "records_quarantined": quarantine_count,
        }, f)


def _read_rows(path: Path) -> list[dict]:
    # The published CSVs lead with a one-cell note row above the column
    # header; skip it so DictReader keys on the real columns.
    with open(path, newline="") as f:
        next(f)  # skip header note
        return list(csv.DictReader(f))


def _quarantine_rows(doc_dir: Path) -> list[dict]:
    return _read_rows(doc_dir / "quarantine.csv")


def _cleaned_rows(doc_dir: Path) -> list[dict]:
    return _read_rows(doc_dir / "senate_data_cleaned.csv")


def _recon_rows(doc_dir: Path) -> list[dict]:
    # reconciliation_report.csv has no header-note row — just the column
    # header, so DictReader keys on it directly.
    with open(doc_dir / "reconciliation_report.csv", newline="") as f:
        return list(csv.DictReader(f))


def _manifest(doc_dir: Path) -> dict:
    return json.loads((doc_dir / "manifest.json").read_text())


def test_cross_report_release_pairs_offsetting_residuals(tmp_path):
    # 114sdoc4 has -2512990.93; 114sdoc7 has +2512990.93 — same normalized
    # office (S.RES. clause stripped), residuals cancel.
    office_a = "ARMED SERVICES ARMED SERVICES - S.RES. 64B (113TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    office_b = "ARMED SERVICES ARMED SERVICES - S.RES. 73B (114TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    a = tmp_path / "114sdoc4"
    b = tmp_path / "114sdoc7"
    _write_report(a, office=office_a, residual=-2512990.93, quarantine_count=3)
    _write_report(b, office=office_b, residual=2512990.93, quarantine_count=2)

    moved = cross_report_release([a, b])

    assert moved == {str(a): 3, str(b): 2}
    # Rows moved quarantine -> cleaned with validation_status flipped.
    assert all(r["validation_status"] == "source_mismatch" for r in _cleaned_rows(a))
    assert all(r["validation_status"] == "source_mismatch" for r in _cleaned_rows(b))
    assert _quarantine_rows(a) == []
    assert _quarantine_rows(b) == []
    # Reconciliation check tagged cross_report.
    assert all(r["second_opinion"] == "cross_report" for r in _recon_rows(a))
    assert all(r["second_opinion"] == "cross_report" for r in _recon_rows(b))
    # Manifest counts updated.
    assert _manifest(a)["records_published"] == 3
    assert _manifest(a)["records_quarantined"] == 0
    assert _manifest(b)["records_published"] == 2
    assert _manifest(b)["records_quarantined"] == 0


def test_cross_report_release_requires_residuals_to_cancel(tmp_path):
    office = "ARMED SERVICES ARMED SERVICES - S.RES. 64B (113TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    office_b = "ARMED SERVICES ARMED SERVICES - S.RES. 73B (114TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    a = tmp_path / "114sdoc4"
    b = tmp_path / "114sdoc7"
    _write_report(a, office=office, residual=-1000.0, quarantine_count=1)
    _write_report(b, office=office_b, residual=500.0, quarantine_count=1)

    moved = cross_report_release([a, b])

    assert moved == {str(a): 0, str(b): 0}
    assert all(r["validation_status"] == "fail" for r in _quarantine_rows(a))
    assert all(r["second_opinion"] == "" for r in _recon_rows(a))


def test_cross_report_release_requires_two_or_more_entries(tmp_path):
    # Only one report has the office — no sibling to net against.
    office = "ARMED SERVICES ARMED SERVICES - S.RES. 64B (113TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    a = tmp_path / "114sdoc4"
    b = tmp_path / "114sdoc7"
    _write_report(a, office=office, residual=-1000.0, quarantine_count=1)
    _write_report(b, office="UNRELATED OFFICE", residual=1000.0, quarantine_count=1)

    moved = cross_report_release([a, b])

    assert moved == {str(a): 0, str(b): 0}


def test_cross_report_release_skips_already_cross_year(tmp_path):
    # Item 1 (within-report netting) already released this row; the
    # cross-report step must not touch it again.
    office_a = "ARMED SERVICES ARMED SERVICES - S.RES. 64B (113TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    office_b = "ARMED SERVICES ARMED SERVICES - S.RES. 73B (114TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    a = tmp_path / "114sdoc4"
    b = tmp_path / "114sdoc7"
    _write_report(a, office=office_a, residual=-1000.0, quarantine_count=1,
                  second_opinion="cross_year")
    _write_report(b, office=office_b, residual=1000.0, quarantine_count=1)

    moved = cross_report_release([a, b])

    # a's row already released by item 1 — skip it. b has no sibling to net
    # against (a's residual is filtered out), so b stays fail too.
    assert moved == {str(a): 0, str(b): 0}
    assert _recon_rows(a)[0]["second_opinion"] == "cross_year"


def test_cross_report_release_is_idempotent(tmp_path):
    office_a = "ARMED SERVICES ARMED SERVICES - S.RES. 64B (113TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    office_b = "ARMED SERVICES ARMED SERVICES - S.RES. 73B (114TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    a = tmp_path / "114sdoc4"
    b = tmp_path / "114sdoc7"
    _write_report(a, office=office_a, residual=-1000.0, quarantine_count=2)
    _write_report(b, office=office_b, residual=1000.0, quarantine_count=1)

    first = cross_report_release([a, b])
    second = cross_report_release([a, b])

    assert first == {str(a): 2, str(b): 1}
    assert second == {str(a): 0, str(b): 0}


def test_cross_report_release_does_not_touch_unrelated_offices_in_same_doc(tmp_path):
    # A doc may have multiple failing segments; only the one whose residual
    # nets cross-report should be released.
    office_a = "ARMED SERVICES ARMED SERVICES - S.RES. 64B (113TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    office_b = "ARMED SERVICES ARMED SERVICES - S.RES. 73B (114TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    a = tmp_path / "114sdoc4"
    b = tmp_path / "114sdoc7"
    _write_report(a, office=office_a, residual=-1000.0, quarantine_count=1)
    _write_report(b, office=office_b, residual=1000.0, quarantine_count=1)
    # Add an unrelated failing office in doc a that has no sibling.
    _append_unrelated_fail(a, office="LONE OFFICE WITH NO SIBLING", residual=-500.0)

    moved = cross_report_release([a, b])

    # a's ARMED SERVICES row released (1); LONE OFFICE row stays fail.
    assert moved[str(a)] == 1
    quarantined = _quarantine_rows(a)
    assert len(quarantined) == 1
    assert quarantined[0]["raw_office"] == "LONE OFFICE WITH NO SIBLING"
    assert quarantined[0]["validation_status"] == "fail"


def _append_unrelated_fail(doc_dir: Path, *, office: str, residual: float) -> None:
    """Add a second segment-fail check + quarantine row to an existing
    synthetic report (for the 'unrelated office stays fail' test)."""
    with open(doc_dir / "reconciliation_report.csv", "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RECON_FIELDS)
        w.writerow({
            "office": office, "funding_year": "", "account": "A",
            "start_page": 200, "label": ROLLUP, "check_page": 201,
            "expected": 0.0, "actual": residual, "status": "fail",
            "basis": "segment", "second_opinion": "", "independent_sum": "",
            "context": "",
        })
    with open(doc_dir / "quarantine.csv", "a", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            doc_dir.name, "", "", "", office, "", "", "",
            200, "", "", "", "", "unrelated", "",
            f"{abs(residual):.2f}", "PAYEE", "fail", ROLLUP,
        ])
    m = _manifest(doc_dir)
    m["records_quarantined"] = m["records_quarantined"] + 1
    (doc_dir / "manifest.json").write_text(json.dumps(m))