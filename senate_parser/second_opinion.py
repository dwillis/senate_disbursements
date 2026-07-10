"""Independent re-check of failing reconciliation segments.

When a segment's itemized records don't sum to the report's own printed
subtotal, one of two things is true: the parser mis-extracted, or the
source document's itemization doesn't add up to its own total (verified
real: INTERN COMPENSATION - BLACKBURN in 118sdoc13 prints 14 rows summing
to $24,774.31 under a subtotal of $24,745.43 -- and the banner agrees
with the subtotal, not the rows).

To tell those apart mechanically, this module re-sums the amount column
straight from the raw page rows, deliberately bypassing the record
classifier (grouping / orphan handling / duplicate-preview logic -- the
historically buggy layer). Three-way comparison of {independent sum,
parser sum, printed subtotal} yields a verdict:

- independent == parser != printed  ->  'source_mismatch': the rows are
  faithful transcriptions; publish them tagged so consumers can filter.
- independent == printed != parser  ->  'parser_suspect': OUR bug; rows
  stay quarantined and an audit entry makes it impossible to miss.
- anything else                     ->  'inconclusive': stays quarantined.

The independent pass is intentionally naive: it will diverge from the
parser on pages needing the classifier's special cases (glued amounts,
duplicate previews), which safely degrades to 'inconclusive'.
"""

from .records import (
    LUMP_SUM_LABELS,
    PAYROLL_ITEMIZED_LABELS,
    _is_page_label_row,
    _is_subtotal_label,
    calibrate_columns,
)
from .reconcile import OK_TOLERANCE, parse_amount
from .segment import header_row_top

# Verdict values (also used as validation_status for released rows).
SOURCE_MISMATCH = "source_mismatch"
PARSER_SUSPECT = "parser_suspect"
INCONCLUSIVE = "inconclusive"


def _independent_segment_sum(block, start_pos, end_pos, template="modern"):
    """Sum amount-column tokens for data rows strictly between two
    (page, top) positions. Returns (sum, ok) -- ok is False when a page
    in range has no recognizable header (both this pass and the parser
    are blind there, so agreement would be meaningless)."""
    total = 0.0
    for page_num in sorted(block.pages):
        if page_num < start_pos[0] or page_num > end_pos[0]:
            continue
        rows = block.rows_by_page[page_num]
        cols = calibrate_columns(rows, template=template)
        if cols is None:
            return 0.0, False
        header_top = header_row_top(rows)
        for r in sorted(rows, key=lambda r: r.top):
            pos = (page_num, r.top)
            if pos <= start_pos or pos >= end_pos:
                continue
            if r.top <= header_top + 20 or _is_page_label_row(r, cols.amount[1]):
                continue
            amount_text = r.text_in(*cols.amount)
            if not amount_text:
                continue
            # Printed subtotal/label rows are boundaries, not records.
            label_text = r.text_in(cols.start_date[0], cols.amount[0])
            if _is_subtotal_label(label_text):
                continue
            amt = parse_amount(amount_text)
            if amt is not None:
                total += amt
    return round(total, 2), True


def apply_second_opinion(block, result, reconciled, template="modern") -> list:
    """Re-check every failing segment in a reconciled block. Mutates the
    failing SubtotalChecks (verdict + independent sum) and, on a
    'source_mismatch' verdict, retags that segment's records so they
    publish. Returns audit-entry dicts for 'parser_suspect' verdicts."""
    audit_items = []
    # In the old-template typed mode, payroll component lines don't close
    # segments (reconcile._reconcile_block_typed), so they aren't segment
    # boundaries either.
    if template == "anchor":
        boundary_subs = [
            s for s in result.subtotals
            if s.label not in LUMP_SUM_LABELS and s.label not in PAYROLL_ITEMIZED_LABELS
        ]
    else:
        boundary_subs = result.subtotals
    subtotal_positions = sorted((s.page, s.top) for s in boundary_subs)

    for check in reconciled.checks:
        if check.basis != "segment" or check.status != "fail":
            continue

        end_pos = (check.page, check.top)
        earlier = [p for p in subtotal_positions if p < end_pos]
        start_pos = earlier[-1] if earlier else (min(block.pages), -1.0)

        independent, readable = _independent_segment_sum(block, start_pos, end_pos, template=template)
        if not readable:
            check.second_opinion = INCONCLUSIVE
            continue
        check.independent_sum = independent

        matches_parser = abs(independent - check.actual) <= OK_TOLERANCE
        matches_printed = abs(independent - check.expected) <= OK_TOLERANCE
        if matches_parser and not matches_printed:
            check.second_opinion = SOURCE_MISMATCH
            for rec in result.records:
                if rec.validation_status == "fail" and rec.category == check.label:
                    rec.validation_status = SOURCE_MISMATCH
        elif matches_printed and not matches_parser:
            check.second_opinion = PARSER_SUSPECT
            audit_items.append(
                {
                    "reason": "second_opinion_disagrees",
                    "detail": (
                        f"{check.label} p{check.page}: independent sum {independent} "
                        f"matches printed {check.expected} but parser got {check.actual} "
                        f"-- likely extraction bug, rows quarantined"
                    ),
                }
            )
        else:
            check.second_opinion = INCONCLUSIVE

    return audit_items
