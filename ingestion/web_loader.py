"""Fetch and clean public web pages into heading-delimited sections.

Pages are fetched once, at ingestion time (not per chat request). That is faster, cheaper,
deterministic, and means a compromised or changed page cannot inject content until you review
and rebuild. Raw HTML snapshots are saved so you can audit exactly what was indexed.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup, Comment

log = logging.getLogger(__name__)

USER_AGENT = "PowellNdlovuSupportBot-Ingest/1.0 (+knowledge-base builder)"
REMOVE_TAGS = ["script", "style", "noscript", "svg", "canvas", "iframe", "form", "button", "nav", "template"]
HIDDEN_STYLE_RE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0", re.I)
HEADING_TAGS = ["h1", "h2", "h3"]
BLOCK_TAGS = ["p", "li", "td", "th", "dt", "dd", "blockquote", "figcaption", "h4", "h5", "h6", "div", "section", "article", "header", "footer", "main", "aside"]
STRUCTURAL_TAGS = set(BLOCK_TAGS + HEADING_TAGS + ["ul", "ol", "dl", "table", "thead", "tbody", "tr"])


@dataclass
class WebSection:
    heading: str
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


@dataclass
class WebPage:
    url: str
    title: str
    sections: list[WebSection]
    links: list[tuple[str, str]]  # (anchor text, absolute URL) for project/dashboard links


def allowed_by_robots(url: str) -> bool:
    parts = urlparse(url)
    robots = RobotFileParser(f"{parts.scheme}://{parts.netloc}/robots.txt")
    try:
        robots.read()
    except Exception:  # robots.txt unreachable -> allowed
        return True
    return robots.can_fetch(USER_AGENT, url)


def fetch_html(url: str, retries: int = 3, timeout: int = 20) -> str:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            resp.raise_for_status()
            if "html" not in resp.headers.get("Content-Type", "html"):
                raise ValueError(f"Unexpected content type: {resp.headers.get('Content-Type')}")
            resp.encoding = resp.apparent_encoding or resp.encoding
            return resp.text
        except Exception as exc:  # noqa: BLE001 - report and retry any network error
            last_exc = exc
            log.warning("Fetch failed (%s/%s) for %s: %s", attempt, retries, url, exc)
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"Could not fetch {url}: {last_exc}")


def _clean_soup(soup: BeautifulSoup) -> None:
    for tag in soup(REMOVE_TAGS):
        tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    # Hidden elements are a classic place to hide prompt injections.
    for tag in soup.find_all(True):
        if tag.attrs is None:
            continue
        if tag.has_attr("hidden") or tag.get("aria-hidden") == "true" or HIDDEN_STYLE_RE.search(tag.get("style", "")):
            tag.decompose()


def _inline_text(tag) -> str:
    """Text of an element and its inline children, joined exactly as a browser renders it.

    `get_text(" ")` inserts a space at every tag boundary, which breaks styled words such as
    `<span>P</span>owell` into "P owell". Real whitespace already exists as text nodes, so the
    strings are concatenated as-is and runs of whitespace collapsed afterwards.
    """
    return re.sub(r"\s+", " ", "".join(tag.strings)).strip()


def _own_text(tag) -> str:
    """Text directly inside a block element plus its inline children (a, span, strong...),
    excluding nested block elements, which are visited separately. Prevents duplicated text."""
    parts = []
    for child in tag.children:
        name = getattr(child, "name", None)
        if name in STRUCTURAL_TAGS:
            parts.append(" ")  # a nested block always separates the text around it
            continue
        parts.append(str(child) if isinstance(child, str) else "".join(child.strings))
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def parse_html(html: str, url: str) -> WebPage:
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.get_text(strip=True) if soup.title else "") or urlparse(url).path.strip("/") or url

    links: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"])
        text = _inline_text(a)
        if href.startswith("http") and text and len(text) < 120:
            links.append((text, href))

    _clean_soup(soup)
    root = soup.body or soup
    sections: list[WebSection] = [WebSection(heading="Introduction")]
    seen: set[str] = set()

    for el in root.find_all(HEADING_TAGS + BLOCK_TAGS):
        if el.name in HEADING_TAGS:
            heading = _inline_text(el)
            if heading:
                sections.append(WebSection(heading=heading[:120]))
            continue
        text = _own_text(el)
        if len(text) < 3 or text in seen:
            continue
        seen.add(text)
        sections[-1].lines.append(f"- {text}" if el.name == "li" else text)

    return WebPage(url=url, title=title, sections=[s for s in sections if s.text], links=links)


def load_page(url: str, snapshot_dir: Path | None = None, offline_dir: Path | None = None) -> WebPage:
    """Fetch (or read a saved copy of) a page and parse it.

    offline_dir: optional folder of saved HTML files named <slug>.html, used when a site
    cannot be reached from the machine running ingestion.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", urlparse(url).path.lower()).strip("-") or "home"
    if offline_dir and (offline_dir / f"{slug}.html").exists():
        html = (offline_dir / f"{slug}.html").read_text(encoding="utf-8", errors="ignore")
        log.info("Using offline copy for %s", url)
    else:
        if not allowed_by_robots(url):
            raise RuntimeError(f"robots.txt disallows fetching {url}")
        html = fetch_html(url)
    if snapshot_dir:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / f"{slug}.html").write_text(html, encoding="utf-8")
    return parse_html(html, url)
