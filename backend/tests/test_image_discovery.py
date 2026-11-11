import asyncio

import httpx

from backend.app.agents.image_discovery import _is_public_https_url, _normalized_https_url, discover_article_images
from backend.app.database import add_article_image_columns, build_engine


def test_rss_images_are_checked_attributed_and_deduplicated(monkeypatch):
    async def public_url(_url):
        return True

    monkeypatch.setattr("backend.app.agents.image_discovery._is_public_https_url", public_url)
    image_bytes = b"\xff\xd8\xff" + b"\x00" * 24

    def handler(request):
        assert request.url.host == "cdn.example.org"
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=image_bytes)

    stories = [
        {
            "title": f"AI story {index}",
            "category": "ai",
            "source_name": "Example Publisher",
            "source_url": f"https://example.org/story-{index}",
            "image_candidates": [{"url": "https://cdn.example.org/shared.jpg", "alt": "Research lab"}],
        }
        for index in range(2)
    ]
    results = asyncio.run(discover_article_images(stories, httpx.MockTransport(handler)))

    assert results[0]["image_url"] == "https://cdn.example.org/shared.jpg"
    assert results[0]["image_verified"] is True
    assert results[0]["is_illustrative"] is False
    assert results[0]["image_alt"] == "Research lab"
    assert "publisher terms" in results[0]["image_credit"]
    assert results[1]["image_url"] is None
    assert results[1]["image_type"] == "category_fallback"
    assert results[1]["is_illustrative"] is True


def test_open_graph_image_is_used_when_rss_has_no_image(monkeypatch):
    async def public_url(_url):
        return True

    monkeypatch.setattr("backend.app.agents.image_discovery._is_public_https_url", public_url)

    def handler(request):
        if request.url.path == "/article":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<html><head><meta property="og:site_name" content="Publisher">'
                '<meta property="og:image" content="/assets/story.jpg">'
                '<meta property="og:image:alt" content="Researchers at work"></head></html>',
            )
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 24,
        )

    result = asyncio.run(discover_article_images(
        [{
            "title": "New research",
            "category": "ai",
            "source_name": "Example",
            "source_url": "https://example.org/article",
            "image_candidates": [],
        }],
        httpx.MockTransport(handler),
    ))[0]

    assert result["image_url"] == "https://example.org/assets/story.jpg"
    assert result["image_alt"] == "Researchers at work"
    assert result["image_source_name"] == "Publisher"
    assert result["image_verified"] is True


def test_existing_article_table_receives_image_columns(tmp_path):
    engine = build_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT NOT NULL)")
        connection.exec_driver_sql("INSERT INTO articles (id, title) VALUES (1, 'Existing story')")

    add_article_image_columns(engine)

    from sqlalchemy import inspect

    columns = {column["name"] for column in inspect(engine).get_columns("articles")}
    assert {
        "image_url",
        "image_alt",
        "image_credit",
        "image_source_name",
        "image_source_url",
        "image_license",
        "image_type",
        "is_illustrative",
        "image_verified",
        "image_checked_at",
    }.issubset(columns)
    with engine.connect() as connection:
        old_article = connection.exec_driver_sql(
            "SELECT image_type, is_illustrative, image_verified FROM articles WHERE id = 1"
        ).one()
    assert old_article == ("category_fallback", 1, 0)
    engine.dispose()


def test_image_urls_require_https_and_public_destinations():
    assert _normalized_https_url("http://cdn.example.org/image.jpg") is None
    assert _normalized_https_url("https://user:pass@cdn.example.org/image.jpg") is None
    assert asyncio.run(_is_public_https_url("https://127.0.0.1/image.jpg")) is False
