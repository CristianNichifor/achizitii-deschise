"""The native CSS is an unchanged public MIT release, not an unpublished package."""

import hashlib
import json
from pathlib import Path


def test_pinned_native_css_files_match_public_release():
    root = Path("site/vendor/civic-ui")
    provenance = json.loads((root / "provenance.json").read_text())
    assert provenance["version"] == "0.4.0"
    assert provenance["source"] == (
        "https://github.com/CristianNichifor/civic-ui/releases/download/"
        "v0.4.0/civic-ui-css-0.4.0.tgz"
    )
    assert set(provenance["files"]) == {
        "LICENSE", "NATIVE.md", "native.css", "styles.css", "foundations.css",
        "controls.css", "themes/neutral.css", "themes/usr.css",
    }
    assert provenance["sha256"] == (
        "269dc6d01f4da09707521229111ac55011f78f55518a7c8d73d660503ed84de7"
    )
    for name, expected in provenance["files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
    assert "MIT License" in (root / "LICENSE").read_text()
