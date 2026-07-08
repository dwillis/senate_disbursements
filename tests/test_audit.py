from senate_parser.audit import audit_rows


def make_row(**overrides):
    row = {
        "source_doc": "118sdoc13",
        "senator_flag": 1,
        "senator_name": "TOM COTTON",
        "bioguide_id": "C001095",
        "raw_office": "SENATOR TOM COTTON",
        "funding_year": 2024,
        "fiscal_year": "",
        "congress_number": 118,
        "reference_page": 1000,
        "document_number": "",
        "date_posted": "",
        "start_date": "",
        "end_date": "",
        "description": "COMMUNICATIONS DIRECTOR",
        "salary_flag": 1,
        "amount": "$110,949.96",
        "payee": "TABLER, CAROLINE R",
        "validation_status": "ok",
        "category": "PERSONNEL COMP. FULL-TIME PERMANENT",
    }
    row.update(overrides)
    return row


def reasons(rows):
    return [v["reason"] for v in audit_rows(rows)]


def test_clean_row_produces_no_violations():
    assert reasons([make_row()]) == []


def test_unparseable_amount_flagged():
    assert "unparseable_amount" in reasons([make_row(amount="$1,23a.45")])


def test_sub_dollar_amount_is_not_flagged():
    assert reasons([make_row(amount="$.80")]) == []


def test_bad_date_flagged():
    assert "unparseable_date" in reasons([make_row(date_posted="1/5/24")])


def test_contaminated_office_name_flagged():
    """The exact class that shipped: 'Funding Year X' (no 4-digit year)
    fell through FUNDING_YEAR_RE and leaked boilerplate into raw_office."""
    row = make_row(raw_office="PHOTOGRAPHIC STUDIO Funding Year X (REVOLVING FUND)")
    assert "contaminated_office_name" in reasons([row])


def test_funding_year_out_of_range_flagged():
    assert "funding_year_out_of_range" in reasons([make_row(funding_year=1214)])


def test_salary_row_missing_payee_flagged_unless_negative_correction():
    assert "salary_row_missing_payee" in reasons([make_row(payee="")])
    # bare negative correction lines legitimately have no payee
    assert reasons([make_row(payee="", amount="-$3,000.00")]) == []


def test_duplicate_rows_are_advisory_flagged_once():
    rows = [make_row(), make_row()]
    violations = [v for v in audit_rows(rows) if v["reason"] == "duplicate_rows_advisory"]
    assert len(violations) == 1
    assert "2x" in violations[0]["detail"]
