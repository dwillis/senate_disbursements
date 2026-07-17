import json
from pathlib import Path

from senate_parser.extract import Word
from senate_parser.reconcile import banner_checks, parse_amount, reconcile_block
from senate_parser.records import parse_block
from senate_parser.rows import cluster_rows
from senate_parser.segment import BannerSummary, Block, BlockHeader

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


def test_net_payroll_expenses_includes_printed_lump_sums():
    """Cotton's block: the printed NET PAYROLL figure = itemized salary
    rows + the PERSONNEL BENEFITS lump-sum subtotal ($1,398.95, no
    itemized rows by design). The rollup basis must include the printed
    lump sums -- 830 of 868 historical rollup mismatches across the 7
    reports were exactly the forgotten lump sums."""
    block = make_block([1000, 1001])
    result = parse_block(block)
    reconciled = reconcile_block(result)

    net_check = next(c for c in reconciled.checks if c.label == "NET PAYROLL EXPENSES")
    assert net_check.basis == "block_running_total"
    assert net_check.status == "ok"
    assert abs(net_check.expected - net_check.actual) <= 0.01


def test_rollup_with_lump_sums_reconciles():
    """Bennet-style (117sdoc8): itemized comp + a no-rows PERSONNEL
    BENEFITS subtotal must together match the printed rollup."""
    a = _rec("$1,788,121.78")
    result = _fake_result(
        [
            ("record", a),
            ("subtotal", _sub("PERSONNEL COMP. FULL-TIME PERMANENT", "$1,788,121.78")),
            ("subtotal", _sub("PERSONNEL BENEFITS", "$206.00")),
            ("subtotal", _sub("NET PAYROLL EXPENSES", "$1,788,327.78")),
        ]
    )
    reconciled = reconcile_block(result)
    rollup = next(c for c in reconciled.checks if c.basis == "block_running_total")
    assert rollup.status == "ok"
    assert a.validation_status == "ok"
    assert reconciled.block_status == "ok"


def test_rollup_residual_beyond_lump_sums_still_fails():
    result = _fake_result(
        [
            ("record", _rec("$100.00")),
            ("subtotal", _sub("PERSONNEL COMP. FULL-TIME PERMANENT", "$100.00")),
            ("subtotal", _sub("PERSONNEL BENEFITS", "$206.00")),
            ("subtotal", _sub("NET PAYROLL EXPENSES", "$500.00")),
        ]
    )
    reconciled = reconcile_block(result)
    rollup = next(c for c in reconciled.checks if c.basis == "block_running_total")
    assert rollup.status == "fail"
    assert rollup.actual == 306.0
    # rollup basis never gates -- the itemized segment reconciled
    assert reconciled.block_status == "ok"


def test_lump_sum_label_with_records_is_not_double_counted():
    """RE-EMPLOYED ANNUITANTS occasionally itemizes rows; those dollars
    are already in the running total, so the printed subtotal must not be
    added again."""
    result = _fake_result(
        [
            ("record", _rec("$100.00")),
            ("subtotal", _sub("PERSONNEL COMP. FULL-TIME PERMANENT", "$100.00")),
            ("record", _rec("$50.00")),
            ("subtotal", _sub("RE-EMPLOYED ANNUITANTS", "$50.00")),
            ("subtotal", _sub("NET PAYROLL EXPENSES", "$150.00")),
        ]
    )
    reconciled = reconcile_block(result)
    rollup = next(c for c in reconciled.checks if c.basis == "block_running_total")
    assert rollup.status == "ok"
    assert rollup.actual == 150.0


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


