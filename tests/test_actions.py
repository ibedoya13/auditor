"""Tests for the repository actions screen."""

import asyncio
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import Input, ListView, LoadingIndicator, Static

from auditor.actions import (
    ACTIONS_REGISTRY,
    ConfirmScreen,
    OverridesScreen,
    RepoActions,
    apply_overrides,
    direct_dependencies_of,
    feature_branch_name,
    remediation_commit_message,
    run_git_commit_remediation,
    run_git_create_feature_branch,
)
from auditor.app import AuditorApp, DirectoryBrowser


def make_valid_repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / "package.json").write_text("{}")
    return tmp_path


def press_s_and_wait(app: AuditorApp, tmp_path: Path) -> None:
    """Start the app on tmp_path and press 's' until the actions screen shows."""

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()

    asyncio.run(run())


def test_selecting_valid_repo_opens_actions_screen(tmp_path: Path) -> None:
    """After selecting a valid repo the actions screen is pushed."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, RepoActions)
            assert app.screen.repo_path == str(repo)

    asyncio.run(run())


def test_actions_screen_lists_npm_audit_when_npm_available(
    tmp_path: Path,
) -> None:
    """With npm on the host, 'npm audit' appears among the actions."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            listing = app.screen.query_one("#actions-list")
            labels = [str(item.children[0].render()) for item in listing.children]
            assert any("npm audit" in label for label in labels)

    asyncio.run(run())


def test_actions_screen_omits_npm_audit_without_npm(tmp_path: Path) -> None:
    """Without npm on the host, the npm audit action is not offered."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        with patch("auditor.runtime.shutil.which", return_value=None):
            async with app.run_test() as pilot:
                await pilot.press("s")
                await pilot.pause()
                listing = app.screen.query_one("#actions-list")
                labels = [str(item.children[0].render()) for item in listing.children]
                assert not any("npm audit" in label for label in labels)

    asyncio.run(run())


def test_actions_registry_keys_actions_by_required_binary() -> None:
    """Each action declares which binary it needs, for host filtering."""
    assert "Ver vulnerabilidades (npm audit)" in ACTIONS_REGISTRY
    assert ACTIONS_REGISTRY["Ver vulnerabilidades (npm audit)"].required_binary == "npm"


def test_registry_offers_both_fix_actions() -> None:
    """The registry includes the safe and forced npm audit fix actions."""
    assert "Corregir vulnerabilidades (npm audit fix)" in ACTIONS_REGISTRY
    assert "Forzar corrección (npm audit fix --force)" in ACTIONS_REGISTRY
    assert (
        (ACTIONS_REGISTRY["Corregir vulnerabilidades (npm audit fix)"]).required_binary
        == "npm"
    )
    assert (
        (ACTIONS_REGISTRY["Forzar corrección (npm audit fix --force)"]).required_binary
        == "npm"
    )


def test_actions_screen_lists_fix_actions(tmp_path: Path) -> None:
    """With npm on the host, both fix actions appear among the actions."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            labels = actions_labels(app)
            assert any(
                "Corregir vulnerabilidades (npm audit fix)" in label for label in labels
            )
            assert any(
                "Forzar corrección (npm audit fix --force)" in label for label in labels
            )

    asyncio.run(run())


def test_git_actions_are_listed_before_npm_actions(tmp_path: Path) -> None:
    """Git actions come first in the actions list, npm actions after."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            labels = actions_labels(app)
            first_git = next(i for i, label in enumerate(labels) if "(git)" in label)
            first_npm = next(i for i, label in enumerate(labels) if "npm" in label)
            assert first_git < first_npm
            # git actions are exactly the first two entries
            assert all("(git)" in label for label in labels[:2])

    asyncio.run(run())


def test_git_clean_action_requires_git_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The git clean action only appears when git is available on the host."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            labels = actions_labels(app)
            assert any("Limpiar repositorio" in label for label in labels)

    asyncio.run(run())

    async def run_without_git() -> None:
        with patch("auditor.runtime.shutil.which", return_value=None):
            app = AuditorApp(start_path=repo)
            async with app.run_test() as pilot:
                await pilot.press("s")
                await pilot.pause()
                labels = actions_labels(app)
                assert not any("Limpiar repositorio" in label for label in labels)

    asyncio.run(run_without_git())


def test_git_clean_resets_staging_and_stashes_pending_changes(
    tmp_path: Path,
) -> None:
    """The git clean action runs git reset and git stash -u on the repo."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            labels = actions_labels(app)
            git_index = next(i for i, label in enumerate(labels) if "Limpiar" in label)
            for _ in range(git_index):
                await pilot.press("down")
            await pilot.press("enter")  # confirmation dialog
            await pilot.pause()
            await pilot.press("s")  # confirm
            await wait_for_idle(app)
            text = actions_output(app).lower()
            assert "staging" in text and "stash" in text

    with patch("auditor.actions.git_run") as git_mock:
        git_mock.side_effect = [
            "Unstaged files after reset",
            "Saved working directory and index state WIP on main",
        ]
        asyncio.run(run())
        commands = [call.args[1] for call in git_mock.call_args_list]
        assert commands[0] == ["reset"]
        assert commands[1] == ["stash", "-u"]


