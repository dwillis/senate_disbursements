import csv
import json
from pathlib import Path

import pytest

from senate_parser.coverage import finalize_finding
from senate_parser.release import ReleaseError, ReleaseGateError, discover_report, release_report


def _pdf(path: Path):
    path.write_bytes(b"%PDF-1.7\nfixture")


def test_discovery_prefers_numbered_real_pdfs_and_computes_offsets(tmp_path):
    report_dir = tmp_path / "118sdoc13"
    report_dir.mkdir()
    _pdf(report_dir / "GPO-CDOC-118sdoc13.pdf")
    _pdf(report_dir / "GPO-CDOC-118sdoc13-1.pdf")
    _pdf(report_dir / "GPO-CDOC-118sdoc13-2.pdf")
    (report_dir / "GPO-CDOC-118sdoc13-3.pdf").write_text("upstream HTML error")
    counts = {
        "GPO-CDOC-118sdoc13.pdf": 22,
        "GPO-CDOC-118sdoc13-1.pdf": 10,
        "GPO-CDOC-118sdoc13-2.pdf": 12,
    }

    discovery = discover_report(tmp_path, "118sdoc13", page_counter=lambda path: counts[path.name])

    assert [volume.path.name for volume in discovery.volumes] == [
        "GPO-CDOC-118sdoc13-1.pdf",
        "GPO-CDOC-118sdoc13-2.pdf",
    ]
    assert [volume.page_offset for volume in discovery.volumes] == [0, 10]
    assert discovery.rejected_candidates[0]["reason"] == "invalid_pdf_signature"
    assert discovery.combined_pdf_check["matches_numbered_volume_pages"] is True


def test_discovery_rejects_numbered_volumes_that_do_not_cover_combined_pdf(tmp_path):
    report_dir = tmp_path / "118sdoc13"
    report_dir.mkdir()
    _pdf(report_dir / "GPO-CDOC-118sdoc13.pdf")
    _pdf(report_dir / "GPO-CDOC-118sdoc13-1.pdf")
    _pdf(report_dir / "GPO-CDOC-118sdoc13-2.pdf")
    counts = {
        "GPO-CDOC-118sdoc13.pdf": 23,
        "GPO-CDOC-118sdoc13-1.pdf": 10,
        "GPO-CDOC-118sdoc13-2.pdf": 12,
    }

    with pytest.raises(ReleaseError, match="numbered volumes total 22 pages"):
        discover_report(tmp_path, "118sdoc13", page_counter=lambda path: counts[path.name])


def test_discovery_tolerates_small_front_matter_difference(tmp_path):
    report_dir = tmp_path / "118sdoc13"
    report_dir.mkdir()
    _pdf(report_dir / "GPO-CDOC-118sdoc13.pdf")
    _pdf(report_dir / "GPO-CDOC-118sdoc13-1.pdf")
    _pdf(report_dir / "GPO-CDOC-118sdoc13-2.pdf")
    counts = {
        "GPO-CDOC-118sdoc13.pdf": 2220,
        "GPO-CDOC-118sdoc13-1.pdf": 1040,
        "GPO-CDOC-118sdoc13-2.pdf": 1198,
    }

    discovery = discover_report(tmp_path, "118sdoc13", page_counter=lambda path: counts[path.name])

    assert discovery.combined_pdf_check["matches_numbered_volume_pages"] is True
    assert discovery.combined_pdf_check["front_matter_pages"] == 18
    assert discovery.combined_pdf_check["page_count"] == 2220


def test_discovery_rejects_large_front_matter_difference(tmp_path):
    report_dir = tmp_path / "118sdoc13"
    report_dir.mkdir()
    _pdf(report_dir / "GPO-CDOC-118sdoc13.pdf")
    _pdf(report_dir / "GPO-CDOC-118sdoc13-1.pdf")
    _pdf(report_dir / "GPO-CDOC-118sdoc13-2.pdf")
    counts = {
        "GPO-CDOC-118sdoc13.pdf": 2200,
        "GPO-CDOC-118sdoc13-1.pdf": 1040,
        "GPO-CDOC-118sdoc13-2.pdf": 1198,
    }

    with pytest.raises(ReleaseError, match="front-matter difference 38 exceeds tolerance"):
        discover_report(tmp_path, "118sdoc13", page_counter=lambda path: counts[path.name])