def test_banner_net_payroll_not_applicable_for_expense_only_block():
    """5,281 modern-era BANNER NET PAYROLL banner_missing checks are
    expense-only blocks (e.g. CONSULTANTS, MISC ITEMS) that structurally
    have no payroll rollup and no salary records -- the banner simply
    doesn't print a NET PAYROLL figure. Reclassify to 'not_applicable'
    so the 2,566 ORGANIZATION TOTALS fails stand out as the real queue."""
    # Expense-only block: no salary records, no rollup check, no banner
    # NET PAYROLL figure.
    summary = BannerSummary(net_payroll=None, organization_totals=1000.0)
    reconciled = reconcile_block(parse_block(make_block([1000, 1001])))
    # Strip any rollup check to simulate an expense-only block.
    reconciled.checks = [c for c in reconciled.checks
                         if c.label not in {"NET PAYROLL EXPENSES"}]
    checks = banner_checks(summary, reconciled, page=17, has_salary_records=False)
    payroll = next(c for c in checks if c.label == "BANNER NET PAYROLL")
    assert payroll.status == "not_applicable", (
        f"expense-only block should be not_applicable, got {payroll.status}")
    # ORGANIZATION TOTALS is unaffected -- it has a banner figure.
    org = next(c for c in checks if c.label == "BANNER ORGANIZATION TOTALS")
    assert org.status in ("ok", "warn", "fail", "banner_missing")


def test_banner_net_payroll_banner_missing_when_block_has_salary_records():
    """A block WITH salary records but no banner NET PAYROLL figure is a
    genuine banner_missing -- the banner should have printed one."""
    summary = BannerSummary(net_payroll=None, organization_totals=1000.0)
    reconciled = reconcile_block(parse_block(make_block([1000, 1001])))
    checks = banner_checks(summary, reconciled, page=17, has_salary_records=True)
    payroll = next(c for c in checks if c.label == "BANNER NET PAYROLL")
    assert payroll.status == "banner_missing", (
        f"block with salary records should be banner_missing, got {payroll.status}")


def test_banner_net_payroll_not_applicable_only_when_no_rollup_line():
    """A block with no salary records BUT a printed rollup line (e.g. a
    zero-dollar NET PAYROLL EXPENSES subtotal) is not 'not_applicable' --
    the rollup exists, so the banner missing it is still a real signal."""
    from senate_parser.reconcile import ReconcileResult, SubtotalCheck
    summary = BannerSummary(net_payroll=None, organization_totals=1000.0)
    reconciled = ReconcileResult(
        checks=[SubtotalCheck(label="NET PAYROLL EXPENSES", page=17,
                              expected=0.0, actual=0.0, status="no_records",
                              basis="segment")],
        block_status="ok",
    )
    checks = banner_checks(summary, reconciled, page=17, has_salary_records=False)
    payroll = next(c for c in checks if c.label == "BANNER NET PAYROLL")
    assert payroll.status == "banner_missing", (
        f"block with a rollup line should be banner_missing, got {payroll.status}")