def test_git_clean_action_requires_confirmation(tmp_path: Path) -> None:
    """Cleaning the repo asks for confirmation before touching anything."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            labels = actions_labels(app)
            git_index = next(i for i, label in enumerate(labels) if "Limpiar" in label)
            for _ in range(git_index):
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("escape")  # cancel
            await pilot.pause()
            assert isinstance(app.screen, RepoActions)
            assert actions_output(app) == ""

    with patch("auditor.actions.git_run") as git_mock:
        asyncio.run(run())
        git_mock.assert_not_called()


def test_feature_branch_name_uses_digits_only_timestamp() -> None:
    """The generated branch name is feature/{digits} with no separators."""
    name = feature_branch_name()
    assert re.fullmatch(r"feature/\d{14}", name)


def test_feature_branch_is_created_from_develop() -> None:
    """When develop exists the branch is created from it."""
    calls: list[list[str]] = []

    def fake_git_run(repo_path: str, args: list[str]) -> str:
        calls.append(args)
        if args[:2] == ["rev-parse", "--verify"]:
            if args[2] == "develop":
                return "develop\n"
            raise RuntimeError(f"fatal: '{args[2]}' not found")
        if args[0] == "checkout":
            return f"Switched to a new branch '{args[2]}'"
        return ""

    with patch("auditor.actions.git_run", side_effect=fake_git_run):
        output = run_git_create_feature_branch("/repo")

    assert calls[0] == ["rev-parse", "--verify", "develop"]
    assert calls[1][0] == "checkout"
    assert calls[1][1] == "-b"
    assert calls[1][3] == "develop"
    assert re.fullmatch(r"feature/\d{14}", calls[1][2])
    assert "creado" in output


def test_feature_branch_falls_back_to_trunk() -> None:
    """Without develop, the branch is created from trunk."""
    calls: list[list[str]] = []

    def fake_git_run(repo_path: str, args: list[str]) -> str:
        calls.append(args)
        if args[:2] == ["rev-parse", "--verify"]:
            if args[2] == "trunk":
                return "trunk\n"
            raise RuntimeError(f"fatal: '{args[2]}' not found")
        if args[0] == "checkout":
            return f"Switched to a new branch '{args[2]}'"
        return ""

    with patch("auditor.actions.git_run", side_effect=fake_git_run):
        run_git_create_feature_branch("/repo")

    assert calls[0] == ["rev-parse", "--verify", "develop"]
    assert calls[1] == ["rev-parse", "--verify", "trunk"]
    assert calls[2][3] == "trunk"


def test_feature_branch_errors_without_develop_or_trunk() -> None:
    """When neither develop nor trunk exists the action reports the error."""
    with (
        patch(
            "auditor.actions.git_run",
            side_effect=RuntimeError("fatal: not found"),
        ),
        pytest.raises(RuntimeError, match="develop.*trunk|trunk.*develop"),
    ):
        run_git_create_feature_branch("/repo")


def test_feature_branch_action_appears_and_runs(tmp_path: Path) -> None:
    """The action shows up with git available and runs without confirmation."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            labels = actions_labels(app)
            branch_index = next(
                i for i, label in enumerate(labels) if "feature" in label
            )
            for _ in range(branch_index):
                await pilot.press("down")
            await pilot.press("enter")  # no confirmation dialog expected
            await pilot.pause()
            assert isinstance(app.screen, RepoActions)
            await wait_for_idle(app)
            text = actions_output(app)
            assert "creado" in text
            assert "develop" in text

    with patch("auditor.actions.git_run") as git_mock:
        git_mock.side_effect = [
            "develop\n",
            "Switched to a new branch 'feature/20260917000000'",
        ]
        asyncio.run(run())


