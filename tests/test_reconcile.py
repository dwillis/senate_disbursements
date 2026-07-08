import json
from pathlib import Path

from senate_parser.extract import Word
from senate_parser.reconcile import parse_amount, reconcile_block
from senate_parser.records import parse_block
from senate_parser.rows import cluster_rows
from senate_parser.segment import Block, BlockHeader

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> list[Word]:
    data = json.loads((FIXTURES / f"vol1_page_{name}.json").read_text())
    return [Word(**d) for d in data]


def make_block(page_nums, office="TEST OFFICE", funding_year=2024):
    rows_by_page = {p: cluster_rows(load(p)) for p in page_nums}
    header = BlockHeader(office=office, funding_year=funding_year, account="TEST ACCOUNT", start_page=page_nums[0])
    return Block(header=header, pages=list(page_nums), rows_by_page=rows_by_page)


def test_parse_amount_handles_sign_and_dollar_sign():
    assert parse_amount("$110,949.96") == 110949.96
    assert parse_amount("-$2,190,920.79") == -2190920.79
    assert parse_amount("") is None
    assert parse_amount(None) is None


def test_parse_amount_handles_sub_dollar_amounts():
    """The report prints sub-dollar amounts with no digit before the
    decimal ("$.80"). The original regex required one, so 9 published
    118sdoc13 rows were silently excluded from every reconciliation sum."""
    assert parse_amount("$.80") == 0.80
    assert parse_amount("-$.22") == -0.22
    assert parse_amount("$.00") == 0.0


def test_personnel_comp_reconciles_against_salary_records():
    """Cotton's block: ~40 salary rows across pages 1000-1001 should sum
    exactly to the printed PERSONNEL COMP. FULL-TIME PERMANENT subtotal."""
    block = make_block([1000, 1001])
    result = parse_block(block)
    reconciled = reconcile_block(result)

    comp_check = next(c for c in reconciled.checks if c.label == "PERSONNEL COMP. FULL-TIME PERMANENT")
    assert comp_check.status == "ok"
    assert abs(comp_check.expected - comp_check.actual) < 0.01


def test_net_payroll_expenses_checked_against_block_running_total():
    block = make_block([1000, 1001])
    result = parse_block(block)
    reconciled = reconcile_block(result)

    net_check = next(c for c in reconciled.checks if c.label == "NET PAYROLL EXPENSES")
    assert net_check.basis == "block_running_total"
    # PERSONNEL BENEFITS ($1,398.95) has no itemized rows, so NET PAYROLL
    # is short by exactly that amount -- a known, explainable gap, not a
    # parsing bug (see records.PERSONNEL_ROLLUP_LABELS).
    assert abs(net_check.expected - net_check.actual - 1398.95) < 0.01


def test_lump_sum_category_with_no_records_is_not_a_failure():
    block = make_block([1000, 1001])
    result = parse_block(block)
    reconciled = reconcile_block(result)

    benefits_check = next(c for c in reconciled.checks if c.label == "PERSONNEL BENEFITS")
    assert benefits_check.status == "no_records"


def test_block_status_ok_when_all_segments_reconcile():
    block = make_block([1000, 1001])
    result = parse_block(block)
    reconciled = reconcile_block(result)
    assert reconciled.block_status in ("ok", "warn")  # PERSONNEL BENEFITS' no_records doesn't fail the block


def _fake_result(events):
    """reconcile_block only reads .events, so a stub suffices for
    scenarios that are hard to source from a single fixture page."""

    class R:
        pass

    r = R()
    r.events = events
    return r


def _rec(amount, page=1):
    from senate_parser.records import Record

    return Record(record_type="salary", amount=amount, page=page)


def _sub(label, amount, page=1):
    from senate_parser.records import Subtotal

    return Subtotal(label=label, amount=amount, page=page)


