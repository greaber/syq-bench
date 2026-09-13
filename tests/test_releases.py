import copy
import json
import re
from pathlib import Path

import pytest

from syq_bench.releases import decorate, manifest, validate_identity

ROOT = Path(__file__).resolve().parents[1]


def test_selector_preserves_release_navigation_and_external_urls():
    page = '<main id="main"><a href="index.html#copy">copy</a><a href="https://example.org/index.html">other</a>'
    page += '<script>{"href": "rclone.html"}</script></main>'
    result = decorate("rclone.html", page, "0.5.2", [{"version": "0.6.0"}, {"version": "0.5.2"}], archive=True)
    assert 'href="index-0.5.2.html#copy"' in result
    assert '"href": "rclone-0.5.2.html"' in result
    assert 'href="https://example.org/index.html"' in result
    assert '<option value="rclone-0.5.2.html" selected>' in result
    assert '<option value="rclone-0.6.0.html">' in result
    assert "<noscript>" in result and "location.hash" in result


def test_old_capture_cannot_be_relabelled_as_new_release():
    run = json.loads((ROOT / "site/data/release-052-public-wan-forward.json").read_text())
    rclone = json.loads((ROOT / "site/data/rclone-exploratory.json").read_text())
    validate_identity([run], rclone, "0.5.2")
    with pytest.raises(ValueError, match="main capture release"):
        validate_identity([run], rclone, "0.6.0")
    modified = copy.deepcopy(rclone)
    next(r for r in modified["rows"] if r["tool"] == "syq")["tool_identity"]["version"] = "syq 0.6.0"
    with pytest.raises(ValueError, match="another syq version"):
        validate_identity([run], modified, "0.5.2")


def test_archive_pages_exist_and_all_local_links_resolve():
    releases = manifest(ROOT / "site")
    for release in releases["releases"]:
        for name in ["index", "all-results", "rclone", "method", "reproduce"]:
            page = ROOT / f"site/{name}-{release['version']}.html"
            text = page.read_text()
            assert 'id="syq-release"' in text
            for href in re.findall(r'href="([^"]+)"', text):
                if not href.startswith(("https://", "#", "mailto:")):
                    assert (ROOT / "site" / href.split("#")[0]).is_file(), href


def test_versioned_syq_control_cannot_escape_identity_validation():
    rclone = {"rows": [{"tool": "syq-j32", "tool_identity": {"version": "syq 0.5.2"}}]}
    with pytest.raises(ValueError, match="another syq version"):
        validate_identity([], rclone, "0.6.0")
