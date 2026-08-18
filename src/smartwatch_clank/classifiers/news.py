"""Deterministic relevance classification for official-news feed items.

No ML/fuzzy scoring: a fixed rule cascade over title + categories +
summary text, in order of specificity. "Contains the word watch" alone is
never enough for SMARTWATCH_RELEVANT (spec: don't alert on that alone).
"""

from __future__ import annotations

from enum import StrEnum

# Exact family/product phrases that unambiguously mean a smartwatch or a
# full-featured sports watch. Deliberately excludes "approach" -- Garmin's
# Approach line covers non-watch handheld golf GPS units too (confirmed
# against the real Garmin newsroom feed: "Approach Z10" is a laser
# rangefinder), so an Approach-titled article is quarantined as POSSIBLY,
# not auto-promoted.
_RELEVANT_PHRASES = (
    "galaxy watch", "pixel watch", "apple watch", "watchos",
    "fenix", "forerunner", "venu", "vivoactive", "instinct", "enduro", "marq",
)

# Family names that double as common English words (Amazfit's "Active"/
# "Balance", COROS's "Pace"/"Pod") are scoped per-OEM rather than added to
# the global list above -- matching them against every OEM's feed would risk
# misfiring on unrelated press releases (e.g. "balance sheet", "active
# users"). They're only checked against that OEM's own newsroom/blog feed.
_OEM_RELEVANT_PHRASES: dict[str, tuple[str, ...]] = {
    "amazfit": ("balance", "bip", "cheetah", "t-rex", "trex", "active max", "active edge", "falcon", "zepp os"),
    "coros": ("pace", "apex", "vertix", "nomad", "dura", "pod"),
}

# Explicit fitness-band / non-watch wearable phrases (spec section 3: the
# fitness-band exclusion boundary). Confirmed against the real Garmin
# newsroom feed: "CIRQA Smart Band" appears there and must be excluded.
_EXCLUDED_PHRASES = (
    "galaxy fit", "galaxy ring", "smart band", "mi band", "fitness band",
    "helio strap", "helio ring", "helio core", "helio armband", "up open-ear", "up earbuds",
)

_WEAK_SIGNAL_PHRASES = ("watch", "wearable")


class NewsClassification(StrEnum):
    SMARTWATCH_RELEVANT = "SMARTWATCH_RELEVANT"
    POSSIBLY_SMARTWATCH_RELEVANT = "POSSIBLY_SMARTWATCH_RELEVANT"
    NOT_SMARTWATCH_RELEVANT = "NOT_SMARTWATCH_RELEVANT"


# Confidence/editorial value stay separate concepts (spec section 18); this
# is the single mapping from classification state to both, shared by
# core/diff.py (Discovery editorial level) and intelligence/news.py
# (evidence confidence/editorial level).
EDITORIAL_BY_CLASSIFICATION = {
    NewsClassification.SMARTWATCH_RELEVANT: ("HIGH", "2"),  # EditorialLevel.NEWSWORTHY
    NewsClassification.POSSIBLY_SMARTWATCH_RELEVANT: ("MEDIUM", "3"),  # EditorialLevel.MONITOR
    NewsClassification.NOT_SMARTWATCH_RELEVANT: ("LOW", "4"),  # EditorialLevel.NOISE
}


def classify_news(
    oem: str, title: str, categories: tuple[str, ...] = (), summary: str | None = None,
) -> tuple[NewsClassification, tuple[str, ...]]:
    text = " ".join((title, " ".join(categories), summary or "")).lower()

    oem_phrases = _OEM_RELEVANT_PHRASES.get(oem, ())
    matched_relevant = [phrase for phrase in (*_RELEVANT_PHRASES, *oem_phrases) if phrase in text]
    if matched_relevant:
        return NewsClassification.SMARTWATCH_RELEVANT, tuple(f"matched_phrase:{p}" for p in matched_relevant)

    matched_excluded = [phrase for phrase in _EXCLUDED_PHRASES if phrase in text]
    if matched_excluded:
        return NewsClassification.NOT_SMARTWATCH_RELEVANT, tuple(f"excluded_phrase:{p}" for p in matched_excluded)

    matched_weak = [phrase for phrase in _WEAK_SIGNAL_PHRASES if phrase in text]
    if matched_weak:
        return (
            NewsClassification.POSSIBLY_SMARTWATCH_RELEVANT,
            tuple(f"weak_signal:{p}" for p in matched_weak),
        )

    return NewsClassification.NOT_SMARTWATCH_RELEVANT, ("no_smartwatch_signal",)
