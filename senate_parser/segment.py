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
HEADER_MARKER = "DOCUMENT NO."

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

FUNDING_YEAR_RE = re.compile(r"Funding Year\s+(\d{4})")


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
    words = [w for w in row.words if abs(w.x0 - left_margin_x0) < LEFT_MARGIN_TOL]
    return " ".join(w.text for w in sorted(words, key=lambda w: w.x0))


def parse_banner(rows: list, page_num: int) -> BlockHeader:
    header_top = header_row_top(rows)
    doc_header_x0 = _header_document_x0(rows)
    left_margin_x0 = (
        doc_header_x0 + LEFT_MARGIN_OFFSET_FROM_DOC_HEADER
        if doc_header_x0 is not None
        else LEFT_MARGIN_FALLBACK_X0
    )
    left_rows = [
        r
        for r in sorted(rows, key=lambda r: r.top)
        if r.top < header_top and _left_margin_text(r, left_margin_x0)
    ]

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
        m = FUNDING_YEAR_RE.search(full_row_text)
        if m:
            funding_year = int(m.group(1))
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
    (NET EXPENDITURES FOR THE PERIOD column). None = not found/parseable."""

    net_payroll: Optional[float] = None
    organization_totals: Optional[float] = None


def _banner_amount(text: str) -> Optional[float]:
    # Reuse the reconciliation amount grammar without importing reconcile
    # (which imports records; keep segment.py at the bottom of the stack).
    m = re.search(r"-?\$?(\d[\d,]*\.\d{2}|\.\d{2})$", text.strip())
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    return -value if text.strip().startswith("-") else value


def parse_banner_summary(rows: list) -> BannerSummary:
    """Extract the banner summary table's period figures. The table sits
    above the DOCUMENT NO. header; values are right-aligned under the
    "NET EXPENDITURES FOR / THE PERIOD" column header, and ORGANIZATION
    TOTALS' values print on a visual row ~3pt above the label (verified
    on both page templates). Words are assigned to columns by nearest
    header center, which absorbs the right-alignment overhang."""
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
        best = None
        for r in top_rows:
            if abs(r.top - label_row.top) > 8.0:
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

    return BannerSummary(
        net_payroll=period_value_near("Net Payroll Expenses"),
        organization_totals=period_value_near("ORGANIZATION TOTALS"),
    )


def segment_blocks(pages: Iterable) -> Iterator[Block]:
    """Consume (page_num, words) pairs and yield completed Blocks in order."""
    current: Optional[Block] = None
    for page_num, words in pages:
        rows = cluster_rows(words)
        kind = classify_page(rows)
        if kind == "banner":
            if current is not None:
                yield current
            header = parse_banner(rows, page_num)
            current = Block(header=header, pages=[page_num], rows_by_page={page_num: rows})
        elif kind == "data":
            if current is not None:
                current.pages.append(page_num)
                current.rows_by_page[page_num] = rows
            # A data page with no open block (e.g. mid-stream extraction) is dropped;
            # the pipeline should always start extraction before the first banner.
        else:
            if current is not None:
                yield current
                current = None
    if current is not None:
        yield current
