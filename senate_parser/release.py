"""Report-level parsing, completeness gating, and atomic publication."""

import argparse
import csv
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .audit import git_commit, git_dirty, sha256_file
from .coverage import load_approved_exceptions, unapproved_findings
from .extract import open_pdf
from .pipeline import CSV_HEADER_NOTE, run as run_pipeline


REPORT_ID_RE = re.compile(r"^(\d{3})sdoc(\d+)$", re.IGNORECASE)
PART_RE_TEMPLATE = r"^GPO-CDOC-{report}-(\d+)\.pdf$"

NOTE_CSVS = ("senate_data_cleaned.csv", "quarantine.csv")
PLAIN_CSVS = (
    "reconciliation_report.csv",
    "unmatched_senators.csv",
    "audit_report.csv",
    "block_summaries.csv",
    "page_ledger.csv",
    "coverage_report.csv",
)
JSONL_FILES = ("unparsed.jsonl",)


class ReleaseError(RuntimeError):
    pass


class ReleaseGateError(ReleaseError):
    def __init__(self, findings: list[dict], diagnostics_dir: Path):
        self.findings = findings
        self.diagnostics_dir = diagnostics_dir
        super().__init__(
            f"release blocked by {len(findings)} unapproved coverage finding(s); "
            f"diagnostics preserved at {diagnostics_dir}"
        )


@dataclass(frozen=True)
class VolumeSpec:
    label: str
    path: Path
    page_count: int
    page_offset: int


@dataclass(frozen=True)
class Discovery:
    report_id: str
    report_dir: Path
    volumes: list[VolumeSpec]
    rejected_candidates: list[dict]
    combined_pdf_check: dict | None = None


def normalize_report_id(value: str) -> str:
    report_id = value.strip().lower()
    if not REPORT_ID_RE.fullmatch(report_id):
        raise ReleaseError(f"invalid report id {value!r}; expected e.g. 118sdoc13")
    return report_id


def _is_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(5) == b"%PDF-"
    except OSError:
        return False


def pdf_page_count(path: Path) -> int:
    count = len(open_pdf(str(path)).pages)
    if count < 1:
        raise ReleaseError(f"PDF has no pages: {path}")
    return count


def _report_directory(data_root: Path, report_id: str) -> Path:
    match = REPORT_ID_RE.fullmatch(report_id)
    assert match is not None
    candidates = [
        data_root / report_id,
        data_root / f"{match.group(1)}_sdoc{match.group(2)}",
    ]
    existing = [path for path in candidates if path.is_dir()]
    if len(existing) != 1:
        if not existing:
            raise ReleaseError(f"report directory not found (tried: {', '.join(map(str, candidates))})")
        raise ReleaseError(f"ambiguous report directories: {', '.join(map(str, existing))}")
    return existing[0]


def discover_report(data_root, report_id: str, page_counter=None) -> Discovery:
    """Select real numbered volumes when present, else the combined PDF."""
    report_id = normalize_report_id(report_id)
    data_root = Path(data_root)
    report_dir = _report_directory(data_root, report_id)
    page_counter = page_counter or pdf_page_count
    part_re = re.compile(PART_RE_TEMPLATE.format(report=re.escape(report_id)), re.IGNORECASE)
    base_name = f"GPO-CDOC-{report_id}.pdf"

    valid_parts = {}
    valid_base = None
    rejected = []
    for path in sorted(report_dir.glob("*.pdf")):
        match = part_re.fullmatch(path.name)
        is_base = path.name.lower() == base_name.lower()
        if not match and not is_base:
            continue
        if not _is_pdf(path):
            rejected.append({"path": str(path), "reason": "invalid_pdf_signature"})
            continue
        if match:
            valid_parts[int(match.group(1))] = path
        elif is_base:
            valid_base = path

    if valid_parts:
        numbers = sorted(valid_parts)
        expected = list(range(1, numbers[-1] + 1))
        if numbers != expected:
            raise ReleaseError(f"non-contiguous PDF volumes for {report_id}: found {numbers}")
        selected = [(f"volume-{number}", valid_parts[number]) for number in numbers]
    else:
        base = report_dir / base_name
        if not _is_pdf(base):
            raise ReleaseError(f"no valid PDF volumes found for {report_id} in {report_dir}")
        selected = [("combined", base)]

    volumes = []
    offset = 0
    for label, path in selected:
        count = page_counter(path)
        volumes.append(VolumeSpec(label=label, path=path, page_count=count, page_offset=offset))
        offset += count
    combined_check = None
    if valid_parts and valid_base is not None:
        combined_count = page_counter(valid_base)
        if combined_count != offset:
            raise ReleaseError(
                f"numbered volumes total {offset} pages but combined PDF has {combined_count}: "
                f"{valid_base}"
            )
        combined_check = {
            "path": str(valid_base),
            "sha256": sha256_file(valid_base),
            "page_count": combined_count,
            "matches_numbered_volume_pages": True,
        }
    return Discovery(report_id, report_dir, volumes, rejected, combined_check)


