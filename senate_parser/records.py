"""Classify and merge rows within a block into itemized records.

Two continuation patterns exist in the source, and they behave
differently:

1. **Expense line items.** One document-number row can be followed by
   further (description, amount) pairs at *normal* line spacing (no
   repeated doc number/payee/dates) -- these are separate line items
   under the same document. A (description-only, no amount) row after
   that is a text wrap of the *previous* line item's description.

2. **Salary title wraps.** When a job title needs two lines, the PDF
   vertically centers the two title lines around the single name+amount
   line, so the continuation can appear *above* the name row, not just
   below it (verified: page 130, "SHAW, TARA L" -- title line 1 sits at
   top=358.63, the name+amount row at top=361.28, title line 2 at
   top=363.93). These three rows are ~2.65pt apart, well under the
   ~6.2pt spacing between distinct records, so a row-gap threshold
   reliably groups them without merging separate people.

`_group_rows` handles tightly wrapped case 2 by clustering on gap;
`parse_block` additionally assigns more widely spaced description-only
rows to the nearest salary anchor within the same uninterrupted salary
run. Lines above that anchor are prepended and lines below it appended.
This covers long 5+ line "When Actually Employed" schedules (verified:
page 342, "DWYER, SHEILA M") without stealing trailing title text from
the preceding employee.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from .rows import Row
from .segment import Block, header_row_top

DOC_NUMBER_RE = re.compile(r"^[A-Z0-9]{6,14}$")
DATE_VALUE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

# A sufficiently long title/description can run right up against its
# printed amount with no separating whitespace in the PDF's content
# stream; natural-pdf tokenizes on whitespace gaps, so the two become one
# word (verified: page 1534 of 119sdoc5, Patty Murray's title
# "PRESIDENT PRO TEMPORE EMERITUS" -- long enough to push the amount out
# of its normal right-aligned position -- glued to "$87,000.00" with the
# combined word's x-position landing entirely inside the description
# column, not the amount column). Splitting the word's bounding box
# wouldn't help since the amount isn't where the amount column expects it
# either, so this is recovered as a text-level fallback: strip a trailing
# dollar amount off the description/wide text when the normal
# column-based amount search comes up empty.
# The digits-before-decimal group is optional so sub-dollar amounts
# ("$.80") are matched too, mirroring reconcile.AMOUNT_RE.
TRAILING_AMOUNT_RE = re.compile(r"^(.*\D)(-?\$(?:[\d,]+)?\.\d{2})$")


def _split_trailing_amount(text: str) -> tuple:
    m = TRAILING_AMOUNT_RE.match(text)
    if m:
        return m.group(1).strip(), m.group(2)
    return text, ""
TIGHT_GROUP_GAP = 4.0

# Reused from process_senate_disbursements.SUBTOTAL_PATTERNS (same category
# names as the legacy parser), plus RENT/PRINTING/SUPPLIES/ORGANIZATION
# TOTALS, which that list omitted but which appear inline in this format.
SUBTOTAL_LABELS = {
    "TRAVEL AND TRANSPORTATION OF PERSONS",
    "INTERDEPARTMENTAL TRANSPORTATION",
    "OTHER CONTRACTUAL SERVICES",
    "ACQUISITION OF ASSETS",
    "PERSONNEL BENEFITS",
    "NET PAYROLL EXPENSES",
    "PERSONNEL COMP. FULL-TIME PERMANENT",
    "OTHER PERSONNEL COMPENSATION",
    "RE-EMPLOYED ANNUITANTS",
    "BENEFITS FOR NON SENATE/FORMER PERSONNEL",
    "RENT, COMMUNICATIONS AND UTILITIES",
    "PRINTING AND REPRODUCTION",
    "SUPPLIES AND MATERIALS",
    "ORGANIZATION TOTALS",
    "WHEN ACTUALLY EMPLOYED (WAE)",
}

# NET PAYROLL EXPENSES is a rollup of the other personnel subtotals
# (PERSONNEL COMP. FULL-TIME PERMANENT + PERSONNEL BENEFITS + ...), not a
# total of itemized rows directly above it -- some of those inputs
# (PERSONNEL BENEFITS, OTHER PERSONNEL COMPENSATION, etc.) are lump-sum
# budget figures with zero itemized rows of their own (verified: PERSONNEL
# BENEFITS has no preceding records on page 1001). Segment-sum
# reconciliation would show those as a false-positive mismatch instead of
# the true "nothing to itemize here" (see reconcile.py, which checks the
# labels below against the block-wide running total instead of the segment,
# and treats plain lump-sum labels' expected zero-record segments as
# `no_records` via the normal path).
PERSONNEL_ROLLUP_LABELS = {"NET PAYROLL EXPENSES"}

# Labels that are ALWAYS lump-sum budget figures with zero itemized rows.
# Derived empirically from all 7 processed reports' reconciliation
# reports: PERSONNEL BENEFITS appeared 1,387/1,387 times with no records,
# RE-EMPLOYED ANNUITANTS 190/190, OTHER PERSONNEL COMPENSATION 11/11.
# (BENEFITS FOR NON SENATE/FORMER PERSONNEL never occurred in these 7 but
# belongs to the same family per the legacy parser's SUBTOTAL_PATTERNS.)
# A zero-record segment under one of these is expected (`no_records`); a
# zero-record segment under any OTHER label gets the distinct advisory
# status `zero_records` -- legitimate lump-summed adjustments exist even
# for normally-itemized labels (all 5 historical cases verified against
# the PDFs: e.g. Feinstein's post-death -$19,384.77 PERSONNEL COMP
# adjustment prints with no rows), so it doesn't gate, but it's kept
# distinct so a row-loss regression shows up as a countable status change
# instead of hiding among 1,400 routine no_records checks.
LUMP_SUM_LABELS = {
    "PERSONNEL BENEFITS",
    "RE-EMPLOYED ANNUITANTS",
    "OTHER PERSONNEL COMPENSATION",
    "BENEFITS FOR NON SENATE/FORMER PERSONNEL",
}

# Subtotal labels that cover itemized *salary* records. Used by the old
# (112th-114th) template's type-aware reconciliation: that era prints all
# of a block's subtotals at the END of the listing (verified: JUDICIARY,
# 114sdoc13 p2221-2226 -- the TRAVEL subtotal prints before the payroll
# ones), and prints lump-sum lines BEFORE the roster's covering subtotal
# (verified: APPROPRIATIONS p57-59, where roster 6,697,710.29 + OTHER
# PERSONNEL COMPENSATION 2,353.84 equals NET PAYROLL 6,700,064.13 to the
# penny). Position-based "records since the last subtotal" misattributes
# whole rosters there; record type + label class is the reliable signal.
PAYROLL_ITEMIZED_LABELS = {
    "PERSONNEL COMP. FULL-TIME PERMANENT",
    "WHEN ACTUALLY EMPLOYED (WAE)",
}

# Column boundaries are computed per-page, anchored on that page's own
# DESCRIPTION header position, rather than fixed absolute x-coordinates.
# Header labels are wide/centered headings that do NOT line up with their
# column's left-justified data (e.g. "PAYEE NAME" sits ~34pt right of
# where payee text actually starts) -- but critically, the header-to-data
# offset for a given column is consistent *within a document* and, for
# DESCRIPTION specifically, consistent *across every report template
# checked so far*: header_x0 - 71.0 predicts the actual data x0 to
# within ~1pt on 118sdoc13 (355.53 est vs 355.1 actual), 118sdoc2 (353.6
# vs 353.4), and 117sdoc8 (417.9 vs 417.2), even though those reports'
# absolute table positions differ by up to 56pt (117sdoc8 uses a wider
# 612x792 page vs. the other two's 423x657). All other boundaries are
# expressed as fixed deltas from that one anchor point, so a whole-table
# translation (a new report on a differently-sized page) shifts every
# boundary together instead of breaking whichever one happened to have
# the tightest margin. Deltas were derived from the column extents
# originally measured directly on 118sdoc13/118sdoc2 (see git history for
# that derivation); reconciliation (reconcile.py) is the backstop if a
# future report's *relative* column geometry, not just its position,
# turns out to differ.
DESCRIPTION_HEADER_TO_DATA_OFFSET = -71.0
COLUMN_DELTAS_FROM_DESCRIPTION_DATA = {
    "document_right": -234.5,
    "date_posted_right": -189.5,
    "payee_right": -79.5,
    "start_date_right": -37.5,
    "end_date_right": -2.5,  # == description left edge
    "amount_left": 180.5,
    "amount_right": 250.5,  # excludes the rotated "B-###" page-label column
}

# 112th-114th Congress reports use an older table generator whose
# *relative* column geometry differs from the modern one (e.g. AMOUNT sits
# 133pt right of DESCRIPTION vs 121-124 on modern reports), and a single
# document mixes two header layouts (committee pages shift PAYEE/
# DESCRIPTION further right). Fixed deltas from one anchor can't fit
# both, so this era derives every boundary from that page's own seven
# header words instead. The header-to-data offsets below are stable
# across all three 114th reports (measured on 100+ sampled pages each:
# payee data starts 34.5-34.8pt left of "PAYEE NAME" on every one), and
# each boundary is that offset plus a small margin. Reconciliation
# remains the backstop.
ANCHOR_HEADER_WORDS = ("DOCUMENT NO.", "DATE", "PAYEE NAME", "START", "END", "DESCRIPTION", "AMOUNT ($)")
# Text variants of the anchor header words seen on 114-era
# COMPENSATION OF MEMBERS pages (3 per 114-era doc, 9 total): the header
# prints 'DOCUMENT NO' (no period) and 'AMOUNT' + '($)' as separate
# words, vs the regular anchor header's 'DOCUMENT NO.' and 'AMOUNT ($)'
# as one word. Map the variants to their canonical anchor names so
# _calibrate_from_anchors recognizes both layouts; the column boundaries
# are derived from each anchor's own x0, so the shifted positions on
# COMPENSATION OF MEMBERS pages (DOCUMENT NO at x0=47 vs 65; AMOUNT at
# x0=543 vs 563) are absorbed automatically.
ANCHOR_HEADER_ALIASES = {
    "DOCUMENT NO": "DOCUMENT NO.",
    "AMOUNT": "AMOUNT ($)",
}
# Vertical window around the DOCUMENT NO. row that contains the full
# header: DESCRIPTION/OBLIGATION sit ~1.4pt above it on most old pages,
# START/END ~15pt below.
ANCHOR_HEADER_WINDOW = 16.0


@dataclass
class ColumnMap:
    document: tuple
    date_posted: tuple
    payee: tuple
    start_date: tuple
    end_date: tuple
    description: tuple
    amount: tuple


@dataclass
class Record:
    record_type: str  # 'salary' or 'expense'
    document_number: str = ""
    date_posted: str = ""
    payee: str = ""
    start_date: str = ""
    end_date: str = ""
    description: str = ""
    amount: str = ""
    page: int = 0
    # Back-filled by reconcile.reconcile_block: the outcome of the
    # subtotal check covering this record's segment ('ok'/'warn'/'fail'/
    # 'unchecked') and that subtotal's label (e.g. "TRAVEL AND
    # TRANSPORTATION OF PERSONS"). 'unchecked' is the honest default:
    # a record after its block's final subtotal is covered by no check.
    validation_status: str = "unchecked"
    category: str = ""


@dataclass
class Subtotal:
    label: str
    amount: str
    page: int
    # Vertical position of the subtotal's printed row, so the
    # second-opinion verifier can bound the segment geometrically.
    top: float = 0.0


@dataclass
class BlockParseResult:
    records: list = field(default_factory=list)
    subtotals: list = field(default_factory=list)
    unparsed: list = field(default_factory=list)
    # ('record', Record) / ('subtotal', Subtotal) in original document order,
    # for reconcile.py to walk sequentially -- records.append(...) order
    # alone doesn't preserve interleaving with subtotals.
    events: list = field(default_factory=list)


def calibrate_columns(rows: list, template: str = "modern") -> Optional[ColumnMap]:
    """Confirm this page has the expected header block and derive the
    column layout from this page's own header row. Returns None if the
    page doesn't look like a normal data page (used to flag anomalies
    rather than guess)."""
    if template == "anchor":
        return _calibrate_from_anchors(rows)
    header = next((r for r in rows if any("DOCUMENT NO." in w.text for w in r.words)), None)
    if header is None:
        return None
    by_x0 = {w.text: w.x0 for w in header.words}
    if "DOCUMENT NO." not in by_x0 or "PAYEE NAME" not in by_x0 or "DESCRIPTION" not in by_x0:
        return None

    has_start_end = any(
        header.top < r.top <= header.top + 20 and any(w.text in ("START", "END") for w in r.words)
        for r in rows
    )
    if not has_start_end:
        return None

    desc_data_x0 = by_x0["DESCRIPTION"] + DESCRIPTION_HEADER_TO_DATA_OFFSET
    d = COLUMN_DELTAS_FROM_DESCRIPTION_DATA

    return ColumnMap(
        document=(0.0, desc_data_x0 + d["document_right"]),
        date_posted=(desc_data_x0 + d["document_right"], desc_data_x0 + d["date_posted_right"]),
        payee=(desc_data_x0 + d["date_posted_right"], desc_data_x0 + d["payee_right"]),
        start_date=(desc_data_x0 + d["payee_right"], desc_data_x0 + d["start_date_right"]),
        end_date=(desc_data_x0 + d["start_date_right"], desc_data_x0 + d["end_date_right"]),
        description=(desc_data_x0 + d["end_date_right"], desc_data_x0 + d["amount_left"]),
        amount=(desc_data_x0 + d["amount_left"], desc_data_x0 + d["amount_right"]),
    )


def _calibrate_from_anchors(rows: list) -> Optional[ColumnMap]:
    """Old-template (112th-114th) calibration: build every column
    boundary from this page's own header anchors plus the measured
    header-to-data offsets (see ANCHOR_HEADER_WORDS comment)."""
    # Match "DOCUMENT NO" (no period) as a substring so the COMPENSATION
    # OF MEMBERS header variant (see ANCHOR_HEADER_ALIASES) is detected
    # too; "DOCUMENT NO" is a substring of both "DOCUMENT NO" and
    # "DOCUMENT NO." so the regular anchor header still matches.
    doc_row = next((r for r in rows if any("DOCUMENT NO" in w.text for w in r.words)), None)
    if doc_row is None:
        return None
    anchors = {}
    for r in rows:
        if abs(r.top - doc_row.top) <= ANCHOR_HEADER_WINDOW:
            for w in r.words:
                if w.text in ANCHOR_HEADER_WORDS and w.text not in anchors:
                    anchors[w.text] = w.x0
                elif w.text in ANCHOR_HEADER_ALIASES:
                    canonical = ANCHOR_HEADER_ALIASES[w.text]
                    if canonical not in anchors:
                        anchors[canonical] = w.x0
    if len(anchors) != len(ANCHOR_HEADER_WORDS):
        return None

    date_x0 = anchors["DATE"]
    payee_x0 = anchors["PAYEE NAME"]
    start_x0 = anchors["START"]
    end_x0 = anchors["END"]
    amount_x0 = anchors["AMOUNT ($)"]
    # Committee pages use a second, wider header layout (DESCRIPTION sits
    # ~417pt right of DOCUMENT NO. vs ~365 on regular pages) whose payee
    # data starts 61pt left of its header (vs 34.5) -- without this, the
    # roster's payees fall in the date column, the rows classify as
    # expense sublines, and whole committee rosters land in the TRAVEL
    # segment (verified: JUDICIARY, 114sdoc13 p2221-2226).
    is_committee = (anchors["DESCRIPTION"] - anchors["DOCUMENT NO."]) > 385
    payee_offset = -63.0 if is_committee else -36.0
    # Committee pages also shift the start-date data ~5.2pt left of the
    # START header (vs ~2.5 on regular pages); start_x0 - 5 was too tight
    # by 0.2pt, so start dates fell in the payee column and appended to
    # the payee text (verified: 114sdoc4 p1887, 'CORDONE,JONATHAN J
    # 09/25/2014' on 1,660-2,082 rows/doc across the 114-era).
    start_offset = -8.0 if is_committee else -5.0
    return ColumnMap(
        document=(0.0, date_x0 - 6.0),  # date data starts at -4.8
        date_posted=(date_x0 - 6.0, payee_x0 + payee_offset),  # payee data at -34.5 / -61
        payee=(payee_x0 + payee_offset, start_x0 + start_offset),
        start_date=(start_x0 + start_offset, end_x0 - 8.0),  # end data at -5.6
        end_date=(end_x0 - 8.0, end_x0 + 22.0),  # desc data at ~+25
        description=(end_x0 + 22.0, amount_x0 - 24.0),  # widest amounts reach -20
        # Right-aligned amounts end by +33 on every measured page; the
        # rotated "B-###" page-label characters start at +50..+57 -- the
        # boundary must fall between or the label chars pollute the amount
        # column (1,320 orphan amounts on the first 114sdoc13 run).
        amount=(amount_x0 - 24.0, amount_x0 + 42.0),
    )


def _is_page_label_row(row: Row, amount_right: float) -> bool:
    """True for rows made up only of the rotated "B-###" marginal page
    label (individual characters, one per visual row, just right of the
    amount column -- verified at x0>=610 on 118sdoc13's page geometry,
    x0>=678 on 117sdoc8's wider one, in both cases just past that page's
    own AMOUNT column). Left in the row stream, these create spurious
    tight gaps between real salary rows and can merge two distinct
    employees into one record, silently dropping one's amount (verified:
    page 123, a lone '-' character between BROXMEYER's and HEMINGWAY's
    rows merged both into a single group and dropped HEMINGWAY's
    $110,949.96).

    114sdoc4 additionally prints the label a second time as one
    unrotated word ("B -1") sitting INSIDE the amount column -- caught by
    the text-shape test (2,050 orphan amounts on its first run)."""
    if not row.words:
        return False
    if all(w.x0 >= amount_right for w in row.words):
        return True
    joined = " ".join(w.text for w in row.words)
    # page numbers >= 1,000 print with a thousands comma ("B -1,000")
    return bool(re.fullmatch(r"[A-Z]\s?-\s?\d{1,3}(,\d{3})*", joined.strip()))


def _group_rows(data_rows: list, tight_gap: float = TIGHT_GROUP_GAP) -> list:
    groups: list = []
    prev_top = None
    for r in data_rows:
        if prev_top is not None and (r.top - prev_top) <= tight_gap:
            groups[-1].append(r)
        else:
            groups.append([r])
        prev_top = r.top
    return groups


def _split_groups_on_document_numbers(groups: list, cols: ColumnMap) -> list:
    """A record has at most ONE document number, so a group containing a
    second doc-numbered row is two records merged by tight vertical
    spacing (verified: 114sdoc4 p1915 prints consecutive $30.00 bank-fee
    rows only ~4.7pt apart, inside any workable tight-group gap --
    spacing alone merges them and silently drops the second amount).
    Split at the second doc number, not the first: wrapped-payee records
    legitimately print their doc number on the group's SECOND visual row
    (verified: DSEC23M50419 on 118sdoc13 p341), and those must stay
    whole."""
    out = []
    for group in groups:
        current = []
        current_has_doc = False
        for r in group:
            has_doc = any(DOC_NUMBER_RE.match(w.text) for w in r.words_in(*cols.document))
            if has_doc and current_has_doc:
                out.append(current)
                current = [r]
            else:
                current.append(r)
                current_has_doc = current_has_doc or has_doc
        out.append(current)
    return out


def _joined_text_in(group: list, x0: float, x1: float) -> str:
    return " ".join(t for t in (r.text_in(x0, x1) for r in group) if t).strip()


_TRAILING_DATE_RE = re.compile(r"\s*(\d{2}/\d{2}/\d{4})\s*$")


def _strip_trailing_date_from_payee(payee: str, start_date: str) -> tuple:
    """Belt-and-braces: if a date leaked into the payee text (column
    boundary too tight, or modern-era word-extraction glued payee to date),
    strip it and move it to start_date. Never overwrites a non-empty
    start_date (the column-based extraction is authoritative when present)."""
    if not payee:
        return payee, start_date
    m = _TRAILING_DATE_RE.search(payee)
    if not m:
        return payee, start_date
    stripped = payee[:m.start()].strip()
    if not start_date:
        start_date = m.group(1)
    return stripped, start_date


def _date_text_in(group: list, x0: float, x1: float) -> str:
    """Return a column value only when it is actually a printed date.

    Long description labels can begin a few points left of the calibrated
    description boundary (verified: BENEFITS FOR FORMER PERSONNEL on pages
    238 and 258 of 118sdoc13).  Such a word lands in both the wide
    description span and the nominal END column; retaining it as an end date
    corrupts an otherwise valid lump-sum record.
    """
    text = _joined_text_in(group, x0, x1)
    return text if DATE_VALUE_RE.fullmatch(text) else ""


# Word extraction occasionally splits a label mid-word at a column-ish
# position ("TRAVEL AND TRANSP" + "ORTATION OF PERSONS", 114sdoc13
# p1011) -- match space-squashed and recover the canonical spelling, or
# the subtotal line is misread as an expense record and its amount both
# double-counts and loses the segment boundary.
_SQUASHED_SUBTOTAL_LABELS = {lbl.replace(" ", ""): lbl for lbl in SUBTOTAL_LABELS}


def _subtotal_label_of(text: str):
    """The canonical SUBTOTAL_LABELS entry for this row text, or None."""
    normalized = re.sub(r"\s+", " ", text.strip().upper())
    if normalized in SUBTOTAL_LABELS:
        return normalized
    return _SQUASHED_SUBTOTAL_LABELS.get(normalized.replace(" ", ""))


def _is_subtotal_label(text: str) -> bool:
    return _subtotal_label_of(text) is not None


def classify_group(group: list, cols: ColumnMap) -> tuple:
    doc_words = [w for r in group for w in r.words_in(*cols.document)]
    payee_text = _joined_text_in(group, *cols.payee)
    amount_text = next((r.text_in(*cols.amount) for r in group if r.text_in(*cols.amount)), "")
    desc_text = _joined_text_in(group, *cols.description)
    wide_text = _joined_text_in(group, cols.start_date[0], cols.amount[0])

    has_doc = any(DOC_NUMBER_RE.match(w.text) for w in doc_words)
    has_payee = bool(payee_text)
    has_amount = bool(amount_text)

    if not has_amount:
        desc_text, amount_text = _split_trailing_amount(desc_text)
        has_amount = bool(amount_text)
    if not has_amount:
        wide_text, amount_text = _split_trailing_amount(wide_text)
        has_amount = bool(amount_text)

    start_date_text = _date_text_in(group, *cols.start_date)
    payee_text, start_date_text = _strip_trailing_date_from_payee(payee_text, start_date_text)

    fields = {
        "document_number": doc_words[0].text if doc_words else "",
        "date_posted": _date_text_in(group, *cols.date_posted),
        "payee": payee_text,
        "start_date": start_date_text,
        "end_date": _date_text_in(group, *cols.end_date),
        "description": desc_text if desc_text else wide_text,
        "amount": amount_text,
    }

    if has_doc:
        return "expense_header", fields
    if has_payee:
        return "salary", fields
    canonical_label = _subtotal_label_of(wide_text)
    if canonical_label and has_amount:
        fields["subtotal_label"] = canonical_label
        return "subtotal", fields
    if has_amount and (desc_text or wide_text):
        return "expense_subline", fields
    if desc_text:
        return "continuation", fields
    if has_amount:
        return "amount_only", fields
    return "noise", fields


def _split_groups_on_second_amount(groups: list, cols: ColumnMap) -> list:
    """A record has at most ONE amount, so a group containing a second
    amount-bearing row is two records merged by tight vertical spacing.
    Old-template multi-line expense entries print several dollar-bearing
    sublines (STAFF PER DIEM, STAFF TRANSPORTATION, ...) under one
    document number -- the same pattern modern reports use too (e.g. one
    DOSS... document number covering INCIDENTALS/PER DIEM/TRANSPORTATION
    lines), but spaced ~3.7-5pt apart here, tight enough to fall inside
    TIGHT_GROUP_GAP where modern reports' looser spacing keeps each
    subline its own group. classify_group's "first amount in the group"
    then silently drops every later one (verified: 114sdoc4 -- three
    segments each off by exactly one dropped STAFF TRANSPORTATION line:
    $150.61, $103.14, $339.82, each merged with the preceding STAFF PER
    DIEM row under the same document number). Splitting before the
    second amount-bearing row lets it fall through classify_group as its
    own expense_subline, inheriting doc context exactly the way modern
    reports' already-separate sublines do."""
    out = []
    for group in groups:
        current = []
        current_has_amount = False
        for r in group:
            has_amount = bool(r.text_in(*cols.amount))
            if has_amount and current_has_amount:
                out.append(current)
                current = [r]
                current_has_amount = True
            else:
                current.append(r)
                current_has_amount = current_has_amount or has_amount
        out.append(current)
    return out


