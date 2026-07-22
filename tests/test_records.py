import json
from pathlib import Path

from senate_parser.extract import Word
from senate_parser.records import parse_block
from senate_parser.rows import cluster_rows
from senate_parser.segment import Block, BlockHeader

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str, prefix: str = "vol1") -> list[Word]:
    data = json.loads((FIXTURES / f"{prefix}_page_{name}.json").read_text())
    return [Word(**d) for d in data]


def make_block(page_nums, office="TEST OFFICE", funding_year=2024, prefix="vol1"):
    rows_by_page = {p: cluster_rows(load(p, prefix)) for p in page_nums}
    header = BlockHeader(office=office, funding_year=funding_year, account="TEST ACCOUNT", start_page=page_nums[0])
    return Block(header=header, pages=list(page_nums), rows_by_page=rows_by_page)


def find(records, payee_substr):
    matches = [r for r in records if payee_substr in r.payee]
    assert matches, f"no record with payee containing {payee_substr!r}"
    return matches[0]


def synthetic_modern_block(data_words, page=1):
    """Build the minimum calibrated modern page around supplied data words."""
    header_words = [
        Word("DOCUMENT NO.", 74.0, 110.0, 0.0, 5.0),
        Word("PAYEE NAME", 174.0, 220.0, 0.0, 5.0),
        Word("DESCRIPTION", 426.1, 470.0, 0.0, 5.0),
        Word("START", 290.0, 310.0, 10.0, 15.0),
        Word("END", 330.0, 345.0, 10.0, 15.0),
    ]
    rows = cluster_rows(header_words + data_words)
    return Block(BlockHeader("TEST", 2024, "TEST", page), [page], {page: rows})


def test_salary_rows_get_correct_amounts_not_desynced_ones():
    block = make_block([1000])
    result = parse_block(block)
    salary = [r for r in result.records if r.record_type == "salary"]

    tabler = find(salary, "TABLER")
    assert tabler.description == "COMMUNICATIONS DIRECTOR"
    assert tabler.amount == "$110,949.96"

    todd = find(salary, "TODD")
    assert todd.amount == "$54,180.67"


def test_title_wrapped_across_three_rows_reconstructed():
    """SHAW's title is split PDF-side into a line above and a line below
    her name+amount row (see records.py module docstring)."""
    block = make_block([130])
    result = parse_block(block)
    salary = [r for r in result.records if r.record_type == "salary"]

    shaw = find(salary, "SHAW")
    assert shaw.description == "DIRECTOR OF THE OFFICE OF COMMUNICATIONS, AND LEGISLATIVE LIAISON"
    assert shaw.amount == "$98,835.21"

    gibson = find(salary, "GIBSON")
    assert gibson.description == "INTERNAL COMMUNICATIONS COORDINATOR"
    assert gibson.amount == "$65,936.40"


def test_senate_page_rows_classified_as_salary_not_expense():
    block = make_block([125])
    result = parse_block(block)
    phifer = find(result.records, "PHIFER")
    assert phifer.record_type == "salary"
    assert phifer.document_number == ""
    assert phifer.description == "PAGE TO JUN. 7"
    assert phifer.amount == "$7,253.72"


def test_expense_sublines_inherit_document_context():
    block = make_block([1001])
    result = parse_block(block)
    expense = [r for r in result.records if r.record_type == "expense" and r.document_number == "DCOT20240317"]

    # header line + 2 sublines under the same document number
    assert len(expense) == 3
    assert all(r.payee == "PATRICIA J GELLER" for r in expense)
    assert all(r.start_date == "03/12/2024" for r in expense)
    assert all(r.end_date == "03/14/2024" for r in expense)

    descriptions = {r.description: r.amount for r in expense}
    assert descriptions["STAFF INCIDENTALS"] == "$19.80"
    assert descriptions["STAFF PER DIEM"] == "$189.69"
    assert descriptions["STAFF TRANSPORTATION WASHINGTON DC TO BENTONVILLE, SPRINGDALE, FORT SMITH, BENTONVILLE AND RETURN"] == "$120.59"