def _merge_csv(volume_dirs: list[Path], destination: Path, has_note: bool) -> None:
    header = None
    with destination.open("w", newline="") as target:
        writer = None
        if has_note:
            target.write(CSV_HEADER_NOTE + "\n")
        for volume_dir in volume_dirs:
            source = volume_dir / destination.name
            with source.open(newline="") as stream:
                if has_note:
                    next(stream, None)
                reader = csv.DictReader(stream)
                fields = reader.fieldnames or []
                if header is None:
                    header = fields
                    writer = csv.DictWriter(target, fieldnames=header)
                    writer.writeheader()
                elif fields != header:
                    raise ReleaseError(f"CSV schema mismatch while merging {source}")
                assert writer is not None
                writer.writerows(reader)


def merge_volume_outputs(volume_dirs: list[Path], destination: Path) -> None:
    for filename in NOTE_CSVS:
        _merge_csv(volume_dirs, destination / filename, has_note=True)
    for filename in PLAIN_CSVS:
        _merge_csv(volume_dirs, destination / filename, has_note=False)
    for filename in JSONL_FILES:
        with (destination / filename).open("w") as target:
            for volume_dir in volume_dirs:
                with (volume_dir / filename).open() as source:
                    shutil.copyfileobj(source, target)


def _output_inventory(directory: Path) -> dict:
    files = {}
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            files[path.name] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    return files


def _aggregate_stats(stats_by_volume: list[dict]) -> dict:
    sum_fields = (
        "blocks", "records_published", "records_quarantined", "unparsed",
        "senator_rows_total", "audit_violations", "hard_audit_violations",
        "unparsed_release_blockers", "banner_check_warnings", "pages_read",
        "orphan_data_pages", "coverage_findings_count",
    )
    totals = {field: sum(item.get(field, 0) or 0 for item in stats_by_volume) for field in sum_fields}
    classifications = Counter()
    for item in stats_by_volume:
        classifications.update(item.get("page_classifications", {}))
    totals["page_classifications"] = dict(sorted(classifications.items()))
    return totals


