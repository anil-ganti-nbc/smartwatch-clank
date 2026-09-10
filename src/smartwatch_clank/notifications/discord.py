"""Discord webhook delivery for persisted discoveries.

Outbox semantics: discovery persistence decides eligibility and writes a
fully rendered payload into the `notifications` table in the SAME transaction
as the discovery row (see SQLiteStore.save_run's `notification_intent`);
`drain()` posts whatever is still `pending` and marks each row `sent`, `held`,
`failed`, or leaves it `pending` with a durable retry floor. A dead webhook or
a killed process loses nothing — the intent is already durable before delivery
is attempted (persistence first).

No production webhook is ever contacted by anything in this module unless a
caller supplies `webhook_url` explicitly (tests never do).

Three delivery-safety properties are enforced here rather than assumed:

1. Serialization. Every path that drains a database takes one OS-level
   cross-process lock derived from that database's resolved path (a
   `.delivery.lock` sibling of the collection RunLock), held across
   select-send-record. Two overlapping senders cannot both read the same
   pending row and both post it.
2. Redaction. The webhook URL is a secret. No error returned, persisted,
   logged or printed by this module may contain it, so transport failures are
   reduced to bounded categories and HTTP status codes.
3. Activation policy. What may be sent is decided by
   `notifications/delivery_policy.py`, so switching delivery on for the first
   time cannot flush historical discoveries at a live channel.

What is deliberately *not* claimed: exactly-once delivery. An HTTP success
followed by a crash before the row is marked `sent` leaves an ambiguous window
in which the next drain re-posts it. Serialization removes concurrent
duplication; it does not remove that window, and no outbox that records after
sending can.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import ssl
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from ..core.lock import RunLock, RunLockError
from ..core.models import CollectorTier, Discovery, EditorialLevel
from .delivery_policy import (
    ACTIVATION_CUTOFF_KEY,
    HELD_REASONS,
    SEND,
    decide,
    load_policy,
    parse_timestamp,
)

log = logging.getLogger("smartwatch_clank.discord")

# Read from the environment only — never config.yaml/local.yaml, never the
# Docker image, never Git, never logs. Empty/unset means "no delivery
# configured": enqueueing still works, drain/test just report the failure
# category and leave queued rows pending.
DISCORD_WEBHOOK_ENV = "SMARTWATCH_CLANK_DISCORD_WEBHOOK_URL"

PROVIDER = "discord"

# Editorial gate (native discovery semantics, no new score): only
# CRITICAL and NEWSWORTHY leave the building. MONITOR and NOISE stay
# dashboard/QC-only — no notification row at all is created for them.
DELIVERY_ELIGIBLE_LEVELS = frozenset({EditorialLevel.CRITICAL, EditorialLevel.NEWSWORTHY})

# After this many failed attempts a notification stops being retried
# automatically and is marked terminally `failed`. An operator can still
# requeue it (deliver --requeue-failed).
MAX_ATTEMPTS = 5

# Ceiling on a server-supplied Retry-After. A hostile or broken header must
# not be able to park the queue indefinitely.
MAX_RETRY_AFTER_SECONDS = 15 * 60
# Used when a 429 arrives with no usable Retry-After header.
DEFAULT_RETRY_AFTER_SECONDS = 60

_LEVEL_TEXT = {
    EditorialLevel.CRITICAL: "CRITICAL",
    EditorialLevel.NEWSWORTHY: "NEWSWORTHY",
    EditorialLevel.MONITOR: "MONITOR",
    EditorialLevel.NOISE: "NOISE",
}

# Colour is a secondary cue only; the editorial level is always carried as
# text so the payload never relies on colour alone.
_LEVEL_COLOR = {
    EditorialLevel.CRITICAL: 0xC0392B,
    EditorialLevel.NEWSWORTHY: 0x2E86C1,
    EditorialLevel.MONITOR: 0x95A5A6,
    EditorialLevel.NOISE: 0x95A5A6,
}

# Human-relevant observation fields surfaced when they actually changed.
_WATCHED_FIELDS = (
    "title", "product_name", "model_number", "regional_model_number", "colour",
    "size", "connectivity", "price", "currency", "availability",
    "firmware_version", "software_version", "classification_state",
)


def resolve_webhook_url() -> str | None:
    return os.environ.get(DISCORD_WEBHOOK_ENV) or None


class DiscoveryNotifier(Protocol):
    def notify(self, discovery: Discovery) -> None: ...


# The transport handle is module-level so tests can substitute it; this
# repo's hard test wall means no test ever reaches the real network.
_urlopen = urllib.request.urlopen


# ---------------------------------------------------------------- redaction


def _classify_transport_error(exc: BaseException) -> str:
    """Map a transport exception to a bounded, secret-free category.

    The exception's message/repr is never used: transport reprs can embed
    the request URL, which for a Discord webhook *is* the credential. This
    project is stdlib-only by design, so transport errors are classified
    from urllib's exception tree, by type only."""
    if isinstance(exc, urllib.error.HTTPError):
        return _status_category(int(exc.code))
    reason = getattr(exc, "reason", None) or exc.__cause__ or exc
    if isinstance(reason, ssl.SSLCertVerificationError) or isinstance(reason, ssl.SSLError):
        return "tls_error"
    if isinstance(reason, socket.timeout) or isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(reason, ConnectionError) or isinstance(exc, ConnectionError):
        return "connection_error"
    if isinstance(reason, OSError) or isinstance(exc, OSError):
        return "connection_error"
    if isinstance(exc, (ValueError, urllib.error.URLError)):
        return "invalid_webhook_url"
    return "transport_error"


