"""The native CSS is an unchanged public MIT release, not an unpublished package."""

import hashlib
import json
from pathlib import Path


def test_pinned_native_css_files_match_public_release():
    root = Path("site/vendor/civic-ui")
    provenance = json.loads((root / "provenance.json").read_text())
    assert provenance["version"] == "0.3.0"
    assert provenance["source"] == (
        "https://github.com/CristianNichifor/civic-ui/releases/download/"
        "v0.3.0/civic-ui-css-0.3.0.tgz"
    )
    assert set(provenance["files"]) == {
        "LICENSE", "NATIVE.md", "native.css", "styles.css", "foundations.css",
        "controls.css", "themes/neutral.css", "themes/usr.css",
    }
    assert provenance["sha256"] == (
        "644b181b1a061516ddfe7cbcbba9d94101e7e0b4f97f4a52c085cccba1f27226"
    )
    for name, expected in provenance["files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
    assert "MIT License" in (root / "LICENSE").read_text()
