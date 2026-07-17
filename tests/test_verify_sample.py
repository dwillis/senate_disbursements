"""Strata enumeration and highlight-reliability for scripts/verify_sample.py.

`all_possible_strata()` is the canonical list the sampling script diffs
against when reporting which (disposition × validation_status ×
record_type) combinations are absent from a report. If it enumerates
combos the pipeline can *never* produce, the script's "skipped empty"
output lists structural impossibilities alongside informative gaps,
burying the signal.

The pipeline (senate_parser/pipeline.py:328-331) routes rows purely on
`validation_status`: only `fail` rows go to quarantine.csv, everything
else goes to senate_data_cleaned.csv. So five (disposition × status)
pairs can never occur, regardless of report content:

    (cleaned, fail)               -- fail rows never go to cleaned
    (quarantine, ok)              -- only fail rows quarantined
    (quarantine, warn)            -- only fail rows quarantined
    (quarantine, unchecked)       -- only fail rows quarantined
    (quarantine, source_mismatch) -- source_mismatch released to cleaned

Cross the 2 record_types and that's 10 strata to drop, leaving 10.

`PdfCache.highlight_reliable()` previously flagged 117sdoc8 pages as
unreliable because natural_pdf's page.width/.height (mediabox
post-rotation) disagree with pypdfium2's get_size (cropbox-clipped).
But empirical testing on 117sdoc8 vol 1 page 500 (a real amount row at
top=162.49) showed natural_pdf's render() clips the highlight bbox to
the visible cropbox area correctly -- red pixels landed at y=219-238
vs expected y=225-232, well within the row's 5pt padding. The
conservative check was masking a non-bug; the method now always
returns True so 117sdoc8 QA images get highlights like every other
report.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.verify_sample import DATA_DIR, PdfCache, all_possible_strata


def test_all_possible_strata_excludes_cleaned_fail():
    strata = all_possible_strata()
    for record_type in ("salary", "expense"):
        assert ("cleaned", "fail", record_type) not in strata


def test_all_possible_strata_excludes_quarantine_non_fail():
    strata = all_possible_strata()
    for status in ("ok", "warn", "unchecked", "source_mismatch"):
        for record_type in ("salary", "expense"):
            assert ("quarantine", status, record_type) not in strata


def test_all_possible_strata_keeps_valid_combinations():
    # The 10 combos the pipeline can actually produce.
    strata = set(all_possible_strata())
    expected = {
        ("cleaned", "ok", "salary"),
        ("cleaned", "ok", "expense"),
        ("cleaned", "warn", "salary"),
        ("cleaned", "warn", "expense"),
        ("cleaned", "unchecked", "salary"),
        ("cleaned", "unchecked", "expense"),
        ("cleaned", "source_mismatch", "salary"),
        ("cleaned", "source_mismatch", "expense"),
        ("quarantine", "fail", "salary"),
        ("quarantine", "fail", "expense"),
    }
    assert strata == expected


def test_all_possible_strata_count_is_ten():
    assert len(all_possible_strata()) == 10


def test_highlight_reliable_always_true_without_consulting_page_dims():
    # The conservative dim-mismatch check is gone -- highlight_reliable
    # no longer consults page_obj.width/.height or pypdfium2's get_size.
    # A Mock page_obj with the "wrong" 117sdoc8 dims (792x612 vs the
    # actual 656.93x422.93) must still return True; the method must not
    # raise trying to open a PDF that isn't there.
    cache = PdfCache("117sdoc8")
    page_obj = MagicMock()
    page_obj.width = 792
    page_obj.height = 612
    assert cache.highlight_reliable("-1", 500, page_obj) is True


@pytest.mark.slow
def test_highlight_reliable_true_on_real_117sdoc8_page():
    pdf = Path(DATA_DIR) / "117sdoc8" / "GPO-CDOC-117sdoc8-1.pdf"
    if not pdf.exists():
        pytest.skip("117sdoc8 PDFs not present locally")
    cache = PdfCache("117sdoc8")
    page_obj, _ = cache.get("-1", 500)
    assert cache.highlight_reliable("-1", 500, page_obj) is True