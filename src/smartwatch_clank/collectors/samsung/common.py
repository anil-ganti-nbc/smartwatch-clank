from __future__ import annotations

import re
import time
import urllib.request
from dataclasses import dataclass
from typing import Protocol


MODEL_RE = re.compile(r"\bSM-[A-Z][A-Z0-9]{3,}\b", re.I)
BASE_MODEL_RE = re.compile(r"\b(SM-[A-Z]\d{3})", re.I)


class HttpClient(Protocol):
    def get_text(self, url: str) -> str: ...


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
                "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8",
            })
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.attempts:
                    time.sleep(self.retry_delay * (attempt + 1))
        assert last_error is not None
        raise last_error


@dataclass(frozen=True, slots=True)
class SamsungRegion:
    code: str
    locale_path: str
    name: str

    @property
    def catalogue_url(self) -> str:
        return f"https://www.samsung.com/{self.locale_path}/watches/all-watches/"

    @property
    def support_sitemap_url(self) -> str:
        return f"https://www.samsung.com/{self.locale_path}/support/sitemap.xml"


PRODUCT_REGIONS = (
    SamsungRegion("US", "us", "United States"),
    SamsungRegion("GB", "uk", "United Kingdom"),
    SamsungRegion("IN", "in", "India"),
    SamsungRegion("DE", "de", "Germany"),
    SamsungRegion("KR", "sec", "South Korea"),
)
SUPPORT_REGIONS = (
    SamsungRegion("IN", "in", "India"),
    SamsungRegion("GB", "uk", "United Kingdom"),
    SamsungRegion("DE", "de", "Germany"),
)


def extract_model(value: str) -> str | None:
    match = MODEL_RE.search(value.replace("-sku-", "-"))
    if not match:
        return None
    model = match.group(0).upper().rstrip("-/")
    return model


def base_model(model: str | None) -> str | None:
    match = BASE_MODEL_RE.search(model or "")
    return match.group(1).upper() if match else None
