import json
from pathlib import Path

from senate_parser.assemble import CSV_COLUMNS, block_rows
from senate_parser.extract import Word
from senate_parser.reconcile import reconcile_block
from senate_parser.records import parse_block
from senate_parser.rows import cluster_rows
from senate_parser.segment import Block, BlockHeader

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> list[Word]:
    data = json.loads((FIXTURES / f"vol1_page_{name}.json").read_text())
    return [Word(**d) for d in data]


def make_block(page_nums, office="SENATOR TOM COTTON", funding_year=2024):
    rows_by_page = {p: cluster_rows(load(p)) for p in page_nums}
    header = BlockHeader(office=office, funding_year=funding_year, account="TEST ACCOUNT", start_page=page_nums[0])
    return Block(header=header, pages=list(page_nums), rows_by_page=rows_by_page)


def test_schema_has_validation_columns_after_legacy_17():
    assert CSV_COLUMNS[:17][-1] == "payee"
    assert CSV_COLUMNS[17:] == ["validation_status", "category"]


def test_rows_carry_validation_status_and_category():
    """Rows assembled after reconciliation must expose the per-row
    validation outcome -- the difference between 'we parsed it' and 'it
    reconciles against the report's own printed totals'."""
    block = make_block([1000, 1001])
    result = parse_block(block)
    reconcile_block(result)
    rows = block_rows(block, result, source_doc="118sdoc13")

    salary_rows = [r for r in rows if r["salary_flag"] == 1]
    assert salary_rows
    assert all(r["validation_status"] == "ok" for r in salary_rows)
    assert all(r["category"] == "PERSONNEL COMP. FULL-TIME PERMANENT" for r in salary_rows)

    # The 2-page fixture ends mid-block: its trailing expense rows have no
    # covering subtotal and must be honestly labeled, not silently 'ok'.
    expense_rows = [r for r in rows if r["salary_flag"] == 0]
    assert expense_rows
    assert all(r["validation_status"] == "unchecked" for r in expense_rows)


def test_rows_without_reconciliation_default_to_unchecked():
    block = make_block([1000])
    result = parse_block(block)
    rows = block_rows(block, result, source_doc="118sdoc13")
    assert all(r["validation_status"] == "unchecked" for r in rows)


class _FakeMatcher:
    def __init__(self, mapping=None, raise_error=False):
        self.mapping = mapping or {}
        self.raise_error = raise_error

    def get_bioguide_id(self, name, year):
        if self.raise_error:
            raise RuntimeError("cache corrupted")
        return self.mapping.get(name, "")


def test_match_senator_distinguishes_outcomes():
    """Matcher exceptions were previously swallowed into the same "" as a
    plain no-match; a matcher regression could zero out every bioguide ID
    unnoticed. Each failure mode must be a distinct, countable outcome."""
    from senate_parser.assemble import match_senator

    ok = _FakeMatcher({"TOM COTTON": "C001095"})
    assert match_senator("SENATOR TOM COTTON", 2024, ok) == (True, "TOM COTTON", "C001095", "matched")
    assert match_senator("SENATOR JANE NOBODY", 2024, ok) == (True, "JANE NOBODY", "", "unmatched")
    assert match_senator("SENATOR TOM COTTON", 2024, _FakeMatcher(raise_error=True))[3] == "error"
    assert match_senator("SENATOR TOM COTTON", "", ok)[3] == "no_year"
    assert match_senator("SENATOR TOM COTTON", 2024, None)[3] == "no_matcher"
    assert match_senator("SERGEANT AT ARMS", 2024, ok)[3] == "not_senator"
