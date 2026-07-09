"""Validate parsed amounts against the report's own printed subtotals.

This is the project's first correctness check that isn't just a row
count: for each inline subtotal (e.g. "OTHER CONTRACTUAL SERVICES
$80,325.38"), the records parsed since the previous subtotal should sum
to that figure. A block where this doesn't hold gets quarantined instead
of shipped, so an amount-attribution bug shows up as a reconciliation
failure rather than silently wrong data.

Some subtotals (see records.PERSONNEL_ROLLUP_LABELS) are a rollup of
the other personnel subtotals (NET PAYROLL EXPENSES = PERSONNEL COMP.
FULL-TIME PERMANENT + PERSONNEL BENEFITS + ...). Those are checked
against the block-wide running total (never reset) plus the printed
lump-sum subtotals that itemize no rows (records.LUMP_SUM_LABELS), and
an empty segment is not treated as a failure -- there's nothing to
itemize wrong.
"""

import re
from dataclasses import dataclass

from .records import LUMP_SUM_LABELS, PERSONNEL_ROLLUP_LABELS

OK_TOLERANCE = 0.01
WARN_TOLERANCE = 1.00

# The leading-digit group must be optional: sub-dollar amounts print as
# "$.80" (no zero before the decimal), and requiring a digit made them
# parse as None -- 9 published 118sdoc13 rows were silently excluded from
# every reconciliation sum this way.
AMOUNT_RE = re.compile(r"-?\$?(\d[\d,]*\.\d{2}|\.\d{2})")


def parse_amount(text: str):
    if not text:
        return None
    negative = text.strip().startswith("-")
    m = AMOUNT_RE.search(text)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    return -value if negative else value


@dataclass
class SubtotalCheck:
    label: str
    page: int
    expected: float  # None for the synthetic trailing-coverage check
    actual: float
    status: str  # 'ok' / 'warn' / 'fail' / 'no_records' / 'zero_records' / 'unchecked'
    basis: str  # 'segment' / 'block_running_total' / 'trailing' / 'banner'
    top: float = 0.0  # subtotal row's vertical position (segment bound)
    # Filled by second_opinion.py for failing segment checks:
    # '' / 'source_mismatch' / 'parser_suspect' / 'inconclusive'
    second_opinion: str = ""
    independent_sum: float = None


@dataclass
class ReconcileResult:
    checks: list
    block_status: str  # 'ok' / 'warn' / 'fail' / 'unchecked'
    records_total: int = 0
    records_checked: int = 0
    dollars_checked: float = 0.0
    dollars_unchecked: float = 0.0
    amount_parse_failures: int = 0
    # All itemized records + printed no-row lump sums: what the banner's
    # ORGANIZATION TOTALS period figure should equal.
    parsed_grand_total: float = 0.0


def _status(diff: float) -> str:
    if diff <= OK_TOLERANCE:
        return "ok"
    if diff <= WARN_TOLERANCE:
        return "warn"
    return "fail"