def _status_category(status_code: int) -> str:
    """Bounded category for a non-2xx response. The response *body* is never
    retained: Discord error payloads can echo request context, and an
    arbitrary remote string does not belong in local durable state."""
    if status_code == 429:
        return "http_429_rate_limited"
    if status_code in (401, 403):
        return "http_unauthorized"
    if status_code == 404:
        return "http_404_webhook_not_found"
    if 400 <= status_code < 500:
        return f"http_{status_code}_client_error"
    if 500 <= status_code < 600:
        return f"http_{status_code}_server_error"
    return f"http_{status_code}"


def _parse_retry_after(value: object) -> float | None:
    """Seconds from a Retry-After header, clamped. Only the numeric-seconds
    form is honoured; an HTTP-date form (or junk) falls back to the caller's
    default rather than being mis-parsed into a wrong instant."""
    if value is None:
        return None
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def _post_webhook(webhook_url: str, payload: dict) -> tuple[bool, str | None, int | None, float | None]:
    """Post one payload. Returns (ok, sanitized_error, http_status, retry_after_seconds).

    Never returns the URL, the token, or the response body.
    """
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "smartwatch-clank-notifier"},
    )
    try:
        with _urlopen(request, timeout=15) as response:
            return True, None, int(getattr(response, "status", 200)), None
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        retry_after = None
        if status == 429:
            retry_after = _parse_retry_after(exc.headers.get("Retry-After") if exc.headers else None)
            if retry_after is None:
                retry_after = DEFAULT_RETRY_AFTER_SECONDS
        return False, _status_category(status), status, retry_after
    except Exception as exc:  # bounded categories only; never the exception text
        return False, _classify_transport_error(exc), None, None


def _normalize_outcome(result) -> tuple[bool, str | None, int | None, float | None]:
    """Accept every sender shape.

    The swappable `sender` contract grew with the work that needed it:
    two-tuples `(ok, error)`, three-tuples `(ok, error, retry_after)` from
    the fleet's older fakes, and the current four-tuple. Existing fakes keep
    working unchanged rather than being rewritten to satisfy new code.
    """
    if isinstance(result, tuple):
        if len(result) == 4:
            ok, err, status, retry_after = result
            return bool(ok), err, status, retry_after
        if len(result) == 3:
            ok, err, retry_after = result
            return bool(ok), err, None, retry_after
        if len(result) == 2:
            ok, err = result
            return bool(ok), err, None, None
    raise TypeError("sender must return a 2/3/4-tuple")


# ---------------------------------------------------------------- payloads


