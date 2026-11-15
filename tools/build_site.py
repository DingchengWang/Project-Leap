#!/usr/bin/env python3
"""Build the bilingual, self-contained GitHub Pages site from checked-in sources.

Run with the development dependency in requirements-site.txt. Application builds
and users of the committed static site do not require Python-Markdown.
"""

from __future__ import annotations

import argparse
import hashlib
import html
from html.parser import HTMLParser
import posixpath
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import quote, unquote, urlsplit, urlunsplit

try:
    import markdown
    from markdown.extensions.toc import slugify_unicode
except ImportError:
    raise SystemExit("Install the site development dependency: python3 -m pip install -r requirements-site.txt")


ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "docs" / "site-content"
SITE = ROOT / "docs" / "site"
REPOSITORY = "https://github.com/DingchengWang/Project-Leap"
PUBLIC_BASE = "https://dingchengwang.github.io/Project-Leap/"
PAGES = ("home", "modules", "guide", "downloads", "about")
COPY = {
    "en": {
        "lang": "en", "other": "zh", "switch": "中文", "switch_label": "Switch this page to Chinese",
        "skip": "Skip to content", "menu": "Menu", "nav": "Main navigation", "toc": "On this page",
        "titles": dict(zip(PAGES, ("Home", "Modules", "User guide", "Downloads", "About"))),
        "eyebrows": {"modules": "ANALYSIS MODULES", "guide": "DOCUMENTATION", "downloads": "GET PROJECT LEAP", "about": "ABOUT THE PROJECT"},
        "description": "Local microscopy image analysis for Apple Silicon Mac, with astrocyte fluorescence measurement and cell counting.",
        "footer": "Research image analysis for Apple Silicon Mac.",
        "source": "Source on GitHub", "license": "License", "document": "View the source document",
        "top": "Back to top", "home": "Project Leap home", "icon": "Project Leap application icon",
    },
    "zh": {
        "lang": "zh-CN", "other": "en", "switch": "English", "switch_label": "将当前页面切换为英文",
        "skip": "跳至正文", "menu": "菜单", "nav": "主导航", "toc": "本页内容",
        "titles": dict(zip(PAGES, ("首页", "分析模块", "使用指南", "下载", "关于"))),
        "eyebrows": {"modules": "分析模块", "guide": "使用文档", "downloads": "获取 PROJECT LEAP", "about": "关于项目"},
        "description": "面向 Apple Silicon Mac 的本地显微图像分析软件，包含星形胶质细胞荧光强度分析和细胞计数。",
        "footer": "面向 Apple Silicon Mac 的科研图像分析软件。",
        "source": "GitHub 源码", "license": "许可协议", "document": "查看文档源文件",
        "top": "返回顶部", "home": "Project Leap 首页", "icon": "Project Leap 应用图标",
    },
}


def route(language: str, page: str) -> str:
    return f"{language}/" + ("" if page == "home" else f"{page}/")


def relative_url(current: str, target: str) -> str:
    result = posixpath.relpath(target.rstrip("/"), current.rstrip("/"))
    return ("./" if result == "." else result + "/") if target.endswith("/") else result


def source_for(language: str, page: str) -> Path:
    if page == "guide":
        return ROOT / "docs" / f"user-guide.{language}.md"
    if page == "about":
        return ROOT / "docs" / f"about.{language}.md"
    return CONTENT / f"{page}.{language}.md"


class DocumentLinks(HTMLParser):
    """Resolve repository-relative links in the shared guide/about documents."""

    def __init__(self, source: Path, current: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.current = current
        self.parts: list[str] = []

    def resolve(self, value: str) -> str:
        url = urlsplit(value)
        if url.scheme or url.netloc or not url.path:
            return value
        target = (self.source.parent / unquote(url.path)).resolve()
        for language in COPY:
            for page in PAGES:
                if target == source_for(language, page):
                    return urlunsplit(("", "", relative_url(self.current, route(language, page)), url.query, url.fragment))
        # Repository README is a landing-page counterpart. Other documents remain
        # source links; browser paths must never point outside the deployed site.
        if target in (ROOT / "README.md", ROOT / "README_中文.md"):
            language = "zh" if target.name == "README_中文.md" else "en"
            return urlunsplit(("", "", relative_url(self.current, route(language, "home")), url.query, url.fragment))
        try:
            path = target.relative_to(ROOT).as_posix()
        except ValueError:
            raise ValueError(f"Document link escapes the repository: {self.source}: {value}")
        if not target.exists():
            raise ValueError(f"Missing document link: {self.source}: {value}")
        return urlunsplit(("https", "github.com", f"/DingchengWang/Project-Leap/blob/main/{quote(path)}", url.query, url.fragment))

    def handle_starttag(self, tag, attrs):
        rewritten = [(key, self.resolve(value) if key in ("href", "src") and value is not None else value) for key, value in attrs]
        self.parts.append("<" + tag + "".join(" " + key if value is None else f' {key}="{html.escape(value, quote=True)}"' for key, value in rewritten) + ">")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.parts[-1] = self.parts[-1][:-1] + " />"

    def handle_endtag(self, tag):
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")

    def handle_comment(self, data):
        self.parts.append(f"<!--{data}-->")


def render_content(language: str, page: str) -> tuple[str, str]:
    source = source_for(language, page)
    raw = source.read_text(encoding="utf-8")
    if page in ("guide", "about"):
        raw = re.sub(r"\A(?:\ufeff)?# [^\n]+\n", "", raw, count=1)
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "attr_list", "md_in_html", "toc"],
        extension_configs={"toc": {"slugify": slugify_unicode, "toc_depth": "2-3"}},
        output_format="html",
    )
    body = md.convert(raw)
    if page in ("guide", "about"):
        parser = DocumentLinks(source, route(language, page))
        parser.feed(body)
        body = "".join(parser.parts)
    return body, md.toc


