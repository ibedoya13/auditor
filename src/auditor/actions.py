"""Actions that can be run on a selected repository."""

import asyncio
import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Grid
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Footer,
    Header,
    Input,
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
    opens_modal: bool = False


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


def git_run(repo_path: str, args: list[str]) -> str:
    """Run a git command in the repo and return its stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Error de git")
    return result.stdout


def run_git_clean(repo_path: str) -> str:
    """Empty the staging area and stash every pending change.

    Runs 'git reset' to unstage everything and 'git stash -u' to save
    all pending changes (including untracked files), leaving the repo
    clean for new changes.
    """
    lines: list[str] = []
    lines.append(git_run(repo_path, ["reset"]).strip())
    stash_output = git_run(repo_path, ["stash", "-u"]).strip()
    lines.append(stash_output)
    return "\n".join(
        [
            "Staging area limpio (git reset).",
            "Cambios pendientes guardados en stash (git stash -u).",
            "",
            stash_output or "No había cambios pendientes.",
        ]
    )


def feature_branch_name() -> str:
    """Build a branch name 'feature/{timestamp}' with digits only."""
    return datetime.now().strftime("feature/%Y%m%d%H%M%S")


def apply_overrides(
    package_json: dict,
    packages: list[str],
    version: str,
    direct_dependencies: set[str] | None = None,
) -> dict:
    """Pin packages to an exact version in the package.json overrides.

    Updates the override if it already exists, adds it otherwise.
    Overrides can only target transitive dependencies: a package listed
    in dependencies/devDependencies/optionalDependencies/peerDependencies
    cannot be overridden (npm rejects that with EOVERRIDE).
    Raises ValueError for an empty package list, an invalid version, or
    a package that is a direct dependency.
    """
    cleaned = [name.strip() for name in packages if name.strip()]
    if not cleaned:
        raise ValueError("Debe indicar al menos un paquete.")
    direct = direct_dependencies or set()
    direct_hits = [name for name in cleaned if name in direct]
    if direct_hits:
        raise ValueError(
            "No se puede fijar con overrides una dependencia directa: "
            + ", ".join(direct_hits)
            + ". Los overrides solo aplican a dependencias transitivas."
        )
    version = version.strip()
    if version.lower().startswith("v"):
        version = version[1:]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(
            f"Versión '{version}' no es una versión exacta válida "
            "(formato esperado: X.Y.Z)."
        )
    overrides = dict(package_json.get("overrides") or {})
    for name in cleaned:
        overrides[name] = version
    package_json["overrides"] = overrides
    return package_json


def direct_dependencies_of(package_json: dict) -> set[str]:
    """Collect the direct dependency names declared in package.json."""
    direct: set[str] = set()
    for section in (
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
    ):
        direct.update((package_json.get(section) or {}).keys())
    return direct


def run_git_create_feature_branch(repo_path: str) -> str:
    """Create 'feature/{timestamp}' from develop, or trunk as fallback.

    Raises RuntimeError when neither develop nor trunk exists in the repo.
    """
    base = None
    for candidate in ("develop", "trunk"):
        try:
            git_run(repo_path, ["rev-parse", "--verify", candidate])
            base = candidate
            break
        except RuntimeError:
            continue
    if base is None:
        raise RuntimeError(
            "El repo no tiene branch 'develop' ni 'trunk' desde donde crear "
            "el branch nuevo."
        )
    branch = feature_branch_name()
    git_run(repo_path, ["checkout", "-b", branch, base])
    return f"Branch '{branch}' creado desde '{base}'."


def remediation_commit_message(fixed: datetime | None = None) -> str:
    """Build the vulnerability remediation commit message with its date."""
    moment = fixed or datetime.now()
    return f"Remediación de vulnerabilidades - {moment:%Y-%m-%d %H:%M}"


def run_git_commit_remediation(repo_path: str) -> str:
    """Stage every change and commit with the remediation message."""
    if not git_run(repo_path, ["status", "--porcelain"]).strip():
        return "No hay cambios pendientes: sin cambios para commitear."
    git_run(repo_path, ["add", "-A"])
    message = remediation_commit_message()
    git_run(repo_path, ["commit", "-m", message])
    return f"Commit creado: '{message}'."


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

    def action_go_back(self) -> None:
        """Dismiss the actions screen and return to the directory browser."""
        self.app.pop_screen()

    def execute(self, action: Action) -> None:
        if action.needs_confirmation:
            self._pending_action = action
            self.app.push_screen(ConfirmScreen(action), callback=self._on_confirmation)
            return
        if action.opens_modal:
            self.app.push_screen(OverridesScreen(self.repo_path))
            return
        self.run_action(action)

    def _on_confirmation(self, confirmed: bool | None) -> None:
        if confirmed and self._pending_action is not None:
            self.run_action(self._pending_action)

    def run_action(self, action: Action) -> None:
        """Run the action in a worker thread, with an overlay while it runs."""
        self._write_output("")
        self.app.push_screen(
            LoadingOverlay(action), callback=self._on_loading_dismissed
        )
        self.run_worker(
            self._run_action_worker(action),
            thread=True,
            exclusive=True,
            group="action-runner",
        )

    def _on_loading_dismissed(self, result: object) -> None:
        # the overlay is dismissed by the worker itself; nothing to do
        pass

    async def _run_action_worker(self, action: Action) -> None:
        try:
            report = await asyncio.to_thread(action.runner, self.repo_path)
        except Exception as error:  # noqa: BLE001 - surfaced to the user
            self.app.call_from_thread(self._finish, "")
            self.app.call_from_thread(
                self.app.notify,
                f"El comando falló: {error}",
                title="Error",
                severity="error",
            )
            return
        self.app.call_from_thread(self._finish, report)
        self.app.call_from_thread(
            self.app.notify,
            f"'{action.label}' terminó correctamente.",
            title="Comando completado",
        )

    def _finish(self, report: str) -> None:
        """Dismiss the overlay and show the report (main thread only)."""
        self.app.pop_screen()
        self._write_output(report)

    def _write_output(self, text: str) -> None:
        self.query_one("#action-output", Static).update(text)


class ConfirmScreen(ModalScreen[bool]):
    """Modal asking the user to confirm a risky action before running it."""

    BINDINGS = [
        ("s", "confirm", "Confirmar"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "Cancelar"),
    ]

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    ConfirmScreen Grid {
        grid-rows: auto auto auto;
        width: 72;
        height: auto;
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }
    ConfirmScreen #confirm-question {
        width: 1fr;
        content-align: center middle;
        text-style: bold;
        color: $text;
    }
    ConfirmScreen #confirm-warning {
        width: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    ConfirmScreen #confirm-help {
        width: 1fr;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }
    """

    def __init__(self, action: Action) -> None:
        super().__init__()
        self.action = action

    def compose(self) -> ComposeResult:
        with Grid():
            yield Static(
                f"¿Ejecutar '{self.action.label}'?",
                id="confirm-question",
            )
            yield Static(
                "Esta acción puede introducir cambios importantes "
                "(breaking changes) en el proyecto.",
                id="confirm-warning",
            )
            yield Static(
                "[b]s[/b] confirmar   ·   [b]n[/b]/[b]esc[/b] cancelar",
                id="confirm-help",
            )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class LoadingOverlay(ModalScreen[None]):
    """Compact modal with a spinner shown while an action is running.

    Blocks any interaction with the screen below: a modal takes the
    focus, so keys cannot reach the actions list until the action ends.
    """

    DEFAULT_CSS = """
    LoadingOverlay {
        align: center middle;
        background: $background 60%;
    }
    LoadingOverlay Grid {
        grid-rows: auto auto;
        width: 60;
        height: auto;
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }
    LoadingOverlay #loading-label {
        width: 1fr;
        content-align: center middle;
        text-style: bold;
        color: $text;
    }
    LoadingOverlay #loading-spinner {
        width: 1fr;
        content-align: center middle;
    }
    """

    def __init__(self, action: Action) -> None:
        super().__init__()
        self.action = action

    def compose(self) -> ComposeResult:
        with Grid():
            yield Static(f"Ejecutando '{self.action.label}'…", id="loading-label")
            yield LoadingIndicator(id="loading-spinner")


