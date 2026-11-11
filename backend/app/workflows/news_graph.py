from langgraph.graph import END, START, StateGraph

from backend.app.agents.editor import edit_report
from backend.app.agents.image_discovery import discover_article_images
from backend.app.agents.ranking import deduplicate_articles, rank_articles
from backend.app.agents.summarizer import summarize_articles
from backend.app.agents.verifier import verify_articles
from backend.app.config import Settings
from backend.app.services.collector import collect_recent_articles
from backend.app.services.governance import authorize_agent_action, ensure_builtin_governance
from backend.app.workflows.state import ResearchState


def build_research_graph(settings: Settings, session_factory):
    ensure_builtin_governance(session_factory)

    def require(agent_id: str, action: str, resource: str) -> None:
        if not authorize_agent_action(
            session_factory,
            agent_id=agent_id,
            action=action,
            resource=resource,
        ):
            raise PermissionError(f"Agent {agent_id} is not authorized for {action} on {resource}")

    async def coordinator(state: ResearchState) -> ResearchState:
        require("coordinator", "workflow.coordinate", "workflow:research")
        return {"categories": state.get("categories", {"ai", "cybersecurity"})}

    async def ai_researcher(state: ResearchState) -> ResearchState:
        if "ai" not in state["categories"]:
            return {"raw_articles": [], "warnings": []}
        require("ai_researcher", "source.read", "sources:feeds:ai")
        require("ai_researcher", "article.analyze", "news:articles")
        articles, warnings = await collect_recent_articles(settings, {"ai"})
        return {"raw_articles": articles, "warnings": warnings}

    async def cyber_researcher(state: ResearchState) -> ResearchState:
        if "cybersecurity" not in state["categories"]:
            return {"raw_articles": [], "warnings": []}
        require("cyber_researcher", "source.read", "sources:feeds:cybersecurity")
        require("cyber_researcher", "article.analyze", "news:articles")
        articles, warnings = await collect_recent_articles(settings, {"cybersecurity"})
        return {"raw_articles": articles, "warnings": warnings}

    async def verifier(state: ResearchState) -> ResearchState:
        require("source_verifier", "article.verify", "news:articles")
        return {"articles": verify_articles(state.get("raw_articles", []))}

    async def image_discovery(state: ResearchState) -> ResearchState:
        require("image_discovery", "image.discover", "news:images")
        if state.get("articles"):
            require("image_discovery", "source.read", "sources:article-pages")
        return {"articles": await discover_article_images(state.get("articles", []))}

    async def ranker(state: ResearchState) -> ResearchState:
        require("news_ranker", "article.rank", "news:articles")
        articles = state.get("articles", [])
        deduplicated = deduplicate_articles(articles)
        warnings = ["Near-duplicate headlines were removed before ranking."] if len(deduplicated) < len(articles) else []
        return {"articles": rank_articles(deduplicated), "warnings": warnings}

    async def summarizer(state: ResearchState) -> ResearchState:
        require("news_summarizer", "article.summarize", "news:articles")
        articles, warnings = await summarize_articles(state.get("articles", []), settings)
        return {"articles": articles, "warnings": warnings}

    async def editor(state: ResearchState) -> ResearchState:
        require("chief_editor", "report.compose", "techbrief:reports")
        return {
            "report": edit_report(
                state.get("articles", []),
                state.get("warnings", []),
                settings.articles_per_category,
            )
        }

    graph = StateGraph(ResearchState)
    graph.add_node("coordinator", coordinator)
    graph.add_node("ai_researcher", ai_researcher)
    graph.add_node("cyber_researcher", cyber_researcher)
    graph.add_node("verifier", verifier)
    graph.add_node("image_discovery", image_discovery)
    graph.add_node("ranker", ranker)
    graph.add_node("summarizer", summarizer)
    graph.add_node("editor", editor)
    graph.add_edge(START, "coordinator")
    graph.add_edge("coordinator", "ai_researcher")
    graph.add_edge("coordinator", "cyber_researcher")
    graph.add_edge(["ai_researcher", "cyber_researcher"], "verifier")
    graph.add_edge("verifier", "image_discovery")
    graph.add_edge("image_discovery", "ranker")
    graph.add_edge("ranker", "summarizer")
    graph.add_edge("summarizer", "editor")
    graph.add_edge("editor", END)
    return graph.compile()
