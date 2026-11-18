const SUGGESTED = [
  "nvidia/nemotron-3-ultra-550b-a55b",
  "google/gemma-4-31b-it",
  "nvidia/nemotron-3.5-lightning-30b-a3b",
  "openai/gpt-oss-20b",
  "google/gemma-3-12b-it",
  "z-ai/glm-5.3-flash",
  "bigcode/starcoder2-15b",
];

const HINTS = {
  "nvidia/nemotron-3-ultra-550b-a55b": "Paper Desk default",
};

const modelList = document.getElementById("modelList");
const modelSearch = document.getElementById("modelSearch");
const modelCount = document.getElementById("modelCount");
const selectedCount = document.getElementById("selectedCount");
const resultList = document.getElementById("resultList");
const resultSearch = document.getElementById("resultSearch");
const sortSelect = document.getElementById("sort");
const runBtn = document.getElementById("runBtn");
const runState = document.getElementById("runState");
const progress = document.getElementById("progress");
const progressText = document.getElementById("progressText");
const progressBar = document.getElementById("progressBar");
const meters = document.getElementById("meters");
const keyState = document.getElementById("keyState");
const keyBox = document.getElementById("keyBox");
const apiKey = document.getElementById("apiKey");
const promptBox = document.getElementById("prompt");
const configDialog = document.getElementById("configDialog");
const configCode = document.getElementById("configCode");
const configNote = document.getElementById("configNote");

let models = [];
let filter = "all";
let selected = new Set();
let results = [];
let previous = loadHistory();
let running = false;
let maxTokens = 32;
let serverHasKey = false;

function loadHistory() {
  try {
    const saved = JSON.parse(localStorage.getItem("nvbench-last") || "null");
    return saved && typeof saved === "object" ? saved : {};
  } catch {
    return {};
  }
}