class OverridesScreen(ModalScreen[None]):
    """Modal to enter packages and a version to pin in package.json."""

    BINDINGS = [
        ("escape", "cancel", "Cancelar"),
    ]

    DEFAULT_CSS = """
    OverridesScreen {
        align: center middle;
    }
    OverridesScreen Grid {
        grid-rows: auto 1fr auto 1fr auto;
        width: 70;
        height: auto;
        border: round $accent;
        padding: 1 2;
    }
    OverridesScreen #packages-input, OverridesScreen #version-input {
        border: round $border;
    }
    OverridesScreen #overrides-help, OverridesScreen #overrides-title {
        color: $text-muted;
    }
    """

    def __init__(self, repo_path: str) -> None:
        super().__init__()
        self.repo_path = repo_path

    def compose(self) -> ComposeResult:
        with Grid():
            yield Static(
                "Fijar dependencias transitivas con overrides\n"
                "Paquetes separados por coma (ej: paquete-a,paquete-b)\n"
                "Solo dependencias transitivas (no directas).",
                id="overrides-title",
            )
            yield Input(placeholder="paquete-a,paquete-b", id="packages-input")
            yield Static("Versión exacta (X.Y.Z):", id="version-label")
            yield Input(placeholder="1.2.3", id="version-input")
            yield Static(
                "enter: aplicar · tab: cambiar campo · esc: cancelar",
                id="overrides-help",
            )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.apply_overrides()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def apply_overrides(self) -> None:
        packages_field = self.query_one("#packages-input", Input)
        version_field = self.query_one("#version-input", Input)
        packages = packages_field.value.split(",")
        try:
            package_path = Path(self.repo_path) / "package.json"
            package_json = json.loads(package_path.read_text())
            updated = apply_overrides(
                package_json,
                packages,
                version_field.value,
                direct_dependencies=direct_dependencies_of(package_json),
            )
            package_path.write_text(json.dumps(updated, indent=2) + "\n")
        except (ValueError, json.JSONDecodeError, OSError) as error:
            self.app.notify(str(error), title="Error", severity="error")
            return
        pinned = ", ".join(
            f"{name}@{updated['overrides'][name]}" for name in updated["overrides"]
        )
        self.dismiss(None)
        self.app.call_later(
            self._apply_overrides_with_install,
            f"Overrides actualizados en package.json:\n  • {pinned}",
        )

    def _apply_overrides_with_install(self, summary: str) -> None:
        """Run npm install so the overrides take effect on node_modules."""
        screen = self.app.screen
        if isinstance(screen, RepoActions):
            screen.run_action(
                Action(
                    label="Fijar dependencias (npm overrides)",
                    required_binary="npm",
                    runner=lambda repo_path: (
                        f"{summary}\n\n"
                        + npm_run(repo_path, ["install", "--no-audit", "--no-fund"])
                        + "\nnpm install completado: overrides aplicados."
                    ),
                )
            )

    def _report_result(self, text: str) -> None:
        screen = self.app.screen
        if isinstance(screen, RepoActions):
            screen._write_output(text)


