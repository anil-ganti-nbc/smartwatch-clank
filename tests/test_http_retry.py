import unittest
from unittest.mock import MagicMock, patch

from smartwatch_clank.collectors.samsung.common import UrlLibHttpClient


class HttpRetryTests(unittest.TestCase):
    @patch("smartwatch_clank.collectors.samsung.common.time.sleep")
    @patch("smartwatch_clank.collectors.samsung.common.urllib.request.urlopen")
    def test_transient_request_is_retried_with_a_bound(self, urlopen, sleep):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"ok"
        response.__enter__.return_value.headers.get_content_charset.return_value = "utf-8"
        urlopen.side_effect = [TimeoutError("transient"), response]
        client = UrlLibHttpClient(attempts=3, retry_delay=0)
        self.assertEqual(client.get_text("https://www.samsung.com/test"), "ok")
        self.assertEqual(urlopen.call_count, 2)

    @patch("smartwatch_clank.collectors.samsung.common.time.sleep")
    @patch("smartwatch_clank.collectors.samsung.common.urllib.request.urlopen")
    def test_persistent_failure_is_not_hidden(self, urlopen, sleep):
        urlopen.side_effect = TimeoutError("persistent")
        with self.assertRaises(TimeoutError):
            UrlLibHttpClient(attempts=3, retry_delay=0).get_text("https://www.samsung.com/test")
        self.assertEqual(urlopen.call_count, 3)


if __name__ == "__main__":
    unittest.main()
