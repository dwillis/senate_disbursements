"""Unit tests for bioguide_matcher name matching.

Constructs the matcher without __init__ (no network/YAML load) and injects
a small synthetic senators list covering the failure modes found in the
7-report unmatched_senators.csv audit: accented names, formal-vs-nickname
mismatches, and funding-year vs calendar-year term boundaries.
"""
import pytest

from bioguide_matcher import BioguideIdMatcher


def _senator(bioguide_id, first, last, middle="", nickname="", terms=()):
    return {
        "bioguide_id": bioguide_id,
        "first_name": first,
        "last_name": last,
        "middle_name": middle,
        "nickname": nickname,
        "official_full": f"{first} {last}",
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