def parse_block(block: Block, template: str = "modern") -> BlockParseResult:
    result = BlockParseResult()
    context = {"document_number": "", "date_posted": "", "payee": "", "start_date": "", "end_date": ""}
    last_record: Optional[Record] = None

    for page_num in sorted(block.pages):
        rows = block.rows_by_page[page_num]
        header_top = header_row_top(rows)
        cols = calibrate_columns(rows, template=template)
        if cols is None:
            result.unparsed.append({"page": page_num, "reason": "no_header"})
            continue

        data_rows = [
            r
            for r in sorted(rows, key=lambda r: r.top)
            if r.top > header_top + 20 and not _is_page_label_row(r, cols.amount[1])
        ]
        groups = _split_groups_on_second_amount(_split_groups_on_document_numbers(_group_rows(data_rows), cols), cols)
        classified_groups = [(group, *classify_group(group, cols)) for group in groups]

        # Loose description-only rows in a salary roster can belong above or
        # below a salary anchor. Assign them geometrically before constructing
        # records. Only the immediately adjacent semantic rows are candidates,
        # so text cannot jump across a subtotal/header into a different roster.
        salary_continuations = {}
        assigned_salary_continuations = set()
        for continuation_index, (continuation_group, continuation_role, continuation_fields) in enumerate(
            classified_groups
        ):
            if continuation_role != "continuation":
                continue
            previous_semantic = None
            for candidate in range(continuation_index - 1, -1, -1):
                candidate_role = classified_groups[candidate][1]
                if candidate_role != "continuation":
                    previous_semantic = candidate
                    break
            next_semantic = None
            for candidate in range(continuation_index + 1, len(classified_groups)):
                candidate_role = classified_groups[candidate][1]
                if candidate_role != "continuation":
                    next_semantic = candidate
                    break
            # An expense's description wrap always belongs to that preceding
            # expense, even when the following row happens to be misclassified
            # as salary (e.g. document number "1033" on page 392 is too short
            # for DOC_NUMBER_RE). Leave it to normal trailing-continuation
            # handling instead of stealing it for the following row.
            if previous_semantic is not None and classified_groups[previous_semantic][1] in {
                "expense_header",
                "expense_subline",
            }:
                continue
            candidates = [
                i
                for i in (previous_semantic, next_semantic)
                if i is not None and classified_groups[i][1] == "salary"
            ]
            if not candidates:
                continue
            continuation_top = continuation_group[0].top
            salary_index = min(
                candidates,
                key=lambda i: (abs(classified_groups[i][0][0].top - continuation_top), i),
            )
            salary_continuations.setdefault(salary_index, []).append(
                (continuation_index, continuation_fields["description"])
            )
            assigned_salary_continuations.add(continuation_index)

        for group_index, (group, role, fields) in enumerate(classified_groups):

            if role == "expense_header":
                rec = Record(record_type="expense", page=page_num, **fields)
                result.records.append(rec)
                result.events.append(("record", rec))
                last_record = rec
                context = {k: fields[k] for k in context}
            elif role == "salary":
                related = salary_continuations.get(group_index, [])
                leading = [text for index, text in related if index < group_index]
                trailing = [text for index, text in related if index > group_index]
                description = " ".join([*leading, fields["description"], *trailing]).strip()
                rec = Record(
                    record_type="salary",
                    page=page_num,
                    payee=fields["payee"],
                    description=description,
                    amount=fields["amount"],
                )
                result.records.append(rec)
                result.events.append(("record", rec))
                last_record = rec
            elif role == "subtotal":
                label = fields.get("subtotal_label") or re.sub(r"\s+", " ", fields["description"].strip().upper())
                sub = Subtotal(label=label, amount=fields["amount"], page=page_num, top=group[0].top)
                result.subtotals.append(sub)
                result.events.append(("subtotal", sub))
            elif role == "expense_subline":
                rec = Record(
                    record_type="expense",
                    page=page_num,
                    document_number=context["document_number"],
                    date_posted=context["date_posted"],
                    payee=context["payee"],
                    start_date=fields["start_date"] or context["start_date"],
                    end_date=fields["end_date"] or context["end_date"],
                    description=fields["description"],
                    amount=fields["amount"],
                )
                result.records.append(rec)
                result.events.append(("record", rec))
                last_record = rec
            elif role == "continuation":
                if group_index in assigned_salary_continuations:
                    continue
                if last_record is not None:
                    last_record.description = (last_record.description + " " + fields["description"]).strip()
                else:
                    result.unparsed.append(
                        {"page": page_num, "reason": "orphan_continuation", "text": fields["description"]}
                    )
            elif role == "amount_only":
                if last_record is not None and not last_record.amount:
                    last_record.amount = fields["amount"]
                elif last_record is not None and fields["amount"].strip().startswith("-"):
                    # A bare *negative* amount with no payee/description that
                    # can't fill in the last record (it already has one) is a
                    # standalone correction/deduction line -- verified: page
                    # 561 of 117sdoc8, a lone "-$3,000.00" row right after
                    # Risch's roster and before the PERSONNEL COMP subtotal;
                    # excluding it left the block's total exactly $3,000 too
                    # high, matching that block's reconciliation gap exactly.
                    # Recorded as its own record (same type as the block's
                    # most recent one, inheriting doc context for expenses)
                    # so its dollar value is still counted.
                    #
                    # Restricted to negative amounts deliberately: a *positive*
                    # bare amount in this position is usually a duplicate
                    # preview of an upcoming lump-sum subtotal (verified:
                    # Boozman's bare "$4,554.00" reprints moments later as the
                    # properly labeled "RE-EMPLOYED ANNUITANTS" subtotal --
                    # counting it here as well double-counts money that
                    # doesn't belong in this segment at all. An earlier,
                    # sign-agnostic version of this fix caused that exact
                    # regression across ~20 senators in this same report).
                    rec = Record(record_type=last_record.record_type, page=page_num, amount=fields["amount"])
                    if last_record.record_type == "expense":
                        rec.document_number = context["document_number"]
                        rec.date_posted = context["date_posted"]
                        rec.payee = context["payee"]
                        rec.start_date = context["start_date"]
                        rec.end_date = context["end_date"]
                    result.records.append(rec)
                    result.events.append(("record", rec))
                    last_record = rec
                else:
                    result.unparsed.append(
                        {"page": page_num, "reason": "orphan_amount", "text": fields["amount"]}
                    )
            else:
                combined = " ".join(v for v in fields.values() if v)
                if combined:
                    result.unparsed.append({"page": page_num, "reason": "unclassified", "text": combined})

    return result
