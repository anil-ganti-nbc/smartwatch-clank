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
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

from ..core.health import SourceHostBlockedError, SourceRateLimitedError


class HttpClient(Protocol):
    def get_text(self, url: str) -> str: ...
    def get_json(self, url: str) -> Any: ...


class UrlLibHttpClient:
    def __init__(self, timeout: float = 45.0, attempts: int = 3, retry_delay: float = 0.25) -> None:
        self.timeout = timeout
        self.attempts = attempts
        self.retry_delay = retry_delay

    def get_text(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            request = urllib.request.Request(url, headers={
                "User-Agent": "SmartwatchClank/0.2 (+primary-source research)",
                "Accept": "application/rss+xml,application/atom+xml,application/xml,application/json,text/html;q=0.9,*/*;q=0.8",
            })
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
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
