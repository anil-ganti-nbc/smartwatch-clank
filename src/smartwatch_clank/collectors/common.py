"""Generic HTTP fetch client shared by non-Samsung collectors.

Deliberately independent from `collectors/samsung/common.py`'s
`UrlLibHttpClient`, even though the implementation is nearly identical:
`tests/test_http_retry.py` patches `smartwatch_clank.collectors.samsung.
common.time.sleep`/`...urlopen` directly, and `unittest.mock.patch`
resolves those names wherever the patched class is *defined*, not wherever
it's imported. Moving the Samsung client out from under that module would
break its test for no benefit. New OEM collectors (news, and later
catalogue/support) import from here instead.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

from ..core.health import ProxyUnreachableError, SourceHostBlockedError, SourceRateLimitedError


class HttpClient(Protocol):
    def get_text(self, url: str) -> str: ...
    def get_json(self, url: str) -> Any: ...


class UrlLibHttpClient:
    """Direct-fetch HTTP client, optionally routed through a per-instance HTTP proxy.

    `proxy_url` is opt-in and per-instance: when set, requests go through an
    `OpenerDirector` built just for this client (never `urllib.request.
    install_opener`), so one proxied client can never leak the proxy onto
    any other client instance sharing the process -- e.g. the Garmin relay
    (see docs/garmin-egress-relay.md) must never affect Amazfit/COROS/Apple
    clients constructed with the default `proxy_url=None`.
    """

    def __init__(self, timeout: float = 45.0, attempts: int = 3, retry_delay: float = 0.25,
                 proxy_url: str | None = None, proxy_probe_timeout: float = 3.0) -> None:
        self.timeout = timeout
        self.attempts = attempts
        self.retry_delay = retry_delay
        self.proxy_url = proxy_url
        self.proxy_probe_timeout = proxy_probe_timeout
        self._opener = (
            urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
            if proxy_url else None
        )

    def _check_proxy_reachable(self) -> None:
        # Cheap (plain TCP connect, no HTTP) so a dead relay tunnel fails
        # fast and distinctly from a real fetch attempt against the source,
        # rather than burning the full retry/timeout budget on a proxy that
        # was never going to answer.
        assert self.proxy_url is not None
        parsed = urllib.parse.urlparse(self.proxy_url)
        host, port = parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            with socket.create_connection((host, port), timeout=self.proxy_probe_timeout):
                return
        except OSError as exc:
            raise ProxyUnreachableError(f"egress proxy {self.proxy_url} unreachable: {exc}") from exc

    def get_text(self, url: str) -> str:
        if self.proxy_url:
            self._check_proxy_reachable()
        opener_open = self._opener.open if self._opener else urllib.request.urlopen
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            request = urllib.request.Request(url, headers={
                "User-Agent": "SmartwatchClank/0.2 (+primary-source research)",
                "Accept": "application/rss+xml,application/atom+xml,application/xml,application/json,text/html;q=0.9,*/*;q=0.8",
            })
            try:
                with opener_open(request, timeout=self.timeout) as response:
                    return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                if exc.code == 403:
                    raise SourceHostBlockedError(f"HTTP 403 fetching {url}") from exc
                if exc.code == 429:
                    raise SourceRateLimitedError(f"HTTP 429 fetching {url}") from exc
                last_error = exc
                if attempt + 1 < self.attempts:
                    time.sleep(self.retry_delay * (attempt + 1))
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.attempts:
                    time.sleep(self.retry_delay * (attempt + 1))
        assert last_error is not None
        raise last_error

    def get_json(self, url: str) -> Any:
        return json.loads(self.get_text(url))
