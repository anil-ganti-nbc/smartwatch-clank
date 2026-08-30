"""COROS official news/stories, via an HTML scrape of coros.com/stories.

Medium confidence, unlike the RSS/Atom-based news collectors: no RSS/JSON
feed was found for `coros.com/stories` (COROS's main site is otherwise a
client-rendered SPA, but this specific listing page returns real
server-rendered links/titles -- confirmed live). This is a structural HTML
scrape and is more fragile to markup changes than a feed parser.

The listing renders each story as two overlapping anchors for the same
href: a short "<title> LEARN MORE" card and a longer "<CATEGORY> <title>
<summary>" card. The short form gives a clean title; where only the long
form is present, a known category-label prefix is stripped as a best-effort
approximation and the full text is preserved in `payload` for review.
"""

from __future__ import annotations

import hashlib
import re

from smartwatch_clank.classifiers.news import classify_news
from smartwatch_clank.core.collector import CollectionContext, Collector
from smartwatch_clank.core.models import CollectorResult, CollectorTier, Observation, SourceClass

from ..common import HttpClient, UrlLibHttpClient

STORIES_URL = "https://www.coros.com/stories"

_ANCHOR_RE = re.compile(r'<a[^>]*href="(/stories/[^"]+)"[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_LEARN_MORE_SUFFIX = re.compile(r"\s*LEARN MORE\s*$", re.I)
# The long-form card's trailing summary ends with a publish date and read
# time (e.g. "... 08/04/2026 3 min read") -- confirmed live. Cut the title
# there so it doesn't run on into the summary/metadata text.
_DATE_READ_TIME_CUT = re.compile(r"\s+\d{2}/\d{2}/\d{4}\s+\d+\s*min read.*$", re.I)
_CATEGORY_LABELS = (
    "COROS COACHES", "LATEST NEWS", "MORE THAN SPLITS", "ATHLETE STORIES",
    "PRESS RELEASE", "APP & SOFTWARE", "COROS METRICS",
)


def parse_stories(html: str) -> tuple[dict, ...]:
    texts_by_href: dict[str, list[str]] = {}
    for match in _ANCHOR_RE.finditer(html):
        href, inner = match.group(1), match.group(2)
        text = _TAG_RE.sub(" ", inner)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            texts_by_href.setdefault(href, []).append(text)

    entries = []
    for href, texts in sorted(texts_by_href.items()):
        short = next((t for t in texts if _LEARN_MORE_SUFFIX.search(t)), None)
        if short:
            title = _LEARN_MORE_SUFFIX.sub("", short).strip()
        else:
            title = max(texts, key=len)
            for label in _CATEGORY_LABELS:
                if title.upper().startswith(label):
                    title = title[len(label):].strip()
                    break
            title = _DATE_READ_TIME_CUT.sub("", title).strip()
        if title:
            entries.append({"href": href, "title": title, "raw_texts": texts})
    return tuple(entries)


class CorosOfficialNewsCollector(Collector):
    name = "coros_official_news"
    tier = CollectorTier.PRODUCTION

    def __init__(self, client: HttpClient | None = None, stories_url: str = STORIES_URL) -> None:
        self.client = client or UrlLibHttpClient()
        self.stories_url = stories_url

    def collect(self, context: CollectionContext) -> CollectorResult:
        html = self.client.get_text(self.stories_url)
        entries = parse_stories(html)
        observations: dict[str, Observation] = {}
        classification_counts = {"SMARTWATCH_RELEVANT": 0, "POSSIBLY_SMARTWATCH_RELEVANT": 0, "NOT_SMARTWATCH_RELEVANT": 0}
        for entry in entries:
            classification, evidence = classify_news("coros", entry["title"])
            classification_counts[classification.value] += 1
            link = f"https://www.coros.com{entry['href']}"
            identity = f"coros:news:{hashlib.sha1(entry['href'].encode()).hexdigest()[:16]}"
            observations.setdefault(identity, Observation(
                collector=self.name, identity=identity, source_url=link, observed_at=context.started_at,
                source_kind="official_news", source_class=SourceClass.OFFICIAL_NEWS.value, oem="coros",
                title=entry["title"], classification_state=classification.value, classification_evidence=evidence,
                payload={"scrape_confidence": "medium", "raw_texts": entry["raw_texts"]},
            ))
        return CollectorResult(
            tuple(observations[key] for key in sorted(observations)),
            {"stories_url": self.stories_url, "classification_counts": classification_counts},
        )
