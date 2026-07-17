"""Group pages into office/account blocks before any row parsing happens.

Each block corresponds to one printed section: a banner page ("DETAILED
AND SUMMARY STATEMENT OF EXPENDITURES") giving the office name, funding
year, and account, followed by data pages until the next banner (or a
non-data page such as a table of contents). Segmenting first means rows
within a block are homogeneous and continuations never need to reason
about page boundaries -- both the block's own concern in records.py.

It also gives every block a natural reconciliation target: the account's
printed subtotals (parsed downstream from inline "Net Payroll Expenses"
/ category rows) belong to exactly the records inside that block.
"""

import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

from .extract import Word
from .rows import Row, cluster_rows

BANNER_TEXT = "DETAILED AND SUMMARY STATEMENT OF EXPENDITURES"
TOC_TEXT = "TABLE OF CONTENTS"
# "DOCUMENT NO" (no period) is a substring of both the regular anchor
# header's "DOCUMENT NO." and the COMPENSATION OF MEMBERS variant's
# "DOCUMENT NO", so this matches both layouts (see records.py
# ANCHOR_HEADER_ALIASES for the third-header-variant background).
HEADER_MARKER = "DOCUMENT NO"

# Office/account labels sit flush against the left margin; category
# labels, amounts, and data columns start well to the right. The exact
# margin varies a lot by report's absolute table position (118sdoc13: x0
# ~= 62.45; 118sdoc2: x0 ~= 57.5; 117sdoc8, a wider 612x792 page: x0 ~=
# 119.3) -- but its offset from that page's own "DOCUMENT NO." header x0
# is consistent across all three (-12.08, -11.3, -11.5), so it's derived
# per-page from the header rather than a fixed constant. Falls back to a
# plain guess only for the rare page with no header row at all.
LEFT_MARGIN_OFFSET_FROM_DOC_HEADER = -11.5
LEFT_MARGIN_TOL = 6.0
LEFT_MARGIN_FALLBACK_X0 = 60.0

# The banner delimiter is not always a numeric year.  No-year and revolving
# accounts print ``Funding Year X (NO-YEAR)`` / ``X (REVOLVING)``; treating
# those rows as office text contaminates every row in the block and also folds
# the account label below them into the office name.  The value is optional so
# a partially extracted/garbled funding-year row still performs its structural
# job as the office/account boundary.
FUNDING_YEAR_RE = re.compile(
    r"Funding\s*Year(?:\s*(?P<year>\d{4})|\s*X\s*\((?:NO[-\s]?YEAR|REVOLVING)\))?",
    re.IGNORECASE,
)


@dataclass
class BlockHeader:
    office: str
    funding_year: Optional[int]
    account: str
    start_page: int


@dataclass
class Block:
    header: BlockHeader
    pages: list = field(default_factory=list)
    rows_by_page: dict = field(default_factory=dict)


def classify_page(rows: list) -> str:
    joined = " ".join(w.text for r in rows for w in r.words)
    # Some reports render decorative headings ("TABLE OF CONTENTS") as
    # individually spaced characters -- one word per letter (verified:
    # 118sdoc11 page 5). That defeats a plain substring check against
    # `joined`, so also check a whitespace-stripped version; squashing
    # spaces can't produce a false BANNER_TEXT/TOC_TEXT match since both
    # are specific enough multi-word phrases.
    squashed = joined.replace(" ", "")
    if TOC_TEXT in joined or TOC_TEXT.replace(" ", "") in squashed:
        return "toc"
    if BANNER_TEXT in joined or BANNER_TEXT.replace(" ", "") in squashed:
        return "banner"
    if HEADER_MARKER in joined and "PAYEE NAME" in joined:
        return "data"
    return "other"


def header_row_top(rows: list) -> float:
    for r in rows:
        if any(HEADER_MARKER in w.text for w in r.words):
            return r.top
    return float("inf")


def _header_document_x0(rows: list) -> Optional[float]:
    for r in rows:
        for w in r.words:
            if w.text == "DOCUMENT NO.":
                return w.x0
    return None


def _left_margin_text(row: Row, left_margin_x0: float) -> str:
    """Text of the office/account column: words anchored at the left
    margin, plus immediately adjacent continuations. Word extraction
    occasionally splits a line mid-word ("SENA"+"TOR ...", "Fund"+"ing
    Year", "SALA"+"RIES ..." -- all observed on 114sdoc13), so a word
    that starts within a few points of the previous word's right edge is
    glued on; banner-table labels sit far to the right and never qualify.
    A 0pt gap is a true mid-word split (join with no space); 1-3pt is a
    real inter-word space (join with a space)."""
    ordered = sorted(row.words, key=lambda w: w.x0)
    out: list = []
    last_x1 = None
    for w in ordered:
        if abs(w.x0 - left_margin_x0) < LEFT_MARGIN_TOL:
            out.append(w)
            last_x1 = w.x1
        elif out and last_x1 is not None and 0 <= w.x0 - last_x1 <= 3.0:
            out.append(w)
            last_x1 = w.x1
    pieces: list = []
    for i, w in enumerate(out):
        if i == 0:
            pieces.append(w.text)
        else:
            gap = w.x0 - (out[i - 1].x1)
            pieces.append("" if gap == 0 else " ")
            pieces.append(w.text)
    return "".join(pieces).strip()


