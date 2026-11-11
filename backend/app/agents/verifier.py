import re
from urllib.parse import urlparse


def verify_articles(articles: list[dict]) -> list[dict]:
    verified = []
    for article in articles:
        parsed = urlparse(article.get("source_url", ""))
        description = re.sub(r"<[^>]*>", " ", article.get("description", "")).strip()
        article["description"] = re.sub(r"\s+", " ", description)[:4000]
        article["verification_status"] = (
            "source_link_present" if parsed.scheme == "https" and parsed.hostname else "unverified"
        )
        text = f'{article.get("title", "")} {article.get("description", "")}'
        article["cve_ids"] = ", ".join(sorted(set(re.findall(r"\bCVE-\d{4}-\d{4,}\b", text, re.I))))
        # A feed entry is not independent confirmation; severity is intentionally not inferred.
        article["severity"] = None
        verified.append(article)
    return verified
