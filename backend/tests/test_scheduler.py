from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory
from backend.app.main import create_app
from backend.app.services.scheduler import build_scheduler

ADMIN_TOKEN = "scheduler-test-token-" + ("z" * 32)


def admin_headers():
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def test_daily_scheduler_uses_configured_timezone_and_time():
    engine = build_engine("sqlite://")
    scheduler = build_scheduler(build_session_factory(engine), Settings(_env_file=None))
    job = scheduler.get_job("daily-report")

    assert job is not None
    assert job.trigger.timezone.key == "Asia/Karachi"
    assert str(job.trigger.fields[5]) == "9"
    assert str(job.trigger.fields[6]) == "0"
    engine.dispose()


def test_scheduler_can_be_disabled_and_reenabled_from_settings(tmp_path):
    database_path = str(tmp_path / "scheduler.db").replace("\\", "/")
    app = create_app(
        Settings(
            _env_file=None,
            database_url=f"sqlite:///{database_path}",
            admin_api_token=ADMIN_TOKEN,
        )
    )
    with TestClient(app) as client:
        assert client.get("/api/dashboard").json()["next_scheduled_report"] is not None
        response = client.put("/api/settings", headers=admin_headers(), json={
            "timezone": "UTC",
            "scheduled_time": "08:30",
            "schedule_enabled": False,
            "enabled_categories": ["ai", "cybersecurity"],
            "articles_per_category": 7,
            "preferred_sources": [],
        })
        assert response.status_code == 200
        assert client.get("/api/dashboard").json()["next_scheduled_report"] is None

        response = client.put("/api/settings", headers=admin_headers(), json={
            "timezone": "Asia/Karachi",
            "scheduled_time": "09:15",
            "schedule_enabled": True,
            "enabled_categories": ["ai", "cybersecurity"],
            "articles_per_category": 7,
            "preferred_sources": [],
        })
        assert response.status_code == 200
        assert client.get("/api/dashboard").json()["next_scheduled_report"] is not None
