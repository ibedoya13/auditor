"""Actions that can be run on a selected repository."""

import subprocess
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from auditor.audit import parse_vulnerabilities
from auditor.runtime import binary_available


@dataclass(frozen=True)
class Action:
    """An action that can be offered for a repository."""

    label: str
    required_binary: str


def run_npm_audit(repo_path: str) -> str:
    """Run 'npm audit --json' in the repo and return its JSON output.

    npm exits with status 1 when vulnerabilities are found, so the
    return code is ignored as long as a report is produced.
    """
    result = subprocess.run(
        ["npm", "audit", "--json"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def format_vulnerabilities(vulnerabilities: list) -> str:
    """Render parsed vulnerabilities as filterable text rows."""
    if not vulnerabilities:
        return "No se encontraron vulnerabilidades."
    lines = []
    for v in vulnerabilities:
        titles = ", ".join(v.titles) if v.titles else "sin detalle"
        lines.append(
            f"[{v.severity.upper()}] {v.name} ({v.range}) - {titles} "
            f"| fix: {v.fix_available}"
        )
    return "\n".join(lines)


class RepoActions(Screen[None]):
    """Shows the actions available for a repository based on host binaries."""

    BINDINGS = [
        ("escape", "go_back", "Volver"),
    ]

    def __init__(self, repo_path: str) -> None:
        super().__init__()
        self.repo_path = repo_path

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(str(self.repo_path), id="repo-path")
        yield ListView(id="actions-list")
        yield Static("", id="action-output", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        listing = self.query_one("#actions-list", ListView)
        self._available: list[Action] = [
            action
            for action in ACTIONS_REGISTRY.values()
            if binary_available(action.required_binary)
        ]
        for action in self._available:
            listing.append(ListItem(Label(action.label)))
        if self._available:
            listing.index = 0
        self.query_one("#repo-path", Static).update(str(self.repo_path))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = self.query_one("#actions-list", ListView).index
        if index is None or not self._available:
            return
        self.execute(self._available[index])

    def execute(self, action: Action) -> None:
        output = self.query_one("#action-output", Static)
        if action.label == "npm audit":
            report = run_npm_audit(self.repo_path)
            vulnerabilities = parse_vulnerabilities(report)
            output.update(format_vulnerabilities(vulnerabilities))
        else:
            output.update(f"Acción no implementada: {action.label}")


ACTIONS_REGISTRY: dict[str, Action] = {
    "npm audit": Action(label="npm audit", required_binary="npm"),
}
