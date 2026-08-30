from __future__ import annotations

import json
import unittest
from pathlib import Path

from smartwatch_clank.collectors.coros.official_news import (
    STORIES_URL,
    CorosOfficialNewsCollector,
    parse_stories,
)
from smartwatch_clank.collectors.coros.support import (
    SECTIONS_URL,
    CorosSupportCollector,
    classify_section,
)
from smartwatch_clank.collectors.coros.updates import (
    ARTICLES_URL_TEMPLATE,
    CorosUpdatesCollector,
    parse_affected_devices,
)

FIXTURES = Path(__file__).parent / "fixtures" / "coros"


class JsonFixtureClient:
    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses

    def get_json(self, url: str):
        return self.responses[url]


class TextFixtureClient:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses

    def get_text(self, url: str) -> str:
        return self.responses[url]


def sections_json() -> dict:
    return json.loads((FIXTURES / "sections.json").read_text(encoding="utf-8"))


def release_notes_json() -> dict:
    return json.loads((FIXTURES / "release_notes_articles.json").read_text(encoding="utf-8"))


def stories_html() -> str:
    return (FIXTURES / "stories.html").read_text(encoding="utf-8")


class ClassifySectionTests(unittest.TestCase):
    def test_device_section_is_known_smartwatch(self):
        state, _ = classify_section("COROS PACE 4")
        self.assertEqual(state, "known_smartwatch")

    def test_account_section_excluded(self):
        state, _ = classify_section("COROS Account")
        self.assertEqual(state, "non_smartwatch")

    def test_heart_rate_monitor_accessory_excluded(self):
        state, _ = classify_section("COROS Heart Rate Monitor")
        self.assertEqual(state, "non_smartwatch")

    def test_pod_accessory_excluded(self):
        state, _ = classify_section("COROS POD 2")
        self.assertEqual(state, "non_smartwatch")

    def test_generic_topic_section_excluded(self):
        state, _ = classify_section("Navigation")
        self.assertEqual(state, "non_smartwatch")

    def test_release_notes_section_excluded_from_support(self):
        state, _ = classify_section("Release Notes for COROS Devices")
        self.assertEqual(state, "non_smartwatch")

    def test_monthly_update_section_excluded(self):
        state, _ = classify_section("August 2026 Feature Update")
        self.assertEqual(state, "non_smartwatch")

    def test_non_coros_prefixed_device_is_ambiguous_not_dropped(self):
        state, evidence = classify_section("KIPRUN GPS 500")
        self.assertEqual(state, "ambiguous")
        self.assertTrue(evidence)


class CorosSupportCollectorTests(unittest.TestCase):
    def test_device_identity_mapping_and_exclusions(self):
        collector = CorosSupportCollector(JsonFixtureClient({SECTIONS_URL: sections_json()}))
        result = collector.run()
        by_id = {item.identity: item for item in result.observations}
        self.assertIn("coros:support:1001", by_id)  # PACE 4
        self.assertIn("coros:support:1002", by_id)  # APEX 4
        self.assertIn("coros:support:1003", by_id)  # NOMAD
        self.assertIn("coros:support:1012", by_id)  # KIPRUN, ambiguous, retained
        self.assertEqual(by_id["coros:support:1012"].classification_state, "ambiguous")
        for excluded_id in (1004, 1005, 1006, 1007, 1008, 1009, 1010, 1011):
            self.assertNotIn(f"coros:support:{excluded_id}", by_id)
        self.assertEqual(result.metadata["accepted"], 4)


class ParseAffectedDevicesTests(unittest.TestCase):
    def test_single_device(self):
        self.assertEqual(parse_affected_devices("COROS PACE 4 Release Notes"), ("COROS PACE 4",))

    def test_multiple_devices_joined_by_and(self):
        self.assertEqual(
            parse_affected_devices("COROS APEX 4 (42) and APEX 4 (46) Release Notes"),
            ("COROS APEX 4 (42)", "APEX 4 (46)"),
        )


