import json
from pathlib import Path

from senate_parser.extract import Word
from senate_parser.rows import cluster_rows
from senate_parser.segment import classify_page, parse_banner, parse_banner_summary, segment_blocks

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> list[Word]:
    data = json.loads((FIXTURES / f"vol1_page_{name}.json").read_text())
    return [Word(**d) for d in data]


def load_fixture(filename: str) -> list[Word]:
    data = json.loads((FIXTURES / f"{filename}.json").read_text())
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


def test_parse_banner_old_template_split_funding_year_fragment():
    """114sdoc13 p80: the word extractor split 'Funding Year' into 'Fund'
    (x0=104.9) and 'ing Year' (x0=120.5). The primary left-margin offset
    (~114.8) catches the 'ing Year' fragment within its 6pt tolerance, so
    primary collect returns non-empty and the self-calibration fallback
    never runs -- but 'Fund' at the real office-column left edge is missed,
    so office and account both come back empty. The fallback must trigger
    when the banner's leftmost word sits significantly left of the primary
    offset, not only when primary collect is empty."""
    data = json.loads((FIXTURES / "114sdoc13_page_80.json").read_text())
    rows = cluster_rows([Word(**d) for d in data])
    header = parse_banner(rows, 80)
    assert header.office == "MINORITY WHIP (D)", f"office={header.office!r}"
    assert header.funding_year == 2016
    assert header.account == "SALARIES, OFFICERS AND EMPLOYEES, SENATE", f"account={header.account!r}"


def test_parse_banner_115th_rotated_margin_chars_not_office():
    """115sdoc2 prints rotated date text on the left edge of every page
    ("J A 2 5 - 1 0 7" / "0 5 / 0 5 / 2 0 1 7") as individual single-char
    words at x0=23.88, vertically stacked above the real office line.
    The self-calibration fallback (banner_min_x0 < primary left margin)
    latches onto x0=23.88 and reads "J A" as the office name,
    contaminating every block in the report (42,890 records shipped with
    office="J A 2 5 ..." on the first 115sdoc2 run). The fallback must
    ignore single-char rotated margin text and pick the real office
    column instead (x0=105.94 on this page)."""
    data = json.loads((FIXTURES / "115sdoc2_page_20.json").read_text())
    rows = cluster_rows([Word(**d) for d in data])
    header = parse_banner(rows, 20)
    assert header.office == "CHAIRMAN MINORITY POLICY COMMITTEE (D)", f"office={header.office!r}"
    assert header.funding_year == 2015
    assert "EXP. ALLOWANCES" in header.account, f"account={header.account!r}"
    # The rotated margin chars must not leak into the office field.
    assert "J A" not in header.office


def _synthetic_special_funding_year_banner(value: str):
    def word(text, x0, top):
        return Word(text=text, x0=x0, x1=x0 + max(20, len(text) * 3), top=top, bottom=top + 5)

    return cluster_rows(
        [
            word("SECRETARY - SENATE COLLECTION", 62.5, 10),
            word(f"Funding Year {value}", 62.5, 20),
            word("SECRETARY OF THE SENATE", 62.5, 30),
            word("DOCUMENT NO.", 74.0, 100),
        ]
    )


def test_parse_banner_no_year_value_is_office_account_boundary():
    header = parse_banner(_synthetic_special_funding_year_banner("X (NO-YEAR)"), 353)

    assert header.office == "SECRETARY - SENATE COLLECTION"
    assert header.funding_year is None
    assert header.account == "SECRETARY OF THE SENATE"


def test_parse_banner_revolving_value_is_office_account_boundary():
    header = parse_banner(_synthetic_special_funding_year_banner("X (REVOLVING)"), 1247)

    assert header.office == "SECRETARY - SENATE COLLECTION"
    assert header.funding_year is None
    assert header.account == "SECRETARY OF THE SENATE"