def parse_banner(rows: list, page_num: int) -> BlockHeader:
    header_top = header_row_top(rows)
    doc_header_x0 = _header_document_x0(rows)
    left_margin_x0 = (
        doc_header_x0 + LEFT_MARGIN_OFFSET_FROM_DOC_HEADER
        if doc_header_x0 is not None
        else LEFT_MARGIN_FALLBACK_X0
    )

    def collect(margin_x0):
        return [
            r
            for r in sorted(rows, key=lambda r: r.top)
            if r.top < header_top and _left_margin_text(r, margin_x0)
        ]

    left_rows = collect(left_margin_x0)
    # Old-template (112th-114th) banners put the office margin ~21.5pt
    # left of DOCUMENT NO. instead of ~11.5. Rather than a second
    # hardcoded offset, self-calibrate: the office column is the
    # leftmost text above the header (banner-table labels start far
    # right of it). Triggered when the modern offset finds nothing OR
    # when it only catches fragments (e.g. 114sdoc13 p80: 'Funding Year'
    # splits into 'Fund' at the real office margin and 'ing Year' at
    # ~120.5, the latter inside the primary 6pt tolerance -- primary
    # returns non-empty but misses the office/account entirely). Modern
    # banners have their leftmost word within ~1pt of primary_lmx, so
    # the > LEFT_MARGIN_TOL guard keeps them on the primary path.
    candidates = [
        w.x0
        for r in rows
        if r.top < header_top
        for w in r.words
        if doc_header_x0 is None or w.x0 < doc_header_x0 + 60
    ]
    if candidates:
        banner_min_x0 = min(candidates)
        if not left_rows or banner_min_x0 < left_margin_x0 - LEFT_MARGIN_TOL:
            left_margin_x0 = banner_min_x0
            left_rows = collect(left_margin_x0)

    funding_year = None
    office_parts: list = []
    account_parts: list = []
    seen_funding_year = False
    for r in left_rows:
        text = _left_margin_text(r, left_margin_x0)
        # Some layouts (118sdoc13) print "Funding Year 2024" as one
        # contiguous string; others (118sdoc2) print "Funding Year" and
        # the year as two separate words, with the year sitting outside
        # the left-margin band entirely -- so search the row's full text,
        # not just its left-margin-filtered slice.
        full_row_text = " ".join(w.text for w in sorted(r.words, key=lambda w: w.x0))
        # Word extraction can split mid-word ("Fund" + "ing Year",
        # 114sdoc13 p211), so also match with all spaces squashed; and
        # the printed year itself is occasionally garbled ("1618"), so
        # keep the row recognized (office/account split intact) but drop
        # an implausible year rather than shipping it.
        m = FUNDING_YEAR_RE.search(full_row_text) or FUNDING_YEAR_RE.search(
            full_row_text.replace(" ", "")
        )
        if m:
            year_text = m.group("year")
            year = int(year_text) if year_text else None
            funding_year = year if year is not None and 1990 <= year <= 2100 else None
            seen_funding_year = True
            continue
        (account_parts if seen_funding_year else office_parts).append(text)

    return BlockHeader(
        office=" ".join(office_parts).strip(),
        funding_year=funding_year,
        account=" ".join(account_parts).strip(),
        start_page=page_num,
    )


@dataclass
class BannerSummary:
    """The two load-bearing figures from a banner page's summary table
    (NET EXPENDITURES FOR THE PERIOD column). None = not found/parseable.

    `categories` is the full set of category rows in the summary table
    (normalized uppercase label -> signed NET EXPENDITURES FOR THE PERIOD
    value), including NET PAYROLL EXPENSES and ORGANIZATION TOTALS. Used
    by banner_checks to detect ORG TOTALS fails that are fully explained
    by categories with no itemized rows in the block body (e.g. Sgt at
    Arms FY2025 in 119sdoc5: five banner-only categories, ~$15.4M)."""

    net_payroll: Optional[float] = None
    organization_totals: Optional[float] = None
    categories: dict = field(default_factory=dict)


def _banner_amount(text: str) -> Optional[float]:
    # Reuse the reconciliation amount grammar without importing reconcile
    # (which imports records; keep segment.py at the bottom of the stack).
    m = re.search(r"-?\$?(\d[\d,]*\.\d{2}|\.\d{2})$", text.strip())
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    return -value if text.strip().startswith("-") else value


# Category labels that appear as rows in the banner summary table, in
# the order they print. Used by parse_banner_summary to populate
# BannerSummary.categories. The labels are case-sensitive substrings
# matched against row text (period_value_near uses substring
# containment), so they must be distinctive enough that no other row
# contains them. "ORGANIZATION TOTALS" is included so the row itself
# appears in `categories`, mirroring how banner_checks reads
# summary.organization_totals.
BANNER_CATEGORY_LABELS = (
    "Net Payroll Expenses",
    "Travel and Transportation of Persons",
    "Transportation of Things",
    "Rent, Communications and Utilities",
    "Printing and Reproduction",
    "Other Contractual Services",
    "Supplies and Materials",
    "Acquisition of Assets",
    "Land and Structures",
    "ORGANIZATION TOTALS",
)


