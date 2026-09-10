"""Discord delivery: persistence-first outbox, activation cutoff, retry and
redaction behaviour.

Hard test wall: no test in this file (or this suite) ever contacts Discord.
Every send goes through a swappable fake sender; the webhook URL strings used
here are dangling example values that must never be observed in persisted
state or logs. No live collector runs.
"""

from __future__ import annotations

import io
import json
import logging
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smartwatch_clank.cli import main
from smartwatch_clank.core.models import (
    ChangeType,
    Confidence,
    CollectorTier,
    Discovery,
    EditorialLevel,
    RunScope,
    utc_now,
)
from smartwatch_clank.core.registry import CollectorRegistry
from smartwatch_clank.core.runner import Runner
from smartwatch_clank.core.store import SQLiteStore
from smartwatch_clank.notifications import discord as discord_module
from smartwatch_clank.notifications.discord import (
    DELIVERY_ELIGIBLE_LEVELS,
    DISCORD_WEBHOOK_ENV,
    PROVIDER,
    DiscordNotifier,
    build_embed,
    build_test_embed,
    delivery_lock_path,
    delivery_preview,
    notification_intent_factory,
)
from tests.helpers import DummyCollector, observation

# A dangling example webhook. If this string ever reaches durable state or
# logs, redaction is broken.
WEBHOOK = "https://discord.com/api/webhooks/000000000000000000/EXAMPLE-SECRET-TOKEN"

ALLOWLIST = ("dummy",)


def discovery(level: EditorialLevel, *, when: datetime | None = None,
              change: ChangeType = ChangeType.PRICE_CHANGE,
              collector: str = "dummy", identity: str = "watch-1") -> Discovery:
    return Discovery(
        collector=collector, identity=identity, change_type=change,
        confidence=Confidence.HIGH, editorial_level=level,
        source_url="https://official.example/watch-1",
        previous={"price": "100", "oem": "Garmin", "region": "GB"},
        current={"price": "120", "oem": "Garmin", "region": "GB"},
        evidence={"source_url": "https://official.example/watch-1"},
        discovered_at=when or utc_now(),
    )


def ok_sender(calls: list):
    def sender(url, payload):
        calls.append(url)
        return (True, None, 204, None)
    return sender


def failing_sender(status_error):
    calls = []
    def sender(url, payload):
        calls.append(url)
        return (False, status_error, 500 if status_error.startswith("http_5") else None, None)
    sender.calls = calls
    return sender


class DeliveryBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.store = SQLiteStore(self.db)
        self.registry = CollectorRegistry()
        self.collector = DummyCollector(items=(observation("dummy", "watch-1", price="100"),
                                               observation("dummy", "watch-2", price="100")),
                                        tier=CollectorTier.PRODUCTION)
        self.registry.register(self.collector)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def runner(self) -> Runner:
        return Runner(self.registry, self.store,
                      notification_intent_factory=notification_intent_factory(self.store, ALLOWLIST))

    def run_baseline_and_change(self):
        self.runner().run(RunScope.PRODUCTION, production_allowlist=ALLOWLIST)
        self.collector.items = (observation("dummy", "watch-1", price="120"),
                                observation("dummy", "watch-2", price="100"))
        outcomes = self.runner().run(RunScope.PRODUCTION, production_allowlist=ALLOWLIST)
        self.assertTrue(outcomes[0].healthy)
        self.assertEqual(outcomes[0].discovery_count, 1)