def test_parse_banner_real_cares_act_no_year_banner():
    """117sdoc8 p306: a real CARES-Act banner with ``Funding Year X
    (NO-YEAR)``. The pre-fix FUNDING_YEAR_RE required 4 digits, so the
    NO-YEAR row fell through and the office name absorbed ``Funding Year
    X (NO-YEAR) SECRETARY OF THE SENATE`` -- 71 contaminated rows on
    this one banner, ~1,385 across the modern era (117sdoc8 + 118/119th).
    With the NO-YEAR/REVOLVING-aware regex, the row is treated as the
    office/account separator with funding_year=None."""
    data = json.loads((FIXTURES / "sdoc8_p306_cares_no_year_banner.json").read_text())
    rows = cluster_rows([Word(**d) for d in data])
    header = parse_banner(rows, 306)
    assert header.office == "CARES ACT EMERG. APPROP. P.L. 116-136", f"office={header.office!r}"
    assert header.funding_year is None
    assert header.account == "MISCELLANEOUS ITEMS", f"account={header.account!r}"
    # The contamination marker itself must not leak into either field.
    assert "FUNDING YEAR" not in header.office.upper()
    assert "FUNDING YEAR" not in header.account.upper()


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


def test_page_ledger_records_every_page_before_segmentation_discards_it():
    ledger = []
    pages = [(125, load(125)), (20, load(20)), (5, load(5))]
    list(segment_blocks(pages, page_ledger=ledger))

    assert [row["source_page"] for row in ledger] == [125, 20, 5]
    assert ledger[0]["classification"] == "data"
    assert ledger[0]["assigned_to_block"] is False
    assert ledger[0]["reason"] == "orphan_data_no_banner"
    assert ledger[1]["assigned_to_block"] is True
    assert ledger[1]["reason"] == "block_start"
    assert ledger[2]["reason"] == "terminates_block"


def test_banner_summary_extracts_period_figures():
    """Cotton's banner (page 1000): Net Payroll Expenses -2,042,937.06 and
    ORGANIZATION TOTALS -2,190,920.79 in the NET EXPENDITURES FOR THE
    PERIOD column. The ORGANIZATION TOTALS values print on a visual row
    ~3pt above the label -- the extractor must still find them."""
    summary = parse_banner_summary(cluster_rows(load(1000)))
    assert summary.net_payroll == -2042937.06
    assert summary.organization_totals == -2190920.79


def test_banner_summary_wide_template():
    """117sdoc8's wider page template positions every column differently;
    nearest-header-center assignment must still land on the period column."""
    summary = parse_banner_summary(cluster_rows(load_fixture("sdoc8_vol1_page_547")))
    assert summary.net_payroll == -1337475.77
    assert summary.organization_totals == -1420145.67


def test_banner_summary_zero_period_block():
    """Page 100's block spent nothing this period: both figures are $.00
    variants and must parse as 0.0, not None."""
    summary = parse_banner_summary(cluster_rows(load(100)))
    assert summary.net_payroll == 0.0
    assert summary.organization_totals == 0.0


def test_banner_summary_missing_on_data_page():
    summary = parse_banner_summary(cluster_rows(load(1001)))
    assert summary.net_payroll is None
    assert summary.organization_totals is None


def test_banner_summary_categories_dict_captures_all_category_rows():
    """parse_banner_summary must populate `categories` with every
    category row in the banner summary table (normalized label ->
    signed NET EXPENDITURES FOR THE PERIOD value), not just the two
    named fields. This is what banner_checks uses to detect when an
    ORG TOTALS fail is fully explained by categories that have no
    itemized rows in the block body (e.g. Sgt at Arms FY2025 in
    119sdoc5 -- five categories, ~$15.4M, that appear only on the
    banner). Budget rows (Authorization, Supplementals, Transfers, Resc
    / Withdrawals) print no period value and must be excluded."""
    summary = parse_banner_summary(cluster_rows(load(1000)))
    expected = {
        "NET PAYROLL EXPENSES": -2042937.06,
        "TRAVEL AND TRANSPORTATION OF PERSONS": -43106.04,
        "RENT, COMMUNICATIONS AND UTILITIES": -31053.59,
        "PRINTING AND REPRODUCTION": -99.75,
        "OTHER CONTRACTUAL SERVICES": -6000.00,
        "SUPPLIES AND MATERIALS": -27765.04,
        "ACQUISITION OF ASSETS": -39959.31,
        "ORGANIZATION TOTALS": -2190920.79,
    }
    assert summary.categories == expected, (
        f"categories mismatch:\n got: {summary.categories}\n want: {expected}")
    # Budget rows must be excluded -- they print no period amount.
    assert "AUTHORIZATION" not in summary.categories
    assert "SUPPLEMENTALS" not in summary.categories
    assert "TRANSFERS" not in summary.categories
    assert "RESC / WITHDRAWALS" not in summary.categories