def test_description_spilling_left_does_not_populate_end_date():
    block = synthetic_modern_block(
        [
            Word("BENEFITS FOR FORMER PERSONNEL", 326.7, 419.0, 30.0, 35.0),
            Word("$82,538.92", 565.7, 588.6, 30.0, 35.0),
        ],
        page=238,
    )

    result = parse_block(block)
    assert len(result.records) == 1
    record = result.records[0]
    assert record.description == "BENEFITS FOR FORMER PERSONNEL"
    assert record.end_date == ""
    assert record.amount == "$82,538.92"


def test_widely_spaced_leading_salary_continuations_are_prepended():
    descriptions = [
        "CONSULTANT MAR 26-29, APR 1-3,",
        "APR 5-8, MAY 1-3,",
        "JUN 10-14, JUL 1-3,",
        "AUG 1-2, SEP 4-6,",
        "SEP 16-20, SEP 23",
    ]
    data_words = [
        Word(descriptions[0], 355.1, 510.0, 30.0, 35.0),
        Word(descriptions[1], 355.1, 510.0, 35.3, 40.3),
        Word("DWYER, SHEILA M", 175.0, 216.0, 40.6, 45.6),
        Word(descriptions[2], 355.1, 510.0, 40.6, 45.6),
        Word("$56,365.54", 565.7, 588.6, 40.6, 45.6),
        Word(descriptions[3], 355.1, 510.0, 45.9, 50.9),
        Word(descriptions[4], 355.1, 510.0, 51.2, 56.2),
    ]

    result = parse_block(synthetic_modern_block(data_words, page=342))
    dwyer = find(result.records, "DWYER")
    assert dwyer.description == " ".join(descriptions)
    assert dwyer.amount == "$56,365.54"
    assert result.unparsed == []


def test_loose_salary_continuations_choose_nearest_employee():
    data_words = [
        Word("ALICE", 175.0, 216.0, 30.0, 35.0),
        Word("TITLE ONE", 355.1, 410.0, 30.0, 35.0),
        Word("$10.00", 565.7, 588.6, 30.0, 35.0),
        Word("ALICE TRAILING", 355.1, 450.0, 35.0, 40.0),
        Word("BOB LEADING", 355.1, 450.0, 40.0, 45.0),
        Word("BOB", 175.0, 216.0, 45.0, 50.0),
        Word("TITLE TWO", 355.1, 410.0, 45.0, 50.0),
        Word("$20.00", 565.7, 588.6, 45.0, 50.0),
    ]

    result = parse_block(synthetic_modern_block(data_words))
    assert find(result.records, "ALICE").description == "TITLE ONE ALICE TRAILING"
    assert find(result.records, "BOB").description == "BOB LEADING TITLE TWO"
    assert result.unparsed == []


def test_expense_wrap_is_not_stolen_by_following_salary_like_row():
    data_words = [
        Word("ABCDEF01", 10.0, 60.0, 30.0, 35.0),
        Word("ALICE", 175.0, 216.0, 30.0, 35.0),
        Word("STAFF TRANSPORTATION", 355.1, 450.0, 30.0, 35.0),
        Word("$10.00", 565.7, 588.6, 30.0, 35.0),
        Word("WASHINGTON DC AND RETURN", 355.1, 500.0, 35.0, 40.0),
        # Four-digit source document numbers do not satisfy DOC_NUMBER_RE,
        # so this otherwise expense-shaped row follows the salary path.
        Word("1033", 10.0, 30.0, 40.0, 45.0),
        Word("FINANCIAL CLERK", 175.0, 250.0, 40.0, 45.0),
        Word("STAFF PER DIEM", 355.1, 430.0, 40.0, 45.0),
        Word("$20.00", 565.7, 588.6, 40.0, 45.0),
    ]

    result = parse_block(synthetic_modern_block(data_words))
    assert find(result.records, "ALICE").description == (
        "STAFF TRANSPORTATION WASHINGTON DC AND RETURN"
    )
    assert find(result.records, "FINANCIAL CLERK").description == "STAFF PER DIEM"