class EnqueueGateTests(DeliveryBase):
    def test_baseline_produces_no_notification(self):
        outcomes = self.runner().run(RunScope.PRODUCTION, production_allowlist=ALLOWLIST)
        self.assertTrue(outcomes[0].baseline)
        self.assertEqual(outcomes[0].discovery_count, 0)
        self.assertEqual(self.store.notification_counts(PROVIDER), {})

    def test_new_production_discovery_enqueued_with_discovery(self):
        self.run_baseline_and_change()
        counts = self.store.notification_counts(PROVIDER)
        self.assertEqual(counts, {"pending": 1})
        row = self.store.pending_notifications(PROVIDER)[0]
        stored = self.store.connection.execute("SELECT id FROM discoveries").fetchone()
        self.assertEqual(row["discovery_id"], stored["id"])
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["attempts"], 0)
        self.assertIsNotNone(row["discovered_at"])

    def test_critical_level_is_eligible(self):
        intent = notification_intent_factory(self.store, ALLOWLIST)(self.collector)
        intent(discovery(EditorialLevel.CRITICAL), discovery_id=101)
        self.assertEqual(self.store.notification_counts(PROVIDER), {"pending": 1})

    def test_newsworthy_level_is_eligible(self):
        intent = notification_intent_factory(self.store, ALLOWLIST)(self.collector)
        intent(discovery(EditorialLevel.NEWSWORTHY), discovery_id=102)
        self.assertEqual(self.store.notification_counts(PROVIDER), {"pending": 1})

    def test_monitor_level_is_silent(self):
        intent = notification_intent_factory(self.store, ALLOWLIST)(self.collector)
        intent(discovery(EditorialLevel.MONITOR), discovery_id=103)
        self.assertEqual(self.store.notification_counts(PROVIDER), {})

    def test_noise_level_is_silent(self):
        intent = notification_intent_factory(self.store, ALLOWLIST)(self.collector)
        intent(discovery(EditorialLevel.NOISE), discovery_id=104)
        self.assertEqual(self.store.notification_counts(PROVIDER), {})

    def test_experimental_collector_is_silent(self):
        experimental = DummyCollector("experimental", tier=CollectorTier.EXPERIMENTAL)
        self.registry.register(experimental)
        intents = notification_intent_factory(self.store, ALLOWLIST)
        self.assertIsNone(intents(experimental))

    def test_production_collector_outside_allowlist_is_silent(self):
        intents = notification_intent_factory(self.store, ("other",))
        self.assertIsNone(intents(self.collector))

    def test_unhealthy_collector_run_enqueues_nothing(self):
        self.run_baseline_and_change()
        self.collector.error = RuntimeError("source exploded")
        outcomes = self.runner().run(RunScope.PRODUCTION, production_allowlist=ALLOWLIST)
        self.assertFalse(outcomes[0].healthy)
        # The failed run persisted no discoveries and enqueued nothing new;
        # the one durable intent from the earlier healthy run is untouched.
        self.assertEqual(self.store.notification_counts(PROVIDER), {"pending": 1})

    def test_reenqueuing_the_same_discovery_does_not_duplicate(self):
        intent = notification_intent_factory(self.store, ALLOWLIST)(self.collector)
        intent(discovery(EditorialLevel.NEWSWORTHY), discovery_id=200)
        intent(discovery(EditorialLevel.NEWSWORTHY), discovery_id=200)
        self.assertEqual(self.store.notification_counts(PROVIDER), {"pending": 1})


class EmbedTests(DeliveryBase):
    def test_embed_identifies_clank_level_confidence_and_discovery(self):
        payload = build_embed(discovery(EditorialLevel.NEWSWORTHY), discovery_id=55)
        (embed,) = payload["embeds"]
        self.assertTrue(embed["title"].startswith("SMARTWATCH CLANK — "))
        description = embed["description"]
        self.assertIn("**Level:** NEWSWORTHY", description)   # text, not colour alone
        self.assertIn("**Confidence:** HIGH", description)
        self.assertIn("**OEM:** Garmin", description)
        self.assertIn("**Region:** GB", description)
        self.assertIn("**Source:** dummy", description)
        self.assertIn("**Detected:**", description)
        self.assertIn("price: 100 → 120", description)
        self.assertIn("Discovery ID: 55", embed["footer"]["text"])
        self.assertEqual(embed["url"], "https://official.example/watch-1")

    def test_embed_does_not_dump_raw_previous_or_current_json(self):
        big = discovery(EditorialLevel.NEWSWORTHY)
        payload = build_embed(big)
        text = json.dumps(payload)
        # The comparable() dumps carry these keys wholesale; the embed must
        # carry only concise changed fields instead.
        self.assertNotIn("specifications", text)
        self.assertNotIn("page_hash", text)
        self.assertLess(len(text), 2000)

    def test_removed_discovery_reports_loss_without_invention(self):
        removed = Discovery(
            collector="dummy", identity="watch-9", change_type=ChangeType.PRODUCT_REMOVED,
            confidence=Confidence.HIGH, editorial_level=EditorialLevel.NEWSWORTHY,
            source_url="https://official.example/watch-9",
            previous={"price": "100"}, current=None, evidence={},
        )
        payload = build_embed(removed, discovery_id=9)
        self.assertIn("no longer present", json.dumps(payload))

    def test_test_message_is_unmistakable_and_editorial_free(self):
        payload = build_test_embed("activation check")
        (embed,) = payload["embeds"]
        self.assertEqual(embed["title"], "SMARTWATCH CLANK — TEST")
        text = json.dumps(payload)
        for word in ("NEWSWORTHY", "CRITICAL", "MONITOR", "NOISE", "Level:", "Confidence:"):
            self.assertNotIn(word, text)
        self.assertNotIn("watch-1", text)  # never a real identity


