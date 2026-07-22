"""Word-level PDF extraction for the modern (115-118 Congress) report layout.

`pdftotext -layout`, which the legacy parser relies on, reflows characters
into fixed-width text lines. On these reports that reflow desynchronizes
columns: an amount printed next to one payee's row can land on a
different row's text line. Extracting words with their PDF coordinates
and reconstructing rows geometrically (see rows.py) avoids that class of
error entirely.
"""

from dataclasses import dataclass
from typing import Iterator, Optional

import natural_pdf as npdf


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float


def _dedoubled_token(token: str) -> str:
    """Collapse a char-doubled token: 'CCOOMM' -> 'COM'.

    A token is doubled when its length is even and every adjacent pair
    matches (w[0]==w[1], w[2]==w[3], ...). Tokens that don't fit the
    pattern (odd length, or any mismatched pair) are returned unchanged.
    """
    if len(token) % 2:
        return token
    for i in range(0, len(token), 2):
        if token[i] != token[i + 1]:
            return token
    return token[::2]


def _dedoubled(text: str) -> str:
    """Collapse char-doubled text, preserving spaces between tokens.

    112th Congress COMPENSATION OF MEMBERS pages (41 across 112sdoc4/7/10)
    have a PDF text-layer defect where every header character is extracted
    twice: 'COMPENSATION OF MEMBERS' -> 'CCOOMMPPEENNSSAATTIIOONN OOFF
    MMEEMMBBEERRSS', '2011' -> '22001111', '04/01/2011' ->
    '0044//0011//22001111'. Data rows on the same pages extract normally.
    Split on spaces so the pair-matching isn't desynchronized by the
    single (non-doubled) spaces between words, then collapse each token
    independently.
    """
    return " ".join(_dedoubled_token(tok) for tok in text.split(" "))


def page_words(pdf: npdf.PDF, page_num: int) -> list[Word]:
    """Return words for a 1-indexed page number."""
    page = pdf.pages[page_num - 1]
    return [
        Word(_dedoubled(w.text), round(w.x0, 2), round(w.x1, 2), round(w.top, 2), round(w.bottom, 2))
        for w in page.words
    ]


def open_pdf(pdf_path: str) -> npdf.PDF:
    return npdf.PDF(pdf_path)


def iter_pages(
    pdf_path: str, first: int = 1, last: Optional[int] = None
) -> Iterator[tuple[int, list[Word]]]:
    """Yield (page_num, words) for pages [first, last], 1-indexed inclusive."""
    pdf = open_pdf(pdf_path)
    if last is None:
        last = len(pdf.pages)
    for page_num in range(first, last + 1):
        yield page_num, page_words(pdf, page_num)
