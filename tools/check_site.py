#!/usr/bin/env python3
"""Check static pages without build dependencies or network access.

Use --rebuild with requirements-site.txt installed to also verify reproducibility.
External URLs are reported as outside this check; new release destinations need
their own publication-stage verification.
"""

from __future__ import annotations

import argparse
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_BASE = "https://dingchengwang.github.io/Project-Leap/"
PAGES = ("", "modules/", "guide/", "downloads/", "about/")
LEGACY_ZIP = "https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/releases/download/v1.0.1/Project-Leap-2D-V1.0.1.zip"


class Page(HTMLParser):
    def __init__(self, text: str):
        super().__init__()
        self.ids: list[str] = []
        self.links: list[tuple[str, str, dict]] = []
        self.tags = Counter()
        self.languages: list[str] = []
        self.switches: list[str] = []
        self.buttons: list[dict] = []
        self.images: list[dict] = []
        self.current: list[str] = []
        self.document_links: list[str] = []
        self._in_document = False
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        self.tags[tag] += 1
        if tag == "article" and "document" in attrs.get("class", "").split():
            self._in_document = True
        if tag == "a" and self._in_document:
            self.document_links.append(attrs.get("href", ""))
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "html":
            self.languages.append(attrs.get("lang", ""))
        for attr in ("href", "src"):
            if attr in attrs:
                self.links.append((tag, attrs[attr], attrs))
        if "language-switch" in attrs.get("class", "").split():
            self.switches.append(attrs.get("href", ""))
        if attrs.get("aria-current") == "page":
            self.current.append(attrs.get("href", ""))
        if tag == "button":
            self.buttons.append(attrs)
        if tag == "img":
            self.images.append(attrs)

    def handle_endtag(self, tag):
        if tag == "article":
            self._in_document = False


def resolve(site: Path, current: Path, value: str) -> tuple[Path | None, str]:
    if value.startswith(PUBLIC_BASE):
        value = "/" + value[len(PUBLIC_BASE):]
    url = urlsplit(value)
    if url.scheme or url.netloc:
        return None, ""
    path = unquote(url.path)
    if not path:
        target = current
    elif path.startswith("/"):
        target = site / path.lstrip("/")
    else:
        target = current.parent / path
    target = target.resolve()
    if target.is_dir():
        target /= "index.html"
    return target, unquote(url.fragment)


def check(site: Path, rebuild: bool) -> int:
    errors: list[str] = []
    expected = {site / language / page / "index.html" for language in ("en", "zh") for page in PAGES}
    expected.add(site / "index.html")
    actual = set(site.rglob("*.html"))
    for missing in sorted(expected - actual):
        errors.append(f"Missing required page: {missing.relative_to(site)}")
    documents: dict[Path, Page] = {}
    external = set()
    references = 0
    for path in sorted(actual):
        text = path.read_text(encoding="utf-8")
        parsed = Page(text)
        documents[path] = parsed
        name = path.relative_to(site).as_posix()
        duplicates = [key for key, count in Counter(parsed.ids).items() if count > 1]
        if duplicates:
            errors.append(f"{name}: duplicate IDs {duplicates}")
        if "/Users/" in text or "/private/tmp/" in text or 'markdown="' in text:
            errors.append(f"{name}: local path or unrendered Markdown attribute")
        if path == site / "index.html":
            if 'content="0; url=en/"' not in text:
                errors.append("Root page must direct to en/ without JavaScript")
            continue
        language = path.relative_to(site).parts[0]
        expected_language = "zh-CN" if language == "zh" else "en"
        if parsed.languages != [expected_language]:
            errors.append(f"{name}: incorrect document language")
        if parsed.tags["h1"] != 1 or parsed.tags["main"] != 1 or parsed.tags["title"] != 1:
            errors.append(f"{name}: requires one h1, main, and title")
        if len(parsed.current) != 1:
            errors.append(f"{name}: requires one active navigation item")
        counterpart = site / ("en" if language == "zh" else "zh") / Path(*path.relative_to(site).parts[1:])
        if len(parsed.switches) != 1 or resolve(site, path, parsed.switches[0])[0] != counterpart:
            errors.append(f"{name}: language switch must preserve the page")
        if any("alt" not in attrs for attrs in parsed.images):
            errors.append(f"{name}: image has no alt attribute")
        if "guide" in path.parts or "about" in path.parts:
            homepage = site / language / "index.html"
            if not any(resolve(site, path, value)[0] == homepage for value in parsed.document_links):
                errors.append(f"{name}: the shared document's home link must lead to the same-language site homepage")
        for tag, value, attrs in parsed.links:
            if tag in ("script", "img") or (tag == "link" and attrs.get("rel") == "stylesheet"):
                if urlsplit(value).scheme or value.startswith("//"):
                    errors.append(f"{name}: external runtime asset {value}")
        if "downloads" in path.parts:
            if not any("disabled" in attrs and attrs.get("aria-describedby") == "download-status" for attrs in parsed.buttons):
                errors.append(f"{name}: prepared desktop download must remain disabled")
            hrefs = {value for _, value, _ in parsed.links}
            if LEGACY_ZIP not in hrefs:
                errors.append(f"{name}: missing fixed historical v1.0.1 ZIP link")
            if any("/releases/download/" in value and value != LEGACY_ZIP for value in hrefs):
                errors.append(f"{name}: an unpublished release has an active download link")
    for path, parsed in documents.items():
        name = path.relative_to(site).as_posix()
        for _, value, _ in parsed.links:
            target, fragment = resolve(site, path, value)
            if target is None:
                external.add(value)
                continue
            references += 1
            if not target.is_relative_to(site):
                errors.append(f"{name}: link escapes the site: {value}")
            elif not target.is_file():
                errors.append(f"{name}: missing local target: {value}")
            elif fragment and target.suffix == ".html" and fragment not in documents[target].ids:
                errors.append(f"{name}: missing fragment: {value}")
    if rebuild:
        with tempfile.TemporaryDirectory(prefix="project-leap-site-") as temporary:
            regenerated = Path(temporary) / "site"
            subprocess.run([sys.executable, str(ROOT / "tools" / "build_site.py"), "--output", str(regenerated)], check=True)
            managed = {"index.html", ".nojekyll"}
            managed.update(path.relative_to(regenerated).as_posix() for path in regenerated.rglob("*") if path.is_file())
            current = {path.relative_to(site).as_posix() for path in site.rglob("*") if path.is_file() and (path.parts[len(site.parts)] in {"en", "zh", "assets"} or path.name in {"index.html", ".nojekyll"})}
            for relative in sorted(current | managed):
                left, right = site / relative, regenerated / relative
                if not left.is_file() or not right.is_file() or left.read_bytes() != right.read_bytes():
                    errors.append(f"Generated output differs: {relative}")
    if errors:
        print("Site validation failed:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(f"PASS: {len(documents)} HTML pages; {references} local references; 5 bilingual page pairs.")
    print("PASS: headings, language switches, image alternatives, self-contained assets, release-preparation download state.")
    if rebuild:
        print("PASS: generated files exactly reproduce the current site content.")
    print(f"External URLs ({len(external)} unique) were not fetched; browser layout and live publication are separate checks.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=ROOT / "docs" / "site")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild in a temporary directory and compare every generated byte")
    args = parser.parse_args()
    sys.exit(check(args.site.resolve(), args.rebuild))