class ActivationCutoffTests(DeliveryBase):
    def set_cutoff(self, iso: str):
        from smartwatch_clank.notifications.delivery_policy import ACTIVATION_CUTOFF_KEY

        self.store.policy_set(ACTIVATION_CUTOFF_KEY, iso)

    def test_historical_discovery_is_held_by_cutoff(self):
        past = utc_now() - timedelta(days=30)
        intent = notification_intent_factory(self.store, ALLOWLIST)(self.collector)
        intent(discovery(EditorialLevel.NEWSWORTHY, when=past), discovery_id=300)
        self.set_cutoff(utc_now().isoformat())
        calls: list = []
        result = DiscordNotifier(self.store, WEBHOOK, sender=ok_sender(calls)).drain()
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["held"], 1)
        self.assertEqual(calls, [])  # nothing contacted Discord
        row = self.store.notifications_by_status(PROVIDER, "held", limit=10)[0]
        self.assertEqual(row["attempts"], 0)  # held is not an attempt
        self.assertEqual(json.loads(row["payload_json"])["embeds"][0]["title"],
                         "SMARTWATCH CLANK — PRICE CHANGE")  # payload preserved

    def test_new_production_discovery_after_cutoff_is_sent(self):
        self.run_baseline_and_change()  # discovered_at = now, cutoff later
        self.set_cutoff((utc_now() - timedelta(minutes=1)).isoformat())
        calls: list = []
        result = DiscordNotifier(self.store, WEBHOOK, sender=ok_sender(calls)).drain()
        self.assertEqual(result["sent"], 1)
        self.assertEqual(self.store.notification_counts(PROVIDER), {"sent": 1})

    def test_unreadable_policy_fails_closed(self):
        intent = notification_intent_factory(self.store, ALLOWLIST)(self.collector)
        intent(discovery(EditorialLevel.NEWSWORTHY), discovery_id=301)
        self.store.policy_set("discord.activation_cutoff", "not-a-timestamp")
        calls: list = []
        result = DiscordNotifier(self.store, WEBHOOK, sender=ok_sender(calls)).drain()
        self.assertEqual(result["held_by_reason"], {"held_policy_unreadable": 1})
        self.assertEqual(calls, [])

    def test_explicit_include_held_replays_held_history(self):
        past = utc_now() - timedelta(days=30)
        intent = notification_intent_factory(self.store, ALLOWLIST)(self.collector)
        intent(discovery(EditorialLevel.NEWSWORTHY, when=past), discovery_id=302)
        self.set_cutoff(utc_now().isoformat())
        DiscordNotifier(self.store, WEBHOOK, sender=ok_sender([])).drain()
        self.assertEqual(len(self.store.notifications_by_status(PROVIDER, "held", limit=10)), 1)
        calls: list = []
        result = DiscordNotifier(self.store, WEBHOOK, sender=ok_sender(calls)).drain(include_held=True)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.notification_counts(PROVIDER), {"sent": 1})

    def test_preview_is_read_only_and_reports_cutoff_effect(self):
        self.run_baseline_and_change()
        before = self.db.read_bytes()
        preview = delivery_preview(self.store, utc_now().isoformat(), webhook_configured=False)
        self.assertEqual(preview["proposed_cutoff_effect"]["would_send"], 0)
        self.assertEqual(preview["proposed_cutoff_effect"]["would_hold"], 1)
        self.assertFalse(preview["webhook_configured"])
        self.assertEqual(self.store.notification_counts(PROVIDER), {"pending": 1})
        # The read-only store never touched the file.
        self.assertEqual(self.db.read_bytes(), before)


