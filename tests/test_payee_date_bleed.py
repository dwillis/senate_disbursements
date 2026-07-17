"""Tests for the payee/date column bleed fix.

Old-era (112th-114th) committee-layout pages shift the START column header
~8pt right of where the start-date data actually prints. The original
`start_x0 - 5` boundary was too tight by ~0.2pt, so start dates (e.g.
09/25/2014) fell in the payee column and appended themselves to the payee
text (e.g. 'CORDONE,JONATHAN J 09/25/2014'). 1,660-2,082 rows/doc affected.

Fix: layout-conditional start_date boundary in _calibrate_from_anchors
(-8 on committee pages, -5 on regular). Belt-and-braces: parse_block
strips a trailing \\d{2}/\\d{2}/\\d{4} from payee into start_date so any
residual bleed (e.g. 119sdoc6's 53 modern-era rows) is caught too.
"""

import json
import re
from pathlib import Path

from senate_parser.extract import Word
from senate_parser.records import calibrate_columns, parse_block
from senate_parser.rows import cluster_rows
from senate_parser.segment import Block, BlockHeader, parse_banner

FIXTURES = Path(__file__).parent / "fixtures"
DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")


def load(name: str) -> list:
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    return [Word(**d) for d in data]


def rows_of(name: str) -> list:
    return cluster_rows(load(name))


def test_committee_layout_start_date_column_contains_dates():
    """On 114sdoc4 p1887 (committee layout), the start-date data at
    x0≈330.8 must fall inside the start_date column, not the payee column."""
    rows = rows_of("114sdoc4_page_1887")
    cols = calibrate_columns(rows, template="anchor")
    assert cols is not None
    # The start date '09/25/2014' prints at x0≈330.8.
    start_date_words = [w for r in rows for w in r.words
                        if DATE_RE.fullmatch(w.text) and 325 < w.x0 < 335]
    assert start_date_words, "expected start-date words near x0=330.8 on this page"
    for w in start_date_words:
        assert cols.start_date[0] <= w.x0 < cols.start_date[1], (
            f"start date {w.text!r} at x0={w.x0} not in start_date column "
            f"{cols.start_date} (would land in payee {cols.payee})")


def test_committee_layout_payee_excludes_dates():
    """The payee column must not contain any date-formatted words on
    committee-layout pages."""
    rows = rows_of("114sdoc4_page_1887")
    cols = calibrate_columns(rows, template="anchor")
    assert cols is not None
    date_in_payee = [w for r in rows for w in r.words
                     if DATE_RE.fullmatch(w.text) and cols.payee[0] <= w.x0 < cols.payee[1]]
    assert date_in_payee == [], (
        f"dates found in payee column: {[(w.text, w.x0) for w in date_in_payee]}")


def test_regular_layout_start_date_boundary_unchanged():
    """Regular-layout pages (desc-doc < 385) keep the original -5 boundary
    — the fix only widens it on committee pages. 114sdoc4 p17 is a regular
    office-expenses page (START header at ~296, desc-doc=208)."""
    rows = rows_of("114sdoc4_page_17")
    cols = calibrate_columns(rows, template="anchor")
    assert cols is not None
    # Regular layout: START header at ~296, boundary at start_x0 - 5.
    # Committee layout would be start_x0 - 8. Verify we got the regular one
    # by checking the boundary is ~5 left of the START anchor.
    start_words = [w for r in rows for w in r.words if w.text == "START"]
    if start_words:
        start_x0 = start_words[0].x0
        assert abs(cols.start_date[0] - (start_x0 - 5.0)) < 0.1, (
            f"regular layout should use start_x0 - 5, got {cols.start_date[0]} vs {start_x0 - 5.0}")


def test_parse_block_1887_no_dates_in_payee():
    """Full parse of 114sdoc4 p1887: no record's payee contains a date,
    and CORDONE's start_date is 09/25/2014."""
    rows = rows_of("114sdoc4_page_1887")
    header = parse_banner(rows, 1887)
    block = Block(header=header, pages=[1887], rows_by_page={1887: rows})
    result = parse_block(block, template="anchor")
    for rec in result.records:
        if rec.payee:
            assert not DATE_RE.search(rec.payee), (
                f"payee {rec.payee!r} contains a date on page 1887")
    # CORDONE record should have start_date set.
    cordone = next((r for r in result.records if "CORDONE" in (r.payee or "")), None)
    if cordone:
        assert cordone.start_date == "09/25/2014", (
            f"CORDONE start_date={cordone.start_date!r}, expected 09/25/2014")
        assert cordone.payee == "CORDONE,JONATHAN J", (
            f"CORDONE payee={cordone.payee!r}, expected CORDONE,JONATHAN J")


def test_belt_and_braces_strips_trailing_date_from_payee():
    """A payee with a trailing date token gets the date moved to start_date.
    Catches any residual bleed the column fix misses (e.g. 119sdoc6 modern)."""
    # Construct a minimal block with one salary record whose payee has a
    # trailing date. parse_block should strip it.
    from senate_parser.records import Record, BlockParseResult
    # Test the strip helper directly — it's the belt-and-braces path.
    from senate_parser.records import _strip_trailing_date_from_payee

    payee, start_date = _strip_trailing_date_from_payee("CORDONE,JONATHAN J 09/25/2014", "")
    assert payee == "CORDONE,JONATHAN J"
    assert start_date == "09/25/2014"

    # No date → unchanged.
    payee, start_date = _strip_trailing_date_from_payee("JP MORGAN CHASE BANK NA", "")
    assert payee == "JP MORGAN CHASE BANK NA"
    assert start_date == ""

    # Already has start_date → still strip from payee (payee should never
    # contain a date), but don't overwrite a non-empty start_date.
    payee, start_date = _strip_trailing_date_from_payee("SMITH,JANE 01/15/2015", "02/01/2015")
    assert payee == "SMITH,JANE"
    assert start_date == "02/01/2015"