def test_running_npm_audit_fix_reports_applied_and_remaining(
    tmp_path: Path,
) -> None:
    """The safe fix action shows what it fixed and what still remains."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Corregir vulnerabilidades")
            await pilot.pause()
            output = app.screen.query_one("#action-output", Static)
            text = str(output.render()).lower()
            assert "corregidas" in text  # applied fixes section
            assert "restantes" in text  # remaining vulnerabilities section
            assert "lodash" in text  # remaining vuln is reported

    with patch(
        "auditor.actions.run_npm_audit_fix",
        return_value=SAMPLE_FIX_OUTPUT,
    ):
        asyncio.run(run())


def test_forced_fix_requires_confirmation_before_running(
    tmp_path: Path,
) -> None:
    """The forced fix asks for confirmation instead of running right away."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Forzar corrección")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            # nothing ran yet
            assert actions_output(app) == ""

    asyncio.run(run())


def test_confirming_runs_the_forced_fix(tmp_path: Path) -> None:
    """Pressing 's' on the confirmation dialog runs the forced fix."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Forzar corrección")
            await pilot.press("s")  # confirm
            await pilot.pause()
            text = actions_output(app).lower()
            assert "corregidas" in text

    with patch(
        "auditor.actions.npm_run",
        return_value=SAMPLE_FIX_OUTPUT,
    ):
        asyncio.run(run())


def test_cancelling_keeps_actions_screen_without_running(
    tmp_path: Path,
) -> None:
    """Pressing 'escape' on the dialog goes back without running anything."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Forzar corrección")
            await pilot.press("escape")  # cancel
            await pilot.pause()
            assert isinstance(app.screen, RepoActions)
            output = app.screen.query_one("#action-output", Static)
            assert str(output.render()) == ""

    with patch("auditor.actions.npm_run") as run_mock:
        asyncio.run(run())
        run_mock.assert_not_called()


def test_safe_fix_still_runs_without_confirmation(tmp_path: Path) -> None:
    """The safe fix action runs immediately, no dialog in between."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Corregir vulnerabilidades")
            await pilot.pause()
            assert isinstance(app.screen, RepoActions)
            output = app.screen.query_one("#action-output", Static)
            text = str(output.render()).lower()
            assert "corregidas" in text

    with patch(
        "auditor.actions.npm_run",
        return_value=SAMPLE_FIX_OUTPUT,
    ):
        asyncio.run(run())


def actions_labels(app: AuditorApp) -> list[str]:
    listing = app.screen.query_one("#actions-list")
    return [str(item.children[0].render()) for item in listing.children]


async def select_action(app: AuditorApp, pilot, needle: str) -> None:
    """Highlight the first action whose label contains needle, then enter."""
    labels = actions_labels(app)
    index = next(i for i, label in enumerate(labels) if needle in label)
    listing = app.screen.query_one("#actions-list", ListView)
    listing.index = index
    await pilot.pause()
    await pilot.press("enter")


def test_spinner_is_hidden_when_the_actions_screen_opens(
    tmp_path: Path,
) -> None:
    """No spinner until the user actually runs a command."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            spinner = app.screen.query_one("#action-spinner", LoadingIndicator)
            assert not spinner.display

    asyncio.run(run())


def test_escape_from_actions_returns_to_directory_browser(
    tmp_path: Path,
) -> None:
    """Pressing escape on the actions screen goes back to the folder browser."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, RepoActions)
            await pilot.press("escape")
            await pilot.pause()
            browser = app.screen
            assert isinstance(browser, DirectoryBrowser)
            # the browser is back with the same listing state
            assert browser.path == repo

    asyncio.run(run())


def test_running_action_shows_spinner_and_finishes_with_report(
    tmp_path: Path,
) -> None:
    """While the command runs a spinner shows; when done the report shows."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)
    release = threading.Event()

    def slow_npm_run(repo_path: str, args: list[str]) -> str:
        release.wait(timeout=5)  # test controls when the command finishes
        return SAMPLE_AUDIT_OUTPUT

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Ver vulnerabilidades")
            await pilot.pause()
            # spinner visible while the command is still running
            spinner = app.screen.query_one("#action-spinner", LoadingIndicator)
            assert spinner.display
            release.set()
            await wait_for_idle(app)
            # spinner hidden and report written once the command finished
            assert not spinner.display
            text = actions_output(app).lower()
            assert "qs" in text and "critical" in text

    with patch(
        "auditor.actions.npm_run",
        side_effect=slow_npm_run,
    ):
        asyncio.run(run())


