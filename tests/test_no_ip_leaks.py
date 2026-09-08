"""No public IP address may be committed to this repository.

One was: a residential address went into the incident report on 2026-09-07 and sat in a
public repo until it was noticed a day later. Removing it took a history rewrite, and even
that did not fully remove it — GitHub still serves the pre-rewrite commits by SHA, because
`refs/pull/N/head` pins them and a force-push cannot touch those refs.

The cost of catching this before the commit is nothing. The cost of catching it after is
a rewrite that does not entirely work. So it is a test.

WHY THE REGEX VALIDATES OCTETS

Romanian formats thousands with dots, so this project's own money column is full of things
that look exactly like IPv4 addresses:

    3.854.469.918    1.788.480.000    1.183.034.477

Requiring every octet to be 0-255 rejects all three (854, 788 and 477 are out of range),
which is what makes the check usable rather than a permanent source of false alarms. A
Romanian number whose every group happens to fall under 256 would still trip it — that is
the intended direction to fail in, and ALLOWED exists for it.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path

import pytest

OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
IPV4 = re.compile(rf"\b{OCTET}\.{OCTET}\.{OCTET}\.{OCTET}\b")

SKIP_SUFFIXES = {".parquet", ".png", ".jpg", ".svg", ".ico", ".gz", ".zip", ".bundle"}
SKIP_DIRS = ("site/data/", "data/")

ALLOWED: set[str] = set()
"""Addresses that may legitimately appear. Empty on purpose.

Add one only with a reason in a comment, and never a real person's address. Loopback,
private and documentation ranges are already permitted by `_is_public` below and do not
need listing.
"""


def _is_public(text: str) -> bool:
    """True only for addresses that identify someone on the open internet."""
    try:
        ip = ipaddress.IPv4Address(text)
    except ValueError:
        return False
    return not (
        ip.is_private          # 10/8, 172.16/12, 192.168/16
        or ip.is_loopback      # 127/8
        or ip.is_link_local    # 169.254/16
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified   # 0.0.0.0
        # 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24 — RFC 5737, made for examples
        or any(ip in ipaddress.IPv4Network(n)
               for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))
    )


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [
        Path(p) for p in out
        if Path(p).suffix not in SKIP_SUFFIXES
        and not p.startswith(SKIP_DIRS)
        and Path(p).is_file()
    ]


def test_no_public_ip_address_is_committed() -> None:
    found: list[str] = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for candidate in IPV4.findall(line):
                if candidate in ALLOWED or not _is_public(candidate):
                    continue
                found.append(f"{path}:{line_no}: {candidate}")

    assert not found, (
        "a public IP address is about to be committed:\n  "
        + "\n  ".join(found)
        + "\n\nAn address identifies a person, and removing one from a public repository "
        "is not reliably possible after the fact — GitHub keeps serving the old commit "
        "by SHA even after a history rewrite. If it is genuinely needed, put it in a "
        "private message, not a file. See docs/incident-2026-09-07-seap-block.md."
    )


@pytest.mark.parametrize(
    ("text", "public"),
    [
        ("82.79.122.161", True),    # the one that leaked
        ("8.8.8.8", True),
        ("127.0.0.1", False),
        ("192.168.1.1", False),
        ("10.0.0.5", False),
        ("0.0.0.0", False),
        ("203.0.113.7", False),     # RFC 5737 documentation range
    ],
)
def test_public_address_detection(text: str, public: bool) -> None:
    assert _is_public(text) is public


@pytest.mark.parametrize(
    "romanian_number", ["3.854.469.918", "1.788.480.000", "1.183.034.477"]
)
def test_romanian_thousands_separators_are_not_mistaken_for_addresses(
    romanian_number: str,
) -> None:
    """These are real values from our own tables; the check must not fire on them."""
    assert not [m for m in IPV4.findall(romanian_number) if _is_public(m)]