def render_page(language: str, page: str) -> str:
    copy = COPY[language]
    current = route(language, page)
    asset = relative_url(current, "assets/")
    css_version = hashlib.sha256((CONTENT / "assets/site.css").read_bytes()).hexdigest()[:12]
    other = relative_url(current, route(copy["other"], page))
    heading = copy["titles"][page]
    title = "Project Leap" if page == "home" else f"{heading} · Project Leap"
    body, toc = render_content(language, page)
    nav = "\n".join(
        f'<a href="{relative_url(current, route(language, item))}"'
        + (' aria-current="page"' if item == page else "")
        + f'>{copy["titles"][item]}</a>' for item in PAGES
    )
    if page == "home":
        body = f'<div class="home-content">{body}</div>'
    else:
        header = f'<header class="page-heading"><p class="eyebrow">{copy["eyebrows"][page]}</p><h1>{heading}</h1></header>'
        if page in ("guide", "about"):
            source_url = REPOSITORY + "/blob/main/" + source_for(language, page).relative_to(ROOT).as_posix()
            body = f'''{header}
<div class="document-layout">
<aside class="document-sidebar"><nav aria-label="{copy['toc']}"><p class="toc-heading">{copy['toc']}</p>{toc}</nav></aside>
<article class="prose document">{body}<p class="document-source"><a href="{source_url}">{copy['document']} <span aria-hidden="true">↗</span></a></p></article>
</div>'''
        else:
            body = header + f'<article class="prose {page}-content">{body}</article>'
    return f'''<!doctype html>
<html lang="{copy['lang']}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="{copy['description']}">
  <meta name="theme-color" content="#f6f8fc">
  <link rel="canonical" href="{PUBLIC_BASE + current}">
  <link rel="alternate" hreflang="en" href="{PUBLIC_BASE + route('en', page)}">
  <link rel="alternate" hreflang="zh-CN" href="{PUBLIC_BASE + route('zh', page)}">
  <link rel="icon" href="{asset}icon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="{asset}site.css?v={css_version}">
  <script src="{asset}site.js" defer></script>
</head>
<body id="top" class="page-{page}">
<a class="skip-link" href="#main">{copy['skip']}</a>
<header class="site-header">
  <div class="header-inner">
    <a class="brand" href="{relative_url(current, route(language, 'home'))}" aria-label="{copy['home']}"><img src="{asset}icon.svg" alt="" width="38" height="38"><span>Project Leap</span></a>
    <button class="menu-toggle" type="button" aria-expanded="false" aria-controls="main-nav" hidden>{copy['menu']} <span aria-hidden="true">☰</span></button>
    <nav id="main-nav" class="main-nav" aria-label="{copy['nav']}">{nav}</nav>
    <a class="language-switch" href="{other}" lang="{COPY[copy['other']]['lang']}" hreflang="{COPY[copy['other']]['lang']}" aria-label="{copy['switch_label']}">{copy['switch']}</a>
  </div>
</header>
<main id="main" class="site-main" tabindex="-1">{body}</main>
<footer class="site-footer">
  <div><a class="footer-brand" href="{relative_url(current, route(language, 'home'))}">Project Leap</a><p>{copy['footer']}</p></div>
  <nav aria-label="{'Footer' if language == 'en' else '页脚导航'}"><a href="{REPOSITORY}">{copy['source']} <span aria-hidden="true">↗</span></a><a href="{REPOSITORY}/blob/main/LICENSE">{copy['license']}</a><a href="#top">{copy['top']} ↑</a></nav>
</footer>
</body>
</html>
'''


def build(output: Path) -> None:
    expected: set[Path] = set()

    def write(relative: str, value: str) -> None:
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        expected.add(path)

    for language in COPY:
        for page in PAGES:
            write(route(language, page) + "index.html", render_page(language, page))
    write("index.html", '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Project Leap</title><meta http-equiv="refresh" content="0; url=en/"><link rel="canonical" href="https://dingchengwang.github.io/Project-Leap/en/"></head><body><p><a href="en/">Continue in English</a> · <a href="zh/" lang="zh-CN">访问中文网站</a></p></body></html>
''')
    write(".nojekyll", "")
    for source in sorted((CONTENT / "assets").rglob("*")):
        if source.is_file():
            target = output / "assets" / source.relative_to(CONTENT / "assets")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            expected.add(target)
    # Delete only obsolete files owned by this generator. Never manage production
    # update feeds, release artifacts, or unrelated future site directories.
    for managed in [output / "en", output / "zh", output / "assets"]:
        if managed.exists():
            for path in sorted(managed.rglob("*"), reverse=True):
                if path.is_file() and path not in expected:
                    path.unlink()
                elif path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
    print(f"Built {len(expected)} static files in {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SITE, help="Static output directory (default: docs/site)")
    args = parser.parse_args()
    if markdown.__version__ != "3.9":
        sys.exit(f"This site uses Markdown 3.9; found {markdown.__version__}. Install requirements-site.txt.")
    build(args.output.resolve())
