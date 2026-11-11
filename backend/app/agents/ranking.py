import re
from datetime import datetime, timezone
from difflib import SequenceMatcher


def deduplicate_articles(articles: list[dict]) -> list[dict]:
    unique: list[dict] = []
    for article in sorted(articles, key=lambda item: (
        item.get("source_name", "").lower() not in {"cisa", "nist nvd", "openai", "google deepmind", "microsoft research"},
        item.get("source_url", ""),
    )):
        normalized = re.sub(r"[^a-z0-9 ]", " ", article["title"].lower())
        normalized = " ".join(normalized.split())
        duplicate = any(
            existing["category"] == article["category"]
            and SequenceMatcher(
                None,
                normalized,
                " ".join(re.sub(r"[^a-z0-9 ]", " ", existing["title"].lower()).split()),
            ).ratio() >= 0.9
            for existing in unique
        )
        if not duplicate:
            unique.append(article)
    return unique


def rank_articles(articles: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc)
    unique = deduplicate_articles(articles)
    for article in unique:
        title = article["title"].lower()
        description = article.get("description", "").lower()
        combined = f"{title} {description}"
        score = 2.0
        reasons = ["Published within the reporting window"]
        high_impact_terms = (
            "zero-day", "zero day", "critical", "ransomware", "breach", "vulnerability",
            "model", "release", "open source", "acquisition", "research",
        )
        matches = [term for term in high_impact_terms if term in combined]
        if matches:
            score += min(2.5, len(matches) * 0.6)
            reasons.append(f"Potential impact signals: {', '.join(matches[:4])}")
        if article.get("cve_ids"):
            score += 1.0
            reasons.append("CVE identifier found in source text")
        if article.get("verification_status") == "source_link_present":
            score += 1.0
            reasons.append("Original HTTPS source link is present")
        if article.get("source_name", "").lower() in {
            "cisa", "nist nvd", "openai", "google deepmind", "microsoft research", "hugging face",
            "bleepingcomputer", "the hacker news", "krebs on security", "arxiv ai",
        }:
            score += 0.8
            reasons.append("Known primary or established publisher")
        published = article["published_at"]
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_hours = max(0.0, (now - published).total_seconds() / 3600)
        score += max(0.0, 1.5 * (1 - age_hours / 24))
        article["importance_score"] = round(min(10.0, score), 1)
        article["score_reasons"] = "; ".join(reasons)
    return sorted(unique, key=lambda item: item["importance_score"], reverse=True)
