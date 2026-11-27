import asyncio
import ipaddress
import logging
import socket
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from backend.app.logging_utils import clean_for_log

logger = logging.getLogger(__name__)
MAX_PAGE_BYTES = 1_000_000
MAX_IMAGE_BYTES = 12_000_000
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}


class _ImageMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, str] = {}
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs if value}
        if tag.lower() == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content", "")
            if key and content and key not in self.metadata:
                self.metadata[key] = content.strip()
        elif tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data.strip())


def _normalized_https_url(value: str, base: str | None = None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(urljoin(base, value) if base else value)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            return None
        return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))
    except ValueError:
        return None


def _is_public_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return False


async def _is_public_https_url(url: str) -> bool:
    normalized = _normalized_https_url(url)
    if not normalized:
        return False
    hostname = urlsplit(normalized).hostname
    if not hostname:
        return False
    if _is_public_ip(hostname):
        return True
    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo, hostname, 443, type=socket.SOCK_STREAM
        )
    except (OSError, UnicodeError):
        return False
    addresses = {record[4][0] for record in records}
    return bool(addresses) and all(_is_public_ip(address) for address in addresses)


async def _read_limited(response: httpx.Response, limit: int) -> bytes | None:
    length = response.headers.get("content-length")
    if length:
        try:
            if int(length) > limit:
                return None
        except ValueError:
            return None
    body = bytearray()
    async for chunk in response.aiter_bytes():
        remaining = limit + 1 - len(body)
        body.extend(chunk[:remaining])
        if len(body) > limit:
            return None
    return bytes(body)


async def _read_image_signature(response: httpx.Response) -> bytes | None:
    length = response.headers.get("content-length")
    if length:
        try:
            if int(length) > MAX_IMAGE_BYTES:
                return None
        except ValueError:
            return None
    prefix = bytearray()
    async for chunk in response.aiter_bytes():
        prefix.extend(chunk[:32 - len(prefix)])
        if len(prefix) >= 32:
            break
    return bytes(prefix)


def _looks_like_image(content_type: str, body: bytes) -> bool:
    if content_type not in ALLOWED_IMAGE_TYPES:
        return False
    if content_type == "image/jpeg":
        return body.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return body.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/gif":
        return body.startswith((b"GIF87a", b"GIF89a"))
    if content_type == "image/webp":
        return body.startswith(b"RIFF") and body[8:12] == b"WEBP"
    if content_type == "image/avif":
        return len(body) >= 12 and body[4:8] == b"ftyp" and b"avif" in body[8:16]
    return False


async def _get_page_metadata(client: httpx.AsyncClient, url: str) -> dict[str, str]:
    if not await _is_public_https_url(url):
        return {}
    try:
        async with client.stream("GET", url, headers={"Accept": "text/html"}) as response:
            if response.status_code != 200:
                return {}
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                return {}
            body = await _read_limited(response, MAX_PAGE_BYTES)
            if body is None:
                return {}
    except (httpx.HTTPError, TimeoutError):
        return {}
    parser = _ImageMetadataParser()
    try:
        parser.feed(body.decode(response.encoding or "utf-8", errors="replace"))
    except (UnicodeError, ValueError):
        return {}
    if parser.title_parts:
        parser.metadata["page_title"] = " ".join(parser.title_parts).strip()[:180]
    return parser.metadata


async def _is_accessible_image(client: httpx.AsyncClient, url: str) -> bool:
    if not await _is_public_https_url(url):
        return False
    try:
        async with client.stream("GET", url, headers={"Accept": ", ".join(sorted(ALLOWED_IMAGE_TYPES))}) as response:
            if response.status_code != 200:
                return False
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type not in ALLOWED_IMAGE_TYPES:
                return False
            body = await _read_image_signature(response)
            return body is not None and _looks_like_image(content_type, body[:32])
    except (httpx.HTTPError, TimeoutError):
        return False


async def _discover_one(article: dict, client: httpx.AsyncClient) -> dict:
    candidates = article.get("image_candidates", [])
    for candidate in candidates[:4] if isinstance(candidates, list) else []:
        if not isinstance(candidate, dict):
            continue
        image_url = _normalized_https_url(
            str(candidate.get("url", "")),
            str(article.get("source_url", "")),
        )
        if image_url and await _is_accessible_image(client, image_url):
            return _image_result(
                article,
                image_url,
                str(candidate.get("alt", "")).strip(),
                article.get("source_name", ""),
                "original_article",
            )

    metadata = await _get_page_metadata(client, str(article.get("source_url", "")))
    page_name = metadata.get("og:site_name") or article.get("source_name", "")
    page_url = str(article.get("source_url", ""))
    for image_key, alt_key in (
        ("og:image", "og:image:alt"),
        ("twitter:image", "twitter:image:alt"),
        ("twitter:image:src", "twitter:image:alt"),
    ):
        image_url = _normalized_https_url(metadata.get(image_key, ""), page_url)
        if image_url and await _is_accessible_image(client, image_url):
            return _image_result(
                article,
                image_url,
                metadata.get(alt_key, ""),
                page_name,
                "original_article",
                page_url,
            )
    return _fallback_result(article)


def _image_result(
    article: dict,
    image_url: str,
    alt: str,
    source_name: str,
    image_type: str,
    source_url: str | None = None,
) -> dict:
    return {
        "image_url": image_url,
        "image_alt": alt[:300] or f"Image accompanying the article: {article['title'][:220]}",
        "image_credit": f"{source_name or 'Original publisher'}; usage subject to publisher terms"[:300],
        "image_source_name": str(source_name or article.get("source_name", ""))[:180],
        "image_source_url": source_url or article.get("source_url"),
        "image_license": "Publisher terms; reuse permission not independently confirmed",
        "image_type": image_type,
        "is_illustrative": False,
        "image_verified": True,
        "image_checked_at": datetime.now(timezone.utc),
    }


def _fallback_result(article: dict) -> dict:
    category = "cybersecurity" if article.get("category") == "cybersecurity" else "ai"
    label = "Cybersecurity" if category == "cybersecurity" else "AI"
    return {
        "image_url": None,
        "image_alt": f"TechBrief {label} category illustration",
        "image_credit": "TechBrief category illustration",
        "image_source_name": "TechBrief",
        "image_source_url": None,
        "image_license": "Original TechBrief illustration",
        "image_type": "category_fallback",
        "is_illustrative": True,
        "image_verified": False,
        "image_checked_at": datetime.now(timezone.utc),
    }


async def discover_article_images(
    articles: list[dict],
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict]:
    headers = {"User-Agent": "TechBrief/1.0 (+daily public news aggregation)"}
    timeout = httpx.Timeout(6.0, connect=3.0)
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=False,
        trust_env=False,
        headers=headers,
        transport=transport,
    ) as client:
        async def process(article: dict) -> dict:
            async with semaphore:
                try:
                    image_fields = await _discover_one(article, client)
                except (httpx.HTTPError, OSError, ValueError) as error:
                    logger.warning(
                        "Image discovery failed for %s: %s",
                        clean_for_log(article.get("source_url")),
                        error,
                    )
                    image_fields = _fallback_result(article)
                clean_article = {key: value for key, value in article.items() if key != "image_candidates"}
                return {**clean_article, **image_fields}

        results = await asyncio.gather(*(process(article) for article in articles))
    seen_urls: set[str] = set()
    for article in results:
        image_url = article.get("image_url")
        if image_url and image_url in seen_urls:
            article.update(_fallback_result(article))
        elif image_url:
            seen_urls.add(image_url)
    return results