def test_completion_notifies_the_user(tmp_path: Path) -> None:
    """When the command finishes the app notifies the user."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Ver vulnerabilidades")
            await wait_for_idle(app)

    with (
        patch("auditor.actions.npm_run", return_value=SAMPLE_AUDIT_OUTPUT),
        patch.object(app, "notify") as notify_mock,
    ):
        asyncio.run(run())
        notify_mock.assert_called()


def actions_output(app: AuditorApp) -> str:
    """Read the actions screen output, even under a modal dialog."""
    for screen in reversed(app.screen_stack):
        if isinstance(screen, RepoActions):
            return str(screen.query_one("#action-output", Static).render())
    raise AssertionError("RepoActions screen is not on the stack")


async def wait_for_idle(app: AuditorApp) -> None:
    """Wait until the app has no active workers (command finished)."""
    while app.workers:
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.1)  # let the UI settle after the worker ends


def test_running_npm_audit_shows_vulnerability_report(tmp_path: Path) -> None:
    """Selecting the npm audit action runs it and shows filtered results."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Ver vulnerabilidades")
            await pilot.pause()
            report = app.screen.query_one("#action-output", Static)
            text = str(report.render()).lower()
            assert "qs" in text and "critical" in text

    with patch(
        "auditor.actions.npm_run",
        return_value=SAMPLE_AUDIT_OUTPUT,
    ):
        asyncio.run(run())


SAMPLE_AUDIT_OUTPUT = """
{
  "vulnerabilities": {
    "qs": {
      "name": "qs",
      "severity": "critical",
      "range": "<6.9.7",
      "via": [{"title": "Denial of Service"}],
      "fixAvailable": false
    }
  }
}
"""

SAMPLE_FIX_OUTPUT = """
{
  "added": 12,
  "removed": 0,
  "changed": 3,
  "audited": 95,
  "vulnerabilities": {
    "lodash": {
      "name": "lodash",
      "severity": "high",
      "range": "<4.17.21",
      "via": [{"title": "Command Injection"}],
      "fixAvailable": false
    }
  }
}
"""

# Real shape observed from `npm audit fix --force --json`: the changes are
# reported in the `change` list (from -> to) even when added/removed/changed
# are all 0, and the re-audit lands under the nested `audit` key.
SAMPLE_FORCE_FIX_OUTPUT = """
{
  "added": 0,
  "removed": 0,
  "changed": 0,
  "audited": 3,
  "change": [
    {
      "from": {"name": "minimist", "version": "1.2.0"},
      "to": {"name": "minimist", "version": "1.2.8"}
    },
    {
      "from": {"name": "lodash", "version": "4.17.15"},
      "to": {"name": "lodash", "version": "4.18.1"}
    }
  ],
  "audit": {
    "vulnerabilities": {
      "qs": {
        "name": "qs",
        "severity": "critical",
        "range": "<6.9.7",
        "via": [{"title": "Denial of Service"}],
        "fixAvailable": false
      }
    }
  }
}
"""


SAMPLE_ERROR_OUTPUT = """
{
  "error": {
    "code": "ETARGET",
    "summary": "No matching version found for multer@2.4.0.",
    "detail": "Package version that doesn't exist."
  }
}
"""


def test_fix_report_shows_npm_error_instead_of_false_success(
    tmp_path: Path,
) -> None:
    """When npm cannot resolve the fix, the error is shown, not a fake report."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Forzar corrección")
            await pilot.press("s")  # confirm
            await wait_for_idle(app)
            text = actions_output(app)
            assert "multer@2.4.0" in text
            assert "no se encontraron vulnerabilidades" not in text.lower()

    with patch(
        "auditor.actions.npm_run",
        return_value=SAMPLE_ERROR_OUTPUT,
    ):
        asyncio.run(run())


def test_fix_report_shows_changes_even_when_counters_are_zero(
    tmp_path: Path,
) -> None:
    """Fixes reported in the `change` list are shown, not just the counters."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Forzar corrección")
            await pilot.press("s")  # confirm
            await wait_for_idle(app)
            text = actions_output(app)
            assert "minimist" in text and "1.2.0" in text
            assert "lodash" in text and "4.17.15" in text
            assert "→ 1.2.8" in text
            assert "→ 4.18.1" in text

    with patch(
        "auditor.actions.npm_run",
        return_value=SAMPLE_FORCE_FIX_OUTPUT,
    ):
        asyncio.run(run())


