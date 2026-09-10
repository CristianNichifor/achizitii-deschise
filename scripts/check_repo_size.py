"""Fail before the repository becomes a problem, rather than after.

`docs/storage.md` already states the rule this check enforces: *git keeps every version of
a file forever*. It was applied to the line items, which went to R2, and not to the
aggregates, which stayed in git. Measured 2026-09-10, that omission is most of the
repository:

    199.9 MB   14 versions  site/data/furnizori_an.parquet
     45.2 MB   14 versions  site/data/autoritati_an.parquet
     26.4 MB   14 versions  site/data/cpv_an.parquet

271 MB of a 302 MB pack, from three files whose *current* versions total 13.7 MB. Each
`achizitii publish` rewrites all three, so each publish adds roughly 20 MB that no clone
can ever avoid downloading. Nobody notices, because no single commit is the problem.

Two kinds of file live under `site/data`, and only one of them is a mistake:

- **Derived.** Everything the manifest lists, rebuilt from the archive on every publish.
  Reproducible, and therefore the wrong thing to version. These belong in a release asset
  that the Pages build fetches — which keeps them served from Pages, so the reasoning in
  `docs/storage.md` about ordinary reads never touching R2 still holds.
- **Durable.** `site/data/preturi/**`, one file per collected day, written once and never
  rewritten. Unit prices cannot be reconstructed retroactively — the bulk exports carry no
  quantities — so this is the only copy that exists. It is versioned on purpose, it is
  small per commit, and this check must not push anyone into deleting it.

So the two are reported separately. A derived total that climbs means the release path
regressed and something started being committed again. A durable total that climbs is the
archive doing its job, and is only worth acting on much later.

Deliberately measured on the *tracked tree*, not on `.git`: CI clones shallow, so the pack
size there says nothing, while the tree is exactly what a regeneration inflates.

When this trips, the fix is not to raise the ceiling.

Usage:
    python scripts/check_repo_size.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# The write-once price archive, which is supposed to grow.
DURABLE_PREFIX = "site/data/preturi/"

# Roughly twice the derived payload as it stands. Sized to catch a payload doubling or a
# large binary arriving by accident, not to be adjusted upward when one does.
DERIVED_LIMIT_MB = 60.0

# The archive adds ~0.4 MB per collected day, so this is years of headroom rather than a
# real constraint. It exists so that growth is noticed and reasoned about once, rather
# than discovered when a clone starts taking minutes.
DURABLE_LIMIT_MB = 400.0

# Reported individually above this, because one large file is a different conversation
# from a thousand small ones.
NOTABLE_MB = 1.0


def _tracked(root: Path) -> list[tuple[int, str]]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    sizes: list[tuple[int, str]] = []
    for name in listing.split("\0"):
        if not name:
            continue
        path = root / name
        # A tracked path can be absent in a worktree that has not checked everything out.
        if path.is_file():
            sizes.append((path.stat().st_size, name))
    return sizes


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    sizes = _tracked(root)

    durable = [(s, n) for s, n in sizes if n.startswith(DURABLE_PREFIX)]
    derived = [(s, n) for s, n in sizes if not n.startswith(DURABLE_PREFIX)]

    durable_mb = sum(s for s, _ in durable) / 1_048_576
    derived_mb = sum(s for s, _ in derived) / 1_048_576

    derived.sort(reverse=True)

    print(
        f"derived + code: {derived_mb:.1f} MB in {len(derived):,} files "
        f"(limit {DERIVED_LIMIT_MB:.0f} MB)"
    )
    for size, name in derived[:5]:
        if size / 1_048_576 < NOTABLE_MB:
            break
        print(f"  {size / 1_048_576:6.1f} MB  {name}")

    print(
        f"price archive:  {durable_mb:.1f} MB in {len(durable):,} files "
        f"(limit {DURABLE_LIMIT_MB:.0f} MB, grows on purpose)"
    )

    failed = False
    if derived_mb > DERIVED_LIMIT_MB:
        print(
            f"\nFAIL: {derived_mb:.1f} MB outside the price archive, over the "
            f"{DERIVED_LIMIT_MB:.0f} MB ceiling.\n"
            "Something reproducible is being committed. Publish it as a release asset "
            "fetched by the Pages build instead of raising this number — see the "
            "docstring in this file and docs/storage.md.",
            file=sys.stderr,
        )
        failed = True

    if durable_mb > DURABLE_LIMIT_MB:
        print(
            f"\nFAIL: the price archive is {durable_mb:.1f} MB, over the "
            f"{DURABLE_LIMIT_MB:.0f} MB ceiling.\n"
            "This one is not an accident — it is the write-once archive doing what it is "
            "for. Do not delete it. Move the closed years to a release asset or to R2 and "
            "keep the open year in git.",
            file=sys.stderr,
        )
        failed = True

    if failed:
        return 1

    print(f"ok: {DERIVED_LIMIT_MB - derived_mb:.1f} MB of derived headroom")
    return 0


if __name__ == "__main__":
    sys.exit(main())
