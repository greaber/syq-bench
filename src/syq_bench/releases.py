"""Static release navigation: versioned URLs preserve the original captures."""

from __future__ import annotations

import re
import tomllib
from html import escape
from pathlib import Path

PAGES = ("index.html", "all-results.html", "rclone.html", "method.html", "reproduce.html")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def manifest(site: Path) -> dict:
    data = tomllib.loads((site / "releases.toml").read_text())
    versions = [r["version"] for r in data["releases"]]
    if not versions or len(set(versions)) != len(versions) or any(not VERSION.fullmatch(v) for v in versions):
        raise ValueError("Release versions must be distinct numeric major.minor.patch labels")
    if data["latest"] not in versions:
        raise ValueError("Latest release must have a configured capture set")
    for release in data["releases"]:
        for field in ("catalog", "rclone_data", "rclone_body", "reproduce_body"):
            path = site / release[field]
            if not path.resolve().is_relative_to(site.resolve()) or not path.is_file():
                raise ValueError(f"Release input must exist inside site: {field}")
    return data


def filename(page: str, version: str) -> str:
    return page.removesuffix(".html") + "-" + version + ".html"


def decorate(page: str, html: str, version: str, releases: list[dict], *, archive: bool) -> str:
    if archive:
        # Rewrite only local page URLs, including JSON search links; external URLs stay intact.
        for original in PAGES:
            html = re.sub(
                r'(?P<prefix>href="|"href":\s*")' + re.escape(original) + r'(?=["#])',
                lambda m, original=original: m["prefix"] + filename(original, version),
                html,
            )
    choices = []
    links = []
    for release in releases:
        value = release["version"]
        target = filename(page, value)
        selected = " selected" if value == version else ""
        choices.append(f'<option value="{escape(target)}"{selected}>syq {escape(value)}</option>')
        links.append(f'<a href="{escape(target)}">syq {escape(value)}</a>')
    control = (
        '<div class="release-picker"><label for="syq-release">Version </label>'
        '<select id="syq-release" aria-label="syq version" '
        'onchange="location.href=this.value+location.hash">'
        + "".join(choices)
        + "</select><noscript><p>Versions: "
        + " · ".join(links)
        + "</p></noscript></div>"
    )
    marker = '<main id="main">'
    if html.count(marker) != 1:
        raise ValueError("Release page requires one main region")
    return html.replace(marker, marker + control, 1)


def validate_identity(runs: list[dict], rclone: dict, version: str) -> None:
    for run in runs:
        if run.get("publication", {}).get("release") != version:
            raise ValueError("Release selection disagrees with main capture release")
        for tool in run["spec"]["tools"]:
            if tool.get("kind", tool["name"]) == "syq":
                if run["tools"][tool["name"]].get("version") != "syq " + version:
                    raise ValueError("Main capture contains another syq version")
    for row in rclone["rows"]:
        if (row["tool"] == "syq" or row["tool"].startswith("syq-")) and str(
            row.get("tool_identity", {}).get("version", "")
        ).strip().removeprefix("syq ") != version:
            raise ValueError("Rclone capture contains another syq version")
