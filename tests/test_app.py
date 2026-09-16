"""Tests for the directory browser screen of the auditor app."""

import asyncio
from pathlib import Path

import pytest
from textual.widgets import ListView

from auditor.app import AuditorApp, DirectoryBrowser
from auditor.runtime import REQUIRED_BINARIES, missing_binaries


def test_app_opens_browser_at_user_home() -> None:
    """On startup the app pushes the directory browser rooted at the user's home."""
    app = AuditorApp()

    async def run() -> None:
        async with app.run_test():
            browser = app.screen
            assert isinstance(browser, DirectoryBrowser)
            assert browser.path == Path.home()

    asyncio.run(run())


def test_browser_lists_only_visible_directories(tmp_path: Path) -> None:
    """The listing shows only non-hidden subdirectories of the current path."""
    (tmp_path / "repo-b").mkdir()
    (tmp_path / "repo-a").mkdir()
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / ".hidden-dir").mkdir()
    app = AuditorApp(start_path=tmp_path)

    async def run() -> None:
        async with app.run_test():
            listing = app.screen.query_one("#directory-list", ListView)
            names = [
                str(child.children[0].render())
                for child in listing.children
            ]
            assert names == ["repo-a", "repo-b"]

    asyncio.run(run())


def test_enter_opens_highlighted_directory(tmp_path: Path) -> None:
    """Pressing enter moves into the highlighted directory."""
    (tmp_path / "repos").mkdir()
    app = AuditorApp(start_path=tmp_path)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            assert browser_of(app).path == tmp_path / "repos"

    asyncio.run(run())


def test_backspace_returns_to_parent_directory(tmp_path: Path) -> None:
    """Pressing backspace moves to the parent directory."""
    parent = tmp_path / "workspace"
    child = parent / "my-repo"
    child.mkdir(parents=True)
    app = AuditorApp(start_path=child)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("backspace")
            assert browser_of(app).path == parent

    asyncio.run(run())


def test_backspace_at_filesystem_root_stays_put() -> None:
    """Backspace at the filesystem root does not move the browser."""
    app = AuditorApp(start_path=Path("/"))

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("backspace")
            assert browser_of(app).path == Path("/")

    asyncio.run(run())


def test_select_exits_app_with_valid_repo(tmp_path: Path) -> None:
    """Pressing 's' exits the app with the chosen path if it is a valid repo."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "package.json").write_text("{}")
    app = AuditorApp(start_path=tmp_path)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")

    asyncio.run(run())
    assert app.return_value == tmp_path


def test_select_stays_and_warns_when_not_a_git_repo(tmp_path: Path) -> None:
    """Without .git the browser stays open and warns the user."""
    (tmp_path / "package.json").write_text("{}")
    app = AuditorApp(start_path=tmp_path)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, DirectoryBrowser)
            assert "git" in warning_text(app).lower()

    asyncio.run(run())


def test_select_stays_and_warns_when_package_json_missing(tmp_path: Path) -> None:
    """Without package.json the browser stays open and warns the user."""
    (tmp_path / ".git").mkdir()
    app = AuditorApp(start_path=tmp_path)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, DirectoryBrowser)
            assert "package.json" in warning_text(app).lower()

    asyncio.run(run())


def test_select_warns_about_every_missing_requirement(tmp_path: Path) -> None:
    """A folder with neither .git nor package.json reports both problems."""
    app = AuditorApp(start_path=tmp_path)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            warning = warning_text(app).lower()
            assert "git" in warning and "package.json" in warning

    asyncio.run(run())


def test_navigating_clears_the_validation_warning(tmp_path: Path) -> None:
    """Moving to another folder clears a previously shown warning."""
    app = AuditorApp(start_path=tmp_path)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            assert warning_text(app)
            browser = app.screen
            assert isinstance(browser, DirectoryBrowser)
            browser.path = tmp_path.parent
            browser.refresh_listing()
            await pilot.pause()
            assert warning_text(app) == ""

    asyncio.run(run())


def test_missing_binaries_reports_nothing_when_all_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With every required binary on PATH, no missing binaries are reported."""
    monkeypatch.setattr(
        "auditor.runtime.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )
    assert missing_binaries() == []


def test_missing_binaries_reports_the_absent_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the binaries that cannot be found on PATH are reported."""
    monkeypatch.setattr(
        "auditor.runtime.shutil.which",
        lambda name: None if name in {"npm", "npx"} else f"/usr/bin/{name}",
    )
    assert missing_binaries() == ["npm", "npx"]


def test_missing_binaries_covers_git_npm_and_npx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When nothing is on PATH, every required binary is reported."""
    monkeypatch.setattr("auditor.runtime.shutil.which", lambda name: None)
    assert missing_binaries() == list(REQUIRED_BINARIES)


def test_app_exits_with_error_when_binaries_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app refuses to start and reports the missing runtime dependencies."""
    monkeypatch.setattr("auditor.runtime.shutil.which", lambda name: None)
    app = AuditorApp()

    async def run() -> None:
        async with app.run_test():
            assert not isinstance(app.screen, DirectoryBrowser)

    asyncio.run(run())
    assert app.return_value is None


def browser_of(app: AuditorApp) -> "DirectoryBrowser":
    screen = app.screen
    assert isinstance(screen, DirectoryBrowser)
    return screen


def warning_text(app: AuditorApp) -> str:
    from textual.widgets import Static

    status = app.screen.query_one("#status", Static)
    return str(status.render())
