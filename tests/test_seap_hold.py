"""No SEAP traffic on a schedule while the access question is open.

DELETE THIS FILE WHEN THE HOLD IS LIFTED. It exists to make lifting it a deliberate act
rather than something that happens because a line was edited for another reason.

Decided 2026-09-08, after a request was sent to ADR on 2026-09-07 asking what this
project may do: whether a bulk export carrying line items exists, whether an automated
client can be whitelisted, and what rate is acceptable. Until that is answered, nothing
here contacts SEAP unless a person starts it.

The hold is narrow on purpose. `daily.yml` also mirrors the RUTI meetings register
(ruti.gov.ro, seven requests, a service that has never restricted us), rebuilds the
published bundle and uploads to R2 — none of which touches SEAP. Removing the schedule
outright would have stopped those as collateral and left the R2 upload path still never
exercised on a runner, which is the opposite of useful while waiting.

WHAT WAITING COSTS, recorded here so it is not rediscovered later: unit prices cannot be
reconstructed retroactively. The bulk exports carry no quantities, so the archive only
ever grows forward from the first day collected, and a day not collected is a day that
can never be added. 2026-09-05 and 2026-09-07 are already gone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WORKFLOWS = Path(".github/workflows")

# Everything that reaches SEAP does it through this CLI verb.
SEAP_STEP = "achizitii seap"


@pytest.fixture(scope="module")
def flows() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in WORKFLOWS.glob("*.yml")}


def test_no_workflow_reaches_seap_without_someone_starting_it(flows: dict[str, str]) -> None:
    """A cron that collects is exactly what the hold is about. Any workflow that both
    runs on a schedule and calls the SEAP ingest has to justify itself here."""
    for name, text in flows.items():
        if SEAP_STEP not in text:
            continue
        if "schedule:" not in text:
            continue  # dispatch-only, which is the shape backfill.yml and bulk.yml have
        assert name == "daily.yml", f"{name} schedules SEAP traffic and is not accounted for"
        # And daily.yml only does it when a person pressed the button.
        assert "if: github.event_name == 'workflow_dispatch'" in text, (
            "the SEAP ingest must be gated on a manual run while the hold is in place"
        )


def test_the_hold_is_written_down_where_the_trigger_is(flows: dict[str, str]) -> None:
    """A guard nobody can find the reason for gets removed by the next person who reads
    the file."""
    daily = flows["daily.yml"]
    assert "HELD, 2026-09-08" in daily
    assert "ADR" in daily
    assert "To resume:" in daily, "and it has to say how to undo itself"


def test_the_cost_of_waiting_is_stated(flows: dict[str, str]) -> None:
    """The hold trades a permanent loss for a temporary caution. That trade belongs next
    to the switch, not in a chat log."""
    daily = flows["daily.yml"]
    assert "only ever grows forward" in daily
    assert "2026-09-05" in daily and "2026-09-07" in daily


def test_a_held_run_does_not_claim_to_have_collected(flows: dict[str, str]) -> None:
    """A scheduled run still commits the meetings register and a rebuilt bundle. Labelling
    that "archive unit prices" would put a claim in the history the run did not earn."""
    daily = flows["daily.yml"]
    assert "refresh the meetings register and rebuild the bundle" in daily
    assert "No SEAP endpoint was contacted." in daily


def test_the_things_that_do_not_touch_seap_keep_running(flows: dict[str, str]) -> None:
    """The point of holding the ingest rather than the workflow: RUTI stays current, the
    bundle stays rebuilt, and the R2 upload finally gets exercised on a runner."""
    daily = flows["daily.yml"]
    for step in ("achizitii ruti", "achizitii publish --only preturi",
                 "achizitii publish --only preturi --r2"):
        assert step in daily, f"{step} must survive the hold"
    # None of them may be gated on the dispatch that gates the ingest.
    ingest = daily.split("- name: Ingest")[1].split("- name:")[0]
    assert "if: github.event_name == 'workflow_dispatch'" in ingest
    assert daily.count("if: github.event_name == 'workflow_dispatch'") == 1, (
        "only the SEAP ingest is held"
    )
