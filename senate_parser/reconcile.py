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

from .records import LUMP_SUM_LABELS, PAYROLL_ITEMIZED_LABELS, PERSONNEL_ROLLUP_LABELS

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
    # Free-text annotation for the reconciliation_report CSV. Currently
    # set by banner_checks when an ORG TOTALS fail is downgraded to warn
    # because the gap equals the sum of banner-only categories.
    context: str = ""


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


def reconcile_block(result, template: str = "modern") -> ReconcileResult:
    if template == "anchor":
        return _reconcile_block_typed(result)
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


def _reconcile_block_typed(result) -> ReconcileResult:
    """Old-template (112th-114th) reconciliation. That era differs from
    the modern one in two verified ways: all of a block's subtotals print
    at the END of the listing rather than inline after each category
    (JUDICIARY, 114sdoc13 p2221-2226: TRAVEL's subtotal prints before the
    payroll ones), and the payroll category lines (PERSONNEL COMP / OTHER
    PERSONNEL COMPENSATION / ...) *partition* the roster's total rather
    than covering distinct row runs -- OPC dollars are itemized inside
    the roster itself (verified: three blocks whose PERSONNEL COMP
    mismatch equals the printed OPC to the penny). Individual roster rows
    can't be attributed to one payroll category, but the roster's sum is
    fully checkable against NET PAYROLL EXPENSES.

    So: records accumulate per record_type; expense-category subtotals
    close the expense stream; NET PAYROLL EXPENSES closes the salary
    stream as its true segment check; the payroll category lines are
    recorded as non-gating 'component' checks.

    Which components are itemized vs lump-sum is verified penny-exact on
    real blocks (Enzi and Inhofe FY2016): the roster sums to PERSONNEL
    COMP + OTHER PERSONNEL COMPENSATION exactly, and the shortfall
    against NET equals RE-EMPLOYED ANNUITANTS + PERSONNEL BENEFITS
    exactly -- so PC/OPC/WAE partition the roster while the benefit
    categories are true lump sums added into the NET expectation."""
    # Itemized inside the roster (partition its total; add nothing):
    partition_labels = PAYROLL_ITEMIZED_LABELS | {"OTHER PERSONNEL COMPENSATION"}
    # True lump sums (no rows; counted by NET on top of the roster):
    lump_at_net = LUMP_SUM_LABELS - {"OTHER PERSONNEL COMPENSATION"}
    checks = []
    sums = {"salary": 0.0, "expense": 0.0}
    buffers = {"salary": [], "expense": []}
    block_sum = 0.0
    lump_sum_total = 0.0
    all_lump_sums = 0.0
    records_total = 0
    records_checked = 0
    dollars_checked = 0.0
    dollars_unchecked = 0.0
    amount_parse_failures = 0

    def tag(buffer, status: str, label: str, checked: bool):
        nonlocal records_checked, dollars_checked, dollars_unchecked
        for rec, dollars in buffer:
            rec.validation_status = status
            rec.category = label
            if checked:
                records_checked += 1
                dollars_checked += dollars
            else:
                dollars_unchecked += dollars
        buffer.clear()

    for event_type, obj in result.events:
        if event_type == "record":
            records_total += 1
            kind = "salary" if obj.record_type == "salary" else "expense"
            amt = parse_amount(obj.amount)
            if amt is not None:
                sums[kind] += amt
                block_sum += amt
            elif obj.amount:
                amount_parse_failures += 1
            buffers[kind].append((obj, abs(amt) if amt is not None else 0.0))
            continue

        expected = parse_amount(obj.amount)
        if expected is None:
            continue

        top = getattr(obj, "top", 0.0)
        if obj.label in partition_labels or obj.label in lump_at_net:
            # A slice of the payroll pie, not a row-run boundary; the
            # roster validates as a whole against NET PAYROLL EXPENSES.
            if obj.label in lump_at_net:
                lump_sum_total += expected
                all_lump_sums += expected
            checks.append(
                SubtotalCheck(label=obj.label, page=obj.page, expected=expected,
                              actual=0.0, status="component", basis="payroll_component", top=top)
            )
            continue

        if obj.label in PERSONNEL_ROLLUP_LABELS:
            kind = "salary"
            actual = round(sums[kind] + lump_sum_total, 2)
            lump_sum_total = 0.0
        else:
            kind = "expense"
            actual = round(sums[kind], 2)
        diff = abs(actual - expected)
        if actual == 0.0 and not buffers[kind]:
            status = "zero_records"
        else:
            status = _status(diff)
        tag(buffers[kind], status, obj.label, checked=True)
        checks.append(
            SubtotalCheck(label=obj.label, page=obj.page, expected=expected,
                          actual=actual, status=status, basis="segment", top=top)
        )
        sums[kind] = 0.0

    leftovers = buffers["salary"] + buffers["expense"]
    if leftovers:
        checks.append(
            SubtotalCheck(
                label="(after final subtotal)",
                page=leftovers[-1][0].page,
                expected=None,
                actual=round(sums["salary"] + sums["expense"], 2),
                status="unchecked",
                basis="trailing",
            )
        )
        tag(buffers["salary"], "unchecked", "", checked=False)
        tag(buffers["expense"], "unchecked", "", checked=False)

    severity = {"ok": 0, "no_records": 0, "zero_records": 0, "warn": 1, "fail": 2}
    segment_statuses = [severity[c.status] for c in checks if c.basis == "segment"]
    if segment_statuses:
        block_status = {0: "ok", 1: "warn", 2: "fail"}[max(segment_statuses)]
    elif records_total == 0:
        block_status = "ok"
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
        parsed_grand_total=round(block_sum + all_lump_sums, 2),
    )