def test_org_totals_downgrades_to_warn_when_uncaptured_categories_explain_gap():
    """The Sgt at Arms FY2025 block in 119sdoc5 has 9 banner summary
    categories; the parser only itemizes 4 of them (Net Payroll, Travel,
    Other Contractual Services, Acquisition of Assets). The other 5
    (Transportation of Things, Rent/Communications/Utilities, Printing,
    Supplies/Materials, Land and Structures) appear only in the banner
    summary table -- no itemized rows in the block body. The ORG TOTALS
    gap is fully explained by those 5 banner-only categories, so the
    check downgrades from 'fail' to 'warn' (with context=
    'banner_only_categories') -- the parser captured everything that
    was itemized; the gap is structural, not a parser bug."""
    from senate_parser.reconcile import ReconcileResult, SubtotalCheck
    # Captured categories: 4 segment checks (with itemized rows) + 1
    # rollup. Their parsed values sum to parsed_grand_total.
    captured_checks = [
        SubtotalCheck(label="NET PAYROLL EXPENSES", page=404,
                      expected=6516.93, actual=6516.93, status="ok",
                      basis="block_running_total"),
        SubtotalCheck(label="TRAVEL AND TRANSPORTATION OF PERSONS", page=437,
                      expected=384191.43, actual=384191.43, status="ok",
                      basis="segment"),
        SubtotalCheck(label="OTHER CONTRACTUAL SERVICES", page=464,
                      expected=16698626.83, actual=16698626.83, status="ok",
                      basis="segment"),
        SubtotalCheck(label="ACQUISITION OF ASSETS", page=484,
                      expected=12763094.65, actual=12763094.65, status="ok",
                      basis="segment"),
    ]
    parsed_grand_total = 6516.93 + 384191.43 + 16698626.83 + 12763094.65
    reconciled = ReconcileResult(
        checks=captured_checks,
        block_status="ok",
        parsed_grand_total=parsed_grand_total,
    )
    # Banner summary has all 9 categories; the 5 uncaptured ones sum to
    # exactly the gap between ORG TOTALS and parsed_grand_total.
    banner_categories = {
        "NET PAYROLL EXPENSES": -6516.93,
        "TRAVEL AND TRANSPORTATION OF PERSONS": -384191.43,
        "TRANSPORTATION OF THINGS": -75271.19,
        "RENT, COMMUNICATIONS AND UTILITIES": -13773804.54,
        "PRINTING AND REPRODUCTION": 19620.85,
        "OTHER CONTRACTUAL SERVICES": -16698626.83,
        "SUPPLIES AND MATERIALS": -1375806.27,
        "ACQUISITION OF ASSETS": -12763094.65,
        "LAND AND STRUCTURES": -211072.30,
        "ORGANIZATION TOTALS": -45268763.29,
    }
    summary = BannerSummary(
        net_payroll=-6516.93,
        organization_totals=-45268763.29,
        categories=banner_categories,
    )
    checks = banner_checks(summary, reconciled, page=404, has_salary_records=True)
    org = next(c for c in checks if c.label == "BANNER ORGANIZATION TOTALS")
    assert org.status == "warn", (
        f"gap explained by banner-only categories should be warn, got {org.status}")
    assert org.context == "banner_only_categories", (
        f"context should tag the downgrade, got {org.context!r}")
    assert abs(org.expected - 45268763.29) < 0.01
    assert abs(org.actual - parsed_grand_total) < 0.01


def test_org_totals_stays_fail_when_uncaptured_categories_do_not_explain_gap():
    """When the gap exceeds what banner-only categories explain, the
    check stays 'fail' -- there's a real parsing discrepancy beyond the
    structural one."""
    from senate_parser.reconcile import ReconcileResult, SubtotalCheck
    captured_checks = [
        SubtotalCheck(label="NET PAYROLL EXPENSES", page=404,
                      expected=6516.93, actual=6516.93, status="ok",
                      basis="block_running_total"),
    ]
    # parsed_grand_total is just the captured NET PAYROLL. The banner
    # says ORG TOTALS is $45.27M, but the uncaptured categories only sum
    # to $44.77M -- a $500K residual that the parser can't account for.
    parsed_grand_total = 6516.93
    reconciled = ReconcileResult(
        checks=captured_checks,
        block_status="ok",
        parsed_grand_total=parsed_grand_total,
    )
    banner_categories = {
        "NET PAYROLL EXPENSES": -6516.93,
        "TRANSPORTATION OF THINGS": -75271.19,
        "RENT, COMMUNICATIONS AND UTILITIES": -13773804.54,
        "PRINTING AND REPRODUCTION": 19620.85,
        "SUPPLIES AND MATERIALS": -1375806.27,
        "LAND AND STRUCTURES": -211072.30,
        "ORGANIZATION TOTALS": -45268763.29,
    }
    summary = BannerSummary(
        net_payroll=-6516.93,
        organization_totals=-45268763.29,
        categories=banner_categories,
    )
    checks = banner_checks(summary, reconciled, page=404, has_salary_records=True)
    org = next(c for c in checks if c.label == "BANNER ORGANIZATION TOTALS")
    assert org.status == "fail", (
        f"unexplained residual should stay fail, got {org.status}")
    assert org.context == ""


