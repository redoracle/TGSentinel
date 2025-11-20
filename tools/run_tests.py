"""Helper script to run pytest without spurious argparse warnings."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

# Ensure project root (and src) are on sys.path when invoked from anywhere.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in __import__("sys").path:
    __import__("sys").path.insert(1, str(PROJECT_ROOT / "src"))

warnings.filterwarnings(
    "ignore",
    message=r"Do not expect file_or_dir in Namespace",
    category=UserWarning,
    module="argparse",
)


def main() -> int:
    return pytest.main(["tests"])


if __name__ == "__main__":
    raise SystemExit(main())