class CorosUpdatesCollectorTests(unittest.TestCase):
    def test_one_event_per_article_with_affected_devices_not_per_device_discoveries(self):
        release_notes_url = ARTICLES_URL_TEMPLATE.format(section_id=1009)
        collector = CorosUpdatesCollector(JsonFixtureClient({
            SECTIONS_URL: sections_json(), release_notes_url: release_notes_json(),
        }))
        result = collector.run()
        by_id = {item.identity: item for item in result.observations}
        self.assertIn("coros:update:2001", by_id)
        pace4 = by_id["coros:update:2001"]
        self.assertEqual(pace4.payload["affected_devices"], ["COROS PACE 4"])
        apex4 = by_id["coros:update:2002"]
        self.assertEqual(apex4.payload["affected_devices"], ["COROS APEX 4 (42)", "APEX 4 (46)"])
        self.assertEqual(result.metadata["per_device_articles"], 2)

    def test_accessory_release_notes_excluded_not_treated_as_device_updates(self):
        # Regression: a live run showed "COROS Heart Rate Monitor Release
        # Notes" and "COROS POD 2 Release Notes" (both real accessories, not
        # watches) appearing as device update events -- the same "COROS
        # <name> Release Notes" title pattern covers accessories too, and
        # this collector wasn't applying support.py's accessory exclusion.
        release_notes_url = ARTICLES_URL_TEMPLATE.format(section_id=1009)
        articles = release_notes_json()
        articles["articles"].append({
            "id": 2003, "title": "COROS Heart Rate Monitor Release Notes",
            "html_url": "https://support.coros.com/hc/en-us/articles/2003", "updated_at": "2026-08-01T00:00:00Z",
        })
        articles["articles"].append({
            "id": 2004, "title": "COROS POD 2 Release Notes",
            "html_url": "https://support.coros.com/hc/en-us/articles/2004", "updated_at": "2026-08-01T00:00:00Z",
        })
        collector = CorosUpdatesCollector(JsonFixtureClient({
            SECTIONS_URL: sections_json(), release_notes_url: articles,
        }))
        result = collector.run()
        identities = {item.identity for item in result.observations}
        self.assertNotIn("coros:update:2003", identities)
        self.assertNotIn("coros:update:2004", identities)
        self.assertIn("coros:update:2001", identities)
        self.assertEqual(result.metadata["accessory_articles_excluded"], 2)
        self.assertEqual(result.metadata["per_device_articles"], 2)

    def test_monthly_sections_become_fleet_wide_events(self):
        release_notes_url = ARTICLES_URL_TEMPLATE.format(section_id=1009)
        collector = CorosUpdatesCollector(JsonFixtureClient({
            SECTIONS_URL: sections_json(), release_notes_url: release_notes_json(),
        }))
        result = collector.run()
        by_id = {item.identity: item for item in result.observations}
        self.assertIn("coros:update:month:1010", by_id)
        self.assertEqual(by_id["coros:update:month:1010"].payload["scope"], "fleet_wide")
        self.assertEqual(result.metadata["monthly_sections"], 2)

    def test_article_timestamp_sweep_does_not_imply_firmware_release(self):
        # Regression for the 2026-08-28T18:43:09Z incident: one site-wide
        # Zendesk touch sweep moved every article's `updated_at` and the old
        # collector mapped those timestamps into `firmware_version`, emitting
        # 23 simultaneous FIRMWARE_RELEASED/HIGH events that were not 23 real
        # firmware releases. Invariant: an article maintenance timestamp
        # changing MUST NOT imply firmware released.
        from smartwatch_clank.core.diff import diff_catalogues

        release_notes_url = ARTICLES_URL_TEMPLATE.format(section_id=1009)
        collector = CorosUpdatesCollector(JsonFixtureClient({
            SECTIONS_URL: sections_json(), release_notes_url: release_notes_json(),
        }))
        first = {item.identity: item for item in collector.run().observations}
        updated_articles = release_notes_json()
        for article in updated_articles["articles"]:
            article["updated_at"] = "2026-09-01T00:00:00Z"
        collector2 = CorosUpdatesCollector(JsonFixtureClient({
            SECTIONS_URL: sections_json(), release_notes_url: updated_articles,
        }))
        second = {item.identity: item for item in collector2.run().observations}
        self.assertTrue(set(first) == set(second))
        for item in second.values():
            self.assertIsNone(item.firmware_version)
        discoveries = diff_catalogues(first, second)
        firmware_discoveries = [d for d in discoveries if d.change_type.value == "FIRMWARE_RELEASED"]
        self.assertEqual(firmware_discoveries, [])

    def test_ordinary_resight_emits_no_events(self):
        from smartwatch_clank.core.diff import diff_catalogues

        release_notes_url = ARTICLES_URL_TEMPLATE.format(section_id=1009)
        collector = CorosUpdatesCollector(JsonFixtureClient({
            SECTIONS_URL: sections_json(), release_notes_url: release_notes_json(),
        }))
        first = {item.identity: item for item in collector.run().observations}
        second = {item.identity: item for item in collector.run().observations}
        self.assertEqual(diff_catalogues(first, second), [])

    def test_first_repaired_cycle_against_prefx_persisted_state_emits_no_events(self):
        # The one-time transition rewrites firmware_version -> null inside the
        # persisted last-healthy-run rows for this collector (see
        # docs/ticket-coros-updates-firmware-novelty.md). This test proves the
        # migration semantics: hydrated pre-fix state (timestamp-shaped
        # firmware_version, as stored before the repair) is nullified by the
        # transition and the first repaired cycle then diffs clean, with no
        # loss of identity/history. Without the transition the same cycle
        # WOULD emit FIRMWARE_RELEASED for every identity (the hazard this
        # migration exists to prevent).
        from smartwatch_clank.core.diff import diff_catalogues
        from smartwatch_clank.core.models import Observation

        release_notes_url = ARTICLES_URL_TEMPLATE.format(section_id=1009)
        collector = CorosUpdatesCollector(JsonFixtureClient({
            SECTIONS_URL: sections_json(), release_notes_url: release_notes_json(),
        }))
        current = {item.identity: item for item in collector.run().observations}
        pre_fix = {}
        for identity, item in current.items():
            pre_fix[identity] = Observation(
                **{**{f: getattr(item, f) for f in item.__dataclass_fields__ if f != "firmware_version"},
                   "firmware_version": "2026-08-28T17:53:02Z"}
            )
        self.assertTrue(set(pre_fix) == set(current))
        # hazard demonstration: WITHOUT the transition this diff bursts
        hazard = [d for d in diff_catalogues(pre_fix, current) if d.change_type.value == "FIRMWARE_RELEASED"]
        self.assertEqual(len(hazard), len(current))
        # with the documented transition applied to the persisted rows:
        transitioned = {
            identity: Observation(
                **{**{f: getattr(item, f) for f in item.__dataclass_fields__ if f != "firmware_version"},
                   "firmware_version": None}
            )
            for identity, item in pre_fix.items()
        }
        discoveries = diff_catalogues(transitioned, current)
        self.assertEqual(discoveries, [])
        self.assertEqual(set(transitioned), set(current))

    def test_no_real_firmware_version_payload_is_parsed(self):
        # Verdict C (docs/ticket-coros-updates-firmware-novelty.md): the
        # section/article endpoints this collector reads expose no firmware
        # version payload, so genuine-version detection (and its regression
        # test) is intentionally absent. This pins that no timestamp-derived
        # firmware_version value is fabricated instead.
        release_notes_url = ARTICLES_URL_TEMPLATE.format(section_id=1009)
        collector = CorosUpdatesCollector(JsonFixtureClient({
            SECTIONS_URL: sections_json(), release_notes_url: release_notes_json(),
        }))
        result = collector.run()
        self.assertTrue(all(item.firmware_version is None for item in result.observations))
        self.assertTrue(all("updated_at" not in item.payload for item in result.observations))


