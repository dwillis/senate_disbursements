import json
from pathlib import Path

from senate_parser.extract import Word
from senate_parser.rows import cluster_rows
from senate_parser.segment import classify_page, parse_banner, segment_blocks

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> list[Word]:
    data = json.loads((FIXTURES / f"vol1_page_{name}.json").read_text())
    return [Word(**d) for d in data]


def test_classify_toc_and_cover_pages_are_not_data_or_banner():
    for pn in (1, 5):
        rows = cluster_rows(load(pn))
        assert classify_page(rows) in ("toc", "other")


def test_classify_banner_page():
    rows = cluster_rows(load(20))
    assert classify_page(rows) == "banner"


def test_classify_continuation_page_without_banner():
    rows = cluster_rows(load(125))
    assert classify_page(rows) == "data"


def test_classify_end_of_volume_marker():
    rows = cluster_rows(load(1495))
    assert classify_page(rows) == "other"


def test_classify_toc_page_with_letter_spaced_heading():
    """118sdoc11 page 5: the "TABLE OF CONTENTS" heading is rendered as
    one word per letter, which defeats a plain substring check -- and the
    same page's "DETAILED AND SUMMARY STATEMENT OF EXPENDITURES" TOC
    *entry* is contiguous text, so without the whitespace-squashed
    fallback this TOC page is misclassified as a banner page."""
    data = json.loads((FIXTURES / "sdoc11_vol1_page_5.json").read_text())
    rows = cluster_rows([Word(**d) for d in data])
    assert classify_page(rows) == "toc"


def test_parse_banner_senator_office():
    rows = cluster_rows(load(1000))
    header = parse_banner(rows, 1000)
    assert header.office == "SENATOR TOM COTTON"
    assert header.funding_year == 2024
    assert "PERSONNEL" in header.account


def test_parse_banner_narrower_left_margin_and_split_funding_year():
    """118sdoc2 uses a different template than 118sdoc13: the office-name
    margin sits at x0~=57.5 (not ~62.5), and "Funding Year" / the year
    value are two separate words instead of one contiguous string. Both
    silently produced an empty office/funding_year before the fix."""
    data = json.loads((FIXTURES / "sdoc2_vol1_page_17.json").read_text())
    rows = cluster_rows([Word(**d) for d in data])
    header = parse_banner(rows, 17)
    assert header.office == "OFFICE OF THE VICE PRESIDENT (D) - HARRIS"
    assert header.funding_year == 2021


def test_parse_banner_wide_page_template():
    """117sdoc8 uses a wider 612x792 page (vs 118sdoc13/118sdoc2's
    423x657) with the whole table shifted ~56pt right -- e.g. its
    DOCUMENT NO. header sits at x0~=130.8 vs 118sdoc13's ~74.6. The
    office-name margin must be derived from this page's own header, not
    a fixed constant tuned to the narrower template."""
    data = json.loads((FIXTURES / "sdoc8_vol1_page_17.json").read_text())
    rows = cluster_rows([Word(**d) for d in data])
    header = parse_banner(rows, 17)
    assert header.office == "OFFICE OF THE VICE PRESIDENT (D) - HARRIS"
    assert header.funding_year == 2021


def test_parse_banner_wrapped_office_name():
    rows = cluster_rows(load(20))
    header = parse_banner(rows, 20)
    assert header.office == "OFFICE OF THE VICE PRESIDENT (D) - HARRIS"
    assert header.funding_year == 2023
    assert "EXPENSE ALLOWANCES" in header.account
    assert "POLICY COMMITTEES" in header.account


def test_same_office_different_funding_year_is_a_distinct_block():
    """Page 20 (FY2023) and page 21 (FY2024) are both banners for the same
    office -- they must segment into two separate blocks."""
    h20 = parse_banner(cluster_rows(load(20)), 20)
    h21 = parse_banner(cluster_rows(load(21)), 21)
    assert h20.office == h21.office
    assert h20.funding_year != h21.funding_year


def test_segment_blocks_groups_continuation_pages():
    pages = [(20, load(20)), (21, load(21))]
    blocks = list(segment_blocks(pages))
    assert len(blocks) == 2
    assert blocks[0].pages == [20]
    assert blocks[1].pages == [21]


def test_segment_blocks_attaches_data_pages_to_open_block():
    pages = [(1000, load(1000)), (1004, load(1004))]
    blocks = list(segment_blocks(pages))
    assert len(blocks) == 1
    assert blocks[0].pages == [1000, 1004]
    assert blocks[0].header.office == "SENATOR TOM COTTON"


def test_segment_blocks_toc_page_closes_open_block():
    pages = [(20, load(20)), (5, load(5)), (100, load(100))]
    blocks = list(segment_blocks(pages))
    assert len(blocks) == 2
    assert blocks[0].pages == [20]
    assert blocks[1].pages == [100]