def test_fix_report_reads_reaudit_from_nested_audit_key(
    tmp_path: Path,
) -> None:
    """The post-fix re-audit under the nested `audit` key is reported."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Forzar corrección")
            await pilot.press("s")  # confirm
            await wait_for_idle(app)
            text = actions_output(app).lower()
            assert "qs" in text and "critical" in text

    with patch(
        "auditor.actions.npm_run",
        return_value=SAMPLE_FORCE_FIX_OUTPUT,
    ):
        asyncio.run(run())


def test_overrides_action_updates_existing_override() -> None:
    """An existing override for the package is updated, not duplicated."""
    package_json = {
        "name": "demo",
        "overrides": {"paquete-a": "1.0.0"},
        "dependencies": {"paquete-a": "^1.0.0"},
    }
    result = apply_overrides(package_json, ["paquete-a"], "2.5.0")
    assert result["overrides"] == {"paquete-a": "2.5.0"}
    assert result["dependencies"] == {"paquete-a": "^1.0.0"}


def test_overrides_action_adds_new_override_to_multiple_packages() -> None:
    """A comma-separated list pins every package to the same version."""
    package_json = {"name": "demo"}
    result = apply_overrides(package_json, ["paquete-a", "paquete-b"], "2.5.0")
    assert result["overrides"] == {
        "paquete-a": "2.5.0",
        "paquete-b": "2.5.0",
    }


def test_overrides_action_preserves_other_overrides() -> None:
    """Existing overrides for other packages remain untouched."""
    package_json = {
        "overrides": {"otro": "3.1.4"},
    }
    result = apply_overrides(package_json, ["paquete-a"], "2.5.0")
    assert result["overrides"] == {"otro": "3.1.4", "paquete-a": "2.5.0"}


def test_overrides_action_rejects_empty_package_list() -> None:
    """An empty package list is rejected with an error."""
    with pytest.raises(ValueError, match="paquete"):
        apply_overrides({}, [], "2.5.0")


def test_overrides_action_rejects_invalid_version() -> None:
    """A version that is not a valid exact semver is rejected."""
    with pytest.raises(ValueError, match="versión"):
        apply_overrides({}, ["paquete-a"], "^2.5.0")


def test_overrides_action_accepts_versions_with_v_prefix() -> None:
    """A 'v' prefix on the version is accepted and normalized away."""
    result = apply_overrides({}, ["paquete-a"], "v2.5.0")
    assert result["overrides"] == {"paquete-a": "2.5.0"}


def test_overrides_action_strips_whitespace_from_package_names() -> None:
    """Spaces around comma-separated package names are ignored."""
    result = apply_overrides({}, [" paquete-a ", "paquete-b "], "2.5.0")
    assert result["overrides"] == {
        "paquete-a": "2.5.0",
        "paquete-b": "2.5.0",
    }


def test_overrides_action_rejects_direct_dependency() -> None:
    """A package declared in dependencies cannot be overridden."""
    package_json = {
        "dependencies": {"paquete-a": "^1.0.0"},
        "devDependencies": {"paquete-dev": "2.0.0"},
    }
    direct = direct_dependencies_of(package_json)
    with pytest.raises(ValueError, match="directa.*paquete-a"):
        apply_overrides(package_json, ["paquete-a"], "2.5.0", direct)
    with pytest.raises(ValueError, match="directa.*paquete-dev"):
        apply_overrides(package_json, ["paquete-dev"], "2.5.0", direct)


def test_overrides_action_rejects_direct_dependency_among_valid_ones() -> None:
    """One direct dependency in the list rejects the whole operation."""
    package_json = {"dependencies": {"paquete-b": "^1.0.0"}}
    direct = direct_dependencies_of(package_json)
    with pytest.raises(ValueError, match="paquete-b"):
        apply_overrides(package_json, ["paquete-a", "paquete-b"], "2.5.0", direct)


def test_overrides_action_allows_transitive_dependencies() -> None:
    """Packages not declared as direct dependencies can be overridden."""
    package_json = {
        "dependencies": {"paquete-a": "^1.0.0"},
        "overrides": {"transitiva": "1.0.0"},
    }
    direct = direct_dependencies_of(package_json)
    result = apply_overrides(package_json, ["transitiva", "otra"], "2.5.0", direct)
    assert result["overrides"] == {
        "transitiva": "2.5.0",
        "otra": "2.5.0",
    }


def test_direct_dependencies_collects_all_sections() -> None:
    """All dependency sections feed the direct dependency set."""
    package_json = {
        "dependencies": {"a": "1.0.0"},
        "devDependencies": {"b": "1.0.0"},
        "optionalDependencies": {"c": "1.0.0"},
        "peerDependencies": {"d": "1.0.0"},
    }
    assert direct_dependencies_of(package_json) == {"a", "b", "c", "d"}


def test_remediation_commit_message_includes_date() -> None:
    """The commit message states the vulnerability remediation and date."""
    message = remediation_commit_message(fixed=datetime(2026, 9, 17, 10, 30))
    assert message.startswith("Remediación de vulnerabilidades")
    assert "2026-09-17 10:30" in message


def test_remediation_commit_adds_and_commits() -> None:
    """The action stages all changes and commits with the message."""
    calls: list[list[str]] = []

    def fake_git_run(repo_path: str, args: list[str]) -> str:
        calls.append(args)
        if args[0] == "status":
            return " M file.txt\n"  # pending changes present
        return ""

    with (
        patch("auditor.actions.git_run", side_effect=fake_git_run),
        patch("auditor.actions.datetime") as dt_mock,
    ):
        dt_mock.now.return_value = datetime(2026, 9, 17, 10, 30)
        run_git_commit_remediation("/repo")

    assert calls[0] == ["status", "--porcelain"]  # check pending changes first
    assert calls[1] == ["add", "-A"]
    assert calls[2][0] == "commit"
    assert calls[2][1] == "-m"
    assert "Remediación de vulnerabilidades" in calls[2][2]
    assert "2026-09-17 10:30" in calls[2][2]


def test_remediation_commit_without_changes_reports_it() -> None:
    """With nothing to commit, the action says so instead of failing."""

    def fake_git_run(repo_path: str, args: list[str]) -> str:
        if args[0] == "status":
            return ""  # empty status: nothing pending
        if args[0] == "commit":
            raise RuntimeError("nothing to commit")
        return ""

    with patch("auditor.actions.git_run", side_effect=fake_git_run):
        output = run_git_commit_remediation("/repo")

    assert "sin cambios" in output.lower()


def test_remediation_commit_action_lists_and_confirms(tmp_path: Path) -> None:
    """The action appears with git and asks for confirmation before commit."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Commitear remediación")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("escape")  # cancel
            await pilot.pause()
            assert isinstance(app.screen, RepoActions)

    with patch("auditor.actions.git_run") as git_mock:
        asyncio.run(run())
        git_mock.assert_not_called()


