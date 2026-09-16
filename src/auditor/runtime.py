"""Runtime dependency checks for the auditor app."""

import shutil

REQUIRED_BINARIES = ("npm",)


def missing_binaries() -> list[str]:
    """Return the required binaries that are not available on the system."""
    return [name for name in REQUIRED_BINARIES if shutil.which(name) is None]


def binary_available(name: str) -> bool:
    """Return True if the given binary can be found on the system."""
    return shutil.which(name) is not None