def parse_banner_summary(rows: list) -> BannerSummary:
    """Extract the banner summary table's period figures. The table sits
    above the DOCUMENT NO. header; values are right-aligned under the
    "NET EXPENDITURES FOR / THE PERIOD" column header, and ORGANIZATION
    TOTALS' values print on a visual row ~3pt above the label (verified
    on both page templates). Words are assigned to columns by nearest
    header center, which absorbs the right-alignment overhang.

    `categories` is populated for every category row whose leftmost
    text is a known summary-table label (see BANNER_CATEGORY_LABELS) --
    budget rows (Authorization, Supplementals, Transfers, Resc /
    Withdrawals) print no period value and are excluded."""
    header_top = header_row_top(rows)
    top_rows = [r for r in sorted(rows, key=lambda r: r.top) if r.top < header_top]

    centers = {}
    for r in top_rows:
        for w in r.words:
            if "EXPENDITURES FOR" in w.text:
                centers["period"] = (w.x0 + w.x1) / 2
            elif "TOTAL FUNDING" in w.text:
                centers["total"] = (w.x0 + w.x1) / 2
            elif "NET FUNDS" in w.text:
                centers["funds"] = (w.x0 + w.x1) / 2
    if "period" not in centers or "total" not in centers:
        return BannerSummary()

    def period_value_near(label: str) -> Optional[float]:
        label_row = next(
            (r for r in top_rows if label in " ".join(w.text for w in r.words)), None
        )
        if label_row is None:
            return None
        # Tolerance is 4.0pt: tight enough to exclude adjacent category
        # rows (which print ~8pt apart on both templates) but loose enough
        # to catch the ORGANIZATION TOTALS amount, which prints ~3pt above
        # the label (the visual row's "top" is the amount's top, the
        # label's baseline sits ~3pt below it).
        best = None
        for r in top_rows:
            if abs(r.top - label_row.top) > 4.0:
                continue
            for w in r.words:
                amt = _banner_amount(w.text)
                if amt is None:
                    continue
                center = (w.x0 + w.x1) / 2
                column = min(centers, key=lambda c: abs(center - centers[c]))
                if column != "period":
                    continue
                distance = abs(center - centers["period"])
                if best is None or distance < best[0]:
                    best = (distance, amt)
        return best[1] if best else None

    categories = {}
    for label in BANNER_CATEGORY_LABELS:
        amt = period_value_near(label)
        if amt is not None:
            categories[label.upper()] = amt

    return BannerSummary(
        net_payroll=categories.get("NET PAYROLL EXPENSES"),
        organization_totals=categories.get("ORGANIZATION TOTALS"),
        categories=categories,
    )


def segment_blocks(pages: Iterable, page_ledger: Optional[list] = None) -> Iterator[Block]:
    """Consume ``(page_num, words)`` pairs and yield completed blocks.

    When ``page_ledger`` is provided, append one dict for every consumed PDF
    page.  This is deliberately recorded here, before pages can be discarded:
    a data page without an open banner block used to disappear silently and
    therefore could not be distinguished from a page that was never read.
    """
    current: Optional[Block] = None
    for page_num, words in pages:
        rows = cluster_rows(words)
        kind = classify_page(rows)
        entry = {
            "source_page": page_num,
            "classification": kind,
            "assigned_to_block": False,
            "block_start_page": None,
            "office": "",
            "funding_year": None,
            "word_count": len(words),
            "visual_row_count": len(rows),
            "reason": "",
        }
        if kind == "banner":
            if current is not None:
                yield current
            header = parse_banner(rows, page_num)
            current = Block(header=header, pages=[page_num], rows_by_page={page_num: rows})
            entry.update(
                assigned_to_block=True,
                block_start_page=page_num,
                office=header.office,
                funding_year=header.funding_year,
                reason="block_start",
            )
        elif kind == "data":
            if current is not None:
                current.pages.append(page_num)
                current.rows_by_page[page_num] = rows
                entry.update(
                    assigned_to_block=True,
                    block_start_page=current.header.start_page,
                    office=current.header.office,
                    funding_year=current.header.funding_year,
                    reason="block_data",
                )
            else:
                entry["reason"] = "orphan_data_no_banner"
            # A data page with no open block (e.g. mid-stream extraction) is dropped;
            # the ledger makes that loss a release-gating coverage finding.
        else:
            if current is not None:
                entry.update(
                    block_start_page=current.header.start_page,
                    office=current.header.office,
                    funding_year=current.header.funding_year,
                    reason="terminates_block",
                )
                yield current
                current = None
            else:
                entry["reason"] = "non_data"
        if page_ledger is not None:
            page_ledger.append(entry)
    if current is not None:
        yield current
