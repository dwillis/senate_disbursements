"""Tests for the FY-boundary-pattern context tag on NET PAYROLL banner fails.

870 modern-era BANNER NET PAYROLL banner-vs-body fails concentrate in
FY-boundary reports: offices whose rosters span multiple funding years
(e.g. a Senator's FY2024 block and FY2025 block in the same report).
The banner's NET PAYROLL figure is per-FY, but the block's body can
include cross-year bookings, so the banner check fails for structural
reasons rather than parser defects. Tagging these with `fy_boundary_pattern`
in the reconciliation report's `context` column lets the 2,566
ORGANIZATION TOTALS fails stand out as the real review queue.
"""

from senate_parser.pipeline import tag_fy_boundary_patterns


def _row(office, fy, label, basis, status, **kw):
    base = {
        "office": office,
        "funding_year": fy,
        "account": "",
        "start_page": 100,
        "label": label,
        "check_page": 100,
        "expected": "",
        "actual": "",
        "status": status,
        "basis": basis,
        "second_opinion": "",
        "independent_sum": "",
        "context": "",
    }
    base.update(kw)
    return base


def test_net_payroll_banner_fail_tagged_when_office_spans_multiple_fy():
    """A BANNER NET PAYROLL fail for an office with blocks in two funding
    years gets context='fy_boundary_pattern'."""
    rows = [
        _row("SENATOR X", 2024, "BANNER NET PAYROLL", "banner", "fail"),
        _row("SENATOR X", 2025, "BANNER NET PAYROLL", "banner", "fail"),
        _row("SENATOR X", 2024, "BANNER ORGANIZATION TOTALS", "banner", "fail"),
    ]
    tag_fy_boundary_patterns(rows)
    payroll = [r for r in rows if r["label"] == "BANNER NET PAYROLL"]
    assert all(r["context"] == "fy_boundary_pattern" for r in payroll), (
        f"expected fy_boundary_pattern, got {[r['context'] for r in payroll]}")


def test_net_payroll_banner_fail_not_tagged_when_office_single_fy():
    """A BANNER NET PAYROLL fail for an office with only one funding year
    is a genuine review signal — no fy_boundary_pattern tag."""
    rows = [
        _row("SENATOR Y", 2024, "BANNER NET PAYROLL", "banner", "fail"),
        _row("SENATOR Y", 2024, "BANNER ORGANIZATION TOTALS", "banner", "ok"),
    ]
    tag_fy_boundary_patterns(rows)
    payroll = next(r for r in rows if r["label"] == "BANNER NET PAYROLL")
    assert payroll["context"] == "", (
        f"single-FY office should not be tagged, got {payroll['context']!r}")


def test_organization_totals_fail_not_tagged_even_for_multi_fy_office():
    """The tag is specific to BANNER NET PAYROLL — ORGANIZATION TOTALS
    fails are the real review queue and must not be hidden behind the
    fy_boundary_pattern tag."""
    rows = [
        _row("SENATOR Z", 2024, "BANNER ORGANIZATION TOTALS", "banner", "fail"),
        _row("SENATOR Z", 2025, "BANNER ORGANIZATION TOTALS", "banner", "fail"),
        _row("SENATOR Z", 2024, "BANNER NET PAYROLL", "banner", "fail"),
    ]
    tag_fy_boundary_patterns(rows)
    org = [r for r in rows if r["label"] == "BANNER ORGANIZATION TOTALS"]
    assert all(r["context"] == "" for r in org), (
        f"ORGANIZATION TOTALS should not be tagged, got {[r['context'] for r in org]}")
    payroll = next(r for r in rows if r["label"] == "BANNER NET PAYROLL")
    assert payroll["context"] == "fy_boundary_pattern"


def test_non_fail_net_payroll_not_tagged():
    """ok/warn/banner_missing/not_applicable NET PAYROLL checks are not
    tagged — only fails."""
    rows = [
        _row("SENATOR W", 2024, "BANNER NET PAYROLL", "banner", "ok"),
        _row("SENATOR W", 2025, "BANNER NET PAYROLL", "banner", "ok"),
    ]
    tag_fy_boundary_patterns(rows)
    assert all(r["context"] == "" for r in rows)


def test_segment_basis_fail_not_tagged():
    """The tag is specific to basis='banner' — segment-basis NET PAYROLL
    fails (the cross-year-netting kind) have their own second_opinion
    tagging and must not be double-tagged."""
    rows = [
        _row("SENATOR V", 2024, "NET PAYROLL EXPENSES", "segment", "fail"),
        _row("SENATOR V", 2025, "NET PAYROLL EXPENSES", "segment", "fail"),
    ]
    tag_fy_boundary_patterns(rows)
    assert all(r["context"] == "" for r in rows)


def test_idempotent():
    """Re-running on already-tagged rows is a no-op."""
    rows = [
        _row("SENATOR U", 2024, "BANNER NET PAYROLL", "banner", "fail"),
        _row("SENATOR U", 2025, "BANNER NET PAYROLL", "banner", "fail"),
    ]
    tag_fy_boundary_patterns(rows)
    first = [r["context"] for r in rows]
    tag_fy_boundary_patterns(rows)
    second = [r["context"] for r in rows]
    assert first == second == ["fy_boundary_pattern", "fy_boundary_pattern"]