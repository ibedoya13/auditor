"""Entry point for the auditor app."""

from auditor.app import AuditorApp


def main() -> None:
    AuditorApp().run()


if __name__ == "__main__":
    main()
