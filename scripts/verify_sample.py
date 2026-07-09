#!/usr/bin/env python3
"""Manual spot-check tool: sample published/quarantined CSV rows, render
the exact PDF page with the matching row highlighted, and produce a
verification log for a human to fill in a verdict.

This is a read-only QA script -- it never touches the production
pipeline (senate_parser/extract.py, rows.py, records.py, reconcile.py)
and never modifies committed data.

Usage:
    # Stratified sample across validation_status / disposition / record_type
    uv run python3 scripts/verify_sample.py 118sdoc13

    # Ad hoc: pull up one specific row you already know about
    uv run python3 scripts/verify_sample.py 118sdoc13 --reference-page 1000 --payee TABLER

See the "Manual Verification Process" plan for the full workflow this
supports: sample -> open verification_samples/<report>/<timestamp>/ ->
compare each image to verification_log.csv -> fill in reviewer_verdict.

Precondition: data/<report>/senate_data_cleaned.csv and quarantine.csv
must be the current 19-column schema (validation_status, category). If
they're the stale 17-column files, re-run senate_parser.pipeline.run for
both volumes and re-merge before sampling -- this script fails fast with
that instruction rather than guessing.

Known limitation: on one report (117sdoc8), the natural-pdf library's own
Page.width/.height are wrong for every content page, which corrupts
page.render(highlights=[...])'s internal layout even though every word's
own coordinates (used for matching) stay correct. Verified by
cross-checking against pypdfium2's raw page size. Images on affected
pages render with no highlight box (filename prefixed NOHIGHLIGHT_,
`highlight_reliable=False` in the log) rather than a silently wrong one --
the row identified in the log fields is still correct, just not boxed.
"""

import argparse
import csv
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pypdfium2 as pdfium

from senate_parser.extract import open_pdf, page_words
from senate_parser.reconcile import parse_amount
from senate_parser.records import calibrate_columns
from senate_parser.rows import Row, cluster_rows
from senate_parser.segment import header_row_top

# Per-report (volume suffix, page count), in order. Duplicated from
# tests/test_full_reports.py rather than imported -- importing from a
# test module across the two entrypoints is more coupling than it's
# worth for a static table. Keep in sync with that file if a new report
# is added.
REPORTS = {
    "117sdoc8": [("-1", 1040), ("-2", 1198)],
    "118sdoc2": [("-1", 1291), ("-2", 1348)],
    "118sdoc11": [("-1", 1387), ("-2", 1278)],
    "118sdoc13": [("-1", 1495), ("-2", 1484)],
    "119sdoc3": [("-1", 1335), ("-2", 1350)],
    "119sdoc5": [("-1", 1476), ("-2", 1542)],
    "119sdoc6": [("-1", 1259), ("-2", 1264)],
}

REQUIRED_COLS = {"validation_status", "category"}

LOG_FIELDS = [
    "sample_id", "source_doc", "disposition", "volume", "reference_page", "raw_page",
    "validation_status", "category", "match_method", "match_confidence", "tie_candidates",
    "highlight_reliable", "document_number", "record_type", "raw_office", "payee", "amount",
    "description", "date_posted", "start_date", "end_date", "image_path",
    "reviewer_verdict", "reviewer_notes",
]

DATA_DIR = "data"


def resolve_page(report: str, reference_page: int):
    """reference_page is combined across volumes (assemble.py's
    page_offset arithmetic); invert it to (volume_suffix, raw_page)."""
    offset = 0
    for suffix, pages in REPORTS[report]:
        if reference_page <= offset + pages:
            return suffix, reference_page - offset
        offset += pages
    raise ValueError(f"reference_page {reference_page} is out of range for {report} (total {offset} pages)")


