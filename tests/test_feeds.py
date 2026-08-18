import unittest

from smartwatch_clank.collectors.feeds import parse_feed

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Galaxy Watch9 launches worldwide</title><link>https://example.com/a</link>
<guid isPermaLink="false">guid-a</guid><pubDate>Wed, 22 Jul 2026 09:00:00 +0000</pubDate>
<category>Mobile</category><category>Galaxy Watch9</category>
<description>Samsung launches its newest watch.</description></item>
<item><title>Unrelated article</title><link>https://example.com/b</link>
<guid isPermaLink="false">guid-b</guid></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Apple Watch Series 12 announced</title>
<link href="https://example.com/c" rel="alternate"/>
<id>id-c</id><updated>2026-08-20T09:00:00.000Z</updated>
<category term="PRESS RELEASE"/><category term="Apple Watch"/>
<content>New health sensors.</content></entry>
</feed>"""


class FeedParsingTests(unittest.TestCase):
    def test_rss_items_parse_with_categories_and_summary(self):
        items = parse_feed(RSS)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first.title, "Galaxy Watch9 launches worldwide")
        self.assertEqual(first.link, "https://example.com/a")
        self.assertEqual(first.guid, "guid-a")
        self.assertEqual(first.categories, ("Mobile", "Galaxy Watch9"))
        self.assertEqual(first.summary, "Samsung launches its newest watch.")

    def test_rss_item_without_category_or_description_still_parses(self):
        items = parse_feed(RSS)
        second = items[1]
        self.assertEqual(second.categories, ())
        self.assertIsNone(second.summary)

    def test_atom_entries_parse_link_href_and_category_term(self):
        items = parse_feed(ATOM)
        self.assertEqual(len(items), 1)
        entry = items[0]
        self.assertEqual(entry.title, "Apple Watch Series 12 announced")
        self.assertEqual(entry.link, "https://example.com/c")
        self.assertEqual(entry.guid, "id-c")
        self.assertEqual(entry.categories, ("PRESS RELEASE", "Apple Watch"))
        self.assertEqual(entry.summary, "New health sensors.")

    def test_malformed_xml_returns_empty_tuple(self):
        self.assertEqual(parse_feed("<not-valid-xml"), ())

    def test_empty_channel_returns_empty_tuple(self):
        self.assertEqual(parse_feed("<rss version=\"2.0\"><channel></channel></rss>"), ())


if __name__ == "__main__":
    unittest.main()
