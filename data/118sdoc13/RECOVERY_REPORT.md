# Data Recovery Report: 118sdoc13

**Date:** 2025-11-19
**Document:** Senate Disbursement Report 118sdoc13 (2024 Q2)

## Summary

The original parsing of 118sdoc13 failed to extract any data into `senate_data.csv`, resulting in all records being written to `missing_data.json`. This was due to format differences between 118sdoc13 and the older Senate disbursement reports that the parser was designed for.

### Recovery Results

| Metric | Count | Percentage |
|--------|-------|------------|
| **Total items in missing_data.json** | 12,585 | 100% |
| **Expense records recovered** | 5,275 | 41.9% |
| **Salary records recovered** | 3,171 | 25.2% |
| **Continuation lines identified** | 3,238 | 25.7% |
| **Category headers skipped** | 257 | 2.0% |
| **Lines remaining unparseable** | 644 | 5.1% |
| **Total records in output CSV** | 8,446 | 67.1% |

## Key Findings

### Format Differences in 118sdoc13

The 118sdoc13 document has significant formatting differences from earlier reports:

1. **Expense Records**: Many expense records lack amounts on the same line, or have different column spacing:
   ```
   DOC_NUM  DATE_POSTED  PAYEE  START_DATE  END_DATE  DESCRIPTION  [AMOUNT]
   ```
   The amount field is often missing or on a continuation line.

2. **Salary Records**: Use a three-column format:
   ```
   NAME  |  POSITION  |  AMOUNT
   ```
   No document numbers or dates are included.

3. **Continuation Lines**: Many records span multiple lines with date+description or description-only continuations.

### Data Quality

**Offices Recovered:**
- Secretary of the Senate: 6,336 records
- Chaplain: 1,614 records
- Appropriations: 180 records
- Majority Conference Committee (D): 227 records
- Consultants: 40 records
- Minority Policy Committee (R): 13 records
- President Pro Tempore (D): 3 records
- Mobile Communications Devices: 6 records
- Resolution and Reorganization Reserve: 26 records
- Minority Leader (R): 1 record

### Unparseable Data (5.1%)

The remaining 644 unparseable lines primarily consist of:
- Incomplete expense records missing payee information
- Continuation lines with unusual formatting
- Standalone amounts or document numbers
- Mixed format lines that don't match any expected pattern

## Files Generated

1. **senate_data_recovered.csv**: 8,447 rows (including header)
   - Contains both expense and salary records
   - Columns: office, record_type, page_num, doc_num, date_posted, payee_or_name, start_date, end_date, description_or_position, amount

2. **missing_data.json** (fixed):
   - JSON formatting corrected
   - Still contains all original missing data for reference

3. **recover_missing_data.py**:
   - Python script that performs the data recovery
   - Can be re-run if needed with modifications
   - Includes caching for efficient office name extraction

## Recommendations

1. **Continuation Line Merging**: The 3,238 continuation lines should be merged with their parent records to create complete expense descriptions. This would improve data completeness.

2. **Manual Review**: The 644 unparseable lines (5.1%) should be manually reviewed to determine if they contain recoverable information or represent data quality issues in the original PDF.

3. ~~**Parser Updates**: The main `process_senate_disbursements.py` script should be updated to handle the 118sdoc13 format natively, incorporating the regex patterns from the recovery script.~~ **COMPLETED** - Main parser updated to support both formats

4. **Data Validation**: Cross-reference recovered amounts with summary totals in the original PDF to ensure accuracy.

## Next Steps

- [x] Fix malformed missing_data.json
- [x] Analyze pattern distribution
- [x] Create recovery script
- [x] Run recovery and generate CSV
- [x] Generate this report
- [x] **Update main parser to handle 118sdoc13 format** - Now supports both old and new formats
- [ ] Merge continuation lines with parent records (optional)
- [ ] Validate amounts against PDF summaries
- [ ] Clean and standardize the recovered data to match standard output format

## Parser Updates (2025-11-19)

The main `process_senate_disbursements.py` parser has been updated to automatically support both older (113-114 Congress) and newer (118+ Congress) document formats:

- **Added flexible regex patterns** for 118+ format with optional dates/amounts
- **Automatic format detection** via fallback pattern matching
- **Backward compatibility** maintained for older documents
- **Test results** on pages 24-100 of 118sdoc13: **82.1% success rate** (279 records extracted, 61 missing)

The updated parser eliminates the need for manual recovery scripts for future 118+ format documents.

## Technical Notes

### Recovery Method

The recovery script uses multiple regex patterns to identify:
1. Expense records with varying column positions
2. Salary records with flexible spacing
3. Date+description continuation lines
4. Description-only continuation lines

### Office Name Extraction

Office names are extracted from page headers using a three-strategy approach:
1. Look for "Funding Year" markers (most reliable)
2. Look for party affiliation markers (R) or (D)
3. Look for committee names

Office names are cached and carried forward across nearby pages to handle continuation pages.

### Data Structure

The output CSV uses a unified schema for both expense and salary records:
- Expense records populate: doc_num, date_posted, payee_or_name, start_date, end_date, description_or_position, amount
- Salary records populate: payee_or_name (employee name), description_or_position (job title), amount
