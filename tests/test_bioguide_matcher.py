"""Unit tests for bioguide_matcher name matching.

Constructs the matcher without __init__ (no network/YAML load) and injects
a small synthetic senators list covering the failure modes found in the
7-report unmatched_senators.csv audit: accented names, formal-vs-nickname
mismatches, and funding-year vs calendar-year term boundaries.
"""
import pytest

from bioguide_matcher import BioguideIdMatcher


def _senator(bioguide_id, first, last, middle="", nickname="", terms=(),
             official_full=None):
    return {
        "bioguide_id": bioguide_id,
        "first_name": first,
        "last_name": last,
        "middle_name": middle,
        "nickname": nickname,
        "official_full": official_full or f"{first} {last}",
        "suffix": "",
        "terms": [
            {"type": "sen", "start": start, "end": end} for start, end in terms
        ],
    }


@pytest.fixture
def matcher():
    m = BioguideIdMatcher.__new__(BioguideIdMatcher)
    m.senators = [
        _senator("L000570", "Ben", "Luján", middle="Ray",
                 terms=[("2021-01-03", "2027-01-03")]),
        _senator("M000355", "Mitch", "McConnell",
                 terms=[("2021-01-03", "2027-01-03")]),
        _senator("M001169", "Christopher", "Murphy", middle="S.",
                 terms=[("2019-01-03", "2025-01-03"), ("2025-01-03", "2031-01-03")]),
        _senator("B001305", "Ted", "Budd",
                 terms=[("2023-01-03", "2029-01-03")]),
        _senator("R000618", "Pete", "Ricketts",
                 terms=[("2023-01-23", "2027-01-03")]),
        _senator("T000250", "John", "Thune",
                 terms=[("2021-01-03", "2027-01-03")]),
        _senator("H001097", "George", "Helmy", middle="S.",
                 terms=[("2024-09-09", "2024-12-08")]),
        _senator("B001320", "Laphonza", "Butler", middle="Romanique",
                 terms=[("2023-10-03", "2024-12-08")]),
        _senator("B001268", "Scott", "Brown",
                 terms=[("2010-02-04", "2013-01-03")],
                 official_full="Scott P. Brown"),
        _senator("C000560", "Thomas", "Coburn", middle="A.",
                 terms=[("2005-01-04", "2015-01-03")],
                 official_full="Tom Coburn"),
        _senator("T000461", "Patrick", "Toomey", middle="J.",
                 terms=[("2011-01-03", "2023-01-03")]),
        _senator("U000038", "Mark", "Udall", middle="E.",
                 terms=[("2009-01-06", "2015-01-03")],
                 official_full="Mark Udall"),
        _senator("U000039", "Tom", "Udall", middle="S.",
                 terms=[("2009-01-06", "2021-01-03")],
                 official_full="Tom Udall"),
        _senator("H001042", "Mazie", "Hirono", middle="K.",
                 terms=[("2013-01-03", "2025-01-03")],
                 official_full="Mazie K. Hirono"),
    ]
    return m


def test_accent_stripped_from_yaml_name(matcher):
    assert matcher.get_bioguide_id("BEN RAY LUJAN", 2024) == "L000570"


def test_accent_stripped_from_input(matcher):
    assert matcher.get_bioguide_id("BEN RAY LUJÁN", 2024) == "L000570"


@pytest.mark.parametrize("report_name,expected", [
    ("A. MITCHELL MCCONNELL, JR.", "M000355"),
    ("CHRIS MURPHY", "M001169"),
    ("THEODORE BUDD", "B001305"),
    ("JOHN PETER RICKETTS", "R000618"),
    ("JOHN R. THUNE", "T000250"),
])
def test_aliases(matcher, report_name, expected):
    assert matcher.get_bioguide_id(report_name, 2024) == expected


def test_funding_year_after_term_end_matches(matcher):
    # Helmy's term ended 2024-12-08; funding year 2025 started 2024-10-01.
    assert matcher.get_bioguide_id("GEORGE HELMY", 2025) == "H001097"
    assert matcher.get_bioguide_id("LAPHONZA BUTLER", 2025) == "B001320"


def test_funding_year_beyond_overlap_does_not_match(matcher):
    assert matcher.get_bioguide_id("GEORGE HELMY", 2026) == ""


def test_alias_still_respects_term_years(matcher):
    # Budd's first term started January 2023; the alias must not bypass
    # the year filter.
    assert matcher.get_bioguide_id("THEODORE BUDD", 2021) == ""


def test_plain_name_still_matches(matcher):
    assert matcher.get_bioguide_id("JOHN THUNE", 2024) == "T000250"


def test_unknown_name_returns_empty(matcher):
    assert matcher.get_bioguide_id("ZAPHOD BEEBLEBROX", 2024) == ""


# 13 reports (112sdoc4 through 115sdoc6) ship 80 unmatched-senator rows for
# 6 distinct senators. Each fails for a different reason; see the audit note
# in docs/plans/ for the full investigation.

def test_period_inside_name_treated_as_separator(matcher):
    """113sdoc2 prints 'SENATOR PATRICK J.TOOMEY' -- no space after the
    middle-initial period. _normalize_name used to strip punctuation without
    adding space, so 'J.TOOMEY' collapsed to 'JTOOMEY' and the name didn't
    match the YAML's 'Patrick J. Toomey'. Periods must separate, not join."""
    assert matcher.get_bioguide_id("PATRICK J.TOOMEY", 2011) == "T000461"


def test_state_suffix_stripped_from_name(matcher):
    """112sdoc4-114sdoc4 print 'SENATOR MARK UDALL (CO)' / 'SENATOR TOM UDALL
    (NM)' -- the disambiguating state is in parens because two Udalls served
    simultaneously. The matcher has no state-stripping path, so the '(CO)'
    survives normalization and the name fails to match 'MARK UDALL'."""
    assert matcher.get_bioguide_id("MARK UDALL (CO)", 2010) == "U000038"
    assert matcher.get_bioguide_id("TOM UDALL (NM)", 2011) == "U000039"


def test_official_full_matches_when_first_last_lacks_middle(matcher):
    """113sdoc2 prints 'SENATOR SCOTT P. BROWN'. The YAML has first='Scott',
    last='Brown' (no middle), but official_full='Scott P. Brown'. The matcher
    only generated 'Scott Brown' variants from first/last, so the input's
    middle initial 'P.' had nothing to match against. official_full must be
    a name variant."""
    assert matcher.get_bioguide_id("SCOTT P. BROWN", 2010) == "B001268"


def test_official_full_matches_when_first_is_formal_name(matcher):
    """112sdoc4-114sdoc4 print 'SENATOR TOM COBURN'. The YAML has
    first='Thomas', middle='A.', last='Coburn', official_full='Tom Coburn'.
    The matcher only generated 'Thomas Coburn' / 'Thomas A Coburn' variants,
    none of which match 'TOM COBURN'. official_full carries the nickname the
    reports actually use."""
    assert matcher.get_bioguide_id("TOM COBURN", 2011) == "C000560"


def test_alias_for_pdf_spelling_error(matcher):
    """113sdoc2 prints 'SENATOR MAIZE HIRONO' -- a typo for 'MAZIE'. The
    matcher can't infer a spelling correction; an explicit alias is the only
    fix."""
    assert matcher.get_bioguide_id("MAIZE HIRONO", 2013) == "H001042"
