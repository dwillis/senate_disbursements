"""Tests for the second-opinion verifier, built by mutating the real
Cotton block fixture (pages 1000-1001, reconciles ok as-committed):

- tamper only the printed subtotal -> parser and independent re-sum agree
  against it -> 'source_mismatch' (the Blackburn-intern class of source
  error), rows retagged to publish.
- glue a salary amount into the description column (parser recovers it
  via the trailing-amount split; the naive re-sum can't see it) and set
  the printed subtotal to the naive sum -> independent sides with the
  printed figure -> 'parser_suspect', rows stay quarantined.
- same glue but a printed figure matching neither -> 'inconclusive'.
"""

import dataclasses
import json
from pathlib import Path

from senate_parser.extract import Word
from senate_parser.records import calibrate_columns, parse_block
from senate_parser.reconcile import parse_amount, reconcile_block
from senate_parser.rows import cluster_rows
from senate_parser.second_opinion import apply_second_opinion
from senate_parser.segment import Block, BlockHeader

FIXTURES = Path(__file__).parent / "fixtures"
COMP_LABEL = "PERSONNEL COMP. FULL-TIME PERMANENT"


def load_words(page: int) -> list:
    data = json.loads((FIXTURES / f"vol1_page_{page}.json").read_text())
    return [Word(**d) for d in data]


def make_block(words_by_page: dict) -> Block:
    rows_by_page = {p: cluster_rows(w) for p, w in words_by_page.items()}
    pages = sorted(words_by_page)
    header = BlockHeader(office="TEST", funding_year=2024, account="TEST", start_page=pages[0])
    return Block(header=header, pages=pages, rows_by_page=rows_by_page)


def find_comp_subtotal(words_by_page: dict):
    """Locate the printed PERSONNEL COMP subtotal's amount word."""
    for page, words in words_by_page.items():
        for row in cluster_rows(words):
            text = " ".join(w.text for w in row.words)
            if COMP_LABEL.split(".")[0] in text and "FULL-TIME" in text:
                amount_words = [w for w in row.words if w.text.startswith("$")]
                if amount_words:
                    return page, amount_words[0]
    raise AssertionError("fixture lost its PERSONNEL COMP subtotal")


def replace_word(words_by_page: dict, page: int, old: Word, new: Word) -> dict:
    out = dict(words_by_page)
    out[page] = [new if w is old else w for w in words_by_page[page]]
    return out


def set_printed_subtotal(words_by_page: dict, value: float) -> dict:
    page, w = find_comp_subtotal(words_by_page)
    return replace_word(words_by_page, page, w, dataclasses.replace(w, text=f"${value:,.2f}"))


def find_salary_amount_word(words_by_page: dict):
    """An amount-column word on an ordinary salary row (not a subtotal)."""
    for page, words in words_by_page.items():
        rows = cluster_rows(words)
        cols = calibrate_columns(rows)
        if cols is None:
            continue
        for row in rows:
            text = " ".join(w.text for w in row.words)
            if "FULL-TIME" in text or "Net Payroll" in text:
                continue
            in_amount = [w for w in row.words if w.x0 >= cols.amount[0] and w.text.startswith("$")]
            in_payee = row.text_in(*cols.payee)
            if in_amount and in_payee:
                return page, in_amount[0], cols
    raise AssertionError("no salary amount word found")


def glue_amount_into_description(words_by_page: dict):
    """Move one salary amount into the description column, glued to a
    letter (the parser's trailing-amount split recovers it; the naive
    re-sum sees an empty amount column). Returns (mutated, amount)."""
    page, w, cols = find_salary_amount_word(words_by_page)
    glued = dataclasses.replace(
        w, text=f"X{w.text}", x0=cols.amount[0] - 80.0, x1=cols.amount[0] - 5.0
    )
    return replace_word(words_by_page, page, w, glued), parse_amount(w.text)


def run_block(words_by_page: dict):
    block = make_block(words_by_page)
    result = parse_block(block)
    reconciled = reconcile_block(result)
    audit = apply_second_opinion(block, result, reconciled)
    comp_check = next(c for c in reconciled.checks if c.label == COMP_LABEL)
    return result, comp_check, audit


