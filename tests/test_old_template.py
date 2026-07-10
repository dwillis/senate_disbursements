"""Golden-page tests for the 112th-114th Congress ("anchor") template.

This era's table generator differs from the modern one in three verified
ways: the header splits across two clustered visual rows (DESCRIPTION /
OBLIGATION sit ~1.4pt above the DOCUMENT NO. row), the office/account
left margin sits ~21.5pt left of DOCUMENT NO. (vs ~11.5 modern), and the
relative column spread is wider -- with a second, further-shifted header
layout on committee pages within the same document. Calibration therefore
derives every boundary from the page's own seven header anchors
(records._calibrate_from_anchors) instead of fixed deltas.
"""

import json
from pathlib import Path

from senate_parser.extract import Word
from senate_parser.records import calibrate_columns, parse_block
from senate_parser.reconcile import reconcile_block
from senate_parser.rows import cluster_rows
from senate_parser.segment import (
    Block,
    classify_page,
    parse_banner,
    parse_banner_summary,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> list:
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    return [Word(**d) for d in data]


def rows_of(name: str) -> list:
    return cluster_rows(load(name))


def make_block(name: str, page: int) -> Block:
    rows = rows_of(name)
    return Block(header=parse_banner(rows, page), pages=[page], rows_by_page={page: rows})


def test_classify_old_banner_page():
    assert classify_page(rows_of("114sdoc13_page_18")) == "banner"


def test_old_banner_office_and_split_funding_year():
    """Old banners put the office margin at doc_header-21.5, outside the
    modern offset's tolerance; the self-calibrating fallback must find it.
    'Funding Year' and the year are separate words here too."""
    header = parse_banner(rows_of("114sdoc13_page_18"), 18)
    assert header.office == "CHAIRMAN MAJORITY CONFERENCE COMMITTEE (D)"
    assert header.funding_year == 2014
    assert "EXP. ALLOWANCES" in header.account


def test_old_banner_other_reports():
    for name, year in (("114sdoc7_page_17", 2013), ("114sdoc4_page_17", 2013)):
        header = parse_banner(rows_of(name), 17)
        assert header.office == "CHAIRMAN MAJORITY CONFERENCE COMMITTEE (D)"
        assert header.funding_year == year


def test_anchor_calibration_puts_early_payee_in_payee_band():
    """'REID,HARRY' starts at x0=232 -- left of where the modern deltas
    would put the date/payee boundary. The anchor-derived payee band
    (PAYEE NAME header - 36) must contain it."""
    cols = calibrate_columns(rows_of("114sdoc13_page_25"), template="anchor")
    assert cols is not None
    assert cols.payee[0] <= 232 <= cols.payee[1]


def test_modern_calibration_refuses_old_page():
    """Without the anchor template, this page's split header must fail
    calibration (DESCRIPTION is not on the DOCUMENT NO. row) rather than
    produce wrong columns."""
    assert calibrate_columns(rows_of("114sdoc13_page_25"), template="modern") is None


def test_old_expense_record_parses_and_reconciles():
    block = make_block("114sdoc13_page_25", 25)
    result = parse_block(block, template="anchor")
    reid = next(r for r in result.records if r.payee == "REID,HARRY")
    assert reid.document_number == "DDOF21600169"
    assert reid.amount == "4,000.00"  # this era prints no $ on amounts
    assert reid.description.startswith("OTHER MISCELLANEOUS SERVICES")

    reconciled = reconcile_block(result)
    check = next(c for c in reconciled.checks if c.label == "OTHER CONTRACTUAL SERVICES")
    assert check.status == "ok"


def test_committee_layout_calibrates_with_same_anchors():
    """Committee pages within the same document shift PAYEE/DESCRIPTION
    right (second header layout); per-page anchor derivation absorbs it.
    This banner page carries only the block's two subtotal lines -- both
    must be recognized (as subtotals, with parseable amounts), not
    misread as records."""
    cols = calibrate_columns(rows_of("114sdoc13_page_2121"), template="anchor")
    assert cols is not None
    block = make_block("114sdoc13_page_2121", 2121)
    result = parse_block(block, template="anchor")
    assert [s.label for s in result.subtotals] == [
        "PERSONNEL COMP. FULL-TIME PERMANENT",
        "NET PAYROLL EXPENSES",
    ]
    assert result.subtotals[0].amount == "3,242.77"


def test_old_banner_summary_period_column():
    """p2120: Net Payroll period $0.00 (spending is all YTD on this
    113th-resolution block) and ORGANIZATION TOTALS period $0.00, with
    values printed on the same visual row as the label."""
    s = parse_banner_summary(rows_of("114sdoc13_page_2120"))
    assert s.net_payroll == 0.0
    assert s.organization_totals == 0.0

    s25 = parse_banner_summary(rows_of("114sdoc13_page_25"))
    assert s25.organization_totals == -6018.59


def _fake_result(events):
    class R:
        pass

    r = R()
    r.events = events
    return r


def _rec(amount, record_type="salary", page=1):
    from senate_parser.records import Record

    return Record(record_type=record_type, amount=amount, page=page)


def _sub(label, amount, page=1):
    from senate_parser.records import Subtotal

    return Subtotal(label=label, amount=amount, page=page)


def test_typed_reconcile_payroll_categories_partition_the_roster():
    """APPROPRIATIONS pattern (114sdoc13 p57-59): the printed payroll
    category lines (OPC 2,353.84 + PERSONNEL COMP 6,697,710.29 = NET
    6,700,064.13, penny-exact on the real block) *partition* the roster's
    total -- OPC dollars are itemized inside the roster itself. The
    category lines must be non-gating components; the roster validates as
    a whole against NET PAYROLL EXPENSES."""
    rows = [_rec("62,041.61"), _rec("84,916.63"), _rec("2,353.84")]
    result = _fake_result(
        [("record", r) for r in rows]
        + [
            ("subtotal", _sub("OTHER PERSONNEL COMPENSATION", "2,353.84")),
            ("subtotal", _sub("PERSONNEL COMP. FULL-TIME PERMANENT", "146,958.24")),
            ("subtotal", _sub("NET PAYROLL EXPENSES", "149,312.08")),
        ]
    )
    reconciled = reconcile_block(result, template="anchor")
    by_label = {c.label: c for c in reconciled.checks}
    assert by_label["OTHER PERSONNEL COMPENSATION"].basis == "payroll_component"
    assert by_label["PERSONNEL COMP. FULL-TIME PERMANENT"].basis == "payroll_component"
    assert by_label["NET PAYROLL EXPENSES"].status == "ok"
    assert by_label["NET PAYROLL EXPENSES"].basis == "segment"
    assert all(r.validation_status == "ok" for r in rows)
    assert all(r.category == "NET PAYROLL EXPENSES" for r in rows)
    assert reconciled.block_status == "ok"


def test_typed_reconcile_subtotals_at_end_of_block():
    """Committee pattern (JUDICIARY, 114sdoc13 p2221-2226): salary roster,
    then travel records, then ALL subtotals -- TRAVEL first, payroll after.
    Type-aware accumulation must keep the roster out of the TRAVEL check."""
    roster = [_rec("100.00"), _rec("200.00")]
    travel = [_rec("40.00", record_type="expense"), _rec("60.00", record_type="expense")]
    result = _fake_result(
        [("record", r) for r in roster]
        + [("record", r) for r in travel]
        + [
            ("subtotal", _sub("TRAVEL AND TRANSPORTATION OF PERSONS", "100.00")),
            ("subtotal", _sub("PERSONNEL COMP. FULL-TIME PERMANENT", "300.00")),
            ("subtotal", _sub("NET PAYROLL EXPENSES", "300.00")),
        ]
    )
    reconciled = reconcile_block(result, template="anchor")
    by_label = {c.label: c for c in reconciled.checks}
    assert by_label["TRAVEL AND TRANSPORTATION OF PERSONS"].status == "ok"
    assert by_label["TRAVEL AND TRANSPORTATION OF PERSONS"].actual == 100.0
    assert by_label["NET PAYROLL EXPENSES"].status == "ok"
    assert all(r.category == "TRAVEL AND TRANSPORTATION OF PERSONS" for r in travel)
    assert all(r.category == "NET PAYROLL EXPENSES" for r in roster)
    assert reconciled.block_status == "ok"


def test_typed_reconcile_trailing_records_flagged():
    result = _fake_result(
        [
            ("record", _rec("10.00")),
            ("subtotal", _sub("NET PAYROLL EXPENSES", "10.00")),
            ("record", _rec("5.00", record_type="expense")),
        ]
    )
    reconciled = reconcile_block(result, template="anchor")
    trailing = [c for c in reconciled.checks if c.basis == "trailing"]
    assert len(trailing) == 1 and trailing[0].actual == 5.0
    assert reconciled.block_status == "ok"


def test_split_subtotal_label_is_recognized():
    """Word extraction splits the label mid-word on 114sdoc13 p1011
    ('TRAVEL AND TRANSP' + 'ORTATION OF PERSONS'). It must classify as a
    subtotal with the canonical label -- misread as an expense record, its
    23,736.42 both double-counts and dissolves the segment boundary."""
    block = make_block("114sdoc13_page_1011", 1011)
    result = parse_block(block, template="anchor")
    travel = [s for s in result.subtotals if s.label == "TRAVEL AND TRANSPORTATION OF PERSONS"]
    assert len(travel) == 1
    assert travel[0].amount == "23,736.42"
    # the subtotal line must not ALSO be counted as an expense record
    assert not any(r.amount == "23,736.42" for r in result.records)


def test_typed_reconcile_true_lumps_feed_net_expectation():
    """Enzi/Inhofe FY2016 pattern (penny-exact on the real blocks): the
    roster itemizes PC + OPC; RE-EMPLOYED ANNUITANTS and PERSONNEL
    BENEFITS have no rows and are counted by NET on top of the roster."""
    rows = [_rec("999,068.98"), _rec("1,166.93")]  # PC + OPC itemized
    result = _fake_result(
        [("record", r) for r in rows]
        + [
            ("subtotal", _sub("OTHER PERSONNEL COMPENSATION", "1,166.93")),
            ("subtotal", _sub("PERSONNEL COMP. FULL-TIME PERMANENT", "999,068.98")),
            ("subtotal", _sub("RE-EMPLOYED ANNUITANTS", "55,794.00")),
            ("subtotal", _sub("PERSONNEL BENEFITS", "2,348.05")),
            ("subtotal", _sub("NET PAYROLL EXPENSES", "1,058,377.96")),
        ]
    )
    reconciled = reconcile_block(result, template="anchor")
    net = next(c for c in reconciled.checks if c.label == "NET PAYROLL EXPENSES")
    assert net.status == "ok"
    assert net.actual == 1058377.96
    assert reconciled.block_status == "ok"


def test_tight_rows_with_own_doc_numbers_stay_separate_records():
    """114sdoc4 p1915: consecutive JP Morgan $30.00 bank-fee rows print
    ~4.7pt apart -- inside the tight-group gap -- and each carries its own
    document number. They must parse as distinct records or the second
    amount silently vanishes."""
    block = make_block("114sdoc4_page_1915", 1915)
    result = parse_block(block, template="anchor")
    fees = [r for r in result.records if r.amount == "30.00" and "FEES" in r.description]
    assert len(fees) == 5
    assert len({r.document_number for r in fees}) == 5


def test_second_amount_in_tight_group_becomes_own_record():
    """114sdoc4 p1954: BITTLEMAN's STAFF PER DIEM (586.81) and the
    following STAFF TRANSPORTATION (103.14) line are only ~3.7pt apart --
    inside TIGHT_GROUP_GAP -- so _group_rows merges them into one group
    under DFIN21500039. classify_group's "first amount wins" then
    silently drops the second figure (verified on the real report: three
    segments each missing exactly one such STAFF TRANSPORTATION line).
    The split must recover it as its own expense_subline, inheriting the
    document number from context. (This page's payee/date column bands
    bleed into each other -- a separate, pre-existing calibration
    imprecision unrelated to this fix -- so this test checks the fields
    the split logic actually governs: both amounts present, correct
    document number, and the recovered subline's own description.)"""
    block = make_block("114sdoc4_page_1954", 1954)
    result = parse_block(block, template="anchor")
    bittleman = [r for r in result.records if "BITTLEMAN" in r.payee]
    assert len(bittleman) == 2
    per_diem = next(r for r in bittleman if r.amount == "586.81")
    transport = next(r for r in bittleman if r.amount == "103.14")
    assert transport.document_number == per_diem.document_number == "DFIN21500039"
    assert "STAFF TRANSPORTATION" in transport.description