ACTIONS_REGISTRY: dict[str, Action] = {
    # Git actions first
    "Limpiar repositorio (git)": Action(
        label="Limpiar repositorio (git)",
        required_binary="git",
        runner=run_git_clean,
        needs_confirmation=True,
    ),
    "Crear branch feature (git)": Action(
        label="Crear branch feature (git)",
        required_binary="git",
        runner=run_git_create_feature_branch,
    ),
    "Commitear remediación (git)": Action(
        label="Commitear remediación (git)",
        required_binary="git",
        runner=run_git_commit_remediation,
        needs_confirmation=True,
    ),
    # npm actions
    "Ver vulnerabilidades (npm audit)": Action(
        label="Ver vulnerabilidades (npm audit)",
        required_binary="npm",
        runner=run_npm_audit,
    ),
    "Corregir vulnerabilidades (npm audit fix)": Action(
        label="Corregir vulnerabilidades (npm audit fix)",
        required_binary="npm",
        runner=run_npm_audit_fix_safe,
    ),
    "Forzar corrección (npm audit fix --force)": Action(
        label="Forzar corrección (npm audit fix --force)",
        required_binary="npm",
        runner=run_npm_audit_fix_force,
        needs_confirmation=True,
    ),
    "Fijar dependencias (npm overrides)": Action(
        label="Fijar dependencias (npm overrides)",
        required_binary="npm",
        runner=lambda repo_path: "",
        opens_modal=True,
    ),
}
