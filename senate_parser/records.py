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

`_group_rows` handles case 2 by clustering on gap; `parse_block` handles
case 1 by tracking the open document context across normal-spaced
sublines.

Known limitation: for very long wrapped titles (5+ lines, e.g. a "When
Actually Employed" consultant's full working-date schedule -- verified
page 342, "DWYER, SHEILA M"), the vertical centering spreads lines out
at ~5.3pt, above the gap threshold. The lines *before* the name row then
have no open record to attach to and are dropped to `unparsed` as
`orphan_continuation`; the payee, amount, and any *trailing* wrap lines
(which attach to the record normally) are unaffected -- only a prefix of
the description text is lost. This degrades gracefully rather than
corrupting financial data, so it's left as-is for the first milestone.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from .rows import Row
from .segment import Block, header_row_top

DOC_NUMBER_RE = re.compile(r"^[A-Z0-9]{6,14}$")

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


@dataclass
class BlockParseResult:
    records: list = field(default_factory=list)
    subtotals: list = field(default_factory=list)
    unparsed: list = field(default_factory=list)
    # ('record', Record) / ('subtotal', Subtotal) in original document order,
    # for reconcile.py to walk sequentially -- records.append(...) order
    # alone doesn't preserve interleaving with subtotals.
    events: list = field(default_factory=list)


def calibrate_columns(rows: list) -> Optional[ColumnMap]:
    """Confirm this page has the expected header block and derive the
    column layout from this page's own header row. Returns None if the
    page doesn't look like a normal data page (used to flag anomalies
    rather than guess)."""
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
    $110,949.96)."""
    return bool(row.words) and all(w.x0 >= amount_right for w in row.words)


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


def _joined_text_in(group: list, x0: float, x1: float) -> str:
    return " ".join(t for t in (r.text_in(x0, x1) for r in group) if t).strip()


def _is_subtotal_label(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().upper())
    return normalized in SUBTOTAL_LABELS


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

    fields = {
        "document_number": doc_words[0].text if doc_words else "",
        "date_posted": _joined_text_in(group, *cols.date_posted),
        "payee": payee_text,
        "start_date": _joined_text_in(group, *cols.start_date),
        "end_date": _joined_text_in(group, *cols.end_date),
        "description": desc_text if desc_text else wide_text,
        "amount": amount_text,
    }

    if has_doc:
        return "expense_header", fields
    if has_payee:
        return "salary", fields
    if _is_subtotal_label(wide_text) and has_amount:
        return "subtotal", fields
    if has_amount and (desc_text or wide_text):
        return "expense_subline", fields
    if desc_text:
        return "continuation", fields
    if has_amount:
        return "amount_only", fields
    return "noise", fields


def parse_block(block: Block) -> BlockParseResult:
    result = BlockParseResult()
    context = {"document_number": "", "date_posted": "", "payee": "", "start_date": "", "end_date": ""}
    last_record: Optional[Record] = None

    for page_num in sorted(block.pages):
        rows = block.rows_by_page[page_num]
        header_top = header_row_top(rows)
        cols = calibrate_columns(rows)
        if cols is None:
            result.unparsed.append({"page": page_num, "reason": "no_header"})
            continue

        data_rows = [
            r
            for r in sorted(rows, key=lambda r: r.top)
            if r.top > header_top + 20 and not _is_page_label_row(r, cols.amount[1])
        ]
        for group in _group_rows(data_rows):
            role, fields = classify_group(group, cols)

            if role == "expense_header":
                rec = Record(record_type="expense", page=page_num, **fields)
                result.records.append(rec)
                result.events.append(("record", rec))
                last_record = rec
                context = {k: fields[k] for k in context}
            elif role == "salary":
                rec = Record(
                    record_type="salary",
                    page=page_num,
                    payee=fields["payee"],
                    description=fields["description"],
                    amount=fields["amount"],
                )
                result.records.append(rec)
                result.events.append(("record", rec))
                last_record = rec
            elif role == "subtotal":
                label = re.sub(r"\s+", " ", fields["description"].strip().upper())
                sub = Subtotal(label=label, amount=fields["amount"], page=page_num)
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
