"""Console entry point for the auditor command."""

from auditor.app import AuditorApp


def main() -> None:
    AuditorApp().run()
