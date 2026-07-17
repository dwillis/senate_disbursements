"""Post-merge cross-report payroll netting.

Item 1 (pipeline._apply_cross_year_release) nets failing NET PAYROLL
EXPENSES segment residuals *within* a single report by grouping on a
normalized office key. Some residuals don't cancel within one report but
do cancel when pooled across sibling reports (114sdoc4 + 114sdoc7 +
114sdoc13 — adjacent reporting periods for the same committees). This
module loads each sibling's reconciliation_report.csv, nets residuals per
normalized office across the group, and rewrites matching rows:

- reconciliation_report.csv: second_opinion -> 'cross_report'
- quarantine.csv -> senate_data_cleaned.csv: validation_status fail ->
  source_mismatch, row moved between files
- manifest.json: records_published / records_quarantined updated

Idempotent: released rows have second_opinion='cross_report' (filtered
out of the input scan) and validation_status='source_mismatch' (filtered
out of the quarantine scan), so re-running is a no-op.
"""

import csv
import json
from pathlib import Path

from .pipeline import _normalize_office_key
from .records import PERSONNEL_ROLLUP_LABELS

RECON_FIELDS = [
    "office", "funding_year", "account", "start_page", "label",
    "check_page", "expected", "actual", "status", "basis",
    "second_opinion", "independent_sum", "context",
]


def _load_reconciliation(doc_dir: Path) -> list[dict]:
    with open(doc_dir / "reconciliation_report.csv", newline="") as f:
        return list(csv.DictReader(f))


def _read_data_csv(path: Path) -> tuple[list[str], list[str], list[list]]:
    """Return (header_note_row, column_header_row, data_rows). The published
    quarantine/cleaned CSVs lead with a one-cell note row above the column
    header; preserve it on rewrite."""
    with open(path, newline="") as f:
        reader = csv.reader(f)
        note = next(reader)
        header = next(reader)
        return note, header, list(reader)


def _write_data_csv(path: Path, note: list[str], header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(note)
        w.writerow(header)
        w.writerows(rows)


def _parse_float(s: str) -> float:
    if s is None or s == "":
        return 0.0
    return float(s)


def _column_index(header: list[str], name: str) -> int:
    return header.index(name)


def _move_rows_for_entry(doc_dir: Path, office: str, start_page: int) -> int:
    """Move quarantine rows matching (raw_office=office, category=NET PAYROLL
    EXPENSES, validation_status=fail) to senate_data_cleaned.csv with
    validation_status flipped to 'source_mismatch'. Constrain reference_page
    to >= the block's start_page as a belt-and-braces guard against any
    cross-block bleed. Return the count moved."""
    q_path = doc_dir / "quarantine.csv"
    c_path = doc_dir / "senate_data_cleaned.csv"
    q_note, q_header, q_rows = _read_data_csv(q_path)
    c_note, c_header, c_rows = _read_data_csv(c_path)

    office_idx = _column_index(q_header, "raw_office")
    cat_idx = _column_index(q_header, "category")
    status_idx = _column_index(q_header, "validation_status")
    page_idx = _column_index(q_header, "reference_page")

    keep = []
    moved = []
    for row in q_rows:
        if (row[office_idx] == office
                and row[cat_idx] in PERSONNEL_ROLLUP_LABELS
                and row[status_idx] == "fail"
                and int(row[page_idx] or 0) >= start_page):
            row[status_idx] = "source_mismatch"
            moved.append(row)
        else:
            keep.append(row)

    if not moved:
        return 0

    _write_data_csv(q_path, q_note, q_header, keep)
    _write_data_csv(c_path, c_note, c_header, c_rows + moved)
    return len(moved)


def _tag_reconciliation(doc_dir: Path, office: str, check_page: str) -> None:
    """Set second_opinion='cross_report' on the matching reconciliation row."""
    path = doc_dir / "reconciliation_report.csv"
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if (row["office"] == office
                and row["check_page"] == check_page
                and row["label"] in PERSONNEL_ROLLUP_LABELS
                and row["basis"] == "segment"):
            row["second_opinion"] = "cross_report"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RECON_FIELDS)
        w.writeheader()
        w.writerows(rows)


def _bump_manifest(doc_dir: Path, moved: int) -> None:
    path = doc_dir / "manifest.json"
    m = json.loads(path.read_text())
    m["records_published"] = (m.get("records_published") or 0) + moved
    m["records_quarantined"] = max(0, (m.get("records_quarantined") or 0) - moved)
    path.write_text(json.dumps(m, indent=2))


def cross_report_release(doc_dirs: list[Path]) -> dict[str, int]:
    """For one sibling group, net failing NET PAYROLL EXPENSES segment
    residuals per normalized office across the reports and release the
    matching rows. Returns {doc_dir: rows_moved}.

    Candidates are segment-fail NET PAYROLL checks whose records are still
    quarantined — i.e. second_opinion is blank, 'inconclusive', or
    'parser_suspect'. Checks already released by the second-opinion
    verifier ('source_mismatch') or item 1's within-report netting
    ('cross_year') are excluded."""
    doc_dirs = [Path(d) for d in doc_dirs]
    RELEASED = {"source_mismatch", "cross_year"}
    candidates = []
    for doc_dir in doc_dirs:
        for row in _load_reconciliation(doc_dir):
            if (row["basis"] == "segment"
                    and row["status"] == "fail"
                    and row["label"] in PERSONNEL_ROLLUP_LABELS
                    and row["second_opinion"] not in RELEASED):
                residual = _parse_float(row["actual"]) - _parse_float(row["expected"])
                candidates.append((
                    doc_dir, row["office"], row["start_page"],
                    row["check_page"], residual,
                ))

    # Group by normalized office key across the sibling group.
    groups: dict[str, list] = {}
    for doc_dir, office, start_page, check_page, residual in candidates:
        key = _normalize_office_key(office)
        groups.setdefault(key, []).append((doc_dir, office, start_page, check_page, residual))

    moved_per_doc = {str(d): 0 for d in doc_dirs}
    for items in groups.values():
        if len(items) < 2:
            continue
        if abs(sum(r for *_, r in items)) > 1.00:
            continue
        for doc_dir, office, start_page, check_page, _ in items:
            _tag_reconciliation(doc_dir, office, check_page)
            moved = _move_rows_for_entry(doc_dir, office, int(start_page or 0))
            if moved:
                _bump_manifest(doc_dir, moved)
            moved_per_doc[str(doc_dir)] += moved

    return moved_per_doc