"""Runtime dependency checks for the auditor app."""

import shutil

REQUIRED_BINARIES = ("git", "npm", "npx")


def missing_binaries() -> list[str]:
    """Return the required binaries that are not available on the system."""
    return [name for name in REQUIRED_BINARIES if shutil.which(name) is None]
