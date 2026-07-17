import json

from senate_parser.coverage import (
    banner_check_findings,
    exception_key,
    finalize_finding,
    hard_audit_findings,
    load_approved_exceptions,
    unparsed_item_findings,
    unapproved_findings,
)
from senate_parser.reconcile import SubtotalCheck


def test_exception_key_is_exact_and_stable():
    finding = finalize_finding(
        {
            "severity": "error",
            "reason": "unexpected_zero_records",
            "source_doc": "118sdoc13",
            "reference_page": 42,
            "office": "SENATOR EXAMPLE",
            "funding_year": 2024,
            "label": "TRAVEL",
            "expected": 123.45,
        }
    )
    assert finding["exception_key"] == exception_key(finding)
    assert "118sdoc13|unexpected_zero_records|42" in finding["exception_key"]


def test_reviewed_exception_only_approves_the_exact_key(tmp_path):
    approved_path = tmp_path / "exceptions.json"
    approved_path.write_text(json.dumps({"approved": [{"key": "approved-key", "reviewer": "QA"}]}))
    findings = [
        {"severity": "error", "exception_key": "approved-key"},
        {"severity": "error", "exception_key": "new-key"},
        {"severity": "warning", "exception_key": "warning-key"},
    ]

    approved = load_approved_exceptions(approved_path)
    assert approved == {"approved-key"}
    assert unapproved_findings(findings, approved) == [findings[1]]


def test_hard_audits_block_but_duplicate_advisories_do_not():
    violations = [
        {
            "reason": "unparseable_date",
            "source_doc": "118sdoc13",
            "reference_page": 42,
            "raw_office": "SENATOR EXAMPLE",
            "payee": "JANE DOE",
            "description": "Travel",
            "amount": "$10.00",
            "detail": "end_date=not-a-date",
        },
        {
            "reason": "duplicate_rows_advisory",
            "source_doc": "118sdoc13",
            "reference_page": 42,
            "raw_office": "SENATOR EXAMPLE",
        },
    ]

    findings = hard_audit_findings(violations, "GPO-CDOC-118sdoc13-1.pdf")

    assert len(findings) == 1
    assert findings[0]["reason"] == "audit_unparseable_date"
    assert findings[0]["severity"] == "error"
    assert findings[0]["source_pdf"] == "GPO-CDOC-118sdoc13-1.pdf"
    assert "JANE DOE" in findings[0]["exception_key"]


def test_each_unparsed_item_has_its_own_blocking_exception_key():
    items = [
        {
            "reason": "orphan_continuation",
            "page": 8,
            "start_page": 7,
            "office": "SENATOR EXAMPLE",
            "funding_year": 2024,
            "text": "first continuation",
        },
        {
            "reason": "orphan_continuation",
            "page": 8,
            "start_page": 7,
            "office": "SENATOR EXAMPLE",
            "funding_year": 2024,
            "text": "second continuation",
        },
    ]

    findings = unparsed_item_findings(items, "118sdoc13", "volume-1.pdf")

    assert [finding["severity"] for finding in findings] == ["error", "error"]
    assert [finding["reason"] for finding in findings] == [
        "unparsed_orphan_continuation",
        "unparsed_orphan_continuation",
    ]
    assert findings[0]["exception_key"] != findings[1]["exception_key"]


def test_banner_failures_are_explicit_nonblocking_review_warnings():
    checks = [
        SubtotalCheck(
            label="BANNER ORGANIZATION TOTALS",
            page=19,
            expected=100.00,
            actual=101.25,
            status="fail",
            basis="banner",
        ),
        SubtotalCheck(
            label="BANNER NET PAYROLL",
            page=19,
            expected=None,
            actual=0.0,
            status="banner_missing",
            basis="banner",
        ),
        SubtotalCheck("BANNER OTHER", 19, 1.0, 1.0, "ok", "banner"),
    ]

    findings = banner_check_findings(
        checks, "118sdoc13", "volume-1.pdf", "SENATOR EXAMPLE", 2024
    )

    assert [finding["reason"] for finding in findings] == [
        "banner_check_discrepancy",
        "banner_check_missing",
    ]
    assert all(finding["severity"] == "warning" for finding in findings)
    assert unapproved_findings(findings, set()) == []
