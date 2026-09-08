"""The page must never look broken while it is loading.

DuckDB-Wasm takes seconds to come up — measured cold, ~170ms to pick a bundle and ~7.5s
to instantiate, and longer on a phone. For that whole window the page has no engine and
nothing to show. It used to display one unchanging sentence, and a tab click during those
seconds ran `selectTab`, which overwrote the message and left an empty table: the page
looked most broken exactly when it was busiest.

These are source-text assertions, not browser tests. They cannot prove the page behaves —
that was verified by driving a real browser — but they do stop the guards being deleted by
someone who does not know why they are there, which is the failure this file exists to
prevent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

INDEX = Path("site/index.html")


@pytest.fixture(scope="module")
def source() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_tabs_start_disabled(source: str) -> None:
    """A tab that cannot answer must not look like it can."""
    assert "b.disabled = true;" in source, (
        "tab buttons must be created disabled and enabled only once the engine is up"
    )
    assert "for (const b of $('tabs').children) b.disabled = false;" in source, (
        "tabs are disabled at creation but never re-enabled"
    )


def test_select_tab_refuses_to_run_before_the_engine_is_up(source: str) -> None:
    """Disabling the buttons is not sufficient on its own.

    The signal dropdown also calls `selectTab` and is not a button, so without this guard
    changing it mid-boot still wipes the loading message.
    """
    guard = source.split("function selectTab(")[1][:600]
    # The front door is a deliberate exception: its rows arrive as JSON, so it can be
    # drawn while DuckDB-Wasm is still downloading. Everything else is a query and still
    # has to wait.
    assert "if (!ready && !(key === 'panorama' && panoramaData)) return;" in guard, (
        "selectTab must no-op before the engine is ready, except for the front door"
    )


def test_the_boot_state_is_visually_distinct(source: str) -> None:
    assert ".boot::before" in source, "the loading state needs a spinner, not just text"
    assert "skeleton" in source, "the empty table needs placeholder rows while loading"


def test_motion_is_optional(source: str) -> None:
    """A spinner and a shimmer are decoration; neither may be the only signal."""
    assert "prefers-reduced-motion" in source
    reduced = source.split("prefers-reduced-motion")[1][:200]
    assert "animation:none" in reduced.replace(" ", "")


def test_the_download_size_is_claimed_on_the_stage_that_pays_it(source: str) -> None:
    """A progress message that names a cost must name it where the cost is incurred.

    Bundle selection is ~170ms; `instantiate` is ~7.5s and is what downloads the engine.
    Putting the size on the fast stage would be a progress indicator that lies.
    """
    boot_block = source.split("selectBundle")[0]
    assert "Se pregătește motorul" in boot_block, "the fast stage must not carry the size"
    assert "MB" not in boot_block.split("booting(")[-1], (
        "the pre-selectBundle stage claims a download size, but that stage is ~170ms"
    )


def test_the_page_does_not_ask_for_a_file_that_is_not_there() -> None:
    """Every visit requested /favicon.ico and collected a 404 — the only console error
    left on the published site, and the one that made a real error harder to notice
    while testing.

    Inline, as a data URI, because a separate file would be the first asset this page
    depends on: it is served as a single document from a static host with no build, and
    that is worth keeping.
    """
    source = INDEX.read_text(encoding="utf-8")
    assert 'rel="icon"' in source
    assert "data:image/svg+xml" in source
