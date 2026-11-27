import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import feedparser
import httpx

from backend.app.config import Settings
from backend.app.logging_utils import clean_for_log

logger = logging.getLogger(__name__)

DEFAULT_FEEDS = [
    {"category": "ai", "name": "OpenAI", "url": "https://openai.com/news/rss.xml"},
    {"category": "ai", "name": "Google DeepMind", "url": "https://deepmind.google/blog/rss.xml"},
    {"category": "ai", "name": "Microsoft Research", "url": "https://www.microsoft.com/en-us/research/feed/"},
    {"category": "ai", "name": "Hugging Face", "url": "https://huggingface.co/blog/feed.xml"},
    {"category": "ai", "name": "arXiv AI", "url": "https://export.arxiv.org/rss/cs.AI"},
    {"category": "cybersecurity", "name": "CISA", "url": "https://www.cisa.gov/news.xml"},
    {"category": "cybersecurity", "name": "NIST NVD", "url": "https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss.xml"},
    {"category": "cybersecurity", "name": "BleepingComputer", "url": "https://www.bleepingcomputer.com/feed/"},
    {"category": "cybersecurity", "name": "The Hacker News", "url": "https://feeds.feedburner.com/TheHackersNews"},
    {"category": "cybersecurity", "name": "Krebs on Security", "url": "https://krebsonsecurity.com/feed/"},
]


def configured_feeds(settings: Settings) -> list[dict[str, str]]:
    if not settings.rss_feeds_json:
        return DEFAULT_FEEDS
    try:
        entries = json.loads(settings.rss_feeds_json)
    except json.JSONDecodeError as error:
        raise ValueError("RSS_FEEDS_JSON must contain a JSON array") from error
    if not isinstance(entries, list):
        raise ValueError("RSS_FEEDS_JSON must contain a JSON array")
    feeds = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("category") not in {"ai", "cybersecurity"}:
            continue
        parsed = urlparse(str(entry.get("url", "")))
        if parsed.scheme != "https" or not parsed.hostname:
            continue
        feeds.append({
            "category": entry["category"],
            "name": str(entry.get("name") or parsed.hostname)[:180],
            "url": str(entry["url"]),
        })
    return feeds


