from datetime import datetime, timezone

from backend.app.agents.ranking import deduplicate_articles, rank_articles
from backend.app.agents.verifier import verify_articles


def test_verifier_cleans_content_and_extracts_cve_without_inventing_severity():
    article = {
        "title": "New <b>CVE-2026-12345</b> advisory",
        "description": "<p>Read the official bulletin.</p>",
        "source_url": "https://security.example/advisory",
    }

    result = verify_articles([article])[0]

    assert result["description"] == "Read the official bulletin."
    assert result["verification_status"] == "source_link_present"
    assert result["cve_ids"] == "CVE-2026-12345"
    assert result["severity"] is None


def test_ranker_scores_newer_high_impact_stories_higher():
    now = datetime.now(timezone.utc)
    stories = [
        {
            "title": "Routine update",
            "description": "Maintenance notes",
            "category": "ai",
            "published_at": now,
            "verification_status": "source_link_present",
            "cve_ids": "",
        },
        {
            "title": "Critical zero-day vulnerability",
            "description": "CVE-2026-12345 active exploitation",
            "category": "cybersecurity",
            "published_at": now,
            "verification_status": "source_link_present",
            "cve_ids": "CVE-2026-12345",
        },
    ]

    ranked = rank_articles(stories)

    assert ranked[0]["title"].startswith("Critical")
    assert ranked[0]["importance_score"] > ranked[1]["importance_score"]
    assert "CVE identifier" in ranked[0]["score_reasons"]


def test_deduplicator_keeps_one_near_identical_headline():
    articles = [
        {"category": "ai", "source_name": "Example", "source_url": "https://example.org/a",
         "title": "Company announces a new AI model release"},
        {"category": "ai", "source_name": "Example", "source_url": "https://example.org/b",
         "title": "Company announces new AI model release"},
    ]

    assert len(deduplicate_articles(articles)) == 1
