"""Tests for the repository actions screen."""

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

from textual.widgets import LoadingIndicator, Static

from auditor.actions import ACTIONS_REGISTRY, ConfirmScreen, RepoActions
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
    assert "npm audit" in ACTIONS_REGISTRY
    assert ACTIONS_REGISTRY["npm audit"].required_binary == "npm"


def test_registry_offers_both_fix_actions() -> None:
    """The registry includes the safe and forced npm audit fix actions."""
    assert "npm audit fix" in ACTIONS_REGISTRY
    assert "npm audit fix --force" in ACTIONS_REGISTRY
    assert ACTIONS_REGISTRY["npm audit fix"].required_binary == "npm"
    assert ACTIONS_REGISTRY["npm audit fix --force"].required_binary == "npm"


def test_actions_screen_lists_fix_actions(tmp_path: Path) -> None:
    """With npm on the host, both fix actions appear among the actions."""
    repo = make_valid_repo(tmp_path)
    app = AuditorApp(start_path=repo)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            labels = actions_labels(app)
            assert any(label.strip() == "npm audit fix" for label in labels)
            assert any("npm audit fix --force" in label for label in labels)

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
            await pilot.press("down")  # highlight 'npm audit fix'
            await pilot.press("enter")
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
            await pilot.press("down")  # 'npm audit fix'
            await pilot.press("down")  # 'npm audit fix --force'
            await pilot.press("enter")
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
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")  # ask confirmation
            await pilot.pause()
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
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")  # ask confirmation
            await pilot.pause()
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
            await pilot.press("down")  # 'npm audit fix' (safe)
            await pilot.press("enter")
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
            await pilot.press("enter")  # run npm audit
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
            await pilot.press("enter")
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
            await pilot.press("enter")  # run the first (highlighted) action
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
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
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
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
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
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("s")  # confirm
            await wait_for_idle(app)
            text = actions_output(app).lower()
            assert "qs" in text and "critical" in text

    with patch(
        "auditor.actions.npm_run",
        return_value=SAMPLE_FORCE_FIX_OUTPUT,
    ):
        asyncio.run(run())
