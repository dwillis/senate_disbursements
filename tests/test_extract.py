"""Tests for word-level PDF extraction, including the doubled-char
text-layer defect fix.

Some 112th Congress PDF pages (COMPENSATION OF MEMBERS, 41 pages across
112sdoc4/7/10) extract with every header character doubled: "COMPENSATION"
becomes "CCOOMMPPEENNSSAATTIIOONN", "2011" becomes "22001111". The data
rows on the same pages extract normally. extract.page_words must detect
and collapse the doubling without touching normal words.
"""
import pytest

from senate_parser.extract import _dedoubled


@pytest.mark.parametrize("doubled,expected", [
    ("CCOOMMPPEENNSSAATTIIOONN", "COMPENSATION"),
    ("OOFF", "OF"),
    ("MMEEMMBBEERRSS", "MEMBERS"),
    ("DDEESSCCRRIIPPTTIIOONN", "DESCRIPTION"),
    ("NNEETT", "NET"),
    ("FFUUNNDDSS", "FUNDS"),
    ("22001111", "2011"),
    ("0044//0011//22001111", "04/01/2011"),
    ("TTHHRROOUUGGHH", "THROUGH"),
    ("YYTTDD", "YTD"),
    ("CCOOMMPPEENNSSAATTIIOONN OOFF MMEEMMBBEERRSS", "COMPENSATION OF MEMBERS"),
])
def test_doubled_tokens_are_collapsed(doubled, expected):
    assert _dedoubled(doubled) == expected


@pytest.mark.parametrize("normal", [
    "Authorization",
    "23,603,773.00",
    "0.00",
    "11,447,508.99",
    "Net Payroll Expenses",
    "Supplemental",
    "Transfers",
    "Rescissions",
    "Net Revenues",
    "DOCUMENT NO.",
    "112TH CONGRESS",
    "S.RES. 89D (110TH)",
    # 2-char repeated-digit tokens are legitimate (page labels "B - 11"),
    # not doubled. The >=4 guard prevents collapsing them.
    "11",
    "22",
    "B - 11",
    "B - 99",
])
def test_normal_words_are_not_changed(normal):
    assert _dedoubled(normal) == normal


def test_empty_string():
    assert _dedoubled("") == ""


def test_single_char_unchanged():
    assert _dedoubled("A") == "A"


def test_two_char_tokens_unchanged():
    # 2-char tokens are below the >=4 guard, so neither "AA" (matched
    # pair) nor "AB" (mismatched pair) is collapsed. This avoids
    # collapsing legitimate page-label digits like "11".
    assert _dedoubled("AA") == "AA"
    assert _dedoubled("AB") == "AB"