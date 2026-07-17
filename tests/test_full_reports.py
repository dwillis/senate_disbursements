"""Full-report snapshot regression.

The fast fixture suite pins individual bug classes to single golden
pages; this tier catches what those can't see -- a segmentation change
that merges blocks, a classifier tweak that shifts hundreds of checks
from ok to warn -- by running the whole pipeline over each locally
available report PDF and diffing summary statistics against a committed
snapshot.

Run:      uv run pytest -m slow            (skips reports whose PDFs are absent)
Update:   UPDATE_SNAPSHOTS=1 uv run pytest -m slow
          (regenerates tests/snapshots/<doc>.json -- do this deliberately
          after an intentional parser change, and review the diff)

Bioguide matching is disabled here on purpose: the matcher pulls
congress-legislators data that changes over time, which would make
snapshots flaky. Match-rate monitoring happens per pipeline run instead
(unmatched_senators.csv + the rate floor in pipeline.py).
"""

import json
import os
from collections import Counter
from pathlib import Path

from senate_parser.reports import SNAPSHOT_REPORTS_DICT as REPORTS

import pytest

from senate_parser.pipeline import run
from senate_parser.reconcile import parse_amount

SNAPSHOTS = Path(__file__).parent / "snapshots"
DATA = Path(__file__).parent.parent / "data"


def _dollar_total(csv_path: Path) -> float:
    import csv as csv_mod

    total = 0.0
    with open(csv_path) as f:
        next(f)  # note line
        for row in csv_mod.DictReader(f):
            amt = parse_amount(row["amount"])
            if amt is not None:
                total += amt
    return round(total, 2)


def build_snapshot(source_doc: str, tmp_path: Path) -> dict:
    volumes = REPORTS[source_doc]
    snapshot = {
        "blocks": 0,
        "records_published": 0,
        "records_quarantined": 0,
        "unparsed": 0,
        "check_status_histogram": Counter(),
        "published_dollars": 0.0,
        "quarantined_dollars": 0.0,
        "block_statuses": [],
    }
    offset = 0
    for suffix, pages in volumes:
        pdf = DATA / source_doc / f"GPO-CDOC-{source_doc}{suffix}.pdf"
        out_dir = tmp_path / f"{source_doc}{suffix}"
        stats = run(
            str(pdf), source_doc=source_doc, out_dir=str(out_dir),
            first=1, last=pages, page_offset=offset, bioguide_matcher=None,
        )
        offset += pages

        snapshot["blocks"] += stats["blocks"]
        snapshot["records_published"] += stats["records_published"]
        snapshot["records_quarantined"] += stats["records_quarantined"]
        snapshot["unparsed"] += stats["unparsed"]
        snapshot["published_dollars"] = round(
            snapshot["published_dollars"] + _dollar_total(out_dir / "senate_data_cleaned.csv"), 2
        )
        snapshot["quarantined_dollars"] = round(
            snapshot["quarantined_dollars"] + _dollar_total(out_dir / "quarantine.csv"), 2
        )

        import csv as csv_mod

        with open(out_dir / "reconciliation_report.csv") as f:
            for row in csv_mod.DictReader(f):
                snapshot["check_status_histogram"][row["status"]] += 1

        for summary in stats["block_summaries"]:
            snapshot["block_statuses"].append(
                {
                    "office": summary["office"],
                    "funding_year": summary["funding_year"],
                    "start_page": summary["start_page"],
                    "status": summary["status"],
                    "record_count": summary["record_count"],
                }
            )

    snapshot["check_status_histogram"] = dict(sorted(snapshot["check_status_histogram"].items()))
    return snapshot


def _pdfs_present(source_doc: str) -> bool:
    return all(
        (DATA / source_doc / f"GPO-CDOC-{source_doc}{suffix}.pdf").exists()
        for suffix, _ in REPORTS[source_doc]
    )


@pytest.mark.slow
@pytest.mark.parametrize("source_doc", sorted(REPORTS))
def test_report_matches_snapshot(source_doc, tmp_path):
    if not _pdfs_present(source_doc):
        pytest.skip(f"{source_doc} PDFs not present locally")

    snapshot_path = SNAPSHOTS / f"{source_doc}.json"
    actual = build_snapshot(source_doc, tmp_path)

    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOTS.mkdir(exist_ok=True)
        snapshot_path.write_text(json.dumps(actual, indent=1) + "\n")
        pytest.skip(f"snapshot updated: {snapshot_path}")

    if not snapshot_path.exists():
        pytest.fail(f"no snapshot for {source_doc}; generate with UPDATE_SNAPSHOTS=1 uv run pytest -m slow")

    expected = json.loads(snapshot_path.read_text())

    # Compare scalars first for a readable failure, then the per-block detail.
    for key in (
        "blocks",
        "records_published",
        "records_quarantined",
        "unparsed",
        "published_dollars",
        "quarantined_dollars",
        "check_status_histogram",
    ):
        assert actual[key] == expected[key], f"{source_doc}: {key} drifted"

    assert actual["block_statuses"] == expected["block_statuses"], f"{source_doc}: per-block detail drifted"