def test_subtotal_rows_are_captured_not_treated_as_records():
    block = make_block([1001])
    result = parse_block(block)
    labels = {s.label: s.amount for s in result.subtotals}

    assert labels["PERSONNEL COMP. FULL-TIME PERMANENT"] == "$2,041,538.11"
    assert labels["PERSONNEL BENEFITS"] == "$1,398.95"
    assert labels["NET PAYROLL EXPENSES"] == "$2,042,937.06"

    # subtotal text must not leak into the itemized records
    assert not any("PAYROLL" in r.description.upper() for r in result.records)


def test_block_spanning_multiple_pages_keeps_page_reference():
    block = make_block([1000, 1001])
    result = parse_block(block)
    pages = {r.page for r in result.records}
    assert pages == {1000, 1001}


def test_payee_column_boundary_covers_118sdoc2_shifted_template():
    """118sdoc2's payee text starts at x0=171.2, just 0.8pt inside the old
    172.0 PAYEE_COL boundary -- it was landing in DATE_POSTED_COL instead,
    producing a garbled 'date_posted' field ("01/27/2023 Charles E
    Schumer") and an empty payee."""
    block = make_block([25], office="MAJORITY LEADER (D)", funding_year=2023, prefix="sdoc2_vol1")
    result = parse_block(block)
    schumer = find(result.records, "Schumer")
    assert schumer.payee == "Charles E Schumer"
    assert schumer.date_posted == "01/27/2023"
    assert schumer.amount == "$51.63"


def test_amount_glued_to_long_title_is_recovered():
    """119sdoc5 page 1534: Patty Murray's title "PRESIDENT PRO TEMPORE
    EMERITUS" is long enough that the amount prints immediately after it
    with no separating space, so natural-pdf tokenizes them as one word
    landing entirely inside the description column -- silently dropping
    her $87,000 salary (this was the exact size of a reconciliation gap
    found in both 119sdoc5 and 119sdoc6's MEMBER COMPENSATION block)."""
    block = make_block([1534], office="MEMBER COMPENSATION", funding_year=2025, prefix="sdoc5_vol2")
    result = parse_block(block)
    murray = find(result.records, "MURRAY")
    assert murray.description == "PRESIDENT PRO TEMPORE EMERITUS"
    assert murray.amount == "$87,000.00"


def test_standalone_correction_amount_is_recorded_not_dropped():
    """117sdoc8 page 561: a lone "-$3,000.00" row with no payee or
    description follows Risch's salary roster, right before the
    PERSONNEL COMP subtotal. It can't fill in the previous record (Melika
    Willoughby already has her own amount), so it must become its own
    record rather than being silently dropped -- excluding it left this
    exact block's total $3,000 too high in the real report."""
    block = make_block([560, 561], office="SENATOR JAMES E. RISCH", funding_year=2021, prefix="sdoc8_vol2")
    result = parse_block(block)

    willoughby = find(result.records, "WILLOUGHBY")
    assert willoughby.amount == "$2,499.96"

    adjustments = [r for r in result.records if r.payee == "" and r.amount == "-$3,000.00"]
    assert len(adjustments) == 1
    assert adjustments[0].record_type == "salary"
    assert not result.unparsed


def test_positive_orphan_amount_is_not_double_counted():
    """117sdoc8 page 548: a bare "$4,554.00" row (no payee/description)
    appears after Boozman's roster, but it's a duplicate preview of the
    RE-EMPLOYED ANNUITANTS subtotal that reprints with its label a few
    rows later -- not a real second record. Recording it anyway would
    double-count $4,554 that RE-EMPLOYED ANNUITANTS already accounts for
    (as a `no_records` lump-sum category). Contrast with the *negative*
    case in test_standalone_correction_amount_is_recorded_not_dropped,
    which is a genuine correction and must be recorded."""
    block = make_block([547, 548], office="SENATOR JOHN BOOZMAN", funding_year=2021, prefix="sdoc8_vol1")
    result = parse_block(block)

    holly = find(result.records, "HOLLY, LAUREN")
    assert holly.amount == "$22,027.73"

    bare_positive = [r for r in result.records if r.payee == "" and r.amount == "$4,554.00"]
    assert bare_positive == []

    labels = {s.label: s.amount for s in result.subtotals}
    assert labels["RE-EMPLOYED ANNUITANTS"] == "$4,554.00"
    assert labels["PERSONNEL COMP. FULL-TIME PERMANENT"] == "$1,332,657.32"


