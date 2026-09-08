"""Tests for the directory browser screen of the auditor app."""

import asyncio
from pathlib import Path

from textual.widgets import ListView

from auditor.app import AuditorApp, DirectoryBrowser


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
            assert app.screen.path == tmp_path / "repos"

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
            assert app.screen.path == parent

    asyncio.run(run())


def test_backspace_at_filesystem_root_stays_put() -> None:
    """Backspace at the filesystem root does not move the browser."""
    app = AuditorApp(start_path=Path("/"))

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("backspace")
            assert app.screen.path == Path("/")

    asyncio.run(run())


def test_select_exits_app_with_current_path(tmp_path: Path) -> None:
    """Pressing 's' dismisses the browser and exits the app with the chosen path."""
    app = AuditorApp(start_path=tmp_path)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")

    asyncio.run(run())
    assert app.return_value == tmp_path