def test_org_totals_uncaptured_downgrade_only_when_categories_populated():
    """A block whose banner has no summary table (BannerSummary returns
    empty categories) falls through to the existing behavior -- fail stays
    fail, no downgrade."""
    from senate_parser.reconcile import ReconcileResult, SubtotalCheck
    captured_checks = [
        SubtotalCheck(label="NET PAYROLL EXPENSES", page=404,
                      expected=1000.0, actual=1000.0, status="ok",
                      basis="block_running_total"),
    ]
    reconciled = ReconcileResult(
        checks=captured_checks,
        block_status="ok",
        parsed_grand_total=1000.0,
    )
    # No categories dict -- simulates old banners or banner_missing.
    summary = BannerSummary(
        net_payroll=-1000.0,
        organization_totals=-5000.0,
    )
    checks = banner_checks(summary, reconciled, page=404, has_salary_records=True)
    org = next(c for c in checks if c.label == "BANNER ORGANIZATION TOTALS")
    assert org.status == "fail"
    assert org.context == ""


def test_org_totals_downgrades_for_summary_only_block():
    """A 1-page block where the office reported a lump-sum expenditure
    on one category without itemizing any rows (e.g. VICE PRESIDENT
    FY2024 in 119sdoc5 page 20: $5,704.52 on Supplies and Materials, no
    body rows). The block has no segment/running_total checks at all --
    every banner category is uncaptured -- and the gap is fully
    explained by the banner-only category. Downgrade fail -> warn so
    the structural case doesn't masquerade as a parser bug."""
    from senate_parser.reconcile import ReconcileResult
    # No body checks -- the block is summary-only.
    reconciled = ReconcileResult(checks=[], block_status="ok",
                                 parsed_grand_total=0.0)
    banner_categories = {
        "SUPPLIES AND MATERIALS": -5704.52,
        "ORGANIZATION TOTALS": -5704.52,
    }
    summary = BannerSummary(net_payroll=None, organization_totals=-5704.52,
                            categories=banner_categories)
    checks = banner_checks(summary, reconciled, page=20, has_salary_records=False)
    org = next(c for c in checks if c.label == "BANNER ORGANIZATION TOTALS")
    assert org.status == "warn", (
        f"summary-only block should downgrade to warn, got {org.status}")
    assert org.context == "banner_only_categories"


def test_org_totals_stays_fail_when_captured_categories_mismatch_their_banner():
    """If a captured category's itemized rows don't match its banner
    figure (a real parser bug for that category), the residual after
    subtracting the uncaptured sum stays nonzero -- the check stays
    fail. The downgrade must not mask per-category parsing bugs."""
    from senate_parser.reconcile import ReconcileResult, SubtotalCheck
    # Captured TRAVEL check fails: parser found $300K, banner says $384K.
    captured_checks = [
        SubtotalCheck(label="TRAVEL AND TRANSPORTATION OF PERSONS", page=404,
                      expected=384191.43, actual=300000.00, status="fail",
                      basis="segment"),
    ]
    parsed_grand_total = 300000.00
    reconciled = ReconcileResult(
        checks=captured_checks, block_status="fail",
        parsed_grand_total=parsed_grand_total,
    )
    # Same banner as the Sgt at Arms case.
    banner_categories = {
        "NET PAYROLL EXPENSES": -6516.93,
        "TRAVEL AND TRANSPORTATION OF PERSONS": -384191.43,
        "TRANSPORTATION OF THINGS": -75271.19,
        "RENT, COMMUNICATIONS AND UTILITIES": -13773804.54,
        "PRINTING AND REPRODUCTION": 19620.85,
        "OTHER CONTRACTUAL SERVICES": -16698626.83,
        "SUPPLIES AND MATERIALS": -1375806.27,
        "ACQUISITION OF ASSETS": -12763094.65,
        "LAND AND STRUCTURES": -211072.30,
        "ORGANIZATION TOTALS": -45268763.29,
    }
    summary = BannerSummary(net_payroll=-6516.93, organization_totals=-45268763.29,
                            categories=banner_categories)
    checks = banner_checks(summary, reconciled, page=404, has_salary_records=True)
    org = next(c for c in checks if c.label == "BANNER ORGANIZATION TOTALS")
    assert org.status == "fail", (
        f"captured category mismatch should stay fail, got {org.status}")
    assert org.context == ""