def _is_page_label_row(row: Row, amount_right: float) -> bool:
    """Duplicated from records._is_page_label_row (3 lines) rather than
    imported: avoids coupling this QA script to a production internal
    that can change without notice. See records.py for the full
    rationale (rotated "B-###" marginal page labels)."""
    return bool(row.words) and all(w.x0 >= amount_right for w in row.words)


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        return ""


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# natural_pdf's Page.width/.height were found to be flatly wrong on one
# report (117sdoc8: reports (792,612) for every content page regardless
# of that page's actual size) while every Word's own x0/top/x1/bottom
# stayed correct -- verified by cross-checking against pypdfium2's raw
# page.get_size() (656.9x422.9, matching every other report exactly) and
# by the fact that text-based row matching (which only uses Word
# coordinates) kept finding the right row while page.render(highlights=
# [...]) (which uses page.width/.height internally to lay out the
# highlight) drew the box on a visibly wrong row every time. Rather than
# reverse-engineer that internal transform, cross-check page size against
# pypdfium2 directly and skip highlighting -- never guess -- when they
# disagree.
PAGE_SIZE_TOLERANCE = 2.0


class PdfCache:
    """Caches opened PDFs, extracted+clustered rows, and page-size
    reliability checks for one report, so a scattered random sample
    doesn't re-open/re-extract a ~1,300+ page volume per row."""

    def __init__(self, report: str):
        self.report = report
        self._pdfs = {}
        self._pdfium_docs = {}
        self._page_rows = {}
        self._reliable = {}

    def _path(self, suffix: str) -> str:
        return os.path.join(DATA_DIR, self.report, f"GPO-CDOC-{self.report}{suffix}.pdf")

    def _pdf(self, suffix: str):
        if suffix not in self._pdfs:
            self._pdfs[suffix] = open_pdf(self._path(suffix))
        return self._pdfs[suffix]

    def _pdfium(self, suffix: str):
        if suffix not in self._pdfium_docs:
            self._pdfium_docs[suffix] = pdfium.PdfDocument(self._path(suffix))
        return self._pdfium_docs[suffix]

    def get(self, suffix: str, raw_page: int):
        key = (suffix, raw_page)
        if key not in self._page_rows:
            pdf = self._pdf(suffix)
            words = page_words(pdf, raw_page)
            rows = cluster_rows(words)
            page_obj = pdf.pages[raw_page - 1]
            self._page_rows[key] = (page_obj, rows)
        return self._page_rows[key]

    def highlight_reliable(self, suffix: str, raw_page: int, page_obj) -> bool:
        key = (suffix, raw_page)
        if key not in self._reliable:
            true_w, true_h = self._pdfium(suffix)[raw_page - 1].get_size()
            self._reliable[key] = (
                abs(page_obj.width - true_w) <= PAGE_SIZE_TOLERANCE
                and abs(page_obj.height - true_h) <= PAGE_SIZE_TOLERANCE
            )
        return self._reliable[key]


def record_type_of(row: dict) -> str:
    return "salary" if row.get("salary_flag") == "1" else "expense"


def load_report_rows(report: str) -> list:
    rows = []
    for disposition, filename in (("cleaned", "senate_data_cleaned.csv"), ("quarantine", "quarantine.csv")):
        path = os.path.join(DATA_DIR, report, filename)
        with open(path) as f:
            next(f)  # skip the CSV_HEADER_NOTE line (see pipeline.CSV_HEADER_NOTE)
            reader = csv.DictReader(f)
            missing = REQUIRED_COLS - set(reader.fieldnames or [])
            if missing:
                sys.exit(
                    f"{path} is missing {sorted(missing)} -- it's the stale pre-validation "
                    f"schema. Re-run senate_parser.pipeline.run for {report}'s volumes and "
                    f"re-merge before sampling (see the plan's Step 0)."
                )
            for row in reader:
                row["_disposition"] = disposition
                rows.append(row)
    return rows


# Fixed, canonical enumeration (not derived from the data) so an absent
# combination -- e.g. a report with zero 'warn' rows anywhere -- is
# reported as a genuine gap rather than simply never appearing. Deriving
# "all strata" from the observed rows themselves (defaultdict of only
# populated keys) can never produce an empty stratum by construction,
# which would silence exactly the transparency this is meant to provide.
ALL_STATUSES = ("ok", "warn", "fail", "unchecked", "source_mismatch")
ALL_DISPOSITIONS = ("cleaned", "quarantine")
ALL_RECORD_TYPES = ("salary", "expense")


