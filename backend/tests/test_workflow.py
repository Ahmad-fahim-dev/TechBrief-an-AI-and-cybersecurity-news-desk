import asyncio
from datetime import datetime, timezone

from backend.app.config import Settings
from backend.app.database import Base, build_engine, build_session_factory
from backend.app.workflows import news_graph


def test_graph_coordinates_research_verification_ranking_and_editor(monkeypatch):
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)
    async def fake_collect(settings, categories):
        category = next(iter(categories))
        return ([{
            "title": f"{category} research update",
            "description": "Publisher-provided details.",
            "category": category,
            "source_name": "Example",
            "source_url": f"https://example.org/{category}",
            "published_at": datetime.now(timezone.utc),
        }], [])

    async def fake_images(articles):
        return [
            {key: value for key, value in article.items() if key != "image_candidates"}
            | {
                "image_url": None,
                "image_type": "category_fallback",
                "is_illustrative": True,
                "image_verified": False,
            }
            for article in articles
        ]

    monkeypatch.setattr(news_graph, "collect_recent_articles", fake_collect)
    monkeypatch.setattr(news_graph, "discover_article_images", fake_images)

    async def fake_images(articles):
        return [
            {key: value for key, value in article.items() if key != "image_candidates"}
            | {"image_url": None, "image_type": "category_fallback"}
            for article in articles
        ]

    monkeypatch.setattr(news_graph, "discover_article_images", fake_images)
    settings = Settings(_env_file=None)
    result = asyncio.run(news_graph.build_research_graph(settings, session_factory).ainvoke({
        "categories": {"ai", "cybersecurity"},
        "raw_articles": [],
        "warnings": [],
    }))

    assert len(result["articles"]) == 2
    assert len(result["report"]["ai_news"]) == 1
    assert len(result["report"]["cybersecurity_news"]) == 1
    assert all(item["verification_status"] == "source_link_present" for item in result["articles"])
    engine.dispose()