class DrainBehaviourTests(DeliveryBase):
    def enqueue_one(self):
        intent = notification_intent_factory(self.store, ALLOWLIST)(self.collector)
        intent(discovery(EditorialLevel.NEWSWORTHY), discovery_id=400)

    def test_send_success_becomes_sent(self):
        self.enqueue_one()
        calls: list = []
        result = DiscordNotifier(self.store, WEBHOOK, sender=ok_sender(calls)).drain()
        self.assertEqual(result, {"sent": 1, "failed": 0, "held": 0,
                                  "deferred": 0, "remaining": 0})
        self.assertEqual(self.store.notification_counts(PROVIDER), {"sent": 1})

    def test_second_drain_does_not_duplicate(self):
        self.enqueue_one()
        calls: list = []
        notifier = DiscordNotifier(self.store, WEBHOOK, sender=ok_sender(calls))
        notifier.drain()
        notifier.drain()
        notifier.drain()
        self.assertEqual(len(calls), 1)  # sent rows are never re-posted
        self.assertEqual(self.store.notification_counts(PROVIDER), {"sent": 1})

    def test_network_failure_remains_retryable(self):
        self.enqueue_one()
        sender = failing_sender("connection_error")
        result = DiscordNotifier(self.store, WEBHOOK, sender=sender).drain()
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["failed"], 0)
        row = self.store.pending_notifications(PROVIDER)[0]
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["last_error"], "connection_error")
        self.assertIsNone(row["http_status"])
        # A later healthy drain delivers it.
        calls: list = []
        result = DiscordNotifier(self.store, WEBHOOK, sender=ok_sender(calls)).drain()
        self.assertEqual(result["sent"], 1)

    def test_429_honours_a_durable_retry_floor(self):
        self.enqueue_one()
        attempts = []
        def rate_limited(url, payload):
            attempts.append(url)
            return (False, "http_429_rate_limited", 429, 900.0)
        result = DiscordNotifier(self.store, WEBHOOK, sender=rate_limited).drain()
        self.assertEqual(result["status"], "rate_limited")
        self.assertEqual(result["deferred"], 1)
        row = self.store.pending_notifications(PROVIDER)[0]
        self.assertEqual(row["attempts"], 0)  # a 429 never burns an attempt
        self.assertIsNotNone(row["not_before"])
        floor = datetime.fromisoformat(row["not_before"])
        self.assertGreater(floor, utc_now())
        self.assertEqual(len(attempts), 1)  # the drain stopped itself
        # While the floor is in force, a later drain defers instead of sending.
        calls: list = []
        result = DiscordNotifier(self.store, WEBHOOK, sender=ok_sender(calls)).drain()
        self.assertEqual(result["deferred"], 1)
        self.assertEqual(calls, [])
        self.assertEqual(self.store.notification_counts(PROVIDER), {"pending": 1})

    def test_429_with_bounded_retry_after(self):
        from smartwatch_clank.notifications.discord import (
            DEFAULT_RETRY_AFTER_SECONDS,
            MAX_RETRY_AFTER_SECONDS,
            _parse_retry_after,
        )
        self.assertEqual(_parse_retry_after("30"), 30.0)
        self.assertEqual(_parse_retry_after("99999"), MAX_RETRY_AFTER_SECONDS)
        self.assertIsNone(_parse_retry_after("junk"))
        self.assertIsNone(_parse_retry_after("-5"))
        # No usable header falls back to the sane default.
        self.assertIsNone(_parse_retry_after(None))
        self.assertEqual(DEFAULT_RETRY_AFTER_SECONDS, 60)

    def test_4xx_permanent_webhook_failure_is_recorded_honestly(self):
        self.enqueue_one()
        sender = failing_sender("http_404_webhook_not_found")
        for expected_attempt in range(1, 5):
            result = DiscordNotifier(self.store, WEBHOOK, sender=sender).drain()
            row = self.store.pending_notifications(PROVIDER)[0]
            self.assertEqual(row["attempts"], expected_attempt)
            self.assertEqual(row["status"], "pending")
        result = DiscordNotifier(self.store, WEBHOOK, sender=sender).drain()
        self.assertEqual(result["failed"], 1)
        row = self.store.notifications_by_status(PROVIDER, "failed", limit=10)[0]
        self.assertEqual(row["attempts"], 5)
        self.assertEqual(row["last_error"], "http_404_webhook_not_found")
        self.assertEqual(row["status"], "failed")  # terminal, honestly recorded

    def test_no_webhook_is_nonfatal_and_sends_nothing(self):
        self.enqueue_one()
        def explode(url, payload):
            raise AssertionError("no webhook configured: nothing may be posted")
        result = DiscordNotifier(self.store, None, sender=explode).drain()
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["remaining"], 1)
        self.assertEqual(self.store.notification_counts(PROVIDER), {"pending": 1})

    def test_delivery_busy_refuses_without_mutating_rows(self):
        from smartwatch_clank.core.lock import RunLock

        self.enqueue_one()
        lock = RunLock(delivery_lock_path(self.store.path))
        lock.acquire()
        try:
            calls: list = []
            result = DiscordNotifier(self.store, WEBHOOK, sender=ok_sender(calls)).drain()
            self.assertEqual(result["status"], "delivery_busy")
            self.assertEqual(calls, [])
            row = self.store.pending_notifications(PROVIDER)[0]
            self.assertEqual(row["attempts"], 0)
        finally:
            lock.release()

    def test_legacy_two_tuple_sender_shape_still_works(self):
        self.enqueue_one()
        calls: list = []
        def old_style_sender(url, payload):
            calls.append(url)
            return (True, None)
        result = DiscordNotifier(self.store, WEBHOOK, sender=old_style_sender).drain()
        self.assertEqual(result["sent"], 1)