def _fmt_value(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 90 else text[:87] + "..."


def _changed_lines(discovery: Discovery) -> list[str]:
    """Concise, human-relevant change evidence. Never dumps the stored
    previous/current JSON objects into the channel."""
    previous, current = discovery.previous or {}, discovery.current or {}
    if discovery.previous is None:
        facts = [f"{field}: {_fmt_value(current[field])}"
                 for field in ("product_name", "model_number", "title")
                 if current.get(field)]
        return facts[:4] or ["first evidence for this identity"]
    if discovery.current is None:
        return ["the previously observed evidence is no longer present"]
    lines = []
    for field in _WATCHED_FIELDS:
        old, new = previous.get(field), current.get(field)
        if old != new and (old is not None or new is not None):
            lines.append(f"{field}: {_fmt_value(old) if old is not None else '—'} → "
                         f"{_fmt_value(new) if new is not None else '—'}")
        if len(lines) >= 6:
            break
    if not lines:
        # comparable() differs somewhere outside the watched fields; say so
        # honestly instead of inventing detail.
        changed = sorted(str(key) for key in set(previous) | set(current)
                         if previous.get(key) != current.get(key))
        lines.append("changed: " + ", ".join(changed[:6]))
    return lines


def build_embed(discovery: Discovery, discovery_id: int | None = None) -> dict:
    """Compact, fleet-style embed. Header always names the clank; editorial
    level is carried as text (never colour alone); every line is a field
    already on the discovery or its evidence. Mobile-compact: no JSON blobs."""
    side = discovery.current or discovery.previous or {}
    title = f"SMARTWATCH CLANK — {discovery.change_type.value.replace('_', ' ')}"
    lines = [f"**Device:** {discovery.identity}"]
    oem = side.get("oem")
    if oem:
        lines.append(f"**OEM:** {oem}")
    region = side.get("region")
    if region:
        lines.append(f"**Region:** {region}")
    lines.append(f"**Level:** {_LEVEL_TEXT.get(discovery.editorial_level, discovery.editorial_level.value)}")
    lines.append(f"**Confidence:** {discovery.confidence.value}")
    lines.append(f"**Source:** {discovery.collector}")
    lines.append(f"**Detected:** {discovery.discovered_at.isoformat()}")
    lines.append("**Changed:**\n" + "\n".join(f"• {line}" for line in _changed_lines(discovery)))

    footer_parts = ["SMARTWATCH CLANK"]
    if discovery_id is not None:
        footer_parts.append(f"Discovery ID: {discovery_id}")

    embed = {
        "title": title[:256],
        "description": "\n".join(lines)[:4096],
        "color": _LEVEL_COLOR.get(discovery.editorial_level, 0x95A5A6),
        "url": discovery.source_url or None,
        "timestamp": discovery.discovered_at.isoformat(),
        "footer": {"text": " · ".join(footer_parts)[:2048]},
    }
    return {"embeds": [embed]}


def build_test_embed(note: str = "") -> dict:
    """An explicit, unmistakable test payload. Never references a real
    discovery or any editorial state — no `discoveries` row is read."""
    embed = {
        "title": "SMARTWATCH CLANK — TEST",
        "description": (
            "This is a test notification confirming Discord delivery wiring. "
            "It does not represent a real discovery." + (f"\n\n{note}" if note else "")
        ),
        "color": 0x95A5A6,
        "footer": {"text": "SMARTWATCH CLANK · owner field-test notification"},
    }
    return {"embeds": [embed]}


# ------------------------------------------------------------ enqueue gate


def notification_intent_factory(store, production_allowlist: tuple[str, ...]
                                ) -> Callable[[Any], Callable[[Discovery, int], None] | None]:
    """Build the per-collector intent factory handed to the Runner.

    A collector gets a notification intent only when it is production-tier
    AND explicitly allowlisted (the same two independent gates as
    RunScope.PRODUCTION — a promoted-but-unallowlisted collector stays
    silent, and an experimental collector is silent unless explicitly
    promoted). The returned per-discovery callback then applies the editorial
    gate: CRITICAL/NEWSWORTHY enqueue; MONITOR/NOISE produce no row at all
    (dashboard/QC-only). Baseline runs never reach this — the runner diffs
    nothing on baseline — and discoveries from unhealthy collector runs are
    never persisted, so they can never enqueue either.
    """
    allowed = set(production_allowlist)

    def factory(collector) -> Callable[[Discovery, int], None] | None:
        if collector.tier is not CollectorTier.PRODUCTION or collector.name not in allowed:
            return None

        def intent(discovery: Discovery, discovery_id: int) -> None:
            if discovery.editorial_level not in DELIVERY_ELIGIBLE_LEVELS:
                return
            store.notification_put(
                PROVIDER, f"discovery:{discovery_id}", build_embed(discovery, discovery_id),
                discovery_id=discovery_id, collector=discovery.collector,
                identity=discovery.identity,
                discovered_at=discovery.discovered_at.isoformat(), status="pending",
            )

        return intent

    return factory


# ----------------------------------------------------------------- notifier


def delivery_lock_path(db_path) -> Path:
    """Lock identity derives from the resolved database path, because the
    thing being serialized is *that database's outbox*. Distinct from the
    collection RunLock: a standalone `deliver` must exclude a concurrent
    run's drain even though neither takes the other's collection lock."""
    return Path(str(db_path)).resolve().with_suffix(".delivery.lock")


class DeliveryBusy(Exception):
    """Another process holds this database's delivery grant."""


def _hold_lock(store) -> RunLock | None:
    """Acquire the database's delivery grant, or raise DeliveryBusy."""
    db_path = getattr(store, "path", None) or getattr(store, "db_path", None)
    if not db_path or str(db_path) == ":memory:":
        # An in-memory database has no cross-process identity and cannot
        # be shared, so there is nothing to serialize against.
        return None
    lock = RunLock(delivery_lock_path(db_path))
    try:
        lock.acquire()
    except RunLockError as exc:
        raise DeliveryBusy(str(exc)) from exc
    return lock


class DiscordNotifier:
    """Thin wrapper around the store's notification outbox + a sender
    function. `sender` is swappable: tests pass a fake, so no test in this
    repo ever needs a real Discord webhook (the hard test wall)."""

    def __init__(self, store, webhook_url: str | None,
                 sender: Callable[[str, dict], tuple] = _post_webhook) -> None:
        self.store = store
        self.webhook_url = webhook_url
        self.sender = sender

    # -- enqueue ---------------------------------------------------------

    def enqueue_test(self, note: str = "") -> dict:
        """Owner field-test support: a marked SMARTWATCH CLANK — TEST message
        through the real send path, never a fabricated Discovery row and
        never any real pending queue content. Delivered synchronously for
        immediate pass/fail feedback. Takes the same delivery grant as
        drain(): a test send during an active drain would otherwise add an
        uncoordinated request against the same rate limit."""
        dedup_key = f"test:{uuid.uuid4().hex}"
        payload = build_test_embed(note)
        with self.store.connection:
            self.store.notification_put(PROVIDER, dedup_key, payload, status="pending")
        row = self.store.notification_by_dedup_key(PROVIDER, dedup_key)
        if row is None:
            return {"sent": False, "error": "enqueue_failed", "dedup_key": dedup_key}
        if not self.webhook_url:
            self.store.mark_notification(row["id"], "failed", "no webhook configured")
            return {"sent": False, "error": "no webhook configured", "dedup_key": dedup_key}
        try:
            lock = _hold_lock(self.store)
        except DeliveryBusy:
            # Leave the row pending and untouched; a later drain sends it.
            return {"sent": False, "error": "delivery_busy", "dedup_key": dedup_key,
                    "status": "delivery_busy"}
        try:
            ok, err, http_status, _retry_after = _normalize_outcome(
                self.sender(self.webhook_url, payload))
        finally:
            if lock is not None:
                lock.release()
        self.store.mark_notification(row["id"], "sent" if ok else "failed", err, http_status)
        return {"sent": ok, "error": err, "dedup_key": dedup_key}

    # -- drain -----------------------------------------------------------

    def _eligible_now(self, row) -> bool:
        """False while a durable retry floor (429 Retry-After) is in force."""
        keys = row.keys() if hasattr(row, "keys") else []
        if "not_before" not in keys:
            return True
        floor = parse_timestamp(row["not_before"])
        if floor is None:
            return True
        return datetime.now(timezone.utc) >= floor

    def _selected_rows(self, include_held: bool) -> list:
        if include_held:
            # The explicit operator replay: held history joins the queue and
            # the activation policy is bypassed for exactly this call.
            return self.store.connection.execute(
                "SELECT * FROM notifications WHERE provider=? AND status IN ('pending','held') "
                "ORDER BY id", (PROVIDER,),
            ).fetchall()
        return self.store.pending_notifications(PROVIDER)

    def drain(self, *, include_held: bool = False) -> dict:
        """Attempt delivery of every eligible queued notification. Never
        raises — a Discord outage degrades delivery, not collection; the
        caller always gets a summary dict back.

        The activation policy (delivery_policy) holds anything predating the
        operator's cutoff by marking it `held` — attempts untouched, payload
        preserved. `include_held=True` is the separate, explicit operator
        action that replays that history; nothing sets it automatically.
        """
        if not self.webhook_url:
            rows = self.store.pending_notifications(PROVIDER)
            if rows:
                log.warning(
                    "%d notification(s) pending but no webhook configured "
                    "(set %s)", len(rows), DISCORD_WEBHOOK_ENV,
                )
            return {"sent": 0, "failed": 0, "held": 0, "deferred": 0,
                    "remaining": len(rows)}

        try:
            lock = _hold_lock(self.store)
        except DeliveryBusy as exc:
            # Explicit, non-destructive refusal: nothing selected for send,
            # no attempt counter touched, no row mutated.
            log.warning("delivery skipped: another process holds the delivery grant")
            return {"status": "delivery_busy", "sent": 0, "failed": 0, "held": 0,
                    "deferred": 0, "remaining": len(self.store.pending_notifications(PROVIDER)),
                    "detail": str(exc)}

        sent = failed = held = deferred = 0
        held_by_reason: dict[str, int] = {}
        rate_limited = False
        try:
            policy = load_policy(self.store.policy_get(ACTIVATION_CUTOFF_KEY))
            # Re-select under the grant: rows may have changed between the
            # unlocked count above and holding exclusivity.
            rows = self._selected_rows(include_held)
            for row in rows:
                if not self._eligible_now(row):
                    deferred += 1
                    continue
                if include_held:
                    verdict = SEND
                else:
                    verdict = decide(row["discovered_at"], policy)
                if verdict in HELD_REASONS:
                    self.store.mark_notification(row["id"], "held", verdict, count_attempt=False)
                    held += 1
                    held_by_reason[verdict] = held_by_reason.get(verdict, 0) + 1
                    continue

                payload = json.loads(row["payload_json"])
                ok, err, http_status, retry_after = _normalize_outcome(
                    self.sender(self.webhook_url, payload))
                if ok:
                    self.store.mark_notification(row["id"], "sent", http_status=http_status)
                    sent += 1
                    continue

                if err == "http_429_rate_limited":
                    # Do not burn an attempt and do not keep hammering the
                    # rest of the queue into an active rate limit: record a
                    # durable floor for this row and stop this drain.
                    wait = retry_after if retry_after is not None else DEFAULT_RETRY_AFTER_SECONDS
                    until = datetime.now(timezone.utc) + timedelta(seconds=wait)
                    self.store.defer_notification(row["id"], until.isoformat())
                    deferred += 1
                    rate_limited = True
                    log.warning(
                        "discord rate limited; deferring delivery for %.0fs (%d row(s) not attempted)",
                        wait, max(0, len(rows) - (sent + failed + held + deferred)),
                    )
                    break

                attempts_next = row["attempts"] + 1
                if attempts_next >= MAX_ATTEMPTS:
                    self.store.mark_notification(row["id"], "failed", err, http_status)
                    failed += 1
                    log.warning("discord delivery permanently failed after %d attempts: %s",
                                attempts_next, err)
                else:
                    self.store.mark_notification(row["id"], "pending", err, http_status)
                    log.warning("discord delivery attempt %d/%d failed, will retry: %s",
                                attempts_next, MAX_ATTEMPTS, err)
        finally:
            if lock is not None:
                lock.release()

        remaining_rows = self.store.connection.execute(
            "SELECT COUNT(*) FROM notifications WHERE provider=? AND status='pending'",
            (PROVIDER,),
        ).fetchone()[0]
        result = {"sent": sent, "failed": failed, "held": held,
                  "deferred": deferred, "remaining": remaining_rows}
        if held_by_reason:
            result["held_by_reason"] = held_by_reason
        if rate_limited:
            result["status"] = "rate_limited"
        return result


# ----------------------------------------------------------------- preview


def delivery_preview(store, proposed_cutoff: str | None, webhook_configured: bool) -> dict:
    """Assemble the read-only activation preview.

    Answers the only question that matters before switching delivery on for
    the first time: if I activate with this cutoff, what exactly goes out,
    and what stays held? Nothing here writes, sends, or resolves a webhook
    URL, so no secret can appear in the output."""
    rows = store.pending_notifications(PROVIDER)
    stored = store.policy_get(ACTIVATION_CUTOFF_KEY)
    preview: dict[str, Any] = {
        "provider": PROVIDER,
        "counts_by_status": store.notification_counts(PROVIDER),
        "installed_activation_policy": load_policy(stored).describe(),
        "webhook_configured": webhook_configured,
        "provenance_gaps": store.provenance_gap_counts(PROVIDER),
    }
    for label, raw in (("installed", stored), ("proposed", proposed_cutoff)):
        if raw is None:
            continue
        policy = load_policy(raw)
        tally: dict[str, int] = {}
        for row in rows:
            verdict = decide(row["discovered_at"], policy)
            tally[verdict] = tally.get(verdict, 0) + 1
        preview[f"{label}_cutoff_effect"] = {
            "cutoff": policy.describe(),
            "would_send": tally.get(SEND, 0),
            "would_hold": sum(v for k, v in tally.items() if k in HELD_REASONS),
            "breakdown": tally,
        }
    return preview
