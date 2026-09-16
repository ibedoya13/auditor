"""Parsing of npm audit JSON reports."""

import json
from dataclasses import dataclass, field

SEVERITY_ORDER = {"critical": 0, "high": 1, "moderate": 2, "low": 3}


@dataclass(frozen=True)
class Vulnerability:
    """A single insecure dependency reported by npm audit."""

    name: str
    severity: str
    range: str
    titles: list[str] = field(default_factory=list)
    fix_available: str = "no"


def parse_vulnerabilities(audit_json: str) -> list[Vulnerability]:
    """Parse the JSON output of 'npm audit --json' into sorted entries."""
    report = json.loads(audit_json)
    vulnerabilities: list[Vulnerability] = []
    for entry in report.get("vulnerabilities", {}).values():
        via = entry.get("via", [])
        titles = [
            f"vía {source}" if isinstance(source, str) else source.get("title", "")
            for source in via
        ]
        fix = entry.get("fixAvailable", False)
        if isinstance(fix, dict):
            fix_text = f"{fix.get('name')}@{fix.get('version')}"
        elif fix is True:
            fix_text = "sí"
        else:
            fix_text = "no"
        vulnerabilities.append(
            Vulnerability(
                name=entry.get("name", ""),
                severity=entry.get("severity", ""),
                range=entry.get("range", ""),
                titles=titles,
                fix_available=fix_text,
            )
        )
    return sorted(
        vulnerabilities,
        key=lambda v: (SEVERITY_ORDER.get(v.severity, 99), v.name),
    )