def original_comp_sum() -> float:
    words = {p: load_words(p) for p in (1000, 1001)}
    _, w = find_comp_subtotal(words)
    return parse_amount(w.text)


def test_source_mismatch_when_only_printed_subtotal_is_off():
    words = {p: load_words(p) for p in (1000, 1001)}
    result, check, audit = run_block(set_printed_subtotal(words, original_comp_sum() + 100.0))

    assert check.status == "fail"
    assert check.second_opinion == "source_mismatch"
    assert abs(check.independent_sum - check.actual) <= 0.01
    assert audit == []
    salary = [r for r in result.records if r.record_type == "salary"]
    assert salary and all(r.validation_status == "source_mismatch" for r in salary)


def test_parser_suspect_when_independent_sum_sides_with_printed():
    words = {p: load_words(p) for p in (1000, 1001)}
    words, glued_amount = glue_amount_into_description(words)
    result, check, audit = run_block(set_printed_subtotal(words, original_comp_sum() - glued_amount))

    assert check.status == "fail"
    assert check.second_opinion == "parser_suspect"
    assert len(audit) == 1 and audit[0]["reason"] == "second_opinion_disagrees"
    salary = [r for r in result.records if r.record_type == "salary"]
    assert all(r.validation_status == "fail" for r in salary)


def test_inconclusive_when_all_three_sums_differ():
    words = {p: load_words(p) for p in (1000, 1001)}
    words, _ = glue_amount_into_description(words)
    result, check, audit = run_block(set_printed_subtotal(words, original_comp_sum() + 77.77))

    assert check.status == "fail"
    assert check.second_opinion == "inconclusive"
    assert audit == []
    salary = [r for r in result.records if r.record_type == "salary"]
    assert all(r.validation_status == "fail" for r in salary)


def test_passing_segments_are_left_alone():
    words = {p: load_words(p) for p in (1000, 1001)}
    result, check, audit = run_block(words)
    assert check.status == "ok"
    assert check.second_opinion == ""
    assert check.independent_sum is None
    assert audit == []


# ---------------------------------------------------------------------------
# Anchor-template (112th-114th) NET PAYROLL EXPENSES + lump_at_net.
#
# The parser (reconcile._reconcile_block_typed) adds `lump_sum_total` -- the
# accumulated `lump_at_net` subtotals (PERSONNEL BENEFITS, RE-EMPLOYED
# ANNUITANTS, BENEFITS FOR NON SENATE/FORMER PERSONNEL) -- to the NET PAYROLL
# EXPENSES actual. second_opinion's naive re-sum skips every subtotal row
# (including those lump labels), so it disagrees with the parser whenever
# lump_sum_total > 0 -- degrading 'source_mismatch' (publish) verdicts to
# 'inconclusive' (quarantine). The fix adds the same lump_at_net subtotals
# inside the segment window to the independent sum, making the comparison
# apples-to-apples. See INTELLIGENCE 4.8x (114sdoc4 p2019): 3 salary rows
# sum to 221,499.85, PERSONNEL BENEFITS 11,747.93 feeds NET PAYROLL, parser
# 233,247.78, printed 48,664.57.


def _fake_result_with_subtotals(subtotals):
    class R:
        pass

    r = R()
    r.subtotals = subtotals
    return r


def test_lump_at_net_adjustment_sums_lump_labels_inside_segment_window():
    """_lump_at_net_adjustment sums the amounts of lump_at_net subtotals
    (PERSONNEL BENEFITS, RE-EMPLOYED ANNUITANTS, BENEFITS FOR NON
    SENATE/FORMER PERSONNEL) whose (page, top) falls strictly between
    start_pos and end_pos. OTHER PERSONNEL COMPENSATION is a LUMP_SUM_LABEL
    but itemizes rows inside the roster, so it is excluded. PERSONNEL COMP.
    FULL-TIME PERMANENT is a payroll category, not a lump, so excluded."""
    from senate_parser.records import Subtotal
    from senate_parser.second_opinion import _lump_at_net_adjustment

    subtotals = [
        Subtotal(label="PERSONNEL COMP. FULL-TIME PERMANENT", amount="221,499.85", page=1, top=40.0),
        Subtotal(label="OTHER PERSONNEL COMPENSATION", amount="999.99", page=1, top=45.0),
        Subtotal(label="PERSONNEL BENEFITS", amount="11,747.93", page=1, top=50.0),
        Subtotal(label="RE-EMPLOYED ANNUITANTS", amount="0.00", page=1, top=55.0),
        Subtotal(label="NET PAYROLL EXPENSES", amount="48,664.57", page=1, top=100.0),
    ]
    result = _fake_result_with_subtotals(subtotals)

    # Segment window: from before everything up to (but excluding) the
    # NET PAYROLL EXPENSES subtotal row.
    adjustment = _lump_at_net_adjustment(result, (1, -1.0), (1, 100.0))
    # PERSONNEL BENEFITS 11,747.93 + RE-EMPLOYED ANNUITANTS 0.00.
    assert adjustment == 11747.93


