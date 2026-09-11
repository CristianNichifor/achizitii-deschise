"""A workflow that runs the tests must first fetch what the tests read.

The bundle moved out of git and into a release asset, and several tests read it directly —
`test_price_drilldown` opens `site/data/manifest.json` unconditionally, which is deliberate:
it used to carry a `skipif` that quietly became a pass. So every workflow that runs pytest has
to fetch the bundle first, and three did while two did not.

It was not caught by review, three times running. The first pass wired `pages.yml` and
`tests.yml` and missed `browser.yml`; fixing that one missed `daily.yml`, which failed on its
first scheduled run afterwards; and `bulk.yml` is dispatch-only, so it had not run since and
was still waiting to fail for whoever reached for it next. Each time the omission was invisible
in the diff, because the evidence is in a file the change does not touch.

So it is a test rather than a habit. Reading the workflow directory is the only way to ask the
question that was actually being got wrong: *is there a workflow that runs pytest and does not
fetch the bundle first* — including one added next year by someone who never read this.
"""

from __future__ import annotations

import re
from pathlib import Path

RADACINA = Path(__file__).resolve().parent.parent
FLUXURI = RADACINA / ".github" / "workflows"

# Anchored to a line that is not a comment, because the comments in these workflows now talk
# about pytest and about fetching, and a pattern that reads them would find what it was looking
# for in the prose explaining the bug. The first version of this file did worse: it matched
# nothing at all, so every workflow was skipped and the test passed on the broken tree it was
# written to catch. It is only here because deleting the fetch step and re-running still said
# "3 passed" — which is the whole argument for checking that a check can fail.
_PYTEST = re.compile(r"^[^#\n]*\bpytest\b", re.M)
_FETCH = re.compile(r"^[^#\n]*fetch_bundle\.sh", re.M)


def _fluxuri() -> list[Path]:
    return sorted(FLUXURI.glob("*.yml"))


def test_there_are_workflows_to_check():
    """A glob that matches nothing passes every assertion below it."""
    assert _fluxuri(), "niciun workflow în .github/workflows"


def test_every_workflow_that_runs_pytest_fetches_the_bundle_first():
    vinovate = []
    for cale in _fluxuri():
        text = cale.read_text(encoding="utf-8")
        if not _PYTEST.search(text):
            continue
        potrivire = _FETCH.search(text)
        if not potrivire:
            vinovate.append(f"{cale.name}: rulează pytest fără să aducă bundle-ul")
            continue
        # Fetching after the tests have run is the same failure with extra steps.
        if potrivire.start() > _PYTEST.search(text).start():
            vinovate.append(f"{cale.name}: aduce bundle-ul după ce rulează pytest")
    assert not vinovate, "\n".join(vinovate)


def test_the_fetch_script_the_workflows_name_exists():
    """The assertion above is satisfied by the string, not by the file. Check the file."""
    script = RADACINA / "scripts" / "fetch_bundle.sh"
    assert script.is_file(), "scripts/fetch_bundle.sh lipsește, dar workflow-urile îl cheamă"
