"""Generic RSS 2.0 / Atom 1.0 feed parsing, shared by all official-news collectors.

Stdlib-only (`xml.etree.ElementTree`). Matches elements by local name
(namespace-stripped) so the same code handles Samsung/Garmin/Google's plain
RSS 2.0 and Apple's Atom 1.0 without two parsers. Confirmed against live
fetches of all four real feeds during Stage B research: RSS `<category>`
text content, Atom `<category term="...">` attribute-only, RSS `<link>`
element text vs Atom `<link href="...">` attribute, `<guid>`/`<id>` either
present or absent.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from xml.etree import ElementTree

_TAG_RE = re.compile(r"<[^>]+>")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(element: ElementTree.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _clean_summary(raw: str | None) -> str | None:
    if not raw:
        return None
    unescaped = html.unescape(raw)
    stripped = _TAG_RE.sub("", unescaped).strip()
    return stripped[:500] if stripped else None


@dataclass(frozen=True, slots=True)
class FeedItem:
    title: str
    link: str
    guid: str | None
    published_at: str | None
    categories: tuple[str, ...]
    summary: str | None


def parse_feed(raw_xml: str) -> tuple[FeedItem, ...]:
    try:
        root = ElementTree.fromstring(raw_xml)
    except ElementTree.ParseError:
        return ()

    root_name = _local(root.tag)
    if root_name == "feed":
        entries = [child for child in root if _local(child.tag) == "entry"]
        return tuple(item for item in (_parse_atom_entry(entry) for entry in entries) if item is not None)
    # RSS 2.0: <rss><channel><item>...
    channel = next((child for child in root if _local(child.tag) == "channel"), root)
    entries = [child for child in channel if _local(child.tag) == "item"]
    return tuple(item for item in (_parse_rss_item(entry) for entry in entries) if item is not None)


def _parse_rss_item(item: ElementTree.Element) -> FeedItem | None:
    title = _text(next((c for c in item if _local(c.tag) == "title"), None))
    link = _text(next((c for c in item if _local(c.tag) == "link"), None))
    if not title or not link:
        return None
    guid = _text(next((c for c in item if _local(c.tag) == "guid"), None))
    published = _text(next((c for c in item if _local(c.tag) == "pubDate"), None))
    categories = tuple(
        value for value in (_text(c) for c in item if _local(c.tag) == "category") if value
    )
    summary = _clean_summary(
        _text(next((c for c in item if _local(c.tag) == "description"), None))
    )
    return FeedItem(title=title, link=link, guid=guid, published_at=published, categories=categories, summary=summary)


def _parse_atom_entry(entry: ElementTree.Element) -> FeedItem | None:
    title = _text(next((c for c in entry if _local(c.tag) == "title"), None))
    link_elements = [c for c in entry if _local(c.tag) == "link"]
    link = next(
        (el.get("href") for el in link_elements if el.get("rel") in (None, "alternate")),
        link_elements[0].get("href") if link_elements else None,
    )
    if not title or not link:
        return None
    guid = _text(next((c for c in entry if _local(c.tag) == "id"), None))
    published = _text(next((c for c in entry if _local(c.tag) in ("updated", "published")), None))
    categories = tuple(
        value for value in (
            (c.get("term") or _text(c)) for c in entry if _local(c.tag) == "category"
        ) if value
    )
    summary = _clean_summary(
        _text(next((c for c in entry if _local(c.tag) in ("summary", "content")), None))
    )
    return FeedItem(title=title, link=link, guid=guid, published_at=published, categories=categories, summary=summary)
