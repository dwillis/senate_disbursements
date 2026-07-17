#!/usr/bin/env python3
"""Post-merge cross-report payroll netting.

After `senate_parser.pipeline.run` generates each report's CSVs (and
item 1's within-report netting has already released what it can), this
script pools failing NET PAYROLL EXPENSES segment residuals across
sibling reports (adjacent reporting periods for the same committees) and
releases the rows whose residuals cancel cross-report:

- reconciliation_report.csv: second_opinion -> 'cross_report'
- quarantine.csv -> senate_data_cleaned.csv: validation_status fail ->
  source_mismatch, row moved between files
- manifest.json: records_published / records_quarantined updated

Idempotent: re-running on already-released reports is a no-op.

Usage:
    uv run python3 scripts/cross_report_net.py
"""

import argparse
from pathlib import Path

from senate_parser.cross_report import cross_report_release

# Adjacent reporting periods whose committee rosters share authorization
# accounts across congresses. 113th/114th residuals that don't cancel
# within a single report cancel here (verified: ~$27.7M across this group
# after item 1's within-report release). Add more groups as more eras are
# processed.
SIBLING_GROUPS = [
    ["114_sdoc4", "114_sdoc7", "114_sdoc13"],
]

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--data-dir", default=str(DATA_DIR),
                        help=f"data directory containing report folders (default: {DATA_DIR})")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    for group in SIBLING_GROUPS:
        doc_dirs = [data_dir / doc for doc in group]
        missing = [d for d in doc_dirs if not d.exists()]
        if missing:
            print(f"SKIP {group}: missing {[d.name for d in missing]}")
            continue
        moved = cross_report_release(doc_dirs)
        total = sum(moved.values())
        per_doc = ", ".join(f"{Path(d).name}={n}" for d, n in moved.items() if n)
        print(f"{group}: released {total} rows" + (f" ({per_doc})" if per_doc else " (nothing to release)"))


if __name__ == "__main__":
    main()