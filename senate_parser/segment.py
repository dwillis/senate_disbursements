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