def test_trailing_records_after_final_subtotal_are_flagged_unchecked():
    """Records after a block's last subtotal were previously covered by no
    check at all and the block still read 'ok' -- the one class of rows
    that shipped with zero validation signal."""
    a, b, c = _rec("$100.00"), _rec("$50.00"), _rec("$25.00")
    result = _fake_result(
        [
            ("record", a),
            ("record", b),
            ("subtotal", _sub("TRAVEL AND TRANSPORTATION OF PERSONS", "$150.00")),
            ("record", c),
        ]
    )
    reconciled = reconcile_block(result)

    trailing = [ch for ch in reconciled.checks if ch.basis == "trailing"]
    assert len(trailing) == 1
    assert trailing[0].status == "unchecked"
    assert trailing[0].actual == 25.0
    assert trailing[0].expected is None

    assert a.validation_status == "ok" and a.category == "TRAVEL AND TRANSPORTATION OF PERSONS"
    assert b.validation_status == "ok"
    assert c.validation_status == "unchecked" and c.category == ""

    assert reconciled.records_total == 3
    assert reconciled.records_checked == 2
    assert reconciled.dollars_checked == 150.0
    assert reconciled.dollars_unchecked == 25.0
    # trailing rows don't gate: the block still publishes (flagged), so
    # block_status reflects the segment checks that did run
    assert reconciled.block_status == "ok"


def test_block_with_no_subtotals_is_unchecked_not_ok():
    result = _fake_result([("record", _rec("$10.00")), ("record", _rec("$20.00"))])
    reconciled = reconcile_block(result)
    assert reconciled.block_status == "unchecked"
    assert reconciled.records_checked == 0
    assert reconciled.dollars_unchecked == 30.0


def test_block_validated_only_by_reconciling_rollup_is_ok():
    """Some blocks' only subtotal is Net Payroll Expenses (e.g. FEDERAL
    EMPLOYEES COMPENSATION ACCOUNT). When that rollup reconciles exactly,
    it genuinely validated the records -- don't downgrade to unchecked."""
    a = _rec("$76,930.66")
    result = _fake_result([("record", a), ("subtotal", _sub("NET PAYROLL EXPENSES", "$76,930.66"))])
    reconciled = reconcile_block(result)
    assert reconciled.block_status == "ok"
    assert a.validation_status == "ok"
    assert reconciled.records_checked == 1


def test_block_with_only_mismatching_rollup_is_unchecked():
    """A mismatching rollup usually just reflects its non-itemized
    lump-sum components -- records aren't known-bad, they're unverified."""
    a = _rec("$100.00")
    result = _fake_result([("record", a), ("subtotal", _sub("NET PAYROLL EXPENSES", "$150.00"))])
    reconciled = reconcile_block(result)
    assert reconciled.block_status == "unchecked"
    assert a.validation_status == "unchecked"


def test_empty_block_is_ok():
    reconciled = reconcile_block(_fake_result([]))
    assert reconciled.block_status == "ok"
    assert reconciled.records_total == 0


def test_zero_record_segment_on_itemized_label_gets_distinct_advisory_status():
    """A normally-itemized label (TRAVEL...) with a zero-record segment
    gets `zero_records`, not `no_records` -- all 5 historical cases are
    verified-legitimate lump-summed adjustments (e.g. Feinstein's
    post-death -$19,384.77 PERSONNEL COMP), so it doesn't gate, but it
    must be countable so a row-loss regression can't hide among the
    ~1,400 routine no_records checks."""
    result = _fake_result([("subtotal", _sub("TRAVEL AND TRANSPORTATION OF PERSONS", "-$26.12"))])
    reconciled = reconcile_block(result)
    assert reconciled.checks[0].status == "zero_records"
    assert reconciled.block_status == "ok"  # advisory, non-gating


def test_zero_record_segment_on_lump_sum_label_stays_no_records():
    result = _fake_result([("subtotal", _sub("PERSONNEL BENEFITS", "$1,398.95"))])
    reconciled = reconcile_block(result)
    assert reconciled.checks[0].status == "no_records"


def test_zero_sum_segment_with_records_is_not_zero_records():
    """A segment whose records genuinely sum to zero (charge + refund) is
    a real reconciliation against the printed figure, not an empty one."""
    result = _fake_result(
        [
            ("record", _rec("$50.00")),
            ("record", _rec("-$50.00")),
            ("subtotal", _sub("TRAVEL AND TRANSPORTATION OF PERSONS", "$.00")),
        ]
    )
    reconciled = reconcile_block(result)
    assert reconciled.checks[0].status == "ok"


def test_records_tagged_with_segment_outcome_on_real_block():
    """Cotton's block (pages 1000-1001): every salary record must be
    tagged ok / PERSONNEL COMP. FULL-TIME PERMANENT."""
    block = make_block([1000, 1001])
    result = parse_block(block)
    reconcile_block(result)

    salary = [r for r in result.records if r.record_type == "salary"]
    assert salary
    assert all(r.validation_status == "ok" for r in salary)
    assert all(r.category == "PERSONNEL COMP. FULL-TIME PERMANENT" for r in salary)