def _fake_runner_factory(findings, calls):
    def runner(pdf_path, source_doc, out_dir, first, last, page_offset, bioguide_matcher):
        calls.append((Path(pdf_path).name, source_doc, first, last, page_offset))
        out = Path(out_dir)
        out.mkdir(parents=True)
        for filename in ("senate_data_cleaned.csv", "quarantine.csv"):
            (out / filename).write_text("note\nvalue\n" + f"{page_offset}\n")
        for filename in (
            "reconciliation_report.csv", "unmatched_senators.csv", "audit_report.csv",
            "block_summaries.csv", "page_ledger.csv", "coverage_report.csv",
        ):
            (out / filename).write_text("value\n" + f"{page_offset}\n")
        (out / "unparsed.jsonl").write_text("")
        (out / "manifest.json").write_text(json.dumps({"page_offset": page_offset}))
        return {
            "blocks": 1,
            "records_published": 1,
            "records_quarantined": 0,
            "unparsed": 0,
            "senator_rows_total": 0,
            "audit_violations": 0,
            "pages_read": last,
            "orphan_data_pages": 0,
            "coverage_findings_count": len(findings),
            "coverage_findings": findings,
            "page_classifications": {"data": last},
        }

    return runner


def _two_volume_report(tmp_path):
    report_dir = tmp_path / "data" / "118sdoc13"
    report_dir.mkdir(parents=True)
    _pdf(report_dir / "GPO-CDOC-118sdoc13-1.pdf")
    _pdf(report_dir / "GPO-CDOC-118sdoc13-2.pdf")
    return tmp_path / "data"


def test_release_publishes_merged_outputs_only_after_gate_passes(tmp_path):
    data_root = _two_volume_report(tmp_path)
    output = tmp_path / "release"
    calls = []

    manifest = release_report(
        "118sdoc13",
        data_root=data_root,
        output_dir=output,
        pipeline_runner=_fake_runner_factory([], calls),
        page_counter=lambda path: {"GPO-CDOC-118sdoc13-1.pdf": 10, "GPO-CDOC-118sdoc13-2.pdf": 12}[path.name],
    )

    assert output.is_dir()
    assert [call[-1] for call in calls] == [0, 10]
    assert all(call[1] == "118sdoc13" for call in calls)
    assert manifest["release_status"] == "passed"
    assert manifest["totals"]["pages_read"] == 22
    with (output / "page_ledger.csv").open() as stream:
        assert [row["value"] for row in csv.DictReader(stream)] == ["0", "10"]


def test_release_gate_preserves_diagnostics_without_publishing(tmp_path):
    data_root = _two_volume_report(tmp_path)
    output = tmp_path / "release"
    finding = finalize_finding(
        {
            "severity": "error",
            "reason": "orphan_data_page",
            "source_doc": "118sdoc13",
            "reference_page": 5,
        }
    )

    with pytest.raises(ReleaseGateError) as caught:
        release_report(
            "118sdoc13",
            data_root=data_root,
            output_dir=output,
            pipeline_runner=_fake_runner_factory([finding], []),
            page_counter=lambda path: 2,
        )

    assert not output.exists()
    assert caught.value.diagnostics_dir.is_dir()
    failed_manifest = json.loads((caught.value.diagnostics_dir / "manifest.json").read_text())
    assert failed_manifest["release_status"] == "blocked"
    assert failed_manifest["coverage"]["unapproved_error_count"] == 2


def test_exact_reviewed_exception_allows_release(tmp_path):
    data_root = _two_volume_report(tmp_path)
    output = tmp_path / "release"
    finding = finalize_finding(
        {
            "severity": "error",
            "reason": "unexpected_zero_records",
            "source_doc": "118sdoc13",
            "reference_page": 9,
            "office": "SENATOR EXAMPLE",
            "funding_year": 2024,
            "label": "TRAVEL",
            "expected": 100.0,
        }
    )
    exceptions = tmp_path / "exceptions.json"
    exceptions.write_text(json.dumps({"approved": [{"key": finding["exception_key"]}]}))

    manifest = release_report(
        "118sdoc13",
        data_root=data_root,
        output_dir=output,
        exceptions_path=exceptions,
        pipeline_runner=_fake_runner_factory([finding], []),
        page_counter=lambda path: 2,
    )

    assert output.is_dir()
    assert manifest["release_status"] == "passed"
    assert manifest["coverage"]["finding_count"] == 2
    assert manifest["coverage"]["unapproved_error_count"] == 0
