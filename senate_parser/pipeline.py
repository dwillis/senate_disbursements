"""Orchestrate extraction -> segmentation -> records -> reconciliation ->
assembly for one PDF volume of a modern-format Senate disbursement report.

Rows whose segment fails reconciliation are quarantined rather than
shipped, so a column-attribution bug shows up as a small, reviewable
quarantine file instead of silently wrong numbers in the published CSV.
Failing segments first get an independent second-opinion re-sum (see
second_opinion.py): when it confirms the parser transcribed the rows
faithfully and the source's own printed subtotal is the odd one out, the
rows publish tagged 'source_mismatch' instead of being quarantined.
"""

import csv
import json
import os
import re
from collections import Counter

from .assemble import CSV_COLUMNS, block_rows, match_senator
from .audit import audit_rows, build_manifest
from .coverage import (
    COVERAGE_FINDING_FIELDS,
    PAGE_LEDGER_FIELDS,
    banner_check_findings,
    finalize_finding,
    hard_audit_findings,
    unparsed_item_findings,
)
from .extract import iter_pages
from .records import PERSONNEL_ROLLUP_LABELS, parse_block
from .reconcile import banner_checks, reconcile_block
from .second_opinion import apply_second_opinion
from .segment import parse_banner_summary, segment_blocks

CSV_HEADER_NOTE = (
    "Parsed from U.S. Senate disbursement reports (govinfo.gov). "
    "See README.md for methodology and known limitations."
)

# Row-weighted bioguide match rate over senator-office rows below this
# triggers a loud warning: observed rates on 7 clean reports are 93-96%,
# so anything under 90% means the matcher (not the data) regressed.
BIOGUIDE_MATCH_RATE_FLOOR = 0.90

# Cross-year payroll netting: 113th/114th-congress committee rosters print
# under one resolution account (S.RES. 64B (113TH)) while the money is
# booked against a sibling year's (S.RES. 73B (114TH)). Strip the resolution
# clause and any 'FY YYYY' suffix so the two blocks collapse to one key and
# their offsetting NET PAYROLL residuals can be recognized as source-side
# cross-year attribution. ETHICS COMMITTEE uses 'FY YYYY' instead of an
# S.RES. clause — same treatment.
_OFFICE_KEY_S_RES_RE = re.compile(r"\s*-?\s*S\.?\s*RES\..*$")
_OFFICE_KEY_FY_RE = re.compile(r"\s*-?\s*FY\s*\d{4}.*$")


def _normalize_office_key(office: str) -> str:
    if not office:
        return office
    key = _OFFICE_KEY_S_RES_RE.sub("", office)
    key = _OFFICE_KEY_FY_RE.sub("", key)
    return key.strip()


def _apply_cross_year_release(processed) -> None:
    """For each normalized office, if its failing NET PAYROLL segment checks
    net to ~$0 across that office's blocks, tag the checks 'cross_year' and
    retag their records 'source_mismatch' (publish). See pipeline.run docstring
    for the verified source-side attribution this releases."""
    net_fails_by_office = {}
    for block, result, reconciled, _ in processed:
        key = _normalize_office_key(block.header.office)
        for check in reconciled.checks:
            if check.basis == "segment" and check.status == "fail" and check.label in PERSONNEL_ROLLUP_LABELS:
                net_fails_by_office.setdefault(key, []).append((result, check))
    for items in net_fails_by_office.values():
        if len(items) < 2:
            continue
        if abs(sum(c.actual - c.expected for _, c in items)) > 1.00:
            continue
        for result, check in items:
            check.second_opinion = "cross_year"
            for rec in result.records:
                if rec.validation_status == "fail" and rec.category == check.label:
                    rec.validation_status = "source_mismatch"


def _write_csv(path: str, rows: list) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([CSV_HEADER_NOTE])
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow([row[col] for col in CSV_COLUMNS])


def tag_fy_boundary_patterns(reconciliation_rows: list) -> None:
    """Tag BANNER NET PAYROLL fails whose office spans multiple funding
    years in this report with context='fy_boundary_pattern'.

    870 modern-era NET PAYROLL banner-vs-body fails concentrate in
    FY-boundary offices (a Senator's FY2024 block and FY2025 block in
    the same report): the banner's NET PAYROLL figure is per-FY, but the
    block body can include cross-year bookings, so the check fails for
    structural reasons. Tagging these lets the 2,566 ORGANIZATION
    TOTALS fails stand out as the real review queue.

    Mutates rows in place. Idempotent: re-running on tagged rows is a
    no-op (the tag is only ever set, never cleared, and the condition
    doesn't change)."""
    from collections import defaultdict
    office_fys = defaultdict(set)
    for row in reconciliation_rows:
        office = row.get("office", "")
        if office:
            office_fys[office].add(row.get("funding_year", ""))
    multi_fy_offices = {o for o, fys in office_fys.items() if len(fys) > 1}
    for row in reconciliation_rows:
        if (row.get("basis") == "banner"
                and row.get("label") == "BANNER NET PAYROLL"
                and row.get("status") == "fail"
                and row.get("office", "") in multi_fy_offices):
            row["context"] = "fy_boundary_pattern"


