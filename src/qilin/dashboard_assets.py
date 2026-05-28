"""Static assets for the lightweight Qilin dashboard."""

from __future__ import annotations

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Qilin Dev Dashboard</title>
  <link rel="stylesheet" href="/dashboard/styles.css">
</head>
<body>
  <main class="container">
    <header>
      <h1>Qilin Dev Dashboard</h1>
      <div id="status">Loading...</div>
    </header>
    <section class="card">
      <h2>Test Bench</h2>
      <form id="search-form">
        <input id="query" placeholder="Search query" required>
        <input id="git_branch" placeholder="Branch (optional)">
        <input id="collection" placeholder="Collection (optional)">
        <input id="top_k" type="number" min="1" value="5">
        <input id="score_threshold" type="number" step="0.01" placeholder="score threshold">
        <input id="mmr_lambda" type="number" min="0" max="1" step="0.05" placeholder="mmr lambda">
        <select id="mode">
          <option value="">auto</option>
          <option value="dense">dense</option>
          <option value="sparse">sparse</option>
          <option value="hybrid">hybrid</option>
        </select>
        <label><input id="rerank" type="checkbox"> rerank</label>
        <button type="submit">Search</button>
      </form>
      <ul id="hits"></ul>
    </section>
    <section class="card">
      <h2>Known Branches & Collections</h2>
      <ul id="branches"></ul>
      <h3>Unknown collections</h3>
      <ul id="unknown-collections"></ul>
    </section>
    <section class="card">
      <h2>Memory Curator</h2>
      <button id="refresh-sources">Refresh sources</button>
      <ul id="sources"></ul>
    </section>
    <section class="card">
      <h2>Feedback Log</h2>
      <ul id="feedback-log"></ul>
    </section>
  </main>
  <script src="/dashboard/app.js"></script>
</body>
</html>
"""

STYLES_CSS = """
body { font-family: Inter, system-ui, sans-serif; margin: 0; background: #0b0c0f; color: #f1f2f4; }
.container { max-width: 1100px; margin: 0 auto; padding: 16px; }
header { display: flex; justify-content: space-between; align-items: center; }
.card { background: #171922; border: 1px solid #2a2d3a; border-radius: 8px; padding: 12px; margin-top: 12px; }
form { display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 8px; }
input, select, button { background: #10121a; color: #f1f2f4; border: 1px solid #323649; border-radius: 6px; padding: 8px; }
button { cursor: pointer; }
ul { padding-left: 18px; }
li { margin: 6px 0; }
.muted { color: #9fa5bc; }
"""

APP_JS = """
async function jsonFetch(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  return response.json();
}

function renderList(el, rows, mapFn) {
  el.innerHTML = "";
  for (const row of rows) {
    const li = document.createElement("li");
    li.innerHTML = mapFn(row);
    el.appendChild(li);
  }
}

async function loadBranches() {
  const data = await jsonFetch("/dashboard/api/branches");
  renderList(document.getElementById("branches"), data.branches || [], (b) =>
    `<strong>${b.branch_name}</strong> <span class="muted">(${b.collections.length} collections)</span><br>${b.collections.join("<br>")}`
  );
  renderList(document.getElementById("unknown-collections"), data.unknown_collections || [], (c) => c);
}

async function loadSources() {
  const data = await jsonFetch("/dashboard/api/sources");
  renderList(document.getElementById("sources"), data.sources || [], (s) =>
    `${s.source} <span class="muted">(${s.chunk_count} chunks)</span> <button data-source="${s.source}">Delete source</button>`
  );
  for (const btn of document.querySelectorAll("#sources button[data-source]")) {
    btn.addEventListener("click", async () => {
      await jsonFetch("/dashboard/api/sources/delete", {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({ source: btn.dataset.source }),
      });
      await loadSources();
    });
  }
}

async function loadFeedback() {
  const data = await jsonFetch("/dashboard/api/feedback");
  renderList(document.getElementById("feedback-log"), data.entries || [], (e) =>
    `<code>${e.timestamp || ""}</code> ${e.query || ""} <span class="muted">(${e.collection || ""})</span>`
  );
}

document.getElementById("search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    query: document.getElementById("query").value,
    git_branch: document.getElementById("git_branch").value || null,
    collection: document.getElementById("collection").value || null,
    top_k: Number(document.getElementById("top_k").value || 5),
    score_threshold: document.getElementById("score_threshold").value ? Number(document.getElementById("score_threshold").value) : null,
    mmr_lambda: document.getElementById("mmr_lambda").value ? Number(document.getElementById("mmr_lambda").value) : null,
    mode: document.getElementById("mode").value || null,
    rerank: document.getElementById("rerank").checked,
  };
  const data = await jsonFetch("/dashboard/api/search", {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify(payload),
  });
  renderList(document.getElementById("hits"), data.hits || [], (h) =>
    `<strong>${h.source || "(no source)"}</strong> <span class="muted">score=${(h.score || 0).toFixed(3)}</span><br><code>${(h.text || "").slice(0, 180)}</code>`
  );
});

document.getElementById("refresh-sources").addEventListener("click", loadSources);

async function bootstrap() {
  await Promise.all([loadBranches(), loadSources(), loadFeedback()]);
  document.getElementById("status").textContent = "Ready";
}

bootstrap().catch((err) => {
  document.getElementById("status").textContent = `Error: ${err.message}`;
});
"""
