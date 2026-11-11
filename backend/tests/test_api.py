import time
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models import Article


def test_settings_news_search_and_bookmarks(tmp_path):
    db_path = str(tmp_path / "test.db").replace("\\", "/")
    app = create_app(Settings(_env_file=None, database_url=f"sqlite:///{db_path}"), start_scheduler=False)

    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "TechBrief" in page.text
        assert client.get("/static/app.js").status_code == 200
        initial = client.get("/api/settings")
        assert initial.status_code == 200
        assert initial.json()["timezone"] == "Asia/Karachi"

        session = app.state.session_factory()
        article = Article(
            title="New foundation model announced",
            description="A source-linked release.",
            summary="The publisher announced a new model.",
            category="ai",
            source_name="Example Lab",
            source_url="https://example.org/model",
            published_at=datetime.now(timezone.utc),
            importance_score=8.2,
            score_reasons="Recent",
            verification_status="source_link_present",
            impact_analysis="Developers may evaluate its capabilities.",
            severity=None,
            cve_ids="",
            image_url="https://cdn.example.org/model.jpg",
            image_alt="Research team at work",
            image_credit="Example Lab; usage subject to publisher terms",
            image_source_name="Example Lab",
            image_source_url="https://example.org/model",
            image_license="Publisher terms; reuse permission not independently confirmed",
            image_type="original_article",
            is_illustrative=False,
            image_verified=True,
            image_checked_at=datetime.now(timezone.utc),
        )
        session.add(article)
        session.commit()
        article_id = article.id
        session.close()

        result = client.get("/api/news", params={"q": "foundation", "category": "ai"})
        assert result.status_code == 200
        assert result.json()["total"] == 1
        assert result.json()["items"][0]["id"] == article_id
        assert result.json()["items"][0]["image_url"] == "https://cdn.example.org/model.jpg"
        assert result.json()["items"][0]["image_verified"] is True

        saved = client.post("/api/bookmarks", json={"article_id": article_id})
        assert saved.status_code == 201
        bookmark = client.get("/api/bookmarks").json()["items"][0]["article"]
        assert bookmark["id"] == article_id
        assert bookmark["image_alt"] == "Research team at work"
        assert client.delete(f"/api/bookmarks/{saved.json()['id']}").status_code == 204

        updated = client.put("/api/settings", json={
            "timezone": "UTC",
            "scheduled_time": "08:30",
            "schedule_enabled": False,
            "enabled_categories": ["ai"],
            "articles_per_category": 5,
            "preferred_sources": [],
        })
        assert updated.status_code == 200
        assert updated.json()["scheduled_time"] == "08:30"


def test_health_and_empty_dashboard(tmp_path):
    db_path = str(tmp_path / "empty.db").replace("\\", "/")
    app = create_app(Settings(_env_file=None, database_url=f"sqlite:///{db_path}"), start_scheduler=False)
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        response = client.get("/api/dashboard")
        assert response.status_code == 200
        assert response.json()["total_articles"] == 0


def test_missing_images_can_be_backfilled_for_existing_articles(tmp_path, monkeypatch):
    from backend.app.api import routes

    database_path = str(tmp_path / "image-backfill.db").replace("\\", "/")
    app = create_app(Settings(_env_file=None, database_url=f"sqlite:///{database_path}"), start_scheduler=False)

    async def fake_discover(articles):
        assert articles[0]["source_url"] == "https://example.org/older-story"
        return [{
            **articles[0],
            "image_url": "https://cdn.example.org/story.jpg",
            "image_alt": "Researchers working",
            "image_credit": "Example Publisher",
            "image_source_name": "Example Publisher",
            "image_source_url": "https://example.org/older-story",
            "image_license": "Publisher terms",
            "image_type": "original_article",
            "is_illustrative": False,
            "image_verified": True,
            "image_checked_at": datetime.now(timezone.utc),
        }]

    monkeypatch.setattr(routes, "discover_article_images", fake_discover)
    with TestClient(app) as client:
        session = app.state.session_factory()
        article = Article(
            title="Existing story without image metadata",
            description="Older saved story.",
            summary="Older saved story.",
            category="ai",
            source_name="Example Publisher",
            source_url="https://example.org/older-story",
            published_at=datetime.now(timezone.utc),
        )
        session.add(article)
        session.commit()
        article_id = article.id
        session.close()

        assert client.post("/api/images/discover-missing", json={}).json() == {"updated": 1}
        result = client.get(f"/api/news/{article_id}").json()
        assert result["image_url"] == "https://cdn.example.org/story.jpg"
        assert result["image_verified"] is True


def test_manual_generation_and_failed_report_retry(tmp_path, monkeypatch):
    from backend.app.api import routes
    from backend.app.models import DailyReport

    database_path = str(tmp_path / "jobs.db").replace("\\", "/")
    app = create_app(Settings(_env_file=None, database_url=f"sqlite:///{database_path}"), start_scheduler=False)

    async def fake_generation(_session_factory, _config, run_key=None, on_progress=None):
        if on_progress:
            on_progress("ai_researcher")
        return SimpleNamespace(status="completed", id=99)

    monkeypatch.setattr(routes, "generate_report", fake_generation)
    with TestClient(app) as client:
        started = client.post("/api/reports/generate", json={})
        assert started.status_code == 202
        deadline = time.monotonic() + 2
        job = client.get("/api/reports/status").json()
        while job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(.02)
            job = client.get("/api/reports/status").json()
        assert job["status"] == "completed"
        assert job["stage"] == "ai_researcher"

        session = app.state.session_factory()
        failed = DailyReport(
            run_key="manual:previous-failure",
            report_date="2026-10-30",
            status="failed",
            executive_summary="Failed run",
        )
        session.add(failed)
        session.commit()
        failed_id = failed.id
        session.close()

        retried = client.post(f"/api/reports/{failed_id}/retry", json={})
        assert retried.status_code == 202
        deadline = time.monotonic() + 2
        job = client.get("/api/reports/status").json()
        while job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(.02)
            job = client.get("/api/reports/status").json()
        assert job["status"] == "completed"
