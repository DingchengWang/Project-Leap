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
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_BASE = "https://dingchengwang.github.io/Project-Leap/"
PAGES = ("", "modules/", "guide/", "downloads/", "about/")
LEGACY_ZIP = "https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/releases/download/v1.0.1/Project-Leap-2D-V1.0.1.zip"
DESKTOP_DMG = "https://github.com/DingchengWang/Project-Leap/releases/download/v1.0.0/Project-Leap-1.0.0-macOS-arm64.dmg"
DESKTOP_RELEASE = "https://github.com/DingchengWang/Project-Leap/releases/tag/v1.0.0"


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
        self.main_navigation: list[str] = []
        self.text_parts: list[str] = []
        self._in_main_navigation = False
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        self.tags[tag] += 1
        if tag == "nav" and attrs.get("id") == "main-nav":
            self._in_main_navigation = True
        if tag == "a" and self._in_main_navigation:
            self.main_navigation.append(attrs.get("href", ""))
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
        if tag == "nav":
            self._in_main_navigation = False

    def handle_data(self, data):
        self.text_parts.append(data)


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
        visible_text = " ".join(parsed.text_parts)
        if re.search(r"\bbuild(?:\s+number)?\s*[:=]?\s*\d+|构建(?:号|编号)\s*(?:为|[：:])?\s*\d+", visible_text, re.IGNORECASE):
            errors.append(f"{name}: internal build number appears in page text")
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
        if len(parsed.current) != 1 or resolve(site, path, parsed.current[0])[0] != path:
            errors.append(f"{name}: requires one active navigation item pointing to this page")
        counterpart = site / ("en" if language == "zh" else "zh") / Path(*path.relative_to(site).parts[1:])
        if len(parsed.switches) != 1 or resolve(site, path, parsed.switches[0])[0] != counterpart:
            errors.append(f"{name}: language switch must preserve the page")
        if any("alt" not in attrs for attrs in parsed.images):
            errors.append(f"{name}: image has no alt attribute")
        if "guide" in path.parts or "about" in path.parts:
            homepage = site / language / "index.html"
            if not any(resolve(site, path, value)[0] == homepage for value in parsed.main_navigation):
                errors.append(f"{name}: main navigation must link to the same-language site homepage")
            for home_class in ("brand", "footer-brand"):
                home_links = [value for tag, value, attrs in parsed.links if tag == "a" and home_class in attrs.get("class", "").split()]
                if len(home_links) != 1 or resolve(site, path, home_links[0])[0] != homepage:
                    errors.append(f"{name}: {home_class} must link to the same-language site homepage")
            leading_paragraph = re.search(r'<article class="prose document">\s*(<p>.*?</p>)', text, re.DOTALL)
            if leading_paragraph:
                leading_links = [resolve(site, path, value)[0] for tag, value, _ in Page(leading_paragraph.group(1)).links if tag == "a"]
                other_page = "about" if "guide" in path.parts else "guide"
                if leading_links == [counterpart, homepage, site / language / other_page / "index.html"]:
                    errors.append(f"{name}: duplicate document navigation must not appear before the body")
        for tag, value, attrs in parsed.links:
            if tag in ("script", "img") or (tag == "link" and attrs.get("rel") == "stylesheet"):
                if urlsplit(value).scheme or value.startswith("//"):
                    errors.append(f"{name}: external runtime asset {value}")
        if "downloads" in path.parts:
            desktop_links = [attrs for tag, value, attrs in parsed.links if tag == "a" and value == DESKTOP_DMG]
            if len(desktop_links) != 1 or any(
                attrs.get("aria-disabled") == "true" or "disabled" in attrs
                or "primary" not in attrs.get("class", "").split()
                for attrs in desktop_links
            ):
                errors.append(f"{name}: requires one active primary link to the fixed desktop DMG")
            if any("disabled" in attrs for attrs in parsed.buttons):
                errors.append(f"{name}: obsolete disabled download button")
            hrefs = {value for _, value, _ in parsed.links}
            if DESKTOP_RELEASE not in hrefs:
                errors.append(f"{name}: missing release details link")
            if LEGACY_ZIP not in hrefs:
                errors.append(f"{name}: missing fixed historical v1.0.1 ZIP link")
            if any("/releases/download/" in value and value not in {LEGACY_ZIP, DESKTOP_DMG} for value in hrefs):
                errors.append(f"{name}: unexpected release download link")
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
    print("PASS: headings, language switches, image alternatives, self-contained assets, fixed desktop and historical downloads.")
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
