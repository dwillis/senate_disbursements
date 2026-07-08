from senate_parser.rows import cluster_rows


def row_containing(rows, text_fragment):
    for row in rows:
        if any(text_fragment in w.text for w in row.words):
            return row
    raise AssertionError(f"no row contains {text_fragment!r}")


def test_salary_row_reconstructed_correctly(page_1000_words):
    """`pdftotext -layout` attached $54,180.67 (TODD's amount) to TABLER's line.
    Coordinate-based row clustering must keep them separate."""
    rows = cluster_rows(page_1000_words)

    tabler = row_containing(rows, "TABLER")
    assert any("COMMUNICATIONS DIRECTOR" in w.text for w in tabler.words)
    assert any("$110,949.96" in w.text for w in tabler.words)
    assert not any("$54,180.67" in w.text for w in tabler.words)

    todd = row_containing(rows, "TODD")
    assert any("$54,180.67" in w.text for w in todd.words)


def test_expense_row_reconstructed_correctly(page_1004_words):
    """Verified desync case: -layout put $81.42 on the wrong document row."""
    rows = cluster_rows(page_1004_words)

    row = row_containing(rows, "DCOT20240398")
    texts = [w.text for w in row.words]
    assert "CAMERON JOSEPH BANDY" in texts
    assert "$81.42" in texts


def test_senate_page_row_has_no_document_number(page_125_words):
    """Senate Page program rows are salary-shaped (name, title, amount) with
    no document number -- the legacy parser misclassified these as expenses."""
    rows = cluster_rows(page_125_words)

    row = row_containing(rows, "PHIFER")
    texts = [w.text for w in row.words]
    assert "PAGE TO JUN. 7" in texts
    assert "$7,253.72" in texts
    assert not any(t.strip().isdigit() is False and t.startswith("DCOT") for t in texts)


def test_header_row_columns_present_on_every_data_page(page_1001_words, page_1004_words):
    for words in (page_1001_words, page_1004_words):
        rows = cluster_rows(words)
        header = row_containing(rows, "DOCUMENT NO.")
        header_texts = {w.text for w in header.words}
        assert "PAYEE NAME" in header_texts
        assert "AMOUNT ($)" in header_texts


def test_row_count_reasonable(page_1000_words):
    rows = cluster_rows(page_1000_words)
    assert 20 <= len(rows) <= 60