def all_possible_strata() -> list:
    return [(d, s, t) for d in ALL_DISPOSITIONS for s in ALL_STATUSES for t in ALL_RECORD_TYPES]


def stratify(rows: list) -> dict:
    strata = defaultdict(list)
    for row in rows:
        key = (row["_disposition"], row.get("validation_status") or "unchecked", record_type_of(row))
        strata[key].append(row)
    return dict(strata)


def sample_strata(strata: dict, rng: random.Random, n_per_stratum: int, min_per_stratum: int, max_total: int):
    """Every non-empty stratum gets its minimum first; round-robin top-ups
    toward n_per_stratum until max_total. (quarantine, ok, *) strata --
    rows correct on their own but held back because a sibling segment in
    the same block failed -- are prioritized if trimming is forced."""

    def sort_key(k):
        disposition, status, _record_type = k
        is_priority = disposition == "quarantine" and status == "ok"
        return (0 if is_priority else 1, k)

    keys = sorted(all_possible_strata(), key=sort_key)
    shuffled = {}
    for k in keys:
        pool = strata.get(k, [])[:]
        rng.shuffle(pool)
        shuffled[k] = pool

    taken = {k: 0 for k in keys}
    total = 0

    for k in keys:
        if total >= max_total:
            break
        want = min(min_per_stratum, len(shuffled[k]))
        taken[k] = want
        total += want

    changed = True
    while total < max_total and changed:
        changed = False
        for k in keys:
            if total >= max_total:
                break
            if taken[k] >= n_per_stratum or taken[k] >= len(shuffled[k]):
                continue
            taken[k] += 1
            total += 1
            changed = True

    sampled = []
    skipped_empty = []
    for k in keys:
        if not shuffled[k]:
            skipped_empty.append(k)
            continue
        sampled.extend(shuffled[k][: taken[k]])
    return sampled, skipped_empty, keys


def get_data_rows(rows: list, cols, header_top: float) -> list:
    return [
        r for r in sorted(rows, key=lambda r: r.top)
        if r.top > header_top + 20 and not _is_page_label_row(r, cols.amount[1])
    ]


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().upper())


@dataclass
class MatchResult:
    rows: list = field(default_factory=list)
    confidence: str = "none"  # 'high' / 'medium' / 'low' / 'none'
    method: str = "not_found"


def locate_row(csv_row: dict, data_rows: list, cols) -> MatchResult:
    """Amount-centered matching cascade -- see the plan for the full
    rationale. Amount is the one field printed on the exact physical row
    for nearly every record type; document_number only narrows a search
    window, since a continuation/subline row inherits its doc number from
    context rather than having it printed on its own line."""
    target_amount = parse_amount(csv_row.get("amount", ""))
    doc_number = (csv_row.get("document_number") or "").strip()

    def row_amount(row: Row):
        return parse_amount(row.text_in(*cols.amount))

    def row_doc(row: Row):
        words = row.words_in(*cols.document)
        return words[0].text if words else ""

    window = data_rows
    used_window = False

    if doc_number:
        doc_hit_indices = [i for i, r in enumerate(data_rows) if row_doc(r) == doc_number]
        if len(doc_hit_indices) == 1:
            start_idx = doc_hit_indices[0]
            header_row = data_rows[start_idx]
            if target_amount is not None and row_amount(header_row) == target_amount:
                return MatchResult([header_row], "high", "document_number")
            end_idx = len(data_rows)
            for i in range(start_idx + 1, len(data_rows)):
                r = data_rows[i]
                if row_doc(r) or r.words_in(*cols.payee):
                    end_idx = i
                    break
            window = data_rows[start_idx:end_idx]
            used_window = True

    if target_amount is None:
        return MatchResult([], "none", "no_amount_to_match")

    amount_hits = [r for r in window if row_amount(r) == target_amount]
    if not amount_hits and used_window:
        amount_hits = [r for r in data_rows if row_amount(r) == target_amount]
        used_window = False

    if not amount_hits:
        return MatchResult([], "none", "not_found")

    if len(amount_hits) == 1:
        return MatchResult(amount_hits, "high" if used_window else "medium",
                            "doc_window+amount" if used_window else "amount")

    payee = _normalize(csv_row.get("payee", ""))
    if payee:
        payee_hits = [
            r for r in amount_hits
            if payee in _normalize(r.text_in(*cols.payee)) or _normalize(r.text_in(*cols.payee)) in payee
        ]
        if len(payee_hits) == 1:
            return MatchResult(payee_hits, "high", "amount+payee")
        if payee_hits:
            amount_hits = payee_hits

    description = _normalize(csv_row.get("description", ""))
    if description:
        probe = description[:20]
        desc_hits = [r for r in amount_hits if probe and probe in _normalize(r.text_in(*cols.description))]
        if len(desc_hits) == 1:
            return MatchResult(desc_hits, "medium", "amount+description")

    return MatchResult(amount_hits, "low", "amount_tied")