function saveHistory(rows) {
  const next = {};
  rows.forEach((row) => {
    if (row.ttft_ms != null) next[row.model] = { ttft_ms: row.ttft_ms, tps: row.tps, at: Date.now() };
  });
  localStorage.setItem("nvbench-last", JSON.stringify(next));
  previous = next;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function visibleModels() {
  const query = modelSearch.value.trim().toLowerCase();
  return models.filter((model) => {
    if (filter !== "all" && model.category !== filter) return false;
    if (!query) return true;
    return model.id.toLowerCase().includes(query) || model.name.toLowerCase().includes(query);
  });
}

function renderModels() {
  const rows = visibleModels();
  modelCount.textContent = `${models.length} live`;
  selectedCount.textContent = `${selected.size} selected`;
  if (!rows.length) {
    modelList.innerHTML = `<p class="empty">No models match.</p>`;
    return;
  }
  modelList.innerHTML = rows.map((model) => {
    const hint = HINTS[model.id] ? `<span class="hint">${escapeHtml(HINTS[model.id])}</span>` : "";
    return `<label class="model">
      <input type="checkbox" value="${escapeHtml(model.id)}" ${selected.has(model.id) ? "checked" : ""}>
      <span>
        <b>${escapeHtml(model.name)}</b>
        <small>${escapeHtml(model.id)} · ${escapeHtml(model.category)}</small>
        ${hint}
      </span>
    </label>`;
  }).join("");
}

function pickSuggested() {
  selected = new Set(SUGGESTED.filter((id) => models.some((model) => model.id === id)).slice(0, 5));
  if (!selected.size) {
    models.filter((model) => model.category !== "other").slice(0, 5).forEach((model) => selected.add(model.id));
  }
}

function metric(value, suffix) {
  if (value == null) return "—";
  return `${value}${suffix || ""}`;
}

function deltaText(modelId, ttft) {
  const prior = previous[modelId];
  if (!prior || prior.ttft_ms == null || ttft == null) return "";
  const diff = Math.round(ttft - prior.ttft_ms);
  if (Math.abs(diff) < 30) return `<span class="delta">same as last run</span>`;
  const word = diff < 0 ? "faster" : "slower";
  return `<span class="delta">${Math.abs(diff)} ms ${word} than last run</span>`;
}

function ranked() {
  const query = resultSearch.value.trim().toLowerCase();
  const sort = sortSelect.value;
  const rows = results.filter((row) => !query || row.model.toLowerCase().includes(query));
  const score = (row) => {
    if (!row.ok) return Number.POSITIVE_INFINITY;
    if (sort === "tps") return row.tps == null ? Number.POSITIVE_INFINITY : -row.tps;
    if (sort === "total") return row.total_ms == null ? Number.POSITIVE_INFINITY : row.total_ms;
    if (sort === "name") return row.model;
    return row.ttft_ms == null ? Number.POSITIVE_INFINITY : row.ttft_ms;
  };
  return rows.sort((a, b) => {
    const left = score(a);
    const right = score(b);
    if (typeof left === "string") return left.localeCompare(right);
    return left - right;
  });
}

function renderResults() {
  const rows = ranked();
  const ok = results.filter((row) => row.ok && row.ttft_ms != null);
  const fastest = ok.reduce((best, row) => (row.ttft_ms < best.ttft_ms ? row : best), ok[0]);
  const tpsRows = results.filter((row) => row.ok && row.tps != null);
  const richest = tpsRows.reduce((best, row) => (row.tps > best.tps ? row : best), tpsRows[0]);
  const failed = results.filter((row) => !row.ok).length;
  if (results.length) {
    meters.hidden = false;
    document.getElementById("bestTtft").textContent = fastest ? `${Math.round(fastest.ttft_ms)} ms` : "—";
    document.getElementById("bestTtftName").textContent = fastest ? fastest.model.split("/").pop() : "";
    document.getElementById("bestTps").textContent = richest ? `${richest.tps}` : "—";
    document.getElementById("bestTpsName").textContent = richest ? richest.model.split("/").pop() : "";
    document.getElementById("doneCount").textContent = String(results.length);
    document.getElementById("failCount").textContent = failed ? `${failed} failed` : "all answered";
  }
  if (!rows.length) {
    resultList.innerHTML = `<p class="empty">${results.length ? "No results match." : "Pick a few models and run a sample. The time to the first token is the queue plus the model."}</p>`;
    return;
  }
  const bestTtft = fastest ? fastest.ttft_ms : null;
  let place = 0;
  resultList.innerHTML = rows.map((row) => {
    const name = row.model.split("/").pop();
    const width = bestTtft && row.ttft_ms ? Math.max(8, Math.min(100, (bestTtft / row.ttft_ms) * 100)) : 0;
    const tokenNote = row.tokens == null ? "" : `${row.tokens} token${row.tokens === 1 ? "" : "s"}${row.tokens_estimated ? " est." : ""}`;
    const status = row.ok ? "Answered" : (row.error || "Failed");
    const rank = row.ok ? ++place : "";
    return `<article class="result${row.ok ? "" : " fail"}">
      <header>
        <span class="rank">${rank}</span>
        <div>
          <b>${escapeHtml(name)}</b>
          <div class="id">${escapeHtml(row.model)}</div>
          ${deltaText(row.model, row.ttft_ms)}
        </div>
        <div class="metric${row.ttft_ms == null ? " bad" : ""}"><b>${metric(row.ttft_ms, " ms")}</b><span>first token</span></div>
        <div class="metric"><b>${metric(row.tps)}</b><span>tokens/s</span></div>
        <div class="metric"><b>${metric(row.total_ms, " ms")}</b><span>${escapeHtml(tokenNote || status)}</span></div>
      </header>
      ${row.ok && width ? `<div class="bar"><span style="width:${width}%"></span></div>` : ""}
      ${row.snippet ? `<p class="snippet">${escapeHtml(row.snippet)}</p>` : ""}
      ${!row.ok ? `<p class="snippet">${escapeHtml(status)}</p>` : ""}
    </article>`;
  }).join("");
}

async function loadModels() {
  const response = await fetch("/api/models", { cache: "no-store" });
  const data = await response.json();
  models = data.models || [];
  pickSuggested();
  renderModels();
}

async function loadStatus() {
  const response = await fetch("/api/status", { cache: "no-store" });
  const data = await response.json();
  serverHasKey = Boolean(data.key);
  const saved = localStorage.getItem("nvidia_api_key") || "";
  if (serverHasKey) {
    keyState.textContent = "Using the key on scratch-pc.";
    return;
  }
  keyBox.open = true;
  if (saved) apiKey.value = saved;
  keyState.textContent = saved
    ? "Using the key saved in this browser."
    : "Add an NVIDIA key. It stays in this browser.";
}

modelList.addEventListener("change", (event) => {
  const box = event.target;
  if (box.type !== "checkbox") return;
  if (box.checked) selected.add(box.value);
  else selected.delete(box.value);
  selectedCount.textContent = `${selected.size} selected`;
});

modelSearch.addEventListener("input", renderModels);
resultSearch.addEventListener("input", renderResults);
sortSelect.addEventListener("change", renderResults);

document.getElementById("chips").addEventListener("click", (event) => {
  const chip = event.target.closest(".chip");
  if (!chip) return;
  filter = chip.dataset.filter;
  document.querySelectorAll("#chips .chip").forEach((item) => item.classList.toggle("on", item === chip));
  renderModels();
});

document.getElementById("presets").addEventListener("click", (event) => {
  const chip = event.target.closest(".chip");
  if (!chip) return;
  promptBox.value = chip.dataset.prompt;
  maxTokens = Number(chip.dataset.tokens) || 48;
  document.querySelectorAll("#presets .chip").forEach((item) => item.classList.toggle("on", item === chip));
});

promptBox.addEventListener("input", () => {
  document.querySelectorAll("#presets .chip").forEach((item) => item.classList.remove("on"));
  maxTokens = promptBox.value.length > 180 ? 80 : 48;
});

document.getElementById("selectVisible").addEventListener("click", () => {
  visibleModels().forEach((model) => selected.add(model.id));
  renderModels();
});
document.getElementById("selectRecommended").addEventListener("click", () => {
  pickSuggested();
  renderModels();
});
document.getElementById("selectNone").addEventListener("click", () => {
  selected.clear();
  renderModels();
});

document.addEventListener("keydown", (event) => {
  const typing = event.target && (event.target.tagName === "INPUT" || event.target.tagName === "TEXTAREA");
  if (event.key === "/" && !typing) {
    event.preventDefault();
    modelSearch.focus();
  }
});

runBtn.addEventListener("click", async () => {
  if (running) return;
  const chosen = Array.from(selected);
  if (!chosen.length) {
    runState.textContent = "Select at least one model.";
    return;
  }
  if (!serverHasKey && !apiKey.value.trim()) {
    keyBox.open = true;
    apiKey.focus();
    runState.textContent = "Add an API key for this run.";
    return;
  }
  if (!serverHasKey && apiKey.value.trim()) localStorage.setItem("nvidia_api_key", apiKey.value.trim());
  running = true;
  runBtn.disabled = true;
  results = [];
  renderResults();
  meters.hidden = true;
  progress.hidden = false;
  progressBar.style.width = "4%";
  progressText.textContent = `Testing ${chosen.length}`;
  runState.textContent = "Running";
  try {
    const response = await fetch("/api/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        models: chosen,
        prompt: promptBox.value.trim(),
        maxTokens,
        apiKey: apiKey.value.trim(),
      }),
    });
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      parts.forEach((part) => {
        const line = part.split("\n").find((item) => item.startsWith("data:"));
        if (!line) return;
        const event = JSON.parse(line.slice(5).trim());
        if (event.type === "result" && event.result) {
          results.push(event.result);
          progressText.textContent = `${event.done} of ${event.total} back`;
          progressBar.style.width = `${Math.round((event.done / event.total) * 100)}%`;
          renderResults();
        }
        if (event.type === "end") runState.textContent = `Finished ${event.timestamp || ""}`.trim();
      });
    }
    saveHistory(results);
    renderResults();
  } catch (error) {
    runState.textContent = "The sample did not finish.";
    resultList.insertAdjacentHTML("afterbegin", `<p class="empty">${escapeHtml(error.message || "Request failed")}</p>`);
  } finally {
    running = false;
    runBtn.disabled = false;
    progress.hidden = true;
  }
});

document.getElementById("configBtn").addEventListener("click", async () => {
  const chosen = Array.from(selected);
  configCode.textContent = "Building…";
  configDialog.showModal();
  const response = await fetch("/api/opencode-config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ models: chosen }),
  });
  const data = await response.json();
  configNote.textContent = data.note || configNote.textContent;
  configCode.textContent = JSON.stringify(data.config, null, 2);
});

document.getElementById("copyConfig").addEventListener("click", async () => {
  await navigator.clipboard.writeText(configCode.textContent);
  document.getElementById("copyConfig").textContent = "Copied";
  setTimeout(() => {
    document.getElementById("copyConfig").textContent = "Copy";
  }, 1200);
});

apiKey.addEventListener("change", () => {
  const value = apiKey.value.trim();
  if (!serverHasKey && value) localStorage.setItem("nvidia_api_key", value);
});

loadStatus().catch(() => {
  keyState.textContent = "Could not read the key status.";
});
loadModels().catch(() => {
  modelCount.textContent = "Catalog failed";
  modelList.innerHTML = `<p class="empty">The model list did not load.</p>`;
});