class ParseStoriesTests(unittest.TestCase):
    def test_prefers_short_learn_more_title_over_long_duplicate(self):
        entries = {e["href"]: e for e in parse_stories(stories_html())}
        self.assertEqual(entries["/stories/press-release/c/coros-pace-5-launch"]["title"], "COROS PACE 5 Launch")

    def test_strips_known_category_label_when_only_long_form_present(self):
        entries = {e["href"]: e for e in parse_stories(stories_html())}
        title = entries["/stories/coros-coaches/c/pacing-ultra-distance-rides"]["title"]
        self.assertFalse(title.upper().startswith("COROS COACHES"))
        self.assertIn("Pacing an Ultra-Distance Ride", title)

    def test_strips_trailing_publish_date_and_read_time(self):
        html = (
            '<html><body>'
            '<a href="/stories/latest-news/c/example">'
            "LATEST NEWS Example Story Title A short summary sentence. 08/04/2026 3 min read"
            "</a></body></html>"
        )
        entries = {e["href"]: e for e in parse_stories(html)}
        title = entries["/stories/latest-news/c/example"]["title"]
        self.assertNotIn("min read", title)
        self.assertNotIn("08/04/2026", title)


class CorosOfficialNewsCollectorTests(unittest.TestCase):
    def test_classifies_and_dedupes_by_href(self):
        collector = CorosOfficialNewsCollector(TextFixtureClient({STORIES_URL: stories_html()}))
        result = collector.run()
        self.assertEqual(len(result.observations), 3)
        by_title = {item.title: item for item in result.observations}
        self.assertEqual(by_title["COROS PACE 5 Launch"].classification_state, "SMARTWATCH_RELEVANT")
        self.assertTrue(all(item.oem == "coros" for item in result.observations))


if __name__ == "__main__":
    unittest.main()
