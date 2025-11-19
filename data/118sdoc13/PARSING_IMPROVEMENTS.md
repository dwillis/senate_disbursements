# 118sdoc13 Parsing Improvements

**Date:** 2025-11-19

## Summary

Fixed critical parser bugs and significantly improved data extraction for 118sdoc13.

### Results

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| **Records extracted** | 8,444 (from recovery script) | 48,195 | 5.7x increase |
| **Parser status** | Crashed on blank pages | Completed successfully | ✓ Fixed |
| **Encoding errors** | Crashed on special characters | Handled gracefully | ✓ Fixed |

## Issues Fixed

###  1. Parser crashed on pages without headers
**Problem:** Pages 2967, 2973 and others without the expected "START END" header caused an assertion error.

**Fix:** Modified `find_header_index()` to return `None` for pages without headers, and updated `parse_pages()` to skip these pages.

```python
# process_senate_disbursements.py:289-305
def find_header_index(line_array):
    """Find the index of the header line in a page."""
    matches = 0
    header_index = None
    for index, line in enumerate(line_array):
        r = header_end.search(line)
        if r:
            matches += 1
            header_index = index

    # Return None if no header found (e.g., blank pages or summary pages)
    if matches == 0:
        return None

    assert matches == 1, f"Expected 1 header, found {matches}"
    return header_index
```

### 2. Parser crashed on pages with encoding issues
**Problem:** Page 1504 and others contained non-UTF-8 characters (byte 0xa7), causing UnicodeDecodeError.

**Fix:** Added encoding fallback to latin-1 for pages with special characters.

```python
# process_senate_disbursements.py:327-334
try:
    with open(filename, 'r', encoding='utf-8') as fh:
        page_array = fh.readlines()
except UnicodeDecodeError:
    # Fall back to latin-1 encoding for pages with special characters
    with open(filename, 'r', encoding='latin-1') as fh:
        page_array = fh.readlines()
```

## Remaining Issues

### 1. Office names are mangled
The `process_top_matter()` function extracts too much text and doesn't clean it properly. Office names appear as:
```
"DE PRESIDENT PRO TEMPORE (D) DE Funding Year 2024 EXPENSE ALLOWANCES..."
```

Instead of:
```
"PRESIDENT PRO TEMPORE (D)"
```

This affects all 48,195 records but doesn't impact the core data (amounts, dates, payees).

### 2. Missing amounts
Many expense records are missing amounts because the flexible patterns don't handle multi-line records where the amount appears on a continuation line.

**Example from page 591:**
```
DBLA20242188      09/24/2024                   STAFF TRANSPORTATION                        $40.00
                  CITIBANK - TRAVEL CBA CARD   09/11/2024  09/11/2024  WASHINGTON DC...    $551.21
```

The second line is a separate record but gets marked as "missing" because it lacks a document number at the start.

## Recommendations

1. **Fix office name extraction:** Update `process_top_matter()` to:
   - Extract only the core office name
   - Remove "DETAILED AND SUMMARY STATEMENT OF EXPENDITURES" header text
   - Remove "Funding Year", "Authorization", "Supplementals" text
   - Stop at "DOCUMENT NO." header line

2. **Improve multi-line parsing:** Some records in 118sdoc13 span 2-3 lines with different formats:
   - Records without document numbers at the start (continuation from previous page)
   - Records with dates and amounts on separate lines
   - Description text that wraps across multiple lines

3. **Consider two-phase parsing:**
   - Phase 1: Use current parser for standard records (captures ~48k records)
   - Phase 2: Use recovery script logic for "missing" records (captures remaining ~4k)
   - Merge results for complete dataset

## Files Modified

- `process_senate_disbursements.py` - Fixed header detection and encoding handling

## Next Steps

- [ ] Fix office name extraction in `process_top_matter()`
- [ ] Improve multi-line record parsing
- [ ] Validate amounts against PDF summaries
- [ ] Run full cleaning step with bioguide ID matching
- [ ] Generate final cleaned CSV
