"""auditor: TUI to automate npm vulnerability remediation in Node.js repos."""

from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from auditor.actions import RepoActions


class DirectoryBrowser(Screen[Path]):
    """Screen to navigate the filesystem and pick the repositories folder."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("s", "select_folder", "Seleccionar carpeta"),
        ("backspace", "go_up", "Carpeta padre"),
        ("q", "quit_app", "Salir"),
    ]

    CSS = """
    ListItem.-auditor-selected {
        background: $accent;
    }
    ListItem.-auditor-selected > Label {
        color: $text;
        text-style: bold;
    }
    """

    def __init__(self, start_path: Path | None = None) -> None:
        super().__init__()
        self.path: Path = start_path or Path.home()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(str(self.path), id="current-path")
        yield ListView(id="directory-list")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_listing()

    def refresh_listing(self) -> None:
        """Rebuild the list with the visible subdirectories of the current path."""
        listing = self.query_one("#directory-list", ListView)
        listing.clear()
        self._entries: list[Path] = sorted(
            entry
            for entry in self.path.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        )
        for directory in self._entries:
            listing.append(ListItem(Label(directory.name)))
        listing.index = 0  # clear() resets the cursor; restore it to the top
        self.query_one("#current-path", Static).update(str(self.path))
        self.query_one("#status", Static).update("")
        self._sync_selection_class(listing)

    def _sync_selection_class(self, listing: ListView) -> None:
        """Mirror the cursor position into a visible selection class."""
        for index, item in enumerate(listing.children):
            item.set_class(index == listing.index, "-auditor-selected")

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Keep the visible selection in sync when the cursor moves."""
        self._sync_selection_class(event.list_view)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Move into the highlighted directory when an entry is selected."""
        listing = self.query_one("#directory-list", ListView)
        index = listing.index
        if index is not None and 0 <= index < len(self._entries):
            self.path = self._entries[index]
            self.refresh_listing()

    def action_go_up(self) -> None:
        parent = self.path.parent
        if parent != self.path:
            self.path = parent
            self.refresh_listing()

    def action_select_folder(self) -> None:
        """Validate the repo structure, then show its available actions."""
        problems: list[str] = []
        if not (self.path / ".git").exists():
            problems.append("No es un repositorio de git (falta la carpeta .git)")
        if not (self.path / "package.json").is_file():
            problems.append("No tiene package.json para gestionar dependencias con npm")
        if problems:
            self.query_one("#status", Static).update(" | ".join(problems))
            return
        self.app.push_screen(RepoActions(repo_path=str(self.path)))

    def action_quit_app(self) -> None:
        self.app.exit()


class AuditorApp(App[Path]):
    """The auditor application, starting at the directory browser screen."""

    def __init__(self, start_path: Path | None = None) -> None:
        super().__init__()
        self._start_path = start_path

    def on_mount(self) -> None:
        self.push_screen(DirectoryBrowser(start_path=self._start_path))


if __name__ == "__main__":
    AuditorApp().run()
