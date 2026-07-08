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


def page_words(pdf: npdf.PDF, page_num: int) -> list[Word]:
    """Return words for a 1-indexed page number."""
    page = pdf.pages[page_num - 1]
    return [
        Word(w.text, round(w.x0, 2), round(w.x1, 2), round(w.top, 2), round(w.bottom, 2))
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
