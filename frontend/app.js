const state = {
  view: "home",
  articles: [],
  reports: [],
  report: null,
  activeCategory: "",
  researchCategory: "",
  archiveCategory: "",
  page: 1,
  total: 0,
  imageBackfillStarted: false,
  adminToken: null,
  adminUser: null,
  demo: new URLSearchParams(location.search).get("demo") === "1",
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const demoStories = [
  { id: "demo-1", category: "ai", title: "New AI model introduces advanced reasoning capabilities", description: "A sample design-preview story demonstrating the editorial layout. This is not live or verified reporting.", summary: "A sample design-preview story demonstrating the editorial layout. This is not live or verified reporting.", source_name: "TechBrief Demo", source_url: "https://example.org/demo-ai", published_at: new Date().toISOString(), importance_score: 8.7, verification_status: "DEMO — NOT VERIFIED", impact_analysis: "Sample content only.", cve_ids: "", demo: true },
  { id: "demo-2", category: "cybersecurity", title: "Global cybersecurity threats increase as new vulnerabilities emerge", description: "A sample design-preview story demonstrating the editorial layout. This is not live or verified reporting.", summary: "A sample design-preview story demonstrating the editorial layout. This is not live or verified reporting.", source_name: "TechBrief Demo", source_url: "https://example.org/demo-security", published_at: new Date().toISOString(), importance_score: 9.1, verification_status: "DEMO — NOT VERIFIED", impact_analysis: "Sample content only.", cve_ids: "", demo: true },
  { id: "demo-3", category: "ai", title: "Major technology company announces new AI research", description: "A sample design-preview story demonstrating the editorial layout. This is not live or verified reporting.", summary: "A sample design-preview story demonstrating the editorial layout. This is not live or verified reporting.", source_name: "TechBrief Demo", source_url: "https://example.org/demo-research", published_at: new Date().toISOString(), importance_score: 7.9, verification_status: "DEMO — NOT VERIFIED", impact_analysis: "Sample content only.", cve_ids: "", demo: true },
  { id: "demo-4", category: "cybersecurity", title: "Security researchers discover a critical software vulnerability", description: "A sample design-preview story demonstrating the editorial layout. This is not live or verified reporting.", summary: "A sample design-preview story demonstrating the editorial layout. This is not live or verified reporting.", source_name: "TechBrief Demo", source_url: "https://example.org/demo-vulnerability", published_at: new Date().toISOString(), importance_score: 9.4, verification_status: "DEMO — NOT VERIFIED", impact_analysis: "Sample content only.", cve_ids: "", demo: true },
  { id: "demo-5", category: "ai", title: "The future of autonomous AI agents", description: "A sample design-preview story demonstrating the editorial layout. This is not live or verified reporting.", summary: "A sample design-preview story demonstrating the editorial layout. This is not live or verified reporting.", source_name: "TechBrief Demo", source_url: "https://example.org/demo-agents", published_at: new Date().toISOString(), importance_score: 7.2, verification_status: "DEMO — NOT VERIFIED", impact_analysis: "Sample content only.", cve_ids: "", demo: true },
];

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (
    state.adminToken
    && /^\/(governance|agents|permissions|actions|approvals|audit-logs)(\/|$)/.test(path)
  ) headers.Authorization = `Bearer ${state.adminToken}`;
  const response = await fetch(`/api${path}`, {
    ...options,
    headers,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

function node(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function safeText(value, fallback = "Not available") {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function safeHref(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function dateLabel(value, options = { month: "short", day: "numeric" }) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Date unavailable" : new Intl.DateTimeFormat(undefined, options).format(date);
}

function timeAgo(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Date unavailable";
  const hours = Math.max(0, Math.floor((Date.now() - date.getTime()) / 3600000));
  if (hours < 1) return "Just now";
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function readingMinutes(article) {
  const words = `${article.title || ""} ${article.summary || article.description || ""}`.trim().split(/\s+/).length;
  return Math.max(1, Math.ceil(words / 200));
}

function categoryIllustration(article) {
  const cybersecurity = article.category === "cybersecurity";
  const art = node("div", `image-art ${cybersecurity ? "cyber" : "research"}`);
  art.setAttribute("role", "img");
  art.setAttribute("aria-label", safeText(article.image_alt, `Illustration for ${categoryName(article)}`));
  art.append(node("span", "", cybersecurity ? "SECURITY / SIGNAL" : "AI / FRONTIER"));
  return art;
}

function imageAttribution(article) {
  const caption = node("span", "image-credit");
  const credit = safeText(article.image_credit, "");
  const sourceUrl = safeHref(article.image_source_url);
  if (sourceUrl) {
    const source = node("a", "", safeText(article.image_source_name, "Image source"));
    source.href = sourceUrl;
    source.target = "_blank";
    source.rel = "noopener noreferrer";
    source.addEventListener("click", (event) => event.stopPropagation());
    caption.append(source);
    if (credit) caption.append(node("span", "", ` · ${credit}`));
  } else {
    caption.textContent = credit;
  }
  return caption;
}

function storyImage(article, _index = 0, className = "story-image") {
  const frame = node("div", className);
  const imageUrl = article.image_verified && !article.is_illustrative ? safeHref(article.image_url) : null;
  if (!imageUrl) {
    frame.append(categoryIllustration(article));
    return frame;
  }
  const image = node("img");
  image.src = imageUrl;
  image.alt = safeText(article.image_alt, `Image accompanying ${article.title}`);
  image.loading = "lazy";
  image.decoding = "async";
  image.referrerPolicy = "no-referrer";
  image.addEventListener("error", () => {
    frame.replaceChildren(categoryIllustration(article));
  }, { once: true });
  frame.append(image);
  if (article.image_credit || article.image_source_name) frame.append(imageAttribution(article));
  return frame;
}

function categoryName(article) {
  return article.category === "cybersecurity" ? "CYBERSECURITY" : "ARTIFICIAL INTELLIGENCE";
}

function sourceLink(article, label = "READ ORIGINAL SOURCE ↗", className = "source-link") {
  const url = safeHref(article.source_url);
  if (!url) return null;
  const link = node("a", className, label);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.addEventListener("click", (event) => event.stopPropagation());
  return link;
}

function authorLine(article, compact = false) {
  const line = node("div", compact ? "story-meta" : "story-meta");
  line.append(node("span", "author-avatar small-avatar", "TB"));
  line.append(node("span", "author-mini", article.demo ? "TechBrief Demo" : safeText(article.source_name, "TechBrief Desk")));
  line.append(node("i", "meta-dot"));
  line.append(node("span", "", timeAgo(article.published_at)));
  line.append(node("i", "meta-dot"));
  line.append(node("span", "", `${readingMinutes(article)} MIN READ`));
  return line;
}

function openArticle(article) {
  state.currentArticle = article;
  navigate("article");
  renderArticle(article);
}

function storyCard(article, index, options = {}) {
  const card = node("article", `story-card${options.spanTwo ? " span-two" : ""}`);
  card.tabIndex = 0;
  card.setAttribute("role", "link");
  card.setAttribute("aria-label", `Read ${article.title}`);
  card.append(storyImage(article, index));
  card.append(authorLine(article, true));
  const title = node("h3", "story-title", safeText(article.title));
  card.append(title);
  if (options.excerpt !== false) card.append(node("p", "story-excerpt", safeText(article.summary || article.description)));
  const category = node("span", `story-category ${article.category === "cybersecurity" ? "cyber" : ""}`, categoryName(article));
  card.append(category);
  const publisherLink = sourceLink(article, "OPEN PUBLISHER ARTICLE ↗", "source-link card-source-link");
  if (publisherLink) card.append(publisherLink);
  if (article.demo) card.append(node("span", "demo-label", "DEMO · NOT VERIFIED"));
  card.addEventListener("click", () => openArticle(article));
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openArticle(article);
    }
  });
  return card;
}

function editorPick(article, index, central = false) {
  const card = node("article", central ? "editor-center" : "editor-pick");
  card.tabIndex = 0;
  card.append(storyImage(article, index));
  if (central) {
    card.append(authorLine(article, true));
    card.append(node("h3", "story-title", safeText(article.title)));
    if (article.severity) card.append(node("span", `security-severity ${article.severity.toLowerCase()}`, `${article.severity} SEVERITY`));
    if (article.cve_ids) card.append(node("span", "cve-reference", article.cve_ids));
    card.append(node("p", "story-excerpt", safeText(article.summary || article.description)));
    card.append(node("span", `story-category ${article.category === "cybersecurity" ? "cyber" : ""}`, categoryName(article)));
  } else {
    const text = node("div");
    text.append(node("span", `story-category ${article.category === "cybersecurity" ? "cyber" : ""}`, article.category === "cybersecurity" ? "SECURITY" : "AI"));
    text.append(node("h3", "", safeText(article.title)));
    text.append(authorLine(article, true));
    card.append(text);
  }
  const publisherLink = sourceLink(article, "OPEN PUBLISHER ARTICLE ↗", "source-link card-source-link");
  if (publisherLink) card.append(publisherLink);
  card.addEventListener("click", () => openArticle(article));
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter") openArticle(article);
  });
  return card;
}

function showNotice(message, success = false) {
  const notice = $("#notice");
  notice.textContent = message;
  notice.classList.toggle("success", success);
  notice.classList.remove("hidden");
  window.setTimeout(() => notice.classList.add("hidden"), 6500);
}

function setLoading(container, message = "Loading source-linked reporting…") {
  container.replaceChildren(node("div", "editorial-empty", message));
}

function demoFallback() {
  if (!state.demo) return;
  state.articles = demoStories;
  state.trendingItems = demoStories;
  state.total = demoStories.length;
  showNotice("DEMO PREVIEW — sample headlines below are fictional and not verified reporting.", true);
  renderAllFeeds();
  renderTrending();
}

async function getNews(params = {}) {
  const query = new URLSearchParams({ page: String(params.page || 1), page_size: String(params.page_size || 50), min_importance: "0" });
  if (params.category) query.set("category", params.category);
  if (params.q) query.set("q", params.q);
  if (params.source) query.set("source", params.source);
  if (params.from_date) query.set("from_date", params.from_date);
  if (params.to_date) query.set("to_date", params.to_date);
  return api(`/news?${query}`);
}

async function loadHome() {
  const latest = $("#latest-grid");
  setLoading(latest);
  try {
    const [feed, dashboard, report] = await Promise.all([
      getNews({ page_size: 30 }),
      api("/dashboard"),
      api("/reports/latest").catch(() => null),
    ]);
    state.articles = feed.items;
    state.total = feed.total;
    if (report) {
      const detailed = await api(`/reports/${report.id}`);
      state.report = detailed;
      renderBrief(detailed.key_takeaways || []);
    } else renderBrief([]);
    $("#home-updated").textContent = dashboard.last_updated
      ? `UPDATED ${dateLabel(dashboard.last_updated, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).toUpperCase()}`
      : "LIVE SOURCE COVERAGE";
    renderAllFeeds();
    backfillMissingImages();
  } catch (error) {
    latest.replaceChildren(node("div", "editorial-empty", `Live reporting is unavailable: ${error.message}. Connect the TechBrief API and try again.`));
    if (state.demo) {
      state.trendingItems = demoStories;
      state.total = demoStories.length;
      renderTrending();
    }
  }
}

async function backfillMissingImages() {
  if (state.imageBackfillStarted || state.demo || !state.articles.some((article) => !article.image_checked_at)) return;
  state.imageBackfillStarted = true;
  try {
    const result = await api("/images/discover-missing", { method: "POST", body: "{}" });
    if (!result.updated) return;
    const feed = await getNews({ page_size: 30 });
    state.articles = feed.items;
    state.total = feed.total;
    if (state.view === "home") renderAllFeeds();
  } catch (error) {
    showNotice(`Story images could not be refreshed: ${error.message}`);
  }
}

function renderBrief(takeaways) {
  const list = $("#brief-list");
  list.replaceChildren();
  if (!takeaways.length) {
    list.append(node("p", "", "Generate a daily briefing to see today's key takeaways."));
    return;
  }
  takeaways.slice(0, 5).forEach((item, index) => {
    const row = node("div", "brief-item");
    row.append(node("span", "brief-number", String(index + 1).padStart(2, "0")));
    row.append(node("p", "", item));
    list.append(row);
  });
}

function renderAllFeeds() {
  const articles = state.articles;
  const latest = $("#latest-grid");
  const editors = $("#editors-grid");
  if (!articles.length) {
    latest.replaceChildren(node("div", "editorial-empty", "No source-linked stories are available yet. Try again after your first collection, or configure RSS feeds in Preferences."));
    editors.replaceChildren(node("div", "editorial-empty", "Editor's selections will appear after the first news collection."));
    $("#lead-title").textContent = "The signals shaping tomorrow, clearly in focus.";
    $("#lead-summary").textContent = "A considered briefing on artificial intelligence and cybersecurity. Source-linked developments, useful context, and no noise.";
    $("#lead-open").disabled = true;
    return;
  }
  $("#lead-open").disabled = false;
  const lead = articles[0];
  const leadImage = $("#lead-image");
  const leadUrl = lead.image_verified && !lead.is_illustrative ? safeHref(lead.image_url) : null;
  leadImage.removeAttribute("src");
  leadImage.alt = leadUrl ? safeText(lead.image_alt, `Image accompanying ${lead.title}`) : "";
  $("#lead-art").classList.toggle("has-source-photo", Boolean(leadUrl));
  $("#lead-art").classList.toggle("is-cyber", lead.category === "cybersecurity");
  const leadCredit = $("#lead-image-credit");
  leadCredit.replaceChildren();
  if (leadUrl) leadCredit.append(...imageAttribution(lead).childNodes);
  leadCredit.classList.toggle("hidden", !leadCredit.textContent);
  if (leadUrl) {
    leadImage.src = leadUrl;
    leadImage.onerror = () => {
      leadImage.removeAttribute("src");
      leadImage.alt = "";
      $("#lead-art").classList.remove("has-source-photo");
      leadCredit.classList.add("hidden");
    };
  }
  $("#lead-category").textContent = categoryName(lead);
  $("#lead-category").className = `category-label ${lead.category === "cybersecurity" ? "cyber-label" : "ai-label"}`;
  $("#lead-title").textContent = lead.title;
  $("#lead-summary").textContent = safeText(lead.summary || lead.description);
  $("#lead-meta").textContent = `${safeText(lead.source_name)} · ${timeAgo(lead.published_at)}`;
  $("#lead-open").onclick = () => openArticle(lead);
  const top = articles.slice(1, 6);
  latest.replaceChildren();
  if (!top.length) latest.append(storyCard(lead, 0, { spanTwo: true }));
  else top.forEach((article, index) => latest.append(storyCard(article, index, { spanTwo: index === 0 })));
  editors.replaceChildren();
  if (articles.length < 2) {
    editors.append(node("div", "editorial-empty", "More curated selections appear as the research network finds new stories."));
  } else {
    const centerIndex = Math.min(2, articles.length - 1);
    const center = editorPick(articles[centerIndex], centerIndex, true);
    const left = node("div", "editors-side");
    const right = node("div", "editors-side");
    [articles[1], articles[3]].filter(Boolean).forEach((item, index) => left.append(editorPick(item, index + 1)));
    [articles[4], articles[5]].filter(Boolean).forEach((item, index) => right.append(editorPick(item, index + 4)));
    editors.append(left, center, right);
  }
}

async function loadTrending(category = state.activeCategory, append = false) {
  if (state.demo) {
    state.trendingItems = demoStories.filter((article) => !category || article.category === category);
    state.total = state.trendingItems.length;
    state.page = 1;
    renderTrending();
    return;
  }
  const params = { page: append ? state.page + 1 : 1, page_size: 12 };
  if (category === "ai" || category === "cybersecurity") params.category = category;
  if (category === "research") params.q = "research";
  if (category === "business") params.q = "business";
  if (category === "startups") params.q = "startup";
  if (category === "technology") params.q = "technology";
  try {
    const result = await getNews(params);
    state.trendingItems = append ? [...(state.trendingItems || []), ...result.items] : result.items;
    state.page = params.page;
    state.total = result.total;
    state.trendingItems.sort((a, b) => Number(b.importance_score || 0) - Number(a.importance_score || 0));
    renderTrending();
  } catch (error) {
    $("#trending-feature").replaceChildren(node("div", "editorial-empty", `Trending stories are unavailable: ${error.message}`));
    if (state.demo) {
      state.trendingItems = demoStories;
      state.total = demoStories.length;
      renderTrending();
    }
  }
}

function renderTrending() {
  const items = state.trendingItems || [];
  const feature = $("#trending-feature");
  const middle = $("#trending-middle");
  const shortlist = $("#trending-shortlist");
  const more = $("#trending-more-grid");
  feature.replaceChildren();
  middle.replaceChildren();
  shortlist.replaceChildren();
  more.replaceChildren();
  if (!items.length) {
    feature.append(node("div", "editorial-empty", "No stories match this topic yet."));
    return;
  }
  feature.append(storyCard(items[0], 0, { excerpt: true }));
  items.slice(1, 3).forEach((article, index) => middle.append(storyCard(article, index + 1)));
  items.slice(3, 8).forEach((article) => {
    const item = node("article", "short-story");
    item.tabIndex = 0;
    item.append(node("span", `story-category ${article.category === "cybersecurity" ? "cyber" : ""}`, categoryName(article)));
    item.append(node("h3", "", safeText(article.title)));
    item.append(authorLine(article, true));
    const publisherLink = sourceLink(article, "OPEN ARTICLE ↗", "source-link shortlist-source-link");
    if (publisherLink) item.append(publisherLink);
    item.addEventListener("click", () => openArticle(article));
    shortlist.append(item);
  });
  items.slice(8).forEach((article, index) => more.append(storyCard(article, index + 8)));
  $("#trending-more").classList.toggle("hidden", items.length >= state.total);
}

function matchesSubcategory(article, subcategory) {
  if (!subcategory) return true;
  const text = `${article.title} ${article.description}`.toLowerCase();
  const keyword = {
    research: "research paper model study arxiv",
    models: "model release model launches",
    agents: "agent autonomous agents",
    vulnerability: "vulnerability cve zero-day patch",
    threat: "threat ransomware malware attack",
    advisory: "advisory alert guidance",
  }[subcategory];
  return keyword ? keyword.split(" ").some((word) => text.includes(word)) : true;
}

async function loadTopic(view, query = "", subcategory = "") {
  const map = { ai: ["ai-grid", "ai"], cyber: ["cyber-grid", "cybersecurity"], research: ["research-grid", ""] , insights: ["insights-grid", ""] };
  const [containerId, category] = map[view];
  const container = $(`#${containerId}`);
  if (state.demo) {
    const items = demoStories.filter((article) =>
      (!category || article.category === category)
      && (!query || `${article.title} ${article.description}`.toLowerCase().includes(query.toLowerCase()))
      && matchesSubcategory(article, subcategory));
    container.replaceChildren();
    if (!items.length) container.append(node("div", "editorial-empty", "No sample stories match this filter."));
    items.forEach((article, index) => container.append(storyCard(article, index)));
    return;
  }
  setLoading(container);
  try {
    const result = await getNews({
      category: view === "research" && ["ai", "cybersecurity"].includes(subcategory) ? subcategory : category,
      q: query || undefined,
      page_size: 100,
      ...(view === "research" ? { q: query || "research" } : {}),
    });
    state.articles = result.items;
    const items = result.items.filter((article) => matchesSubcategory(article, subcategory));
    container.replaceChildren();
    if (!items.length) {
      container.append(node("div", "editorial-empty", "No source-linked stories match these filters."));
      return;
    }
    items.forEach((article, index) => container.append(storyCard(article, index)));
  } catch (error) {
    container.replaceChildren(node("div", "editorial-empty", `Coverage unavailable: ${error.message}`));
  }
}

async function loadArchive() {
  const grid = $("#archive-grid");
  const reports = $("#archive-reports");
  if (state.demo) {
    const query = $("#archive-search").value.toLowerCase();
    const items = demoStories.filter((article) =>
      (!state.archiveCategory || article.category === state.archiveCategory)
      && (!query || article.title.toLowerCase().includes(query)));
    grid.replaceChildren();
    items.forEach((item, index) => grid.append(storyCard(item, index)));
    reports.replaceChildren(node("div", "editorial-empty", "Demo mode does not include archived report records."));
    return;
  }
  setLoading(grid, "Searching the archive…");
  const query = $("#archive-search").value.trim();
  const category = state.archiveCategory;
  try {
    const [feed, reportData] = await Promise.all([
      getNews({ page_size: 100, q: query || undefined, category: category || undefined }),
      api("/reports?page_size=100"),
    ]);
    grid.replaceChildren();
    if (!feed.items.length) grid.append(node("div", "editorial-empty", "No archived stories match your search."));
    feed.items.forEach((item, index) => grid.append(storyCard(item, index)));
    reports.replaceChildren();
    const reportItems = reportData.items.filter((report) => !query || report.executive_summary.toLowerCase().includes(query.toLowerCase()));
    if (!reportItems.length) return;
    reportItems.forEach((report) => {
      const item = node("article", "archive-report");
      item.append(node("h3", "", `Daily briefing · ${dateLabel(report.created_at, { month: "long", day: "numeric", year: "numeric" })}`));
      item.append(node("p", "", report.executive_summary));
      const button = node("button", "", "READ THIS BRIEFING ↗");
      button.addEventListener("click", async () => {
        try {
          state.report = await api(`/reports/${report.id}`);
          renderReportAsArticle(state.report);
          navigate("article");
        } catch (error) { showNotice(error.message); }
      });
      item.append(button);
      reports.append(item);
    });
  } catch (error) {
    grid.replaceChildren(node("div", "editorial-empty", `Archive unavailable: ${error.message}`));
  }
}

function renderBookmarks(items) {
  const grid = $("#bookmarks-grid");
  grid.replaceChildren();
  if (!items.length) grid.append(node("div", "editorial-empty", "No saved stories yet. Save a story from any page to keep it here."));
  items.forEach((bookmark, index) => {
    const card = node("article", "story-card");
    const article = bookmark.article;
    card.append(storyImage(article, index), authorLine(article, true), node("h3", "story-title", article.title));
    const publisherLink = sourceLink(article, "OPEN PUBLISHER ARTICLE ↗", "source-link card-source-link");
    if (publisherLink) card.append(publisherLink);
    const remove = node("button", "bookmark-remove", "REMOVE FROM SAVED");
    remove.addEventListener("click", async () => {
      try {
        await api(`/bookmarks/${bookmark.id}`, { method: "DELETE" });
        await loadBookmarks();
      } catch (error) { showNotice(error.message); }
    });
    card.append(remove);
    card.addEventListener("click", (event) => {
      if (!event.target.closest(".bookmark-remove")) openArticle(article);
    });
    grid.append(card);
  });
}

async function loadBookmarks() {
  try {
    const result = await api("/bookmarks");
    renderBookmarks(result.items);
  } catch (error) {
    $("#bookmarks-grid").replaceChildren(node("div", "editorial-empty", `Saved stories unavailable: ${error.message}`));
  }
}

function governanceRow(title, details, status = "") {
  const row = node("article", "governance-row");
  const copy = node("div", "governance-row-copy");
  copy.append(node("strong", "", title), node("span", "", details));
  row.append(copy);
  if (status) row.append(node("span", "governance-status-pill", status.toUpperCase()));
  return row;
}

function permissionScopeForAction(action) {
  return {
    "workflow.coordinate": "workflow:*",
    "source.read": "sources:*",
    "article.analyze": "news:articles",
    "article.verify": "news:articles",
    "article.rank": "news:articles",
    "article.summarize": "news:articles",
    "image.discover": "news:images",
    "report.compose": "techbrief:reports",
    "article.write": "techbrief:articles",
    "report.write": "techbrief:reports",
    "external.ticket.draft": "external:drafts",
    "external.incident_report.draft": "external:drafts",
    "external.detection_rule.draft": "external:drafts",
    "soc.ticket.create": "external:tickets",
    "soc.detection_rule.modify": "soc:detection_rules",
    "soc.security_config.modify": "soc:security_config",
  }[action] || "";
}

function refreshPermissionActionOptions() {
  const form = $("#governance-permission-form");
  const agentId = form.elements.agent_id.value;
  const agent = state.governanceAgents?.find((item) => item.id === agentId);
  const select = form.elements.allowed_action;
  select.replaceChildren();
  (agent?.allowed_tools || []).forEach((action) => {
    const option = node("option", "", action);
    option.value = action;
    select.append(option);
  });
  form.elements.resource_scope.value = permissionScopeForAction(select.value);
}

function updatePermissionScope() {
  const form = $("#governance-permission-form");
  form.elements.resource_scope.value = permissionScopeForAction(form.elements.allowed_action.value);
}

async function loadGovernanceDashboard() {
  const dashboard = $("#governance-dashboard");
  if (!state.adminToken) {
    dashboard.classList.add("hidden");
    $("#governance-status").textContent = "Enter the administrator token configured in backend .env.";
    return;
  }
  try {
    const [session, agents, permissions, approvals, actions, audit] = await Promise.all([
      api("/governance/session"),
      api("/agents"),
      api("/permissions"),
      api("/approvals"),
      api("/actions"),
      api("/audit-logs?limit=100"),
    ]);
    state.adminUser = session.user_id;
    $("#governance-user").textContent = `SIGNED IN · ${session.user_id} · ${session.role.toUpperCase()}`;
    $("#governance-status").textContent = "Administrator access verified.";
    $("#governance-status").classList.add("success");
    $("#governance-token").value = "";
    $("#governance-lock").classList.remove("hidden");
    dashboard.classList.remove("hidden");

    const agentGrid = $("#governance-agents");
    agentGrid.replaceChildren();
    const agentSelect = $('#governance-action-form [name="agent_id"]');
    agentSelect.replaceChildren();
    const grantAgentSelect = $('#governance-permission-form [name="agent_id"]');
    grantAgentSelect.replaceChildren();
    state.governanceAgents = agents.items;
    agents.items.forEach((agent) => {
      const card = node("article", "governance-agent-card");
      card.append(
        node("strong", "", agent.agent_name),
        node("span", "", `${agent.role.replaceAll("_", " ")} · ${agent.status}`),
        node("small", "", `Allowed tools: ${agent.allowed_tools.join(", ") || "none"}`),
      );
      agentGrid.append(card);
      const grantOption = node("option", "", agent.agent_name);
      grantOption.value = agent.id;
      grantAgentSelect.append(grantOption);
      if (agent.id === "soc_action_agent") {
        const option = node("option", "", agent.agent_name);
        option.value = agent.id;
        agentSelect.append(option);
      }
    });
    refreshPermissionActionOptions();

    const permissionList = $("#governance-permissions");
    permissionList.replaceChildren();
    const currentPermissions = permissions.items.filter((grant) => grant.status === "active");
    if (!currentPermissions.length) permissionList.append(node("p", "governance-empty", "No active grants."));
    currentPermissions.forEach((grant) => {
      const expiry = new Date(grant.expires_at);
      const minutesLeft = Math.max(0, Math.round((expiry.getTime() - Date.now()) / 60000));
      const row = governanceRow(
        `${grant.agent_id} · ${grant.allowed_actions.join(", ")}`,
        `Scope ${grant.resource_scope} · granted by ${grant.granted_by} · expires ${dateLabel(grant.expires_at, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`,
        minutesLeft <= 60 ? "Expiring soon" : "Active",
      );
      const revoke = node("button", "governance-small-button", "REVOKE");
      revoke.type = "button";
      revoke.addEventListener("click", async () => {
        const reason = window.prompt("Why are you revoking this permission?");
        if (!reason?.trim()) return;
        try {
          await api("/permissions/revoke", {
            method: "POST",
            body: JSON.stringify({ permission_id: grant.id, reason: reason.trim() }),
          });
          await loadGovernanceDashboard();
        } catch (error) {
          showNotice(error.message);
        }
      });
      row.append(revoke);
      permissionList.append(row);
    });

    const approvalList = $("#governance-approvals");
    approvalList.replaceChildren();
    const pending = approvals.items.filter((item) => item.decision === "pending");
    if (!pending.length) approvalList.append(node("p", "governance-empty", "No pending approvals."));
    pending.forEach((approval) => {
      const action = approval.action;
      const row = governanceRow(
        `${action?.agent_id || "Unknown agent"} · ${action?.requested_action || "Action"}`,
        `${action?.target_resource || "Unknown resource"} · ${action?.reason || ""} · expires ${dateLabel(approval.expires_at, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`,
        "Pending",
      );
      for (const decision of ["approve", "reject"]) {
        const button = node("button", `governance-small-button${decision === "approve" ? " approve" : ""}`, decision.toUpperCase());
        button.type = "button";
        button.addEventListener("click", async () => {
          const reason = window.prompt(`${decision === "approve" ? "Approval" : "Rejection"} reason:`);
          if (!reason?.trim()) return;
          try {
            await api(`/approvals/${approval.id}/${decision}`, {
              method: "POST",
              body: JSON.stringify({ reason: reason.trim() }),
            });
            await loadGovernanceDashboard();
          } catch (error) {
            showNotice(error.message);
          }
        });
        row.append(button);
      }
      approvalList.append(row);
    });

    const actionList = $("#governance-actions");
    actionList.replaceChildren();
    if (!actions.items.length) actionList.append(node("p", "governance-empty", "No action requests yet."));
    actions.items.forEach((action) => {
      const row = governanceRow(
        `${action.agent_id} · ${action.requested_action}`,
        `${action.target_resource} · ${action.reason} · ${dateLabel(action.created_at, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`,
        action.status,
      );
      if (action.status === "Approved" && action.requested_action.endsWith(".draft")) {
        const execute = node("button", "governance-small-button approve", "CREATE INTERNAL DRAFT");
        execute.type = "button";
        execute.addEventListener("click", async () => {
          try {
            await api(`/actions/${action.id}/execute`, { method: "POST" });
            await loadGovernanceDashboard();
          } catch (error) {
            showNotice(error.message);
          }
        });
        row.append(execute);
      }
      if (action.failure_reason) row.append(node("small", "governance-failure", action.failure_reason));
      actionList.append(row);
    });

    const auditList = $("#governance-audit");
    auditList.replaceChildren();
    if (!audit.items.length) auditList.append(node("p", "governance-empty", "Audit history is empty."));
    audit.items.forEach((entry) => {
      auditList.append(governanceRow(
        `${entry.authorization_result} · ${entry.action}`,
        `${entry.agent_id || entry.user_id || "system"} · ${entry.resource} · ${dateLabel(entry.timestamp, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}${entry.failure_reason ? ` · ${entry.failure_reason}` : ""}`,
        entry.authorization_result,
      ));
    });
  } catch (error) {
    state.adminToken = null;
    state.adminUser = null;
    dashboard.classList.add("hidden");
    $("#governance-lock").classList.add("hidden");
    $("#governance-status").classList.remove("success");
    $("#governance-status").textContent = `Governance unavailable: ${error.message}`;
  }
}

async function unlockGovernance(event) {
  event.preventDefault();
  const token = $("#governance-token").value.trim();
  state.adminToken = token;
  try {
    const session = await api("/governance/session");
    state.adminUser = session.user_id;
    await loadGovernanceDashboard();
  } catch (error) {
    state.adminToken = null;
    $("#governance-status").textContent = `Access denied: ${error.message}`;
  }
}

function lockGovernance() {
  state.adminToken = null;
  state.adminUser = null;
  $("#governance-dashboard").classList.add("hidden");
  $("#governance-lock").classList.add("hidden");
  $("#governance-status").textContent = "Governance is locked.";
}

async function submitGovernanceAction(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const body = Object.fromEntries(new FormData(form).entries());
  $("#governance-action-status").textContent = "Submitting request…";
  try {
    const action = await api("/actions/request", { method: "POST", body: JSON.stringify(body) });
    $("#governance-action-status").textContent = `Request status: ${action.status}.`;
    await loadGovernanceDashboard();
  } catch (error) {
    $("#governance-action-status").textContent = error.message;
  }
}

async function submitPermissionGrant(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const body = {
    agent_id: form.elements.agent_id.value,
    resource_scope: form.elements.resource_scope.value,
    allowed_actions: [form.elements.allowed_action.value],
    expires_in_minutes: Number(form.elements.expires_in_minutes.value),
    approval_reference: form.elements.approval_reference.value.trim() || null,
  };
  $("#governance-permission-status").textContent = "Creating scoped grant…";
  try {
    await api("/permissions/grant", { method: "POST", body: JSON.stringify(body) });
    $("#governance-permission-status").textContent = "Permission granted and audited.";
    await loadGovernanceDashboard();
  } catch (error) {
    $("#governance-permission-status").textContent = error.message;
  }
}

async function loadSettings() {
  try {
    const [settings, sources] = await Promise.all([api("/settings"), api("/sources")]);
    const form = $("#settings-form");
    form.elements.scheduled_time.value = settings.scheduled_time;
    form.elements.timezone.value = settings.timezone;
    form.elements.schedule_enabled.checked = settings.schedule_enabled;
    form.elements.articles_per_category.value = String(settings.articles_per_category);
    form.elements.category_ai.checked = settings.enabled_categories.includes("ai");
    form.elements.category_cyber.checked = settings.enabled_categories.includes("cybersecurity");
    const sourceGrid = $("#source-options");
    sourceGrid.replaceChildren();
    sources.items.forEach((source) => {
      const label = node("label");
      const input = node("input");
      input.type = "checkbox";
      input.name = "preferred_source";
      input.value = source.name;
      input.checked = source.enabled;
      label.append(input, node("span", "", source.name));
      sourceGrid.append(label);
    });
  } catch (error) {
    $("#settings-message").textContent = error.message;
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const settings = {
    scheduled_time: form.elements.scheduled_time.value,
    timezone: form.elements.timezone.value,
    schedule_enabled: form.elements.schedule_enabled.checked,
    enabled_categories: [
      ...(form.elements.category_ai.checked ? ["ai"] : []),
      ...(form.elements.category_cyber.checked ? ["cybersecurity"] : []),
    ],
    articles_per_category: Number(form.elements.articles_per_category.value),
    preferred_sources: $$('input[name="preferred_source"]:checked', form).map((input) => input.value),
  };
  if (!settings.enabled_categories.length) {
    $("#settings-message").textContent = "Choose at least one coverage category.";
    return;
  }
  if ($$('input[name="preferred_source"]', form).length && !settings.preferred_sources.length) {
    $("#settings-message").textContent = "Choose at least one preferred source.";
    return;
  }
  try {
    await api("/settings", { method: "PUT", body: JSON.stringify(settings) });
    $("#settings-message").textContent = "Preferences saved.";
  } catch (error) {
    $("#settings-message").textContent = error.message;
  }
}

async function generateDailyBrief() {
  const buttons = [$("#generate-button"), $("#fetch-news-button")].filter(Boolean);
  const buttonLabels = new Map(buttons.map((button) => [button, button.innerHTML]));
  const setBusy = (busy) => {
    buttons.forEach((button) => {
      button.disabled = busy;
      button.textContent = busy ? "AGENT FETCHING NEWS…" : "";
      if (!busy) button.innerHTML = buttonLabels.get(button);
    });
  };
  setBusy(true);
  try {
    await api("/reports/generate", { method: "POST", body: "{}" });
    showNotice("The research agent is fetching and reviewing the latest news.", true);
    state.reportPoll = setInterval(async () => {
      try {
        const job = await api("/reports/status");
        if (job.status === "running") return;
        clearInterval(state.reportPoll);
        state.reportPoll = null;
        setBusy(false);
        if (job.status === "completed" || job.status === "completed_with_warnings") {
          showNotice("Latest news is ready.", true);
          await loadHome();
        } else {
          showNotice("The news agent could not complete its fetch. Check source configuration and backend logs.");
        }
      } catch {
        clearInterval(state.reportPoll);
        state.reportPoll = null;
        setBusy(false);
        showNotice("Could not check the research status.");
      }
    }, 2500);
  } catch (error) {
    setBusy(false);
    showNotice(error.message);
  }
}

async function toggleBookmark(article, button) {
  try {
    const result = await api("/bookmarks", { method: "POST", body: JSON.stringify({ article_id: article.id }) });
    button.textContent = "SAVED ✓";
    button.dataset.bookmarkId = result.id;
  } catch (error) {
    showNotice(error.message);
  }
}

function renderArticle(article) {
  const container = $("#article-content");
  container.replaceChildren();
  const category = node("span", `story-category ${article.category === "cybersecurity" ? "cyber" : ""}`, categoryName(article));
  container.append(category);
  container.append(node("h1", "article-title", safeText(article.title)));
  container.append(node("p", "article-deck", safeText(article.description || article.summary)));
  container.append(authorLine(article));
  const imageFrame = storyImage(article, 0, "article-hero-image");
  container.append(imageFrame);
  const summary = safeText(article.summary || article.description);
  const description = safeText(article.description);
  const body = node("div", "article-body", summary === description
    ? "Open the original publisher's article for the complete report."
    : summary);
  container.append(body);
  if (article.impact_analysis) {
    const context = node("blockquote", "article-context");
    context.append(node("span", "section-kicker", "WHY IT MATTERS"));
    context.append(node("p", "", article.impact_analysis));
    container.append(context);
  }
  const source = sourceLink(article);
  if (source) container.append(source);
  container.append(node("p", "verification-note", `Source status: ${safeText(article.verification_status)}. Consult the original source for full context and independent confirmation.`));
  const actions = node("div", "article-actions");
  if (typeof article.id === "number") {
    const save = node("button", "", "SAVE STORY ☆");
    save.addEventListener("click", () => toggleBookmark(article, save));
    actions.append(save);
  }
  const share = node("button", "", "SHARE STORY ↗");
  share.addEventListener("click", async () => {
    try {
      if (navigator.share) await navigator.share({ title: article.title, url: location.href });
      else if (navigator.clipboard) {
        await navigator.clipboard.writeText(location.href);
        showNotice("Article link copied.", true);
      } else showNotice("Sharing is not supported in this browser.");
    } catch (error) {
      if (error.name !== "AbortError") showNotice("Could not share this story.");
    }
  });
  actions.append(share);
  container.append(actions);
  const related = state.articles.filter((item) => item.id !== article.id && item.category === article.category).slice(0, 3);
  if (related.length) {
    const section = node("section", "related-section");
    section.append(node("span", "section-kicker", "KEEP READING"));
    section.append(node("h2", "", "Related stories"));
    const grid = node("div", "topic-grid");
    related.forEach((item, index) => grid.append(storyCard(item, index)));
    section.append(grid);
    container.append(section);
  }
}

function renderReportAsArticle(report) {
  const container = $("#article-content");
  container.replaceChildren(node("span", "story-category", "DAILY INTELLIGENCE"));
  container.append(node("h1", "article-title", `TechBrief daily briefing · ${report.report_date}`));
  container.append(node("p", "article-deck", report.executive_summary));
  container.append(node("div", "article-body", report.impact_analysis));
  const stories = [...(report.stories || [])].sort((a, b) => b.importance_score - a.importance_score);
  stories.forEach((story) => container.append(storyCard(story, stories.indexOf(story))));
}

async function loadTopicPage(view) {
  if (view === "ai") return loadTopic("ai", $("#ai-search").value, activeSubcategory("ai"));
  if (view === "cyber") return loadTopic("cyber", $("#cyber-search").value, activeSubcategory("cyber"));
  if (view === "research") return loadTopic("research", $("#research-search").value, state.researchCategory);
  if (view === "insights") return loadTopic("insights");
}

function activeSubcategory(view) {
  return $(`#view-${view} .topic-tab.active`)?.dataset.subcategory || "";
}

function navigate(view) {
  state.view = view;
  $$(".view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
  $$(".site-nav [data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $("#site-nav").classList.remove("open");
  $("#menu-toggle").setAttribute("aria-expanded", "false");
  if (view === "home") loadHome();
  if (view === "trending") {
    state.activeCategory = "";
    $$(".filter-chip", $("#trending-filters")).forEach((chip) => chip.classList.toggle("selected", chip.dataset.category === ""));
    loadTrending("");
  }
  if (["ai", "cyber", "research", "insights"].includes(view)) loadTopicPage(view);
  if (view === "archive") loadArchive();
  if (view === "bookmarks") loadBookmarks();
  if (view === "settings") loadSettings();
  if (view === "governance") loadGovernanceDashboard();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function loadReportAndRenderBrief() {
  try {
    const latest = await api("/reports/latest");
    state.report = await api(`/reports/${latest.id}`);
    renderBrief(state.report.key_takeaways || []);
  } catch {
    renderBrief([]);
  }
}

$("#lead-open").addEventListener("click", () => {
  const article = state.articles[0];
  if (article) openArticle(article);
});
$("#menu-toggle").addEventListener("click", () => {
  const open = $("#site-nav").classList.toggle("open");
  $("#menu-toggle").setAttribute("aria-expanded", String(open));
});
$$("[data-view]").forEach((button) => button.addEventListener("click", () => {
  const category = button.dataset.category;
  if (button.classList.contains("filter-chip")) {
    state.activeCategory = category || "";
    $$(".filter-chip").forEach((chip) => chip.classList.toggle("selected", chip === button));
    state.page = 1;
    loadTrending(state.activeCategory);
  } else navigate(button.dataset.view);
}));
$$("[data-view-link]").forEach((link) => link.addEventListener("click", (event) => {
  event.preventDefault();
  navigate(link.dataset.viewLink);
}));
$("#article-back").addEventListener("click", () => navigate("home"));
$("#generate-button").addEventListener("click", generateDailyBrief);
$("#fetch-news-button").addEventListener("click", generateDailyBrief);
$("#trending-more").addEventListener("click", () => loadTrending(state.activeCategory, true));
$("#settings-form").addEventListener("submit", saveSettings);
$("#governance-login").addEventListener("submit", unlockGovernance);
$("#governance-lock").addEventListener("click", lockGovernance);
$("#governance-refresh").addEventListener("click", loadGovernanceDashboard);
$("#governance-action-form").addEventListener("submit", submitGovernanceAction);
$("#governance-permission-form").addEventListener("submit", submitPermissionGrant);
$('#governance-permission-form [name="agent_id"]').addEventListener("change", refreshPermissionActionOptions);
$('#governance-permission-form [name="allowed_action"]').addEventListener("change", updatePermissionScope);
$('#governance-action-form [name="requested_action"]').addEventListener("change", (event) => {
  const resource = $('#governance-action-form [name="target_resource"]');
  resource.value = event.target.value.endsWith(".draft") ? "external:drafts"
    : event.target.value === "soc.ticket.create" ? "external:tickets"
    : event.target.value === "soc.detection_rule.modify" ? "soc:detection_rules"
    : "soc:security_config";
});
$("#trending-filters").addEventListener("click", (event) => {
  const button = event.target.closest("[data-category]");
  if (!button) return;
  state.activeCategory = button.dataset.category;
  $$(".filter-chip", $("#trending-filters")).forEach((chip) => chip.classList.toggle("selected", chip === button));
  state.page = 1;
  loadTrending(state.activeCategory);
});
$("#subscribe-button").addEventListener("click", () => $("#subscribe-modal").classList.remove("hidden"));
$("#newsletter-button").addEventListener("click", () => $("#subscribe-modal").classList.remove("hidden"));
$("#modal-close").addEventListener("click", () => $("#subscribe-modal").classList.add("hidden"));
$("#subscribe-modal").addEventListener("click", (event) => {
  if (event.target === $("#subscribe-modal")) $("#subscribe-modal").classList.add("hidden");
});
$("#modal-preferences").addEventListener("click", () => {
  $("#subscribe-modal").classList.add("hidden");
  navigate("settings");
});
$("#ai-search").addEventListener("input", () => loadTopic("ai", $("#ai-search").value, activeSubcategory("ai")));
$("#cyber-search").addEventListener("input", () => loadTopic("cyber", $("#cyber-search").value, activeSubcategory("cyber")));
$("#research-search").addEventListener("input", () => loadTopic("research", $("#research-search").value, state.researchCategory));
$("#archive-search").addEventListener("input", () => {
  clearTimeout(state.archiveDebounce);
  state.archiveDebounce = setTimeout(loadArchive, 200);
});
$$('#view-ai [data-subcategory]').forEach((button) => button.addEventListener("click", () => {
  $$("#view-ai .topic-tab").forEach((tab) => tab.classList.toggle("active", tab === button));
  loadTopic("ai", $("#ai-search").value, button.dataset.subcategory);
}));
$$('#view-cyber [data-subcategory]').forEach((button) => button.addEventListener("click", () => {
  $$("#view-cyber .topic-tab").forEach((tab) => tab.classList.toggle("active", tab === button));
  loadTopic("cyber", $("#cyber-search").value, button.dataset.subcategory);
}));
$$("[data-research-category]").forEach((button) => button.addEventListener("click", () => {
  state.researchCategory = button.dataset.researchCategory;
  $$("#view-research .topic-tab").forEach((tab) => tab.classList.toggle("active", tab === button));
  loadTopic("research", $("#research-search").value, state.researchCategory);
}));
$$("[data-archive-category]").forEach((button) => button.addEventListener("click", () => {
  state.archiveCategory = button.dataset.archiveCategory;
  $$("#view-archive .topic-tab").forEach((tab) => tab.classList.toggle("active", tab === button));
  loadArchive();
}));

$("#edition-date").textContent = new Intl.DateTimeFormat(undefined, {
  weekday: "short", month: "short", day: "numeric", year: "numeric",
}).format(new Date()).toUpperCase();
if (state.demo) {
  demoFallback();
} else {
  loadHome();
}