def render_match(page_obj, match: MatchResult, out_path: str, highlight_reliable: bool, resolution: int = 200) -> None:
    if not match.rows or not highlight_reliable:
        img = page_obj.render(resolution=resolution)
    else:
        highlights = []
        for r in match.rows:
            bottom = max(w.bottom for w in r.words)
            highlights.append({"bbox": (0, r.top - 2, page_obj.width, bottom + 2), "color": "red"})
        img = page_obj.render(resolution=resolution, highlights=highlights)
    img.save(out_path)


def build_log_row(sample_id: int, report: str, row: dict, suffix: str, raw_page: int,
                   match: MatchResult, highlight_reliable: bool, image_rel_path: str) -> dict:
    return {
        "sample_id": sample_id,
        "source_doc": report,
        "disposition": row["_disposition"],
        "volume": suffix.lstrip("-"),
        "reference_page": row.get("reference_page", ""),
        "raw_page": raw_page,
        "validation_status": row.get("validation_status", ""),
        "category": row.get("category", ""),
        "match_method": match.method,
        "match_confidence": match.confidence,
        "tie_candidates": len(match.rows) if match.confidence == "low" else "",
        "highlight_reliable": highlight_reliable,
        "document_number": row.get("document_number", ""),
        "record_type": record_type_of(row),
        "raw_office": row.get("raw_office", ""),
        "payee": row.get("payee", ""),
        "amount": row.get("amount", ""),
        "description": row.get("description", ""),
        "date_posted": row.get("date_posted", ""),
        "start_date": row.get("start_date", ""),
        "end_date": row.get("end_date", ""),
        "image_path": image_rel_path,
        "reviewer_verdict": "",
        "reviewer_notes": "",
    }


def process_one(report: str, cache: PdfCache, row: dict, sample_id: int, images_dir: str) -> dict:
    reference_page = int(row["reference_page"])
    suffix, raw_page = resolve_page(report, reference_page)
    page_obj, pdf_rows = cache.get(suffix, raw_page)
    highlight_reliable = cache.highlight_reliable(suffix, raw_page, page_obj)

    header_top = header_row_top(pdf_rows)
    cols = calibrate_columns(pdf_rows)
    if cols is None:
        match = MatchResult([], "none", "no_header_on_page")
    else:
        data_rows = get_data_rows(pdf_rows, cols, header_top)
        match = locate_row(row, data_rows, cols)

    tag = {"none": "NOTFOUND_", "low": "TIED_"}.get(match.confidence, "")
    if not highlight_reliable:
        tag = "NOHIGHLIGHT_" + tag
    filename = f"{sample_id:03d}_{tag}{row['_disposition']}_{row.get('validation_status') or 'unchecked'}_{record_type_of(row)}.png"
    render_match(page_obj, match, os.path.join(images_dir, filename), highlight_reliable)

    return build_log_row(sample_id, report, row, suffix, raw_page, match, highlight_reliable,
                          os.path.join("images", filename))


