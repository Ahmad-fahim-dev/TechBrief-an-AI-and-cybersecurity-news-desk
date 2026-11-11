import asyncio
import json
from datetime import datetime, timezone
from email.utils import format_datetime

import httpx

from backend.app.config import Settings
from backend.app.services.collector import collect_recent_articles


def test_rss_collection_keeps_only_recent_https_entries():
    now = datetime.now(timezone.utc)
    feed_date = format_datetime(now)
    old_date = format_datetime(now.replace(year=now.year - 1))
    payload = f"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item><title>New AI research release</title><link>https://example.org/new</link>
        <description>Fresh source summary</description><pubDate>{feed_date}</pubDate>
        <enclosure url="https://cdn.example.org/new.jpg" type="image/jpeg"/></item>
      <item><title>Old research</title><link>https://example.org/old</link>
        <description>Old item</description><pubDate>{old_date}</pubDate></item>
      <item><title>Unsafe link</title><link>http://example.org/unsafe</link>
        <description>Insecure link</description><pubDate>{feed_date}</pubDate></item>
    </channel></rss>"""

    def handler(request):
        return httpx.Response(200, text=payload, request=request)

    settings = Settings(
        _env_file=None,
        rss_feeds_json=json.dumps([{
            "category": "ai",
            "name": "Example Research",
            "url": "https://feeds.example.org/rss",
        }]),
    )
    articles, warnings = asyncio.run(collect_recent_articles(
        settings,
        {"ai"},
        transport=httpx.MockTransport(handler),
    ))

    assert [article["title"] for article in articles] == ["New AI research release"]
    assert articles[0]["published_at"].tzinfo == timezone.utc
    assert articles[0]["image_candidates"] == [{"url": "https://cdn.example.org/new.jpg", "alt": ""}]
    assert any("not independently cross-confirmed" in warning for warning in warnings)
