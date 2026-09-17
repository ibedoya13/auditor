"""Actions that can be run on a selected repository."""

import asyncio
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Grid
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    LoadingIndicator,
    Static,
)

from auditor.audit import parse_vulnerabilities
from auditor.runtime import binary_available

Runner = Callable[[str], str]


@dataclass(frozen=True)
class Action:
    """An action that can be offered for a repository."""

    label: str
    required_binary: str
    runner: Runner
    needs_confirmation: bool = False


def npm_run(repo_path: str, args: list[str]) -> str:
    """Run an npm command in the repo and return its stdout.

    npm exits with a non-zero status in several normal situations
    (audit finds vulnerabilities, fix cannot fix everything), so the
    return code is ignored as long as a report is produced.
    """
    result = subprocess.run(
        ["npm", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def run_npm_audit(repo_path: str) -> str:
    """Run 'npm audit --json' in the repo and return its formatted report."""
    report = npm_run(repo_path, ["audit", "--json"])
    return format_vulnerabilities(parse_vulnerabilities(report))


def run_npm_audit_fix(repo_path: str, force: bool = False) -> str:
    """Run 'npm audit fix --json' (optionally --force) in the repo."""
    args = ["audit", "fix", "--json"]
    if force:
        args.append("--force")
    return npm_run(repo_path, args)


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


def format_fix_report(fix_json: str) -> str:
    """Render the outcome of an 'npm audit fix' run.

    Shows the installed changes and a re-audit of what still remains.
    npm reports package upgrades in the `change` list (from -> to) even
    when the added/removed/changed counters are all 0, so the list is
    the source of truth for what was fixed. The re-audit may come in the
    top-level `vulnerabilities` or nested under `audit.vulnerabilities`.
    When npm cannot resolve the fix it returns an `error` object instead.
    """
    report = json.loads(fix_json)
    npm_error = report.get("error")
    if npm_error:
        summary = npm_error.get("summary", "Error desconocido de npm")
        return f"El comando falló: {summary}"
    lines: list[str] = []
    changes = report.get("change") or []
    if changes:
        lines.append(f"Corregidas: {len(changes)} actualización(es):")
        for change in changes:
            from_version = change.get("from", {}).get("version", "?")
            to_version = change.get("to", {}).get("version", "?")
            name = change.get("to", {}).get("name", "?")
            lines.append(f"  • {name}: {from_version} → {to_version}")
    else:
        added = report.get("added", 0)
        removed = report.get("removed", 0)
        changed = report.get("changed", 0)
        lines.append(
            f"Corregidas: {added} añadidas, {removed} eliminadas, {changed} cambiadas."
        )
    remaining = parse_vulnerabilities(json.dumps(report.get("audit", report)))
    lines.append("")
    lines.append("Vulnerabilidades restantes:")
    lines.append(format_vulnerabilities(remaining))
    return "\n".join(lines)


def run_npm_audit_fix_safe(repo_path: str) -> str:
    """Safe fix: only semver-compatible updates."""
    return format_fix_report(run_npm_audit_fix(repo_path, force=False))


def run_npm_audit_fix_force(repo_path: str) -> str:
    """Forced fix: may install breaking major versions."""
    return format_fix_report(run_npm_audit_fix(repo_path, force=True))


class RepoActions(Screen[None]):
    """Shows the actions available for a repository based on host binaries."""

    BINDINGS = [
        ("escape", "go_back", "Volver"),
    ]

    def __init__(self, repo_path: str) -> None:
        super().__init__()
        self.repo_path = repo_path
        self._pending_action: Action | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(str(self.repo_path), id="repo-path")
        yield ListView(id="actions-list")
        yield LoadingIndicator(id="action-spinner")
        yield Static("", id="action-output", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._spinner_display(False)
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
        if action.needs_confirmation:
            self._pending_action = action
            self.app.push_screen(ConfirmScreen(action), callback=self._on_confirmation)
            return
        self.run_action(action)

    def _on_confirmation(self, confirmed: bool | None) -> None:
        if confirmed and self._pending_action is not None:
            self.run_action(self._pending_action)

    def run_action(self, action: Action) -> None:
        """Run the action in a worker thread, with a spinner while it runs."""
        self._write_output("")
        self._spinner_display(True)
        self.run_worker(
            self._run_action_worker(action),
            thread=True,
            exclusive=True,
            group="action-runner",
        )

    async def _run_action_worker(self, action: Action) -> None:
        try:
            report = await asyncio.to_thread(action.runner, self.repo_path)
        except Exception as error:  # noqa: BLE001 - surfaced to the user
            self.app.notify(
                f"El comando falló: {error}", title="Error", severity="error"
            )
            return
        self._write_output(report)
        self._spinner_display(False)
        self.app.notify(
            f"'{action.label}' terminó correctamente.",
            title="Comando completado",
        )

    def _spinner_display(self, visible: bool) -> None:
        self.query_one("#action-spinner", LoadingIndicator).display = visible

    def _write_output(self, text: str) -> None:
        self.query_one("#action-output", Static).update(text)


class ConfirmScreen(ModalScreen[bool]):
    """Modal asking the user to confirm a risky action before running it."""

    BINDINGS = [
        ("s", "confirm", "Confirmar"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "Cancelar"),
    ]

    def __init__(self, action: Action) -> None:
        super().__init__()
        self.action = action

    def compose(self) -> ComposeResult:
        with Grid():
            yield Static(
                f"¿Ejecutar '{self.action.label}'?\n"
                "Esta acción puede introducir cambios importantes "
                "(breaking changes) en el proyecto.",
                id="confirm-message",
            )
            yield Static("s: confirmar · n/esc: cancelar", id="confirm-help")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


ACTIONS_REGISTRY: dict[str, Action] = {
    "npm audit": Action(
        label="npm audit",
        required_binary="npm",
        runner=run_npm_audit,
    ),
    "npm audit fix": Action(
        label="npm audit fix",
        required_binary="npm",
        runner=run_npm_audit_fix_safe,
    ),
    "npm audit fix --force": Action(
        label="npm audit fix --force",
        required_binary="npm",
        runner=run_npm_audit_fix_force,
        needs_confirmation=True,
    ),
}