def run_sample(report: str, args) -> None:
    seed = args.seed if args.seed is not None else random.SystemRandom().randint(0, 2**31 - 1)
    rng = random.Random(seed)

    rows = load_report_rows(report)
    strata = stratify(rows)
    sampled, skipped_empty, all_keys = sample_strata(
        strata, rng, args.n_per_stratum, args.min_per_stratum, args.max_total
    )
    rng.shuffle(sampled)  # interleave strata in output order

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join("verification_samples", report, timestamp)
    images_dir = os.path.join(out_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    cache = PdfCache(report)
    log_rows = [process_one(report, cache, row, i, images_dir) for i, row in enumerate(sampled, start=1)]

    with open(os.path.join(out_dir, "verification_log.csv"), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        writer.writeheader()
        writer.writerows(log_rows)

    def key_str(k):
        return f"{k[0]}/{k[1]}/{k[2]}"

    manifest = {
        "report": report,
        "seed": seed,
        "args": {k: v for k, v in vars(args).items() if k != "report"},
        "git_commit": _git_commit(),
        "pdf_sha256": {
            suffix: _sha256(os.path.join(DATA_DIR, report, f"GPO-CDOC-{report}{suffix}.pdf"))
            for suffix, _ in REPORTS[report]
        },
        "strata_requested": len(all_keys),
        "strata_populated": sorted(key_str(k) for k in all_keys if k not in skipped_empty),
        "strata_skipped_empty": sorted(key_str(k) for k in skipped_empty),
        "total_sampled": len(sampled),
    }
    with open(os.path.join(out_dir, "run_metadata.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Sampled {len(sampled)} rows -> {out_dir}  (seed={seed})")
    for k in skipped_empty:
        print(f"  (skipped empty stratum: {key_str(k)})")


def run_adhoc(report: str, args) -> None:
    row = {
        "_disposition": "adhoc",
        "reference_page": str(args.reference_page),
        "payee": args.payee or "",
        "amount": args.amount or "",
        "document_number": args.document_number or "",
        "description": args.description or "",
        "validation_status": "",
        "category": "",
        "raw_office": "",
        "date_posted": "",
        "start_date": "",
        "end_date": "",
        "salary_flag": "",
    }
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join("verification_samples", report, "adhoc")
    images_dir = os.path.join(out_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    cache = PdfCache(report)
    log_row = process_one(report, cache, row, 1, images_dir)
    # process_one's filename doesn't collide-guard across repeated ad hoc
    # calls; rename with a timestamp so each lookup is kept, not overwritten.
    old_path = os.path.join(images_dir, os.path.basename(log_row["image_path"]))
    new_name = f"{timestamp}_{os.path.basename(log_row['image_path'])}"
    new_path = os.path.join(images_dir, new_name)
    os.replace(old_path, new_path)
    log_row["image_path"] = os.path.join("images", new_name)

    log_path = os.path.join(out_dir, "verification_log.csv")
    write_header = not os.path.exists(log_path)
    with open(log_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(log_row)

    print(f"Rendered: {os.path.join(out_dir, log_row['image_path'])}")
    print(f"  match: {log_row['match_method']} ({log_row['match_confidence']})")
    print(f"  logged to: {log_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report", choices=sorted(REPORTS))
    parser.add_argument("--n-per-stratum", type=int, default=3)
    parser.add_argument("--min-per-stratum", type=int, default=1)
    parser.add_argument("--max-total", type=int, default=30)
    parser.add_argument("--seed", type=int, default=None, help="Reproduce a specific past sample")
    parser.add_argument("--reference-page", type=int, default=None,
                         help="Ad hoc mode: look up one specific row instead of sampling")
    parser.add_argument("--payee", default="")
    parser.add_argument("--amount", default="")
    parser.add_argument("--document-number", default="")
    parser.add_argument("--description", default="")
    args = parser.parse_args()

    if args.reference_page is not None:
        run_adhoc(args.report, args)
    else:
        run_sample(args.report, args)


if __name__ == "__main__":
    main()