def test_wide_page_template_columns_calibrate_correctly():
    """117sdoc8's whole table sits ~56-65pt right of 118sdoc13's (a wider
    612x792 page vs. 423x657), so fixed absolute column boundaries would
    misclassify or drop this row entirely. Column calibration is now
    derived from this page's own DESCRIPTION header position."""
    block = make_block([17], office="OFFICE OF THE VICE PRESIDENT (D) - HARRIS", funding_year=2021, prefix="sdoc8_vol1")
    result = parse_block(block)
    expense = [r for r in result.records if r.record_type == "expense"]
    assert len(expense) == 1
    rec = expense[0]
    assert rec.document_number == "CV210002812057"
    assert rec.date_posted == "09/28/2021"
    assert rec.payee == "SERGEANT AT ARMS"
    assert rec.start_date == "08/01/2021"
    assert rec.end_date == "08/31/2021"
    assert rec.description == "RECORDING STUDIO CERTIFICATIONS"
    assert rec.amount == "$265.00"


def test_rotated_page_label_does_not_swallow_adjacent_employee():
    """A lone rotated page-label character ('-' at x0=610) sits between
    BROXMEYER's and HEMINGWAY's rows on page 123. Without filtering it
    out, the tight-gap grouping merges all three rows into one record and
    drops HEMINGWAY's amount entirely."""
    block = make_block([123])
    result = parse_block(block)
    salary = [r for r in result.records if r.record_type == "salary"]

    broxmeyer = find(salary, "BROXMEYER")
    assert broxmeyer.amount == "$108,255.77"

    hemingway = find(salary, "HEMINGWAY")
    assert hemingway.amount == "$110,949.96"
    assert hemingway.payee != broxmeyer.payee


def test_wrapped_payee_with_doc_number_on_second_row_stays_one_record():
    """118sdoc13 p341 (DSEC23M50419): the payee wraps onto a second visual
    row and the document number prints on the SECOND row of the group.
    The doc-number group-split rule (one doc number per record) must not
    sever it -- splitting mangles the payee to 'INC.' and emits a spurious
    amount-less fragment."""
    rows = cluster_rows(load("341"))
    block = Block(header=BlockHeader("X", 2024, "X", 341), pages=[341], rows_by_page={341: rows})
    result = parse_block(block)
    rec = next(r for r in result.records if r.document_number == "DSEC23M50419")
    assert rec.payee == "GOVERNMENT RETIREMENT & BENEFITS, INC."
    assert rec.amount == "$27,484.00"


def test_subtotal_label_recognized_without_hyphen():
    """112th-114th COMPENSATION OF MEMBERS pages print the lump-sum label
    as 'REEMPLOYED ANNUITANTS' (no hyphen), while the canonical
    SUBTOTAL_LABELS entry is 'RE-EMPLOYED ANNUITANTS' (with hyphen). The
    space-squashed fallback (_SQUASHED_SUBTOTAL_LABELS) strips only spaces,
    so 'REEMPLOYEDANNUITANTS' never matches the squashed key
    'RE-EMPLOYEDANNUITANTS' (hyphen retained). The label is misread as an
    expense record, its amount ($11,082.00 on 114sdoc7/13, $17,442.53 on
    114sdoc4) becomes an 'unchecked' orphan, and NET PAYROLL EXPENSES
    undercounts by that figure (the COMPENSATION OF MEMBERS parser_suspect
    findings surfaced by the lump_at_net second_opinion fix). The
    squashed lookup must strip punctuation too, so the no-hyphen PDF text
    matches the canonical label."""
    from senate_parser.records import _subtotal_label_of

    assert _subtotal_label_of("REEMPLOYED ANNUITANTS") == "RE-EMPLOYED ANNUITANTS"
    # Canonical hyphenated form must still match.
    assert _subtotal_label_of("RE-EMPLOYED ANNUITANTS") == "RE-EMPLOYED ANNUITANTS"
    # Other punctuation-bearing labels must still match their canonical forms.
    assert _subtotal_label_of("PERSONNEL COMP. FULL-TIME PERMANENT") == "PERSONNEL COMP. FULL-TIME PERMANENT"