def run(
    pdf_path: str,
    source_doc: str,
    out_dir: str,
    first: int = 1,
    last=None,
    page_offset: int = 0,
    bioguide_matcher=None,
    template: str = None,
) -> dict:
    os.makedirs(out_dir, exist_ok=True)

    # 112th-114th Congress reports use an older table generator with
    # different relative column geometry (see records.ANCHOR_HEADER_WORDS);
    # derive the calibration mode from the congress number unless the
    # caller overrides it.
    if template is None:
        m = re.match(r"(\d{3})sdoc", source_doc)
        template = "anchor" if m and int(m.group(1)) <= 114 else "modern"

    cleaned_rows = []
    quarantine_rows = []
    reconciliation_rows = []
    unparsed_items = []
    block_summaries = []
    second_opinion_audit_rows = []
    unmatched_senator_rows = []
    senator_rows_total = 0
    senator_rows_matched = 0
    processed = []
    page_ledger = []
    coverage_findings = []

    blocks = segment_blocks(iter_pages(pdf_path, first, last), page_ledger=page_ledger)
    for block in blocks:
        result = parse_block(block, template=template)
        reconciled = reconcile_block(result, template=template)

        # Failing segments get an independent amount-column re-sum; when it
        # confirms the parser against the printed subtotal, the segment's
        # records are retagged 'source_mismatch' and publish (must happen
        # before block_rows copies statuses into CSV rows).
        for item in apply_second_opinion(block, result, reconciled, template=template):
            second_opinion_audit_rows.append(
                {
                    "reason": item["reason"],
                    "source_doc": source_doc,
                    "reference_page": block.header.start_page + page_offset,
                    "raw_office": block.header.office,
                    "detail": item["detail"],
                }
            )

        # Advisory banner cross-check: the banner page's summary table is
        # an additional printed source for the block's period totals.
        has_salary = any(r.record_type == "salary" for r in result.records)
        banner = banner_checks(
            parse_banner_summary(block.rows_by_page[block.header.start_page]),
            reconciled,
            block.header.start_page,
            has_salary_records=has_salary,
        )
        reconciled.checks.extend(banner)
        banner_severity = {"ok": 0, "not_applicable": 0, "banner_missing": 1, "warn": 2, "fail": 3}
        banner_status = max((c.status for c in banner), key=lambda s: banner_severity[s])

        processed.append((block, result, reconciled, banner_status))

    # Cross-year release: this era books payroll adjustments against one
    # funding year while itemizing the rows in a sibling year's roster
    # (verified: Cantwell's FY2015 block prints OTHER PERSONNEL
    # COMPENSATION 102,388.98 with no rows, and her FY2016 roster exceeds
    # its own printed NET by exactly that amount). When an office's
    # failing NET PAYROLL residuals cancel out across its blocks, the
    # rows are faithful transcriptions of a source-side cross-year
    # attribution -- release them like any other source_mismatch,
    # recording the distinct 'cross_year' verdict. The netting key is
    # normalized (see _normalize_office_key) so a 113th-congress roster
    # and its 114th-congress sibling — which print under different
    # resolution accounts — pair correctly.
    _apply_cross_year_release(processed)

    for block, result, reconciled, banner_status in processed:
        no_header_pages = {
            item.get("page")
            for item in result.unparsed
            if item.get("reason") == "no_header"
        }
        for page in sorted(p for p in no_header_pages if p is not None):
            coverage_findings.append(
                finalize_finding(
                    {
                        "severity": "error",
                        "reason": "column_calibration_failed",
                        "source_doc": source_doc,
                        "source_pdf": os.path.basename(pdf_path),
                        "reference_page": page + page_offset,
                        "office": block.header.office,
                        "funding_year": block.header.funding_year,
                        "detail": "Data page header was recognized, but its columns could not be calibrated.",
                    }
                )
            )

        if len(block.pages) > 1 and not result.records and not result.subtotals:
            coverage_findings.append(
                finalize_finding(
                    {
                        "severity": "error",
                        "reason": "empty_data_block",
                        "source_doc": source_doc,
                        "source_pdf": os.path.basename(pdf_path),
                        "reference_page": block.header.start_page + page_offset,
                        "office": block.header.office,
                        "funding_year": block.header.funding_year,
                        "detail": f"Block spans {len(block.pages) - 1} data page(s) but produced no records or subtotals.",
                    }
                )
            )

        for check in reconciled.checks:
            reconciliation_rows.append(
                {
                    "office": block.header.office,
                    "funding_year": block.header.funding_year,
                    "account": block.header.account,
                    "start_page": block.header.start_page + page_offset,
                    "label": check.label,
                    "check_page": check.page + page_offset,
                    "expected": check.expected,
                    "actual": round(check.actual, 2),
                    "status": check.status,
                    "basis": check.basis,
                    "second_opinion": check.second_opinion,
                    "independent_sum": check.independent_sum,
                    "context": check.context,
                }
            )
            if check.status == "zero_records":
                coverage_findings.append(
                    finalize_finding(
                        {
                            "severity": "error",
                            "reason": "unexpected_zero_records",
                            "source_doc": source_doc,
                            "source_pdf": os.path.basename(pdf_path),
                            "reference_page": check.page + page_offset,
                            "office": block.header.office,
                            "funding_year": block.header.funding_year,
                            "label": check.label,
                            "expected": check.expected,
                            "detail": "A normally itemized subtotal has no parsed records; exact review is required.",
                        }
                    )
                )

        coverage_findings.extend(
            banner_check_findings(
                reconciled.checks,
                source_doc,
                os.path.basename(pdf_path),
                block.header.office,
                block.header.funding_year,
                page_offset,
            )
        )

        for item in result.unparsed:
            unparsed_items.append(
                {
                    "office": block.header.office,
                    "funding_year": block.header.funding_year,
                    "start_page": block.header.start_page + page_offset,
                    **item,
                    "page": item.get("page", 0) + page_offset,
                }
            )

        rows = block_rows(block, result, source_doc=source_doc, page_offset=page_offset, bioguide_matcher=bioguide_matcher)

        senator_flag, senator_name, _, match_outcome = match_senator(
            block.header.office, block.header.funding_year or "", bioguide_matcher
        )
        if senator_flag and rows:
            senator_rows_total += len(rows)
            if match_outcome == "matched":
                senator_rows_matched += len(rows)
            else:
                unmatched_senator_rows.append(
                    {
                        "senator_name": senator_name,
                        "funding_year": block.header.funding_year,
                        "start_page": block.header.start_page + page_offset,
                        "record_count": len(rows),
                        "outcome": match_outcome,
                    }
                )

        # Per-segment quarantine: each row is routed by its own segment's
        # outcome, so one failing segment no longer holds back the whole
        # block's innocent rows (previously 5,121 correct rows / $2.3M
        # across 7 reports). Only 'fail' quarantines -- 'source_mismatch'
        # (second-opinion-confirmed faithful transcriptions) publishes
        # tagged.
        block_quarantined = [r for r in rows if r["validation_status"] == "fail"]
        quarantine_rows.extend(block_quarantined)
        cleaned_rows.extend(r for r in rows if r["validation_status"] != "fail")

        block_summaries.append(
            {
                "office": block.header.office,
                "funding_year": block.header.funding_year,
                "start_page": block.header.start_page + page_offset,
                "status": reconciled.block_status,
                "banner_status": banner_status,
                "record_count": len(rows),
                "page_count": len(block.pages),
                "continuation_page_count": max(0, len(block.pages) - 1),
                "subtotal_count": len(result.subtotals),
                "rows_quarantined": len(block_quarantined),
                "records_checked": reconciled.records_checked,
                "dollars_checked": reconciled.dollars_checked,
                "dollars_unchecked": reconciled.dollars_unchecked,
                "amount_parse_failures": reconciled.amount_parse_failures,
                "pages_skipped": sum(1 for u in result.unparsed if u.get("reason") == "no_header"),
            }
        )

    source_pdf = os.path.basename(pdf_path)
    for entry in page_ledger:
        entry["source_doc"] = source_doc
        entry["source_pdf"] = source_pdf
        entry["reference_page"] = entry["source_page"] + page_offset
        block_start = entry.get("block_start_page")
        entry["block_reference_start"] = (
            block_start + page_offset if block_start is not None else ""
        )
        if entry["classification"] == "data" and not entry["assigned_to_block"]:
            coverage_findings.append(
                finalize_finding(
                    {
                        "severity": "error",
                        "reason": "orphan_data_page",
                        "source_doc": source_doc,
                        "source_pdf": source_pdf,
                        "reference_page": entry["reference_page"],
                        "detail": "Recognized data page was not assigned to an office/account block.",
                    }
                )
            )

    if last is not None:
        expected_sequence = list(range(first, last + 1))
        actual_sequence = [entry["source_page"] for entry in page_ledger]
        if actual_sequence != expected_sequence:
            coverage_findings.append(
                finalize_finding(
                    {
                        "severity": "error",
                        "reason": "page_range_incomplete",
                        "source_doc": source_doc,
                        "source_pdf": source_pdf,
                        "expected": f"{first}-{last}",
                        "detail": (
                            f"Requested {len(expected_sequence)} sequential pages but consumed "
                            f"{len(actual_sequence)}: {actual_sequence[:3]}...{actual_sequence[-3:]}"
                        ),
                    }
                )
            )

    _write_csv(os.path.join(out_dir, "senate_data_cleaned.csv"), cleaned_rows)
    _write_csv(os.path.join(out_dir, "quarantine.csv"), quarantine_rows)

    tag_fy_boundary_patterns(reconciliation_rows)

    with open(os.path.join(out_dir, "reconciliation_report.csv"), "w", newline="") as f:
        fieldnames = [
            "office",
            "funding_year",
            "account",
            "start_page",
            "label",
            "check_page",
            "expected",
            "actual",
            "status",
            "basis",
            "second_opinion",
            "independent_sum",
            "context",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(reconciliation_rows)

    with open(os.path.join(out_dir, "unparsed.jsonl"), "w") as f:
        for item in unparsed_items:
            f.write(json.dumps(item) + "\n")

    with open(os.path.join(out_dir, "unmatched_senators.csv"), "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["senator_name", "funding_year", "start_page", "record_count", "outcome"]
        )
        writer.writeheader()
        writer.writerows(unmatched_senator_rows)

    # rate is None (not 0%) when matching wasn't attempted at all -- e.g.
    # snapshot-regression runs pass bioguide_matcher=None for determinism.
    bioguide_match_rate = (
        senator_rows_matched / senator_rows_total
        if senator_rows_total and bioguide_matcher is not None
        else None
    )
    if bioguide_match_rate is not None and bioguide_match_rate < BIOGUIDE_MATCH_RATE_FLOOR:
        print(
            f"WARNING: bioguide match rate {bioguide_match_rate:.1%} is below the "
            f"{BIOGUIDE_MATCH_RATE_FLOOR:.0%} floor (observed 93-96% on clean reports) -- "
            f"see {os.path.join(out_dir, 'unmatched_senators.csv')}"
        )

    # Quarantined rows are audited too: they may be released after review
    # and should be just as clean as published ones.
    violations = audit_rows(cleaned_rows + quarantine_rows) + second_opinion_audit_rows
    source_pdf = os.path.basename(pdf_path)
    audit_gate_findings = hard_audit_findings(violations, source_pdf)
    unparsed_gate_findings = unparsed_item_findings(
        unparsed_items, source_doc, source_pdf
    )
    coverage_findings.extend(audit_gate_findings)
    coverage_findings.extend(unparsed_gate_findings)
    with open(os.path.join(out_dir, "audit_report.csv"), "w", newline="") as f:
        fieldnames = [
            "reason", "source_doc", "reference_page", "raw_office",
            "payee", "description", "amount", "detail",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(violations)

    block_summary_fields = [
        "office", "funding_year", "start_page", "status", "banner_status",
        "record_count", "page_count", "continuation_page_count", "subtotal_count",
        "rows_quarantined", "records_checked", "dollars_checked",
        "dollars_unchecked", "amount_parse_failures", "pages_skipped",
    ]
    with open(os.path.join(out_dir, "block_summaries.csv"), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=block_summary_fields)
        writer.writeheader()
        writer.writerows(block_summaries)

    with open(os.path.join(out_dir, "page_ledger.csv"), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PAGE_LEDGER_FIELDS)
        writer.writeheader()
        writer.writerows(page_ledger)

    with open(os.path.join(out_dir, "coverage_report.csv"), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COVERAGE_FINDING_FIELDS)
        writer.writeheader()
        writer.writerows(coverage_findings)

    page_classifications = Counter(entry["classification"] for entry in page_ledger)

    stats = {
        "blocks": len(block_summaries),
        "records_published": len(cleaned_rows),
        "records_quarantined": len(quarantine_rows),
        "unparsed": len(unparsed_items),
        "bioguide_match_rate": bioguide_match_rate,
        "senator_rows_total": senator_rows_total,
        "audit_violations": len(violations),
        "hard_audit_violations": len(audit_gate_findings),
        "unparsed_release_blockers": len(unparsed_gate_findings),
        "banner_check_warnings": sum(
            1 for finding in coverage_findings if finding["reason"].startswith("banner_check_")
        ),
        "block_summaries": block_summaries,
        "pages_read": len(page_ledger),
        "page_classifications": dict(sorted(page_classifications.items())),
        "orphan_data_pages": sum(
            1 for finding in coverage_findings if finding["reason"] == "orphan_data_page"
        ),
        "coverage_findings_count": len(coverage_findings),
        "coverage_findings": coverage_findings,
    }

    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(build_manifest(pdf_path, source_doc, first, last, page_offset, stats), f, indent=2)

    return stats