def _write_manifest(
    staging: Path,
    discovery: Discovery,
    stats_by_volume: list[dict],
    findings: list[dict],
    approved: set[str],
    exceptions_path,
    status: str,
    bioguide_matching: str,
) -> dict:
    project_root = Path(__file__).resolve().parent.parent
    volumes = []
    for spec in discovery.volumes:
        volume_dir = staging / "volumes" / spec.label
        volume_manifest = volume_dir / "manifest.json"
        volumes.append(
            {
                "label": spec.label,
                "source_path": str(spec.path),
                "source_sha256": sha256_file(spec.path),
                "page_count": spec.page_count,
                "page_offset": spec.page_offset,
                "volume_manifest_sha256": sha256_file(volume_manifest),
            }
        )
    exceptions_file = Path(exceptions_path) if exceptions_path else None
    manifest = {
        "report_id": discovery.report_id,
        "release_status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parser_git_commit": git_commit(),
        "parser_git_dirty": git_dirty(),
        "report_directory": str(discovery.report_dir),
        "volumes": volumes,
        "rejected_candidates": discovery.rejected_candidates,
        "combined_pdf_check": discovery.combined_pdf_check,
        "bioguide_matching": bioguide_matching,
        "exceptions": {
            "path": str(exceptions_file) if exceptions_file else None,
            "sha256": sha256_file(exceptions_file) if exceptions_file else None,
            "approved_key_count": len(approved),
        },
        "totals": _aggregate_stats(stats_by_volume),
        "coverage": {
            "finding_count": len(findings),
            "unapproved_error_count": len(unapproved_findings(findings, approved)),
            "reason_counts": dict(sorted(Counter(item["reason"] for item in findings).items())),
        },
        "dependency_lock_sha256": (
            sha256_file(project_root / "uv.lock") if (project_root / "uv.lock").exists() else None
        ),
        "outputs": _output_inventory(staging),
    }
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def release_report(
    report_id: str,
    data_root="data",
    output_dir=None,
    exceptions_path=None,
    bioguide_matcher=None,
    pipeline_runner=None,
    page_counter=None,
) -> dict:
    """Parse all report volumes and publish only if coverage checks pass."""
    pipeline_runner = pipeline_runner or run_pipeline
    discovery = discover_report(data_root, report_id, page_counter=page_counter)
    output = Path(output_dir) if output_dir else Path("output") / discovery.report_id
    if output.exists():
        raise ReleaseError(f"output already exists; refusing to overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{discovery.report_id}.staging-", dir=output.parent))

    stats_by_volume = []
    try:
        volume_root = staging / "volumes"
        volume_root.mkdir()
        for spec in discovery.volumes:
            volume_dir = volume_root / spec.label
            stats = pipeline_runner(
                str(spec.path),
                source_doc=discovery.report_id,
                out_dir=str(volume_dir),
                first=1,
                last=spec.page_count,
                page_offset=spec.page_offset,
                bioguide_matcher=bioguide_matcher,
            )
            stats_by_volume.append(stats)

        volume_dirs = [volume_root / spec.label for spec in discovery.volumes]
        merge_volume_outputs(volume_dirs, staging)
        findings = [finding for stats in stats_by_volume for finding in stats.get("coverage_findings", [])]
        approved = load_approved_exceptions(exceptions_path)
        blocked = unapproved_findings(findings, approved)
        status = "blocked" if blocked else "passed"
        manifest = _write_manifest(
            staging,
            discovery,
            stats_by_volume,
            findings,
            approved,
            exceptions_path,
            status,
            "enabled" if bioguide_matcher is not None else "disabled",
        )
        if blocked:
            failed = output.parent / (
                f".{discovery.report_id}.failed-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
            )
            os.replace(staging, failed)
            raise ReleaseGateError(blocked, failed)
        os.replace(staging, output)
        return manifest
    except ReleaseGateError:
        raise
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse and verify every PDF volume for one Senate disbursement report."
    )
    parser.add_argument("report_id", help="Report identifier, e.g. 118sdoc13")
    parser.add_argument("--data-root", default="data", help="Directory containing report folders")
    parser.add_argument("--output-dir", help="Final release directory (must not already exist)")
    parser.add_argument("--exceptions", help="Reviewed JSON file of exact coverage exception keys")
    parser.add_argument(
        "--no-bioguide",
        action="store_true",
        help="Explicitly disable senator bioguide matching (recorded as unattempted)",
    )
    args = parser.parse_args(argv)

    matcher = None
    if not args.no_bioguide:
        try:
            from bioguide_matcher import BioguideIdMatcher

            matcher = BioguideIdMatcher()
        except Exception as exc:
            parser.error(f"could not initialize bioguide matcher: {exc}; use --no-bioguide to opt out")
    try:
        manifest = release_report(
            args.report_id,
            data_root=args.data_root,
            output_dir=args.output_dir,
            exceptions_path=args.exceptions,
            bioguide_matcher=matcher,
        )
    except ReleaseError as exc:
        parser.exit(2, f"ERROR: {exc}\n")
    print(
        f"Published {manifest['report_id']}: {manifest['totals']['records_published']} records, "
        f"{manifest['totals']['pages_read']} pages, coverage gate passed."
    )
    return 0
