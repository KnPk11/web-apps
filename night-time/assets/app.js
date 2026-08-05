const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

const state = {
  data: null,
  timerId: null,
  timerEnds: null,
  currentVideo: null,
};

const thumbUrl = (id) => `https://i.ytimg.com/vi/${id}/hqdefault.jpg`;
const embedUrl = (id) =>
  `https://www.youtube-nocookie.com/embed/${id}?rel=0&modestbranding=1&playsinline=1`;

async function loadData() {
  const res = await fetch("data/curated.json", { cache: "no-cache" });
  if (!res.ok) throw new Error("Could not load curated.json");
  return res.json();
}

function renderFavourites(list) {
  const root = $("#favourites");
  root.innerHTML = "";
  for (const v of list) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fav-card";
    btn.innerHTML = `
      <div class="thumb" style="background-image:url('${thumbUrl(v.id)}')"></div>
      <div class="meta">
        <strong>${escapeHtml(v.title)}</strong>
        <div class="sub">${escapeHtml(v.note || v.channel || "")}</div>
      </div>`;
    btn.addEventListener("click", () => openPlayer(v));
    root.appendChild(btn);
  }
}

function renderCategories(cats) {
  const root = $("#categories");
  root.innerHTML = "";
  for (const c of cats) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cat-card";
    btn.innerHTML = `
      <span class="emoji" aria-hidden="true">${c.emoji || ""}</span>
      <strong>${escapeHtml(c.name)}</strong>
      <span>${escapeHtml(c.blurb || "")}</span>`;
    btn.addEventListener("click", () => showCategory(c));
    root.appendChild(btn);
  }
}

function showCategory(cat) {
  $("#library").classList.remove("hidden");
  document.querySelector(".hero")?.classList.add("hidden");
  $("#categories").parentElement.classList.add("hidden");
  $("#lib-title").textContent = cat.name;
  $("#lib-blurb").textContent = cat.blurb || "";
  const grid = $("#video-grid");
  grid.innerHTML = "";
  for (const v of cat.videos || []) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "vid-card";
    btn.innerHTML = `
      <div class="thumb" style="background-image:url('${thumbUrl(v.id)}')"></div>
      <div class="meta">
        <strong>${escapeHtml(v.title)}</strong>
        <div class="sub">${escapeHtml(v.channel || "YouTube")}</div>
      </div>`;
    btn.addEventListener("click", () => openPlayer(v));
    grid.appendChild(btn);
  }
  $("#library").scrollIntoView({ behavior: "smooth", block: "start" });
}

function showHome() {
  $("#library").classList.add("hidden");
  document.querySelector(".hero")?.classList.remove("hidden");
  $("#categories").parentElement.classList.remove("hidden");
}

function openPlayer(video) {
  state.currentVideo = video;
  const overlay = $("#player-overlay");
  const frame = $("#player-frame");
  $("#player-title").textContent = video.title || "Playing";
  frame.innerHTML = "";
  const iframe = document.createElement("iframe");
  iframe.src = embedUrl(video.id);
  iframe.title = video.title || "YouTube video";
  iframe.allow =
    "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share";
  iframe.allowFullscreen = true;
  iframe.referrerPolicy = "strict-origin-when-cross-origin";
  frame.appendChild(iframe);
  overlay.classList.remove("hidden");
  $("#btn-close").focus();
}

function closePlayer() {
  const overlay = $("#player-overlay");
  $("#player-frame").innerHTML = "";
  overlay.classList.add("hidden");
  state.currentVideo = null;
}

function pauseEmbedBestEffort() {
  // YouTube iframe API not loaded; removing iframe stops playback.
  $("#player-frame").innerHTML = "";
}

function allVideos() {
  const d = state.data;
  if (!d) return [];
  const out = [...(d.favourites || [])];
  for (const c of d.categories || []) {
    for (const v of c.videos || []) out.push(v);
  }
  return out;
}

function surprise() {
  const list = allVideos();
  if (!list.length) return;
  const v = list[Math.floor(Math.random() * list.length)];
  openPlayer(v);
}

function setTimer(minutes) {
  clearTimer(false);
  const ms = minutes * 60 * 1000;
  state.timerEnds = Date.now() + ms;
  const status = $("#timer-status");
  status.hidden = false;
  $("#btn-timer-cancel").classList.remove("hidden");

  const tick = () => {
    const left = state.timerEnds - Date.now();
    if (left <= 0) {
      clearTimer(false);
      pauseEmbedBestEffort();
      closePlayer();
      $("#rest-overlay").classList.remove("hidden");
      return;
    }
    const m = Math.floor(left / 60000);
    const s = Math.floor((left % 60000) / 1000);
    status.textContent = `Rest in ${m}:${String(s).padStart(2, "0")}`;
  };
  tick();
  state.timerId = setInterval(tick, 1000);
}

function clearTimer(updateUi = true) {
  if (state.timerId) clearInterval(state.timerId);
  state.timerId = null;
  state.timerEnds = null;
  if (updateUi) {
    const status = $("#timer-status");
    status.hidden = true;
    status.textContent = "";
    $("#btn-timer-cancel").classList.add("hidden");
  }
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function wireUi() {
  $("#btn-back").addEventListener("click", showHome);
  $("#btn-surprise").addEventListener("click", surprise);
  $("#btn-close").addEventListener("click", closePlayer);
  $("#player-overlay").addEventListener("click", (e) => {
    if (e.target.id === "player-overlay") closePlayer();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("#rest-overlay").classList.contains("hidden")) {
        $("#rest-overlay").classList.add("hidden");
      } else {
        closePlayer();
      }
    }
  });

  const warmBtn = $("#btn-lowblue");
  const saved = localStorage.getItem("night-time-warm") === "1";
  if (saved) {
    document.body.classList.add("warm");
    warmBtn.setAttribute("aria-pressed", "true");
  }
  warmBtn.addEventListener("click", () => {
    const on = document.body.classList.toggle("warm");
    warmBtn.setAttribute("aria-pressed", on ? "true" : "false");
    localStorage.setItem("night-time-warm", on ? "1" : "0");
  });

  $$(".timer-btns [data-min]").forEach((btn) => {
    btn.addEventListener("click", () => setTimer(Number(btn.dataset.min)));
  });
  $("#btn-timer-cancel").addEventListener("click", () => clearTimer(true));
  $("#btn-rest-dismiss").addEventListener("click", () => {
    $("#rest-overlay").classList.add("hidden");
  });
}

async function main() {
  wireUi();
  try {
    state.data = await loadData();
  } catch (err) {
    $("#tagline").textContent = "Could not load library — check data/curated.json";
    console.error(err);
    return;
  }
  const site = state.data.site || {};
  if (site.tagline) $("#tagline").textContent = site.tagline;
  if (site.footer) $("#footer-note").textContent = site.footer;
  if (site.title) document.title = site.title;
  renderFavourites(state.data.favourites || []);
  renderCategories(state.data.categories || []);
}

main();