def test_org_totals_downgrades_when_captured_bvb_mismatch_explains_gap():
    """Item 9: a block where captured categories' body subtotals disagree
    with their banner values (source-side internal inconsistency — the
    body prints one figure, the banner prints another for the same
    category), and those mismatches sum (with any uncaptured categories)
    to the ORG TOTALS gap. Each captured body check passes ok, so the
    parser itemized correctly; the gap is structural, not a parser bug.
    Downgrade fail -> warn with context='captured_bvb_mismatch'.

    Canonical shape: senator blocks where the body's printed NET PAYROLL
    EXPENSES subtotal disagrees with the banner's NET PAYROLL figure by
    a few thousand dollars (independently flagged by the BANNER NET
    PAYROLL check), and the ORG TOTALS gap equals that disagreement."""
    from senate_parser.reconcile import ReconcileResult, SubtotalCheck
    # Two captured categories, both ok. NET PAYROLL's body matches its
    # banner; TRAVEL's body prints $3000 but its banner says $5000 -- a
    # $2000 source-side mismatch. ORG TOTALS banner ($6000) minus
    # parsed_grand_total ($4000) = $2000 gap, fully explained by the
    # captured bvb mismatch.
    captured_checks = [
        SubtotalCheck(label="NET PAYROLL EXPENSES", page=10,
                      expected=1000.0, actual=1000.0, status="ok",
                      basis="block_running_total"),
        SubtotalCheck(label="TRAVEL AND TRANSPORTATION OF PERSONS", page=10,
                      expected=3000.0, actual=3000.0, status="ok",
                      basis="segment"),
    ]
    parsed_grand_total = 1000.0 + 3000.0
    reconciled = ReconcileResult(
        checks=captured_checks, block_status="ok",
        parsed_grand_total=parsed_grand_total,
    )
    banner_categories = {
        "NET PAYROLL EXPENSES": -1000.0,
        "TRAVEL AND TRANSPORTATION OF PERSONS": -5000.0,
        "ORGANIZATION TOTALS": -6000.0,
    }
    summary = BannerSummary(net_payroll=-1000.0, organization_totals=-6000.0,
                            categories=banner_categories)
    checks = banner_checks(summary, reconciled, page=10, has_salary_records=True)
    org = next(c for c in checks if c.label == "BANNER ORGANIZATION TOTALS")
    assert org.status == "warn", (
        f"captured bvb mismatch explaining gap should warn, got {org.status}")
    assert org.context == "captured_bvb_mismatch", (
        f"context should tag captured_bvb_mismatch, got {org.context!r}")


def test_org_totals_stays_fail_when_captured_bvb_does_not_explain_gap():
    """When the captured bvb mismatches plus uncaptured categories don't
    fully explain the ORG TOTALS gap, the check stays fail -- there's a
    real residual the parser can't account for."""
    from senate_parser.reconcile import ReconcileResult, SubtotalCheck
    # TRAVEL body $3500 vs banner $5000 (mismatch $1500). Gap = $2500,
    # captured_bvb = $1500, residual = $1000 -> fail.
    captured_checks = [
        SubtotalCheck(label="TRAVEL AND TRANSPORTATION OF PERSONS", page=10,
                      expected=3500.0, actual=3500.0, status="ok",
                      basis="segment"),
    ]
    parsed_grand_total = 3500.0
    reconciled = ReconcileResult(
        checks=captured_checks, block_status="ok",
        parsed_grand_total=parsed_grand_total,
    )
    banner_categories = {
        "TRAVEL AND TRANSPORTATION OF PERSONS": -5000.0,
        "ORGANIZATION TOTALS": -6000.0,
    }
    summary = BannerSummary(net_payroll=None, organization_totals=-6000.0,
                            categories=banner_categories)
    checks = banner_checks(summary, reconciled, page=10, has_salary_records=False)
    org = next(c for c in checks if c.label == "BANNER ORGANIZATION TOTALS")
    assert org.status == "fail", (
        f"unexplained residual should stay fail, got {org.status}")
    assert org.context == ""