def banner_checks(summary, reconciled: ReconcileResult, page: int, has_salary_records: bool = True) -> list:
    """Advisory two-source checks against the banner summary table (see
    segment.parse_banner_summary). Banner figures print negated (they're
    expenditures against the authorization), so compare magnitudes.
    Never gates: block_status only considers basis='segment' checks.

    `has_salary_records` drives the not_applicable reclassification for
    expense-only blocks: when the block has no salary records AND no
    NET PAYROLL rollup line AND the banner prints no NET PAYROLL figure,
    the block structurally has no payroll to cross-check, so the check
    is not_applicable rather than banner_missing. Clears ~5,281 modern
    expense-only blocks (CONSULTANTS, MISC ITEMS, etc.) from the queue.

    For BANNER ORGANIZATION TOTALS, when the body-vs-banner diff is
    fully explained by banner summary categories that have no matching
    check in the block (categories that print only on the banner, with
    no itemized rows), the check downgrades from 'fail' to 'warn' with
    context='banner_only_categories'. The Sgt at Arms FY2025 block in
    119sdoc5 is the canonical case: 5 of 9 categories are banner-only,
    ~$15.4M, and the parser captured every itemized row correctly."""
    # The printed NET PAYROLL figure lives on a block_running_total check
    # on the modern template and on a segment check in the old-template
    # typed mode -- match by label, not basis.
    rollup = next((c for c in reconciled.checks if c.label in PERSONNEL_ROLLUP_LABELS), None)
    out = []
    for label, banner_value, body_value in (
        ("BANNER NET PAYROLL", summary.net_payroll, rollup.expected if rollup else None),
        ("BANNER ORGANIZATION TOTALS", summary.organization_totals, reconciled.parsed_grand_total),
    ):
        if banner_value is None or body_value is None:
            if (label == "BANNER NET PAYROLL"
                    and banner_value is None
                    and body_value is None
                    and not has_salary_records):
                status = "not_applicable"
            else:
                status = "banner_missing"
            expected = banner_value if banner_value is None else abs(banner_value)
            actual = 0.0 if body_value is None else abs(body_value)
            context = ""
        else:
            expected = abs(banner_value)
            actual = abs(body_value)
            status = _status(abs(expected - actual))
            context = ""
            if (status == "fail" and label == "BANNER ORGANIZATION TOTALS"
                    and getattr(summary, "categories", None)):
                uncaptured_abs = _uncaptured_banner_categories_abs(
                    summary.categories, reconciled.checks)
                if uncaptured_abs is not None:
                    residual = abs((expected - actual) - uncaptured_abs)
                    if residual <= OK_TOLERANCE:
                        status = "warn"
                        context = "banner_only_categories"
        out.append(
            SubtotalCheck(label=label, page=page, expected=expected, actual=actual,
                          status=status, basis="banner", context=context)
        )
    return out


def _uncaptured_banner_categories_abs(banner_categories: dict, checks: list):
    """Sum the magnitudes of banner summary category figures that have no
    matching check in the block (categories that print only on the
    banner, with no itemized rows). Returns None if ORGANIZATION TOTALS
    is absent from `banner_categories` (no anchor to compare against).

    A banner category is 'captured' if any check.label matches its
    normalized uppercase label. ORGANIZATION TOTALS itself is the row
    being checked, not a category, so it's always excluded from the
    uncaptured sum (even though it appears in `banner_categories`)."""
    if "ORGANIZATION TOTALS" not in banner_categories:
        return None
    check_labels = {c.label.upper() for c in checks}
    total = 0.0
    for label, value in banner_categories.items():
        if label == "ORGANIZATION TOTALS":
            continue
        if label in check_labels:
            continue
        if value is None:
            continue
        total += value
    return abs(total)