def _entry_datetime(entry: Any) -> datetime | None:
    for field in ("published", "updated", "created"):
        raw = entry.get(field)
        if not raw:
            continue
        try:
            value = parsedate_to_datetime(raw)
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            parsed = entry.get(f"{field}_parsed")
            if parsed:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def _search_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def collect_recent_articles(
    settings: Settings,
    categories: set[str],
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    collected: list[dict[str, Any]] = []
    warnings: list[str] = []
    feeds = [feed for feed in configured_feeds(settings) if feed["category"] in categories]
    headers = {"User-Agent": "TechBrief/1.0 (+daily public news aggregation)"}

    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds, follow_redirects=False, headers=headers, transport=transport
    ) as client:
        results = await asyncio.gather(
            *(client.get(feed["url"]) for feed in feeds),
            return_exceptions=True,
        )
    for feed, result in zip(feeds, results, strict=True):
        if isinstance(result, Exception):
            logger.warning("RSS source request failed for %s: %s", clean_for_log(feed["name"]), result)
            warnings.append(f"Could not fetch {feed['name']}.")
            continue
        if not 200 <= result.status_code < 300:
            logger.warning("RSS source %s returned HTTP %s", clean_for_log(feed["name"]), result.status_code)
            warnings.append(f"{feed['name']} returned HTTP {result.status_code}.")
            continue
        parsed = feedparser.parse(result.content)
        if parsed.bozo:
            logger.warning("RSS source %s returned malformed feed content", clean_for_log(feed["name"]))
            warnings.append(f"{feed['name']} returned malformed feed content.")
        for entry in parsed.entries:
            published = _entry_datetime(entry)
            link = str(entry.get("link", "")).strip()
            parsed_url = urlparse(link)
            title = str(entry.get("title", "")).strip()
            if not published or published < cutoff or published > now or not title:
                continue
            if parsed_url.scheme != "https" or not parsed_url.hostname:
                continue
            collected.append({
                "title": title[:500],
                "description": str(entry.get("summary", entry.get("description", "")))[:12000],
                "category": feed["category"],
                "source_name": feed["name"],
                "source_url": link,
                "published_at": published,
                "image_candidates": _rss_image_candidates(entry),
            })

    if settings.tavily_api_key:
        queries = {
            "ai": "artificial intelligence AI model research product news",
            "cybersecurity": "cybersecurity vulnerability threat advisory news",
        }
        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds, follow_redirects=False, headers=headers, transport=transport
        ) as client:
            search_jobs = [
                (category, client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": settings.tavily_api_key,
                        "query": queries[category],
                        "topic": "news",
                        "days": 1,
                        "max_results": 10,
                        "include_answer": False,
                    },
                ))
                for category in categories
            ]
            search_results = await asyncio.gather(
                *(job for _, job in search_jobs),
                return_exceptions=True,
            )
        known_urls = {item["source_url"] for item in collected}
        for (category, _), result in zip(search_jobs, search_results, strict=True):
            if isinstance(result, Exception):
                logger.warning("Tavily search failed for %s: %s", category, result)
                warnings.append(f"Tavily search failed for {category}.")
                continue
            if not 200 <= result.status_code < 300:
                logger.warning("Tavily search returned HTTP %s for %s", result.status_code, category)
                warnings.append(f"Tavily search returned HTTP {result.status_code} for {category}.")
                continue
            try:
                payload = result.json()
            except ValueError:
                warnings.append(f"Tavily returned an unreadable response for {category}.")
                continue
            results = payload.get("results", []) if isinstance(payload, dict) else []
            if not isinstance(results, list):
                warnings.append(f"Tavily returned an invalid results list for {category}.")
                continue
            for item in results:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url", "")).strip()
                parsed_url = urlparse(url)
                published = _search_datetime(item.get("published_date"))
                title = str(item.get("title", "")).strip()
                if (
                    not title or not published or published < cutoff or published > now
                    or parsed_url.scheme != "https" or not parsed_url.hostname or url in known_urls
                ):
                    continue
                collected.append({
                    "title": title[:500],
                    "description": str(item.get("content", ""))[:12000],
                    "category": category,
                    "source_name": parsed_url.hostname,
                    "source_url": url,
                    "published_at": published,
                    "image_candidates": [],
                })
                known_urls.add(url)
    if not feeds:
        warnings.append("No RSS feeds are enabled for the selected categories.")
    if collected:
        warnings.append("Publication dates and HTTPS links were checked against provider/feed metadata; major claims are not independently cross-confirmed.")
    return collected, warnings


def _rss_image_candidates(entry: Any) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for field in ("media_content", "media_thumbnail"):
        for media in entry.get(field, []) or []:
            if not isinstance(media, dict):
                continue
            url = str(media.get("url", "")).strip()
            if url:
                candidates.append({"url": url, "alt": str(media.get("title") or media.get("description") or "")})
    for enclosure in entry.get("enclosures", []) or []:
        if not isinstance(enclosure, dict):
            continue
        url = str(enclosure.get("href") or enclosure.get("url") or "").strip()
        media_type = str(enclosure.get("type") or "").lower()
        if url and media_type.startswith("image/"):
            candidates.append({"url": url, "alt": ""})
    for link in entry.get("links", []) or []:
        if not isinstance(link, dict):
            continue
        url = str(link.get("href") or "").strip()
        if (
            str(link.get("rel", "")).lower() == "enclosure"
            and str(link.get("type", "")).lower().startswith("image/")
            and url
        ):
            candidates.append({"url": url, "alt": ""})
    inline_parser = _InlineImageParser()
    inline_content = entry.get("content", []) or []
    for content in inline_content:
        if isinstance(content, dict):
            inline_parser.feed(str(content.get("value", ""))[:12000])
    inline_parser.feed(str(entry.get("summary", entry.get("description", "")))[:12000])
    candidates.extend(inline_parser.images)
    unique: dict[str, dict[str, str]] = {}
    for candidate in candidates:
        unique.setdefault(candidate["url"], candidate)
    return list(unique.values())[:6]


class _InlineImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        values = {key.lower(): value for key, value in attrs if value}
        url = str(values.get("src") or values.get("data-src") or "").strip()
        if url:
            self.images.append({"url": url, "alt": str(values.get("alt", "")).strip()})