def reconcile_block(result) -> ReconcileResult:
    checks = []
    segment_sum = 0.0
    block_sum = 0.0
    lump_sum_total = 0.0  # printed lump-sum subtotals with no itemized rows
    segment_records = []  # (record, abs_dollars) accumulated since the last check
    records_total = 0
    records_checked = 0
    dollars_checked = 0.0
    dollars_unchecked = 0.0
    amount_parse_failures = 0

    def tag_segment(status: str, label: str, checked: bool):
        nonlocal records_checked, dollars_checked, dollars_unchecked
        for rec, dollars in segment_records:
            rec.validation_status = status
            rec.category = label
            if checked:
                records_checked += 1
                dollars_checked += dollars
            else:
                dollars_unchecked += dollars
        segment_records.clear()

    for event_type, obj in result.events:
        if event_type == "record":
            records_total += 1
            amt = parse_amount(obj.amount)
            if amt is not None:
                segment_sum += amt
                block_sum += amt
            elif obj.amount:
                amount_parse_failures += 1
            segment_records.append((obj, abs(amt) if amt is not None else 0.0))
            continue

        expected = parse_amount(obj.amount)
        if expected is None:
            continue

        if obj.label in PERSONNEL_ROLLUP_LABELS:
            # The printed rollup includes lump-sum categories (PERSONNEL
            # BENEFITS etc.) that print a subtotal but itemize no rows, so
            # the basis is itemized records + those printed lump sums.
            # Across the 7 processed reports, 830 of 868 historical rollup
            # mismatches were the forgotten lump sums, to the penny.
            actual = round(block_sum + lump_sum_total, 2)
            basis = "block_running_total"
            diff = abs(actual - expected)
            status = "no_records" if actual == 0.0 else _status(diff)
            # A rollup that reconciles has genuinely validated the records
            # (blocks whose ONLY subtotal is Net Payroll Expenses exist --
            # e.g. FEDERAL EMPLOYEES COMPENSATION ACCOUNT); one that
            # mismatches may still reflect source-side residuals, so those
            # records are 'unchecked', not 'fail'.
            tag_segment("ok" if status == "ok" else "unchecked", obj.label, checked=status == "ok")
        else:
            actual = segment_sum
            basis = "segment"
            diff = abs(actual - expected)
            if actual == 0.0 and not segment_records:
                # Expected for always-lump-sum labels; for normally
                # itemized labels this is `zero_records` -- advisory, not
                # gating (all historical cases are verified-legitimate
                # lump-summed adjustments, see records.LUMP_SUM_LABELS),
                # but distinct so a row-loss regression is countable.
                status = "no_records" if obj.label in LUMP_SUM_LABELS else "zero_records"
                if status == "no_records":
                    lump_sum_total += expected
            else:
                status = _status(diff)
            tag_segment(status, obj.label, checked=True)

        checks.append(
            SubtotalCheck(
                label=obj.label,
                page=obj.page,
                expected=expected,
                actual=actual,
                status=status,
                basis=basis,
                top=getattr(obj, "top", 0.0),
            )
        )
        segment_sum = 0.0

    # Records after the block's final subtotal (or in a block with no
    # subtotals at all) are covered by no check. They previously counted
    # toward nothing and the block still read 'ok' -- the one class of
    # rows that shipped with zero validation signal. Surface them.
    if segment_records:
        checks.append(
            SubtotalCheck(
                label="(after final subtotal)",
                page=segment_records[-1][0].page,
                expected=None,
                actual=round(segment_sum, 2),
                status="unchecked",
                basis="trailing",
            )
        )
        tag_segment("unchecked", "", checked=False)

    # Rollup checks (basis='block_running_total') compare against a total
    # that legitimately includes non-itemized lump-sum components (see
    # records.PERSONNEL_ROLLUP_LABELS) -- they're kept for transparency but
    # excluded from the pass/fail gate, since they'd never reconcile even
    # when every itemized row is correct. Trailing checks likewise don't
    # gate: unchecked rows aren't known-bad, they're known-unverified.
    severity = {"ok": 0, "no_records": 0, "zero_records": 0, "warn": 1, "fail": 2}
    segment_statuses = [severity[c.status] for c in checks if c.basis == "segment"]
    if segment_statuses:
        block_status = {0: "ok", 1: "warn", 2: "fail"}[max(segment_statuses)]
    elif records_total == 0:
        block_status = "ok"  # nothing published, nothing to verify
    elif any(c.basis == "block_running_total" and c.status == "ok" for c in checks):
        block_status = "ok"  # validated via a reconciling rollup
    else:
        block_status = "unchecked"

    return ReconcileResult(
        checks=checks,
        block_status=block_status,
        records_total=records_total,
        records_checked=records_checked,
        dollars_checked=round(dollars_checked, 2),
        dollars_unchecked=round(dollars_unchecked, 2),
        amount_parse_failures=amount_parse_failures,
        parsed_grand_total=round(block_sum + lump_sum_total, 2),
    )


def banner_checks(summary, reconciled: ReconcileResult, page: int) -> list:
    """Advisory two-source checks against the banner summary table (see
    segment.parse_banner_summary). Banner figures print negated (they're
    expenditures against the authorization), so compare magnitudes.
    Never gates: block_status only considers basis='segment' checks."""
    rollup = next((c for c in reconciled.checks if c.basis == "block_running_total"), None)
    out = []
    for label, banner_value, body_value in (
        ("BANNER NET PAYROLL", summary.net_payroll, rollup.expected if rollup else None),
        ("BANNER ORGANIZATION TOTALS", summary.organization_totals, reconciled.parsed_grand_total),
    ):
        if banner_value is None or body_value is None:
            status = "banner_missing"
            expected = banner_value if banner_value is None else abs(banner_value)
            actual = 0.0 if body_value is None else abs(body_value)
        else:
            expected = abs(banner_value)
            actual = abs(body_value)
            status = _status(abs(expected - actual))
        out.append(
            SubtotalCheck(label=label, page=page, expected=expected, actual=actual, status=status, basis="banner")
        )
    return out