def test_lump_at_net_adjustment_excludes_lumps_outside_segment_window():
    """A PERSONNEL BENEFITS subtotal on a later page (outside this segment's
    window) must not be added -- it belongs to a different NET PAYROLL
    segment."""
    from senate_parser.records import Subtotal
    from senate_parser.second_opinion import _lump_at_net_adjustment

    subtotals = [
        Subtotal(label="PERSONNEL BENEFITS", amount="11,747.93", page=1, top=50.0),
        Subtotal(label="NET PAYROLL EXPENSES", amount="100.00", page=1, top=100.0),
        Subtotal(label="PERSONNEL BENEFITS", amount="99,999.00", page=2, top=50.0),
    ]
    result = _fake_result_with_subtotals(subtotals)

    # Window ends at page 1 top 100 -- the page-2 lump is outside.
    adjustment = _lump_at_net_adjustment(result, (1, -1.0), (1, 100.0))
    assert adjustment == 11747.93


def test_apply_second_opinion_publishes_net_payroll_when_lump_at_net_explains_gap(monkeypatch):
    """End-to-end: with the fix, the INTELLIGENCE 4.8x pattern (3 salary
    rows + a PERSONNEL BENEFITS lump, parser 233,247.78, printed 48,664.57)
    must flip from INCONCLUSIVE to SOURCE_MISMATCH. The geometric re-sum
    is stubbed (it would need real PDF rows); the test exercises only the
    adjustment logic and the three-way comparison."""
    from senate_parser.records import Record, Subtotal
    from senate_parser.second_opinion import apply_second_opinion

    salary_rows = [
        Record(record_type="salary", amount="78,999.96", page=1),
        Record(record_type="salary", amount="69,999.96", page=1),
        Record(record_type="salary", amount="72,499.93", page=1),
    ]
    subtotals = [
        Subtotal(label="PERSONNEL BENEFITS", amount="11,747.93", page=1, top=50.0),
        Subtotal(label="NET PAYROLL EXPENSES", amount="48,664.57", page=1, top=100.0),
    ]

    class FakeResult:
        pass
    result = FakeResult()
    result.records = salary_rows
    result.subtotals = subtotals
    result.events = [("record", r) for r in salary_rows] + [("subtotal", s) for s in subtotals]

    # Reconcile first so the check has actual/expected populated.
    from senate_parser.reconcile import reconcile_block
    reconciled = reconcile_block(result, template="anchor")
    net_check = next(c for c in reconciled.checks if c.label == "NET PAYROLL EXPENSES")
    assert net_check.status == "fail", f"expected fail, got {net_check.status}"

    # Stub the geometric re-sum: 221,499.85 is the 3 salary rows' sum.
    from senate_parser import second_opinion as so
    monkeypatch.setattr(
        so,
        "_independent_segment_sum",
        lambda block, start, end, template="modern": (221499.85, True),
    )

    class FakeBlock:
        pages = [1]
    audit = apply_second_opinion(FakeBlock(), result, reconciled, template="anchor")
    net_check = next(c for c in reconciled.checks if c.label == "NET PAYROLL EXPENSES")
    assert net_check.second_opinion == "source_mismatch", (
        f"expected source_mismatch, got {net_check.second_opinion} "
        f"(independent={net_check.independent_sum} actual={net_check.actual} "
        f"expected={net_check.expected})"
    )
    assert audit == []
    assert all(r.validation_status == "source_mismatch" for r in salary_rows)
