"""Garmin egress-relay proxy: scoping and failure-isolation tests.

Covers the SMARTWATCH_CLANK_GARMIN_PROXY wiring in collectors/registry.py
and the per-instance proxy support in collectors/common.py's
UrlLibHttpClient -- see docs/garmin-egress-relay.md for the design.
"""
from __future__ import annotations

import os
import socket
import unittest
from unittest.mock import MagicMock, patch

from smartwatch_clank.collectors.common import UrlLibHttpClient
from smartwatch_clank.collectors.registry import build_registry
from smartwatch_clank.core.health import ProxyUnreachableError


def _fake_response(body: bytes = b"ok") -> MagicMock:
    response = MagicMock()
    response.__enter__.return_value.read.return_value = body
    response.__enter__.return_value.headers.get_content_charset.return_value = "utf-8"
    return response


class ProxyClientTests(unittest.TestCase):
    @patch("smartwatch_clank.collectors.common.socket.create_connection")
    @patch("smartwatch_clank.collectors.common.urllib.request.OpenerDirector.open")
    def test_uses_proxy_when_configured(self, opener_open, create_connection) -> None:
        create_connection.return_value.__enter__.return_value = MagicMock()
        opener_open.return_value = _fake_response(b"through-proxy")
        client = UrlLibHttpClient(proxy_url="http://127.0.0.1:18888")
        self.assertEqual(client.get_text("https://www.garmin.com/en-US/product-sitemap.xml"), "through-proxy")
        opener_open.assert_called_once()
        create_connection.assert_called_once_with(("127.0.0.1", 18888), timeout=client.proxy_probe_timeout)

    @patch("smartwatch_clank.collectors.common.urllib.request.urlopen")
    def test_stays_direct_when_proxy_unset(self, urlopen) -> None:
        urlopen.return_value = _fake_response(b"direct")
        client = UrlLibHttpClient()
        self.assertIsNone(client._opener)
        self.assertEqual(client.get_text("https://www.garmin.com/en-US/product-sitemap.xml"), "direct")
        urlopen.assert_called_once()

    @patch("smartwatch_clank.collectors.common.socket.create_connection")
    @patch("smartwatch_clank.collectors.common.urllib.request.OpenerDirector.open")
    def test_unreachable_proxy_fails_fast_without_attempting_fetch(self, opener_open, create_connection) -> None:
        create_connection.side_effect = OSError("connection refused")
        client = UrlLibHttpClient(proxy_url="http://127.0.0.1:18888", attempts=3)
        with self.assertRaises(ProxyUnreachableError):
            client.get_text("https://www.garmin.com/en-US/product-sitemap.xml")
        # The probe must reject before any retry-loop fetch attempt is made --
        # a dead tunnel must not burn the collector's normal retry budget.
        opener_open.assert_not_called()

    def test_proxy_url_never_leaks_between_client_instances(self) -> None:
        proxied = UrlLibHttpClient(proxy_url="http://127.0.0.1:18888")
        direct = UrlLibHttpClient()
        self.assertIsNotNone(proxied._opener)
        self.assertIsNone(direct._opener)
        self.assertIsNone(direct.proxy_url)


class RegistryScopingTests(unittest.TestCase):
    def _garmin_client(self, registry, name: str) -> UrlLibHttpClient:
        return registry.get(name).client

    @patch.dict(os.environ, {"SMARTWATCH_CLANK_GARMIN_PROXY": "http://127.0.0.1:18888"})
    def test_only_the_two_blocked_garmin_collectors_get_the_proxy(self) -> None:
        registry = build_registry()
        self.assertEqual(self._garmin_client(registry, "garmin_catalogue").proxy_url, "http://127.0.0.1:18888")
        self.assertEqual(self._garmin_client(registry, "garmin_official_news").proxy_url, "http://127.0.0.1:18888")
        # forums.garmin.com is not blocked -- must stay direct even with the
        # env var set, so it never depends on the relay tunnel being up.
        self.assertIsNone(self._garmin_client(registry, "garmin_updates").proxy_url)

    @patch.dict(os.environ, {"SMARTWATCH_CLANK_GARMIN_PROXY": "http://127.0.0.1:18888"})
    def test_non_garmin_collectors_ignore_the_proxy_env_var(self) -> None:
        registry = build_registry()
        for name in ("amazfit_catalogue", "amazfit_official_news", "coros_official_news",
                     "apple_official_news", "google_official_news"):
            with self.subTest(collector=name):
                self.assertIsNone(self._garmin_client(registry, name).proxy_url)

    def test_garmin_collectors_stay_direct_when_env_var_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SMARTWATCH_CLANK_GARMIN_PROXY", None)
            registry = build_registry()
            self.assertIsNone(self._garmin_client(registry, "garmin_catalogue").proxy_url)
            self.assertIsNone(self._garmin_client(registry, "garmin_official_news").proxy_url)


if __name__ == "__main__":
    unittest.main()
