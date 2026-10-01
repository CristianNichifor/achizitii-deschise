"""Run the bounded, existing fixture regressions without fetching a dataset.

Run from the repository root after installing development dependencies.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "tests/test_ocds.py",
    "tests/test_normalize.py",
    "tests/test_govdata.py",
    "tests/test_indicators.py",
    "tests/test_price_archive.py",
]

if __name__ == "__main__":
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q", *TESTS], cwd=ROOT))
