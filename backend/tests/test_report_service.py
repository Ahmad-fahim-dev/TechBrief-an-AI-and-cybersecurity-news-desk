import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import select

from backend.app.config import Settings
from backend.app.database import Base, build_engine, build_session_factory
from backend.app.models import Article, DailyReport
from backend.app.services import report_service
from backend.app.workflows import news_graph


def test_scheduled_generation_is_idempotent(tmp_path, monkeypatch):
    database_path = str(tmp_path / "reports.db").replace("\\", "/")
    database_url = f"sqlite:///{database_path}"
    engine = build_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)
    calls = 0

    class FakeGraph:
        async def astream(self, _state, stream_mode):
            nonlocal calls
            calls += 1
            assert stream_mode == "updates"
            yield {"editor": {"report": {
                    "ai_news": [],
                    "cybersecurity_news": [],
                    "critical_alerts": [],
                    "key_takeaways": ["No new source-linked stories."],
                    "executive_summary": "No eligible developments found.",
                    "impact_analysis": "No analysis available.",
                    "sources": [],
                    "warnings": ["No RSS feeds are enabled for the selected categories."],
                }}}

    monkeypatch.setattr(report_service, "build_research_graph", lambda _config, _session_factory: FakeGraph())
    settings = Settings(_env_file=None, timezone="Asia/Karachi", rss_feeds_json="[]")

    first = asyncio.run(report_service.generate_report(session_factory, settings, "scheduled:2026-11-01"))
    second = asyncio.run(report_service.generate_report(session_factory, settings, "scheduled:2026-11-01"))

    assert first.id == second.id
    assert first.status == "completed_with_warnings"
    assert calls == 1
    session = session_factory()
    assert len(session.scalars(select(DailyReport)).all()) == 1
    session.close()
    engine.dispose()


def test_report_workflow_persists_ranked_articles(tmp_path, monkeypatch):
    database_path = str(tmp_path / "workflow.db").replace("\\", "/")
    engine = build_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)

    async def fake_collect(_settings, categories):
        category = next(iter(categories))
        return ([{
            "title": f"{category} research release",
            "description": "Publisher-provided details.",
            "category": category,
            "source_name": "Example Publisher",
            "source_url": f"https://example.org/{category}",
            "published_at": datetime.now(timezone.utc),
        }], [])

    monkeypatch.setattr(news_graph, "collect_recent_articles", fake_collect)

    async def fake_images(articles):
        image_fields = {
            "image_url": "https://cdn.example.org/research.jpg",
            "image_alt": "Research team",
            "image_credit": "Example Publisher; usage subject to publisher terms",
            "image_source_name": "Example Publisher",
            "image_source_url": "https://example.org/ai",
            "image_license": "Publisher terms",
            "image_type": "original_article",
            "is_illustrative": False,
            "image_verified": True,
            "image_checked_at": datetime.now(timezone.utc),
        }
        return [
            {key: value for key, value in article.items() if key != "image_candidates"}
            | image_fields
            for article in articles
        ]

    monkeypatch.setattr(news_graph, "discover_article_images", fake_images)
    report = asyncio.run(report_service.generate_report(
        session_factory,
        Settings(_env_file=None, database_url=f"sqlite:///{database_path}"),
        "scheduled:workflow-test",
    ))

    assert report.status == "completed_with_warnings"
    assert json.loads(report.ai_news)
    session = session_factory()
    article = session.scalar(select(Article).where(Article.category == "ai"))
    assert article is not None
    assert article.summary == "Publisher-provided details."
    assert article.image_url == "https://cdn.example.org/research.jpg"
    assert article.image_alt == "Research team"
    assert article.image_verified is True
    assert any("OPENAI_API_KEY" in warning for warning in json.loads(report.warnings))
    session.close()
    engine.dispose()
