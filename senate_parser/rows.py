"""Reconstruct visual rows from a page's words by vertical position.

Words on the same printed row share `top` to within a small fraction of
a point (verified on 118sdoc13 page 1000: exact to 1e-6). Row spacing on
these reports is >= ~6pt, so a 1pt tolerance absorbs baseline/font-metric
jitter between columns without merging distinct rows.
"""

from dataclasses import dataclass, field

from .extract import Word

Y_TOL = 1.0


@dataclass
class Row:
    words: list[Word] = field(default_factory=list)

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    def words_in(self, x0: float, x1: float) -> list[Word]:
        """Words whose left edge falls in [x0, x1)."""
        return sorted((w for w in self.words if x0 <= w.x0 < x1), key=lambda w: w.x0)

    def text_in(self, x0: float, x1: float) -> str:
        return " ".join(w.text for w in self.words_in(x0, x1))

    @property
    def rightmost_x1(self) -> float:
        return max((w.x1 for w in self.words), default=0.0)


def cluster_rows(words: list[Word], y_tol: float = Y_TOL) -> list[Row]:
    """Group words into rows by proximity of `top`, ordered top-to-bottom."""
    ordered = sorted(words, key=lambda w: w.top)
    rows: list[Row] = []
    for w in ordered:
        if rows and abs(w.top - rows[-1].top) <= y_tol:
            rows[-1].words.append(w)
        else:
            rows.append(Row(words=[w]))
    return rows