class RedactionTests(DeliveryBase):
    def test_transport_failure_redacts_the_url_on_the_real_path(self):
        import urllib.error

        original_urlopen = discord_module._urlopen
        def hostile_urlopen(request, timeout):
            # Transport exceptions can embed the request URL (which for a
            # Discord webhook IS the credential) in their repr.
            raise urllib.error.URLError(f"cannot reach host for {request.full_url}")
        discord_module._urlopen = hostile_urlopen
        try:
            ok, err, status, retry_after = discord_module._post_webhook(WEBHOOK, {"x": 1})
        finally:
            discord_module._urlopen = original_urlopen
        self.assertFalse(ok)
        self.assertEqual(err, "connection_error")
        self.assertNotIn("EXAMPLE-SECRET-TOKEN", repr((ok, err, status, retry_after)))

    def test_no_webhook_url_reaches_errors_logs_or_database(self):
        past = utc_now() - timedelta(days=1)
        intent = notification_intent_factory(self.store, ALLOWLIST)(self.collector)
        intent(discovery(EditorialLevel.NEWSWORTHY, when=past), discovery_id=500)
        # Drive the row through retry, then terminal failure: every persisted
        # byte of that history must be free of the secret.
        sender = failing_sender("connection_error")
        with self.assertLogs("smartwatch_clank.discord", level="WARNING") as logs:
            for _ in range(5):
                DiscordNotifier(self.store, WEBHOOK, sender=sender).drain()
        combined_logs = " ".join(logs.output)
        self.assertNotIn("EXAMPLE-SECRET-TOKEN", combined_logs)
        for status in ("pending", "failed", "sent", "held"):
            for row in self.store.notifications_by_status(PROVIDER, status, limit=100):
                self.assertNotIn("EXAMPLE-SECRET-TOKEN", json.dumps(dict(row)))

    def test_transport_error_categories_are_bounded(self):
        import urllib.error

        from smartwatch_clank.notifications.discord import _classify_transport_error

        cases = [
            (urllib.error.URLError("name resolution failed"), "connection_error"),
            (urllib.error.URLError(TimeoutError("read timed out")), "timeout"),
            (TimeoutError("timed out"), "timeout"),
            (ConnectionResetError("reset by peer"), "connection_error"),
            (ValueError("unknown url type"), "invalid_webhook_url"),
            (RuntimeError("???"), "transport_error"),
        ]
        for exc, expected in cases:
            self.assertEqual(_classify_transport_error(exc), expected)

    def test_http_error_bodies_are_never_persisted(self):
        from smartwatch_clank.notifications.discord import _status_category

        self.assertEqual(_status_category(429), "http_429_rate_limited")
        self.assertEqual(_status_category(401), "http_unauthorized")
        self.assertEqual(_status_category(404), "http_404_webhook_not_found")
        self.assertEqual(_status_category(400), "http_400_client_error")
        self.assertEqual(_status_category(502), "http_502_server_error")


