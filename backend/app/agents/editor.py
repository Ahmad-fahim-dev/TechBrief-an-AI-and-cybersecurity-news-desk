from collections import Counter


def edit_report(articles: list[dict], warnings: list[str], articles_per_category: int) -> dict:
    ai_news = [item for item in articles if item["category"] == "ai"][:articles_per_category]
    cyber_news = [item for item in articles if item["category"] == "cybersecurity"][:articles_per_category]
    critical_alerts = [
        item for item in cyber_news
        if item.get("severity") == "Critical"
    ]
    sources = sorted({item["source_name"] for item in articles})
    if articles:
        lead = "; ".join(item["title"] for item in articles[:3])
        executive = f"Within the last 24 hours, {len(ai_news)} AI and {len(cyber_news)} cybersecurity stories were collected. " \
                    f"Highest-ranked developments: {lead}."
    else:
        executive = "No eligible stories were collected in the last 24 hours. Review source availability and try again."
    topic_counts = Counter(
        "AI developments" if item["category"] == "ai" else "Cybersecurity developments"
        for item in articles[:5]
    )
    takeaways = [f"{topic}: monitor today's verified source updates." for topic, _ in topic_counts.most_common(5)]
    if not takeaways:
        takeaways = ["No verified developments were available for the reporting window."]
    impact = (
        "Review the linked primary sources before making operational decisions. This report is an information aid, "
        "not an incident-response or investment recommendation."
    )
    return {
        "executive_summary": executive,
        "ai_news": ai_news,
        "cybersecurity_news": cyber_news,
        "critical_alerts": critical_alerts,
        "key_takeaways": takeaways,
        "impact_analysis": impact,
        "sources": sources,
        "warnings": list(dict.fromkeys(warnings)),
    }
