"""Tests for the repository actions screen."""

import asyncio
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Static

from auditor.app import ACTIONS_REGISTRY, AuditorApp
from auditor.actions import RepoActions


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
            labels = [
                str(item.children[0].render()) for item in listing.children
            ]
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
                labels = [
                    str(item.children[0].render())
                    for item in listing.children
                ]
                assert not any("npm audit" in label for label in labels)

    asyncio.run(run())


def test_actions_registry_keys_actions_by_required_binary() -> None:
    """Each action declares which binary it needs, for host filtering."""
    assert "npm audit" in ACTIONS_REGISTRY
    assert ACTIONS_REGISTRY["npm audit"].required_binary == "npm"


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
        "auditor.actions.run_npm_audit",
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