class CliIntegrationTests(DeliveryBase):
    def setUp(self):
        super().setUp()
        # The CLI passes the repo's real production allowlist to the runner,
        # so the CLI-level collector must be a genuinely allowlisted name.
        self.cli_collector = DummyCollector(
            "samsung_product_catalogue",
            items=(observation("samsung_product_catalogue", "watch-cli-1", price="100"),),
            tier=CollectorTier.PRODUCTION,
        )
        self.registry.register(self.cli_collector)

    def test_collector_success_remains_success_when_discord_is_down(self):
        """Full CLI run: healthy collection + a dead Discord must still exit 0
        with the run's own results intact, and the durable intent survives."""
        main(["--database", str(self.db), "run", "--no-lock"], self.registry)  # baseline
        self.cli_collector.items = (observation("samsung_product_catalogue", "watch-cli-1", price="120"),)
        import urllib.error

        original_urlopen = discord_module._urlopen
        def dead_urlopen(request, timeout):
            raise urllib.error.URLError("network down")
        discord_module._urlopen = dead_urlopen
        env_before = dict(__import__("os").environ)
        __import__("os").environ[DISCORD_WEBHOOK_ENV] = WEBHOOK
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(["--database", str(self.db), "run", "--no-lock"], self.registry)
        finally:
            discord_module._urlopen = original_urlopen
            __import__("os").environ.clear()
            __import__("os").environ.update(env_before)
        self.assertEqual(status, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["failed"], 0)
        self.assertTrue(all(item["healthy"] for item in result["outcomes"]))
        self.assertEqual(result["notifications"]["sent"], 0)
        self.assertEqual(result["notifications"]["failed"], 0)
        self.assertEqual(result["notifications"]["remaining"], 1)  # durable, retryable
        row = self.store.pending_notifications(PROVIDER)[0]
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["last_error"], "connection_error")

    def test_cli_run_without_webhook_reports_remaining_and_stays_healthy(self):
        main(["--database", str(self.db), "run", "--no-lock"], self.registry)  # baseline
        self.cli_collector.items = (observation("samsung_product_catalogue", "watch-cli-1", price="120"),)
        env_before = dict(__import__("os").environ)
        __import__("os").environ.pop(DISCORD_WEBHOOK_ENV, None)
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(["--database", str(self.db), "run", "--no-lock"], self.registry)
        finally:
            __import__("os").environ.clear()
            __import__("os").environ.update(env_before)
        self.assertEqual(status, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["notifications"]["remaining"], 1)

    def test_cli_deliver_preview_touches_nothing(self):
        self.run_baseline_and_change()
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["--database", str(self.db), "deliver", "--preview",
                           "--cutoff", utc_now().isoformat()], self.registry)
        self.assertEqual(status, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["proposed_cutoff_effect"]["would_hold"], 1)
        self.assertEqual(result["proposed_cutoff_effect"]["would_send"], 0)
        self.assertFalse(result["webhook_configured"])
        self.assertEqual(self.store.notification_counts(PROVIDER), {"pending": 1})

    def test_cli_test_notify_refuses_configured_webhook_without_approval(self):
        env_before = dict(__import__("os").environ)
        __import__("os").environ[DISCORD_WEBHOOK_ENV] = WEBHOOK
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(["--database", str(self.db), "test-notify",
                               "--note", "must be refused"], self.registry)
        finally:
            __import__("os").environ.clear()
            __import__("os").environ.update(env_before)
        self.assertEqual(status, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "refused")
        self.assertEqual(self.store.notification_counts(PROVIDER), {})

    def test_cli_test_notify_with_local_webhook_sends_only_a_test_row(self):
        calls: list = []
        captured: dict = {}
        original_urlopen = discord_module._urlopen
        class FakeResponse:
            status = 204
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        def fake_urlopen(request, timeout):
            calls.append(request.full_url)
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()
        discord_module._urlopen = fake_urlopen
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(["--database", str(self.db), "test-notify",
                               "--webhook", "https://example.invalid/hook",
                               "--note", "local check"], self.registry)
        finally:
            discord_module._urlopen = original_urlopen
        self.assertEqual(status, 0)
        self.assertEqual(calls, ["https://example.invalid/hook"])
        (embed,) = captured["payload"]["embeds"]
        self.assertEqual(embed["title"], "SMARTWATCH CLANK — TEST")
        counts = self.store.notification_counts(PROVIDER)
        self.assertEqual(counts, {"sent": 1})
        row = self.store.notifications_by_status(PROVIDER, "sent", limit=10)[0]
        self.assertIsNone(row["discovery_id"])  # never a real Discovery row

    def test_cli_notifications_and_diagnose_are_json_and_safe(self):
        self.run_baseline_and_change()
        for command in (["notifications"],
                        ["notifications", "--status", "pending"],
                        ["diagnose-notifications"]):
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(["--database", str(self.db), *command], self.registry)
            self.assertEqual(status, 0)
            text = output.getvalue()
            json.loads(text)
            self.assertNotIn("EXAMPLE-SECRET-TOKEN", text)
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["--database", str(self.db), "diagnose-notifications"], self.registry)
        result = json.loads(output.getvalue())
        self.assertEqual(result["counts_by_status"], {"pending": 1})
        self.assertFalse(result["webhook_configured"])
        self.assertEqual(result["provenance_gaps"]["null_discovery_id"], 0)

    def test_cli_delivery_activation_roundtrip(self):
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["--database", str(self.db), "delivery-activation"], self.registry)
        result = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertFalse(result["activation_policy"]["configured"])
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["--database", str(self.db), "delivery-activation",
                           "--set", "2026-09-10T00:00:00Z"], self.registry)
        self.assertEqual(status, 0)
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["--database", str(self.db), "delivery-activation",
                           "--set", "garbage"], self.registry)
        self.assertEqual(status, 2)  # refused, nothing persisted
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["--database", str(self.db), "delivery-activation"], self.registry)
        result = json.loads(output.getvalue())
        self.assertEqual(result["activation_policy"]["cutoff"], "2026-09-10T00:00:00+00:00")


class EligibilityConstantsTests(DeliveryBase):
    def test_gate_covers_exactly_critical_and_newsworthy(self):
        self.assertEqual(DELIVERY_ELIGIBLE_LEVELS,
                         {EditorialLevel.CRITICAL, EditorialLevel.NEWSWORTHY})
        self.assertNotIn(EditorialLevel.MONITOR, DELIVERY_ELIGIBLE_LEVELS)
        self.assertNotIn(EditorialLevel.NOISE, DELIVERY_ELIGIBLE_LEVELS)


if __name__ == "__main__":
    unittest.main()