def test_remediation_commit_action_runs_after_confirmation(
    tmp_path: Path,
) -> None:
    """Confirming the dialog stages and commits with the message."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Commitear remediación")
            await pilot.press("s")  # confirm
            await wait_for_idle(app)
            text = actions_output(app).lower()
            assert "commit" in text
            assert "remediación" in text

    with patch("auditor.actions.git_run") as git_mock:
        git_mock.side_effect = [
            "staged",  # git status
            "",  # git add -A
            "",  # git commit
        ]
        asyncio.run(run())


def test_overrides_screen_opens_when_action_selected(tmp_path: Path) -> None:
    """Selecting the overrides action opens the input modal."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Fijar dependencias")
            await pilot.pause()
            assert isinstance(app.screen, OverridesScreen)

    asyncio.run(run())


def test_overrides_screen_writes_package_json(tmp_path: Path) -> None:
    """Submitting the modal updates the repo's package.json."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            await select_action(app, pilot, "Fijar dependencias")
            await pilot.pause()
            app.screen.query_one("#packages-input", Input).value = "lodash"
            app.screen.query_one("#version-input", Input).value = "4.17.21"
            await pilot.press("enter")  # submit
            await wait_for_idle(app)
            assert isinstance(app.screen, RepoActions)
            text = actions_output(app)
            assert "lodash" in text and "4.17.21" in text
            assert "npm install completado" in text

    with patch("auditor.actions.npm_run", return_value="added 1 package") as npm_mock:
        asyncio.run(run())
        # npm install runs chained after writing the overrides
        install_call = next(
            (c for c in npm_mock.call_args_list if c.args[1][:1] == ["install"]),
            None,
        )
        assert install_call is not None
        assert "--no-audit" in install_call.args[1]

    asyncio.run(asyncio.sleep(0))
    saved = json.loads((repo / "package.json").read_text())
    assert saved["overrides"] == {"lodash": "4.17.21"}
    # existing keys are preserved (make_valid_repo only writes "{}")
    assert set(saved) == {"overrides"}