def test_org_totals_item9_bails_when_captured_category_fails():
    """If a captured category's body check fails, the equation
    parsed_grand_total = sum of |body_subtotal| breaks down -- the
    failing check's |actual - expected| discrepancy is an unaccounted
    term that could mask a real per-category parsing bug. Item 9 must
    bail (return None) and the check stays fail via the Item 8 path."""
    from senate_parser.reconcile import ReconcileResult, SubtotalCheck
    # TRAVEL body check fails: parser found $1000, body prints $5000.
    # Banner TRAVEL = $5000, ORG TOTALS = $5000. Gap = $4000. The
    # uncaptured sum is $0 (TRAVEL is captured). Without bailing,
    # captured_bvb (using expected) = 5000 - 5000 = 0, residual = 4000
    # -> fail. But consider the looser case where not bailing could
    # wrongly downgrade: bail-on-fail guarantees we never mask a real
    # TRAVEL parsing bug as a structural mismatch.
    captured_checks = [
        SubtotalCheck(label="TRAVEL AND TRANSPORTATION OF PERSONS", page=10,
                      expected=5000.0, actual=1000.0, status="fail",
                      basis="segment"),
    ]
    parsed_grand_total = 1000.0
    reconciled = ReconcileResult(
        checks=captured_checks, block_status="fail",
        parsed_grand_total=parsed_grand_total,
    )
    banner_categories = {
        "TRAVEL AND TRANSPORTATION OF PERSONS": -5000.0,
        "ORGANIZATION TOTALS": -5000.0,
    }
    summary = BannerSummary(net_payroll=None, organization_totals=-5000.0,
                            categories=banner_categories)
    checks = banner_checks(summary, reconciled, page=10, has_salary_records=False)
    org = next(c for c in checks if c.label == "BANNER ORGANIZATION TOTALS")
    assert org.status == "fail", (
        f"failing captured check should bail Item 9 and stay fail, got {org.status}")
    assert org.context == ""


def test_org_totals_item9_uses_zero_body_subtotal_for_zero_records():
    """A zero_records body check (normally-itemized label, body printed a
    subtotal but the parser captured no rows -- a verified-legitimate
    lump-summed adjustment per the audit) does NOT contribute its printed
    subtotal to parsed_grand_total. The body_subtotal for the equation is
    0, not |c.expected|. Otherwise captured_bvb is understated by |expected|
    and the check stays fail when it should downgrade.

    Scenario: TRAVEL body printed $5000 with zero rows (status=zero_records,
    verified adjustment). Banner TRAVEL = $5000, ORG TOTALS = $5000.
    parsed_grand_total = 0 (no itemized records, no lump_sum added).
    Gap = $5000, captured_bvb = $5000 - 0 = $5000, residual = 0 -> warn."""
    from senate_parser.reconcile import ReconcileResult, SubtotalCheck
    captured_checks = [
        SubtotalCheck(label="TRAVEL AND TRANSPORTATION OF PERSONS", page=10,
                      expected=5000.0, actual=0.0, status="zero_records",
                      basis="segment"),
    ]
    parsed_grand_total = 0.0  # zero_records contributes nothing
    reconciled = ReconcileResult(
        checks=captured_checks, block_status="ok",
        parsed_grand_total=parsed_grand_total,
    )
    banner_categories = {
        "TRAVEL AND TRANSPORTATION OF PERSONS": -5000.0,
        "ORGANIZATION TOTALS": -5000.0,
    }
    summary = BannerSummary(net_payroll=None, organization_totals=-5000.0,
                            categories=banner_categories)
    checks = banner_checks(summary, reconciled, page=10, has_salary_records=False)
    org = next(c for c in checks if c.label == "BANNER ORGANIZATION TOTALS")
    assert org.status == "warn", (
        f"zero_records body_subtotal should be 0, got {org.status}")
    assert org.context == "captured_bvb_mismatch", (
        f"context should tag captured_bvb_mismatch, got {org.context!r}")
