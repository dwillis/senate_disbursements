"""Orchestrate extraction -> segmentation -> records -> reconciliation ->
assembly for one PDF volume of a modern-format Senate disbursement report.

Blocks that fail segment-level reconciliation are quarantined rather than
shipped, so a column-attribution bug shows up as a small, reviewable
quarantine file instead of silently wrong numbers in the published CSV.
"""

import csv
import json
import os

from .assemble import CSV_COLUMNS, block_rows, match_senator
from .audit import audit_rows, build_manifest
from .extract import iter_pages
from .records import parse_block
from .reconcile import reconcile_block
from .segment import segment_blocks

CSV_HEADER_NOTE = (
    "Parsed from U.S. Senate disbursement reports (govinfo.gov). "
    "See README.md for methodology and known limitations."
)

# Row-weighted bioguide match rate over senator-office rows below this
# triggers a loud warning: observed rates on 7 clean reports are 93-96%,
# so anything under 90% means the matcher (not the data) regressed.
BIOGUIDE_MATCH_RATE_FLOOR = 0.90


def _write_csv(path: str, rows: list) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([CSV_HEADER_NOTE])
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow([row[col] for col in CSV_COLUMNS])


def run(
    pdf_path: str,
    source_doc: str,
    out_dir: str,
    first: int = 1,
    last=None,
    page_offset: int = 0,
    bioguide_matcher=None,
) -> dict:
    os.makedirs(out_dir, exist_ok=True)

    cleaned_rows = []
    quarantine_rows = []
    reconciliation_rows = []
    unparsed_items = []
    block_summaries = []
    unmatched_senator_rows = []
    senator_rows_total = 0
    senator_rows_matched = 0

    blocks = segment_blocks(iter_pages(pdf_path, first, last))
    for block in blocks:
        result = parse_block(block)
        reconciled = reconcile_block(result)

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
                }
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

        block_summaries.append(
            {
                "office": block.header.office,
                "funding_year": block.header.funding_year,
                "start_page": block.header.start_page + page_offset,
                "status": reconciled.block_status,
                "record_count": len(rows),
                "records_checked": reconciled.records_checked,
                "dollars_checked": reconciled.dollars_checked,
                "dollars_unchecked": reconciled.dollars_unchecked,
                "amount_parse_failures": reconciled.amount_parse_failures,
                "pages_skipped": sum(1 for u in result.unparsed if u.get("reason") == "no_header"),
            }
        )
        if reconciled.block_status == "fail":
            quarantine_rows.extend(rows)
        else:
            cleaned_rows.extend(rows)

    _write_csv(os.path.join(out_dir, "senate_data_cleaned.csv"), cleaned_rows)
    _write_csv(os.path.join(out_dir, "quarantine.csv"), quarantine_rows)

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
    violations = audit_rows(cleaned_rows + quarantine_rows)
    with open(os.path.join(out_dir, "audit_report.csv"), "w", newline="") as f:
        fieldnames = [
            "reason", "source_doc", "reference_page", "raw_office",
            "payee", "description", "amount", "detail",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(violations)

    if block_summaries:
        with open(os.path.join(out_dir, "block_summaries.csv"), "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(block_summaries[0].keys()))
            writer.writeheader()
            writer.writerows(block_summaries)

    stats = {
        "blocks": len(block_summaries),
        "records_published": len(cleaned_rows),
        "records_quarantined": len(quarantine_rows),
        "unparsed": len(unparsed_items),
        "bioguide_match_rate": bioguide_match_rate,
        "senator_rows_total": senator_rows_total,
        "audit_violations": len(violations),
        "block_summaries": block_summaries,
    }

    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(build_manifest(pdf_path, source_doc, first, last, page_offset, stats), f, indent=2)

    return stats
