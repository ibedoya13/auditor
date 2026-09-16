"""Tests for parsing npm audit JSON output."""

from auditor.audit import parse_vulnerabilities

SAMPLE_AUDIT_JSON = """
{
  "vulnerabilities": {
    "lodash": {
      "name": "lodash",
      "severity": "high",
      "range": "<4.17.19",
      "via": [{"title": "Prototype Pollution", "url": "https://example.com"}],
      "fixAvailable": {"name": "lodash", "version": "4.17.21", "isSemVerMajor": false}
    },
    "minimist": {
      "name": "minimist",
      "severity": "low",
      "range": "<1.2.6",
      "via": ["lodash"],
      "fixAvailable": true
    },
    "qs": {
      "name": "qs",
      "severity": "critical",
      "range": "<6.9.7",
      "via": [{"title": "Denial of Service", "url": "https://example.com"}],
      "fixAvailable": false
    }
  }
}
"""


def test_parse_returns_one_vulnerability_per_entry() -> None:
    vulnerabilities = parse_vulnerabilities(SAMPLE_AUDIT_JSON)
    assert {v.name for v in vulnerabilities} == {"lodash", "minimist", "qs"}


def test_parse_sorts_by_severity_first() -> None:
    vulnerabilities = parse_vulnerabilities(SAMPLE_AUDIT_JSON)
    assert [v.name for v in vulnerabilities] == ["qs", "lodash", "minimist"]


def test_parse_extracts_advisory_titles_and_indirect_sources() -> None:
    vulnerabilities = parse_vulnerabilities(SAMPLE_AUDIT_JSON)
    by_name = {v.name: v for v in vulnerabilities}
    assert by_name["lodash"].titles == ["Prototype Pollution"]
    assert by_name["qs"].titles == ["Denial of Service"]
    assert by_name["minimist"].titles == ["vía lodash"]


def test_parse_extracts_fix_availability() -> None:
    vulnerabilities = parse_vulnerabilities(SAMPLE_AUDIT_JSON)
    by_name = {v.name: v for v in vulnerabilities}
    assert by_name["lodash"].fix_available == "lodash@4.17.21"
    assert by_name["minimist"].fix_available == "sí"
    assert by_name["qs"].fix_available == "no"


def test_parse_handles_empty_audit_report() -> None:
    assert parse_vulnerabilities('{"vulnerabilities": {}}') == []