def test_banner_summary_categories_dict_missing_on_data_page():
    """A data page has no banner summary table; categories is empty."""
    summary = parse_banner_summary(cluster_rows(load(1001)))
    assert summary.categories == {}


def test_calibrate_columns_116th_congress_uses_anchor_template():
    """116th Congress PDFs (116sdoc2/10/19) share the 114th's split-header
    layout -- OBLIGATION/SERVICE + DESCRIPTION at top=274.2, DOCUMENT NO.
    + DATE + PAYEE NAME + AMOUNT ($) at top=275.6, 1.4pt apart -- and payee
    data offset (-34.7pt left of PAYEE NAME). The modern template's fixed
    COLUMN_DELTAS are tuned to 117th+ single-row headers and misalign by
    ~0.3pt on the 116th (payee data at x0=229.8 falls left of the modern
    boundary 230.1). The anchor template derives every column from the
    page's own seven header words and parses the 116th correctly. The
    pipeline routes 116th reports to the anchor template (see pipeline.py
    template threshold)."""
    from senate_parser.records import calibrate_columns

    data = json.loads((FIXTURES / "116sdoc19_page_22.json").read_text())
    rows = cluster_rows([Word(**d) for d in data])
    cols = calibrate_columns(rows, template="anchor")
    assert cols is not None, "anchor calibrate_columns returned None for 116th page"
    # The header words on this page: DOCUMENT NO. x0=124.4, PAYEE NAME x0=264.5,
    # AMOUNT ($) x0=624. The calibrated columns must contain them.
    assert cols.document[0] <= 124.4 < cols.document[1]
    assert cols.payee[0] <= 264.5 < cols.payee[1]
    assert cols.amount[0] <= 624.0 < cols.amount[1]


def test_parse_block_115sdoc2_rotated_left_margin_chars_not_unparsed():
    """115sdoc2 prints rotated date text on the left edge of every page
    ("J A 2 5 - 1 0 7" / "0 5 / 0 5 / 2 0 1 7") as individual single-char
    words at x0=23.88, vertically stacked. After parse_banner stops
    reading them as the office name (see
    test_parse_banner_115th_rotated_margin_chars_not_office), the chars
    below the data header still enter the row stream and classify as
    unparsed_unclassified -- 18,535 such findings on the first 115sdoc2
    run, one per rotated char per page. The row filter must drop them
    before classification."""
    from senate_parser.records import parse_block
    from senate_parser.segment import Block, BlockHeader

    data = json.loads((FIXTURES / "115sdoc2_page_20.json").read_text())
    rows = cluster_rows([Word(**d) for d in data])
    header = BlockHeader(
        office="CHAIRMAN MINORITY POLICY COMMITTEE (D)",
        funding_year=2015,
        account="EXP. ALLOWANCES",
        start_page=20,
    )
    block = Block(header=header, pages=[20], rows_by_page={20: rows})
    result = parse_block(block, template="anchor")
    rotated = [u for u in result.unparsed if u.get("reason") == "unclassified" and len(u.get("text", "")) <= 2]
    assert not rotated, f"rotated margin chars leaked into unparsed: {rotated[:5]}"
