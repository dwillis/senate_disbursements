"""Tests for the cross-year payroll-netting key normalization.

Old-era (112th-114th Congress) reports book a committee's payroll
adjustments against one funding year's authorization while itemizing
the rows under a sibling year's roster (verified: ARMED SERVICES 113TH
−$2,512,990.93 vs 114TH +$2,512,990.93 — the offsets pair exactly). The
existing netting step in pipeline.py keyed on the raw office string, which
embeds the resolution clause (S.RES. 64B (113TH) ...) and so never paired
them. ETHICS COMMITTEE uses an 'FY YYYY' suffix instead of an S.RES.
clause and was likewise missed.

These tests cover the normalized key and the end-to-end release step on
synthetic two-block inputs that mimic the 113th/114th pairing.
"""

from senate_parser.pipeline import _apply_cross_year_release, _normalize_office_key
from senate_parser.records import BlockParseResult, Record
from senate_parser.reconcile import ReconcileResult, SubtotalCheck
from senate_parser.segment import Block, BlockHeader

ROLLUP = "NET PAYROLL EXPENSES"


def _make_processed(office, residual):
    """One block whose NET PAYROLL EXPENSES segment check fails by `residual`
    (actual - expected). Records carry validation_status='fail' so the
    release step can retag them to 'source_mismatch'."""
    block = Block(
        header=BlockHeader(office=office, funding_year=None, account="A", start_page=1),
        pages=[1],
        rows_by_page={},
    )
    rec = Record(record_type="salary", amount=f"{abs(residual):.2f}",
                 validation_status="fail", category=ROLLUP)
    result = BlockParseResult(records=[rec], events=[("record", rec)])
    check = SubtotalCheck(
        label=ROLLUP, page=1,
        expected=0.0, actual=residual,
        status="fail", basis="segment",
    )
    reconciled = ReconcileResult(checks=[check], block_status="fail")
    return (block, result, reconciled, "fail")


def test_normalize_office_key_strips_s_res_clause():
    office_113 = "ARMED SERVICES ARMED SERVICES - S.RES. 64B (113TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    office_114 = "ARMED SERVICES ARMED SERVICES - S.RES. 73B (114TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    assert _normalize_office_key(office_113) == _normalize_office_key(office_114)
    assert _normalize_office_key(office_113) == "ARMED SERVICES ARMED SERVICES"


def test_normalize_office_key_strips_fy_suffix():
    # ETHICS COMMITTEE uses 'FY YYYY' rather than an S.RES. clause.
    fy_2013 = "ETHICS COMMITTEE ON ETHICS - FY 2013 EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    fy_2014 = "ETHICS COMMITTEE ON ETHICS - FY 2014 EXPENSES OF INQUIRIES AND INVESTIGATIONS"
    assert _normalize_office_key(fy_2013) == _normalize_office_key(fy_2014)
    assert _normalize_office_key(fy_2013) == "ETHICS COMMITTEE ON ETHICS"


def test_normalize_office_key_preserves_offices_without_year_marker():
    # Modern-era offices without a resolution/fy marker are returned intact
    # (stripped of trailing whitespace only) — they don't participate in
    # cross-year netting but the key must still be stable.
    office = "SENATOR TOM COTTON"
    assert _normalize_office_key(office) == "SENATOR TOM COTTON"


def test_normalize_office_key_handles_empty():
    assert _normalize_office_key("") == ""


def test_cross_year_release_pairs_resolution_suffixed_offices():
    # 113TH block under-shoots by $2,512,990.93; 114TH block over-shoots by
    # the same amount — they cancel and should release as 'cross_year'.
    processed = [
        _make_processed(
            "ARMED SERVICES ARMED SERVICES - S.RES. 64B (113TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS",
            -2512990.93,
        ),
        _make_processed(
            "ARMED SERVICES ARMED SERVICES - S.RES. 73B (114TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS",
            2512990.93,
        ),
    ]

    _apply_cross_year_release(processed)

    checks = [c for _, _, r, _ in processed for c in r.checks]
    records = [rec for _, r, _, _ in processed for rec in r.records]
    assert all(c.second_opinion == "cross_year" for c in checks)
    assert all(rec.validation_status == "source_mismatch" for rec in records)


def test_cross_year_release_does_not_pair_unrelated_offices():
    # Two offices with offsetting residuals but different committee names
    # must NOT be netted together.
    processed = [
        _make_processed(
            "ARMED SERVICES ARMED SERVICES - S.RES. 64B (113TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS",
            -1000.00,
        ),
        _make_processed(
            "BANKING, HOUSING AND URBAN AFFAIRS BANKING - S.RES. 73B (114TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS",
            1000.00,
        ),
    ]

    _apply_cross_year_release(processed)

    checks = [c for _, _, r, _ in processed for c in r.checks]
    records = [rec for _, r, _, _ in processed for rec in r.records]
    assert all(c.second_opinion == "" for c in checks)
    assert all(rec.validation_status == "fail" for rec in records)


def test_cross_year_release_requires_residuals_to_cancel():
    # Same committee, same normalized key, but residuals don't cancel —
    # leave them as fails (still a real discrepancy).
    processed = [
        _make_processed(
            "ARMED SERVICES ARMED SERVICES - S.RES. 64B (113TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS",
            -1000.00,
        ),
        _make_processed(
            "ARMED SERVICES ARMED SERVICES - S.RES. 73B (114TH) EXPENSES OF INQUIRIES AND INVESTIGATIONS",
            500.00,
        ),
    ]

    _apply_cross_year_release(processed)

    checks = [c for _, _, r, _ in processed for c in r.checks]
    records = [rec for _, r, _, _ in processed for rec in r.records]
    assert all(c.second_opinion == "" for c in checks)
    assert all(rec.validation_status == "fail" for rec in records)