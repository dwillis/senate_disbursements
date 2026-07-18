"""Report inventory and template classification, shared across the test
suite and the verify_sample QA script.

The 112th-114th era ships as a single PDF (no volume split, suffix "")
with reference_page == raw_page directly (page_offset=0). The 115th+
era ships as two volume PDFs ("-1", "-2") whose page_offset is volume
1's page count, matching how the shipped CSVs were produced.

OLD_TEMPLATE_REPORTS mirrors pipeline.run's own auto-detection
(congress number <= 114) so consumers stay in sync as older reports are
added.
"""

import re

# (source_doc, volume file suffix -> page count).
REPORTS = {
    "114sdoc4": [("", 2084)],
    "114sdoc7": [("", 2266)],
    "114sdoc13": [("", 2271)],
    "117sdoc8": [("-1", 1040), ("-2", 1198)],
    "118sdoc2": [("-1", 1291), ("-2", 1348)],
    "118sdoc11": [("-1", 1387), ("-2", 1278)],
    "118sdoc13": [("-1", 1495), ("-2", 1484)],
    "119sdoc3": [("-1", 1335), ("-2", 1350)],
    "119sdoc5": [("-1", 1476), ("-2", 1542)],
    "119sdoc6": [("-1", 1259), ("-2", 1264)],
}

# Reports parsed with senate_parser.records's old-template ("anchor")
# column calibration (see records.ANCHOR_HEADER_WORDS) -- everything
# 114th Congress and earlier.
OLD_TEMPLATE_REPORTS = {
    r for r in REPORTS if re.match(r"(\d{3})sdoc", r) and int(r[:3]) <= 114
}

# Reports with committed snapshot regressions (tests/test_full_reports.py).
# All 10 docs (7 modern-era + 3 114-era). Slow tests take ~45 min total.
SNAPSHOT_REPORTS = set(REPORTS)
# Dict form (source_doc -> volume list) filtered to snapshot reports, for
# tests/test_full_reports.py which needs both membership tests and volume
# lookup. SNAPSHOT_REPORTS is a set for fast `in` checks; this is the
# equivalent dict for code that needs REPORTS[doc] subscript.
SNAPSHOT_REPORTS_DICT = {k: v for k, v in REPORTS.items() if k in SNAPSHOT_REPORTS}