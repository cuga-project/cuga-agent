// Event-Driven CUGA — white-paper UI.
// Single column. TOC index page + numbered sections. Full delegation.

let GRAPH = null;

// Section order = the order in the TOC and the prev/next pager
const TOC_ORDER = [
  // 1. Overview
  { group: "Overview & narrative", ids: ["readme", "deck"] },
  // 2. Architecture & primitives
  { group: "Architecture & primitives", ids: ["blocks", "arch_full"] },
  // 3. Flows (setup + runtime)
  { group: "Flows (setup + runtime)", ids: ["setup_flow", "multi_flow"] },
  // 4. Reference
  { group: "Reference & design decisions", ids: ["reference", "decisions"] },
  // 5. Roadmap
  { group: "Roadmap & evolution", ids: ["roadmap", "from_loops"] },
  // 6. Kafka
  { group: "Production: Kafka", ids: ["kafka_doc", "kafka_arch"] },
  // 7. Flow animations
  { group: "Flow animations", ids: ["flow_push", "flow_timed", "flow_pull", "flow_swarm"] },
  // 8. Historical
  { group: "Historical reference", ids: ["proposal"] },
];

// Flat list of all section ids in order (for prev/next pager)
let FLAT_IDS = [];
let CHARTS_BY_GROUP = {};

// ─── INIT ────────────────────────────────────────────────────────
async function init() {
  const r = await fetch("/api/graph");
  GRAPH = await r.json();
  FLAT_IDS = TOC_ORDER.flatMap(g => g.ids);
  showIndex();
  bindGlobal();

  // Allow deep-link via URL hash, e.g. #deck
  window.addEventListener("hashchange", routeFromHash);
  routeFromHash();
}

function findNode(id) { return GRAPH.nodes.find(n => n.id === id); }
function nodeIndex(id) { return FLAT_IDS.indexOf(id); }

function routeFromHash() {
  const h = window.location.hash.slice(1);
  if (!h) { showIndex(); return; }
  const node = findNode(h);
  if (node) showSection(node);
  else showIndex();
}

// ─── INDEX / COVER PAGE ─────────────────────────────────────────
function showIndex() {
  const v = document.getElementById("content");
  v.innerHTML = "";

  // Build sections
  let counter = 0;
  const sectionsHtml = TOC_ORDER.map(group => {
    const meta = inferGroupMeta(group);
    const entries = group.ids.map(id => {
      const n = findNode(id);
      if (!n) return "";
      counter++;
      return `
        <a class="toc-entry" data-id="${n.id}" href="#${n.id}">
          <span class="toc-num">${String(counter).padStart(2, "0")}</span>
          <span class="toc-title">
            ${escapeHTML(n.label)}
            <small>${escapeHTML(n.blurb || "")}</small>
          </span>
          <span class="toc-kind ${n.kind}">${n.kind}</span>
        </a>
      `;
    }).join("");
    return `
      <div class="toc-section">
        <div class="section-title">
          <span class="cat-dot" style="background:${meta.color}"></span>
          ${escapeHTML(group.group)}
        </div>
        ${entries}
      </div>
    `;
  }).join("");

  v.innerHTML = `
    <div class="cover">
      <div class="eyebrow">Design Package · 2026</div>
      <h1>Event-Driven CUGA</h1>
      <div class="subtitle">From request/response to a unifying event primitive: trigger × agent × emit, with one envelope, one inbox, and one routing model.</div>
      <div class="meta">
        <span><b>Author:</b> Anupama Murthi</span>
        <span><b>Status:</b> Design</span>
        <span><b>${FLAT_IDS.length}</b> sections</span>
      </div>
    </div>

    <div class="abstract">
      <span class="label">Abstract</span>
      CUGA today is request → response. This paper proposes a single primitive — an
      Event envelope dropped into a per-agent inbox — that unifies cron triggers,
      gateway messages, webhooks, pollers, and agent-to-agent collaboration. A
      CUGA agent does the intelligent routing once at setup time; a dumb in-process
      dispatcher handles runtime delivery. Loops becomes the canonical timed-trigger
      producer with a single line of code change. The architecture ships in five
      shippable phases over ~3 months and can scale to multi-tenant production via
      a one-file swap to Kafka or Redis Streams.
    </div>

    <div class="toc">
      <h2>Table of contents</h2>
      <div class="toc-list">${sectionsHtml}</div>
    </div>
  `;
  window.scrollTo({ top: 0 });
}

function inferGroupMeta(group) {
  // First node's category drives the color
  const first = findNode(group.ids[0]);
  const cat = first ? GRAPH.categories[first.cat] : null;
  return cat || { color: "#888", label: group.group };
}

// ─── SECTION PAGE ───────────────────────────────────────────────
async function showSection(node) {
  const v = document.getElementById("content");
  const idx = nodeIndex(node.id);
  const prev = idx > 0 ? findNode(FLAT_IDS[idx - 1]) : null;
  const next = idx < FLAT_IDS.length - 1 ? findNode(FLAT_IDS[idx + 1]) : null;
  const cat = GRAPH.categories[node.cat] || {};

  // Utility bar
  const bar = `
    <div class="utility-bar">
      <div class="crumb">
        <a href="#" data-action="index">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M15 18l-6-6 6-6"/></svg>
          Index
        </a>
      </div>
      <div class="progress">Section ${idx + 1} of ${FLAT_IDS.length}</div>
    </div>
  `;

  const header = `
    <div class="section-header">
      <div class="label" style="color:${cat.color || 'var(--accent)'}">${escapeHTML(cat.label || node.cat)} · Section ${idx + 1}</div>
      <h1>${escapeHTML(node.label)}</h1>
      ${node.blurb ? `<p class="blurb">${escapeHTML(node.blurb)}</p>` : ""}
    </div>

    <div class="path-bar" data-path="${escapeHTML(node.abs_path || node.file)}">
      <span class="path-label">File on disk</span>
      <code class="path-value">${escapeHTML(node.abs_path || node.file)}</code>
      <button class="btn path-copy" title="Copy path">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
        Copy
      </button>
      <a class="btn" href="/asset/${node.file}" download>
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        Download
      </a>
    </div>
  `;

  // Body
  let body = "";
  if (node.kind === "md") {
    const res = await fetch(`/api/doc/${node.file}`);
    const md = await res.text();
    body = `<div class="md">${marked.parse(rewriteRelative(md))}</div>`;
  } else if (node.kind === "gif" && Array.isArray(node.frames) && node.frames.length > 0) {
    body = `
      <div class="image-view flow-player">
        ${node.blurb ? `<p class="caption">${escapeHTML(node.blurb)}</p>` : ""}
        ${renderFlowPlayer(node)}
      </div>
    `;
  } else {
    body = `
      <div class="image-view">
        ${node.blurb ? `<p class="caption">${escapeHTML(node.blurb)}</p>` : ""}
        ${renderZoomImage("/asset/" + node.file, node.label, node.file)}
      </div>
    `;
  }

  // Pager
  const pager = `
    <div class="nav-pager">
      ${prev ? `
        <a class="nav-link prev" data-id="${prev.id}" href="#${prev.id}">
          <div class="nav-direction">← Previous</div>
          <div class="nav-title">${escapeHTML(prev.label)}</div>
        </a>` : `<div class="nav-link empty"></div>`}
      ${next ? `
        <a class="nav-link next" data-id="${next.id}" href="#${next.id}">
          <div class="nav-direction">Next →</div>
          <div class="nav-title">${escapeHTML(next.label)}</div>
        </a>` : `<div class="nav-link empty"></div>`}
    </div>
  `;

  v.innerHTML = bar + header + body + pager;

  // initialize the flow player if present
  v.querySelectorAll(".flow-block").forEach(initFlowPlayer);
  window.scrollTo({ top: 0 });
}

// ─── SIMPLE IMAGE VIEW ──────────────────────────────────────────
// No custom zoom. The browser already has the best image viewer in the world —
// just give the user a button to open the asset in a new Chrome tab.
function renderZoomImage(url, label, file) {
  return `
    <figure class="image-block">
      <div class="image-toolbar">
        <a class="btn open-btn" href="${url}" target="_blank" rel="noopener" title="Open in Chrome (native zoom)">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
            <polyline points="15 3 21 3 21 9"/>
            <line x1="10" y1="14" x2="21" y2="3"/>
          </svg>
          Open in Chrome
        </a>
        <a class="btn" href="${url}" download="${file.split('/').pop()}" title="Download original">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Download
        </a>
      </div>
      <a class="image-link" href="${url}" target="_blank" rel="noopener" title="Click to open at native size in Chrome">
        <img class="static-img" src="${url}" alt="${escapeHTML(label)}" loading="lazy">
      </a>
      <figcaption>Click the image, or "Open in Chrome", for Chrome's native zoom/pan controls.</figcaption>
    </figure>
  `;
}

// Kept for compatibility with the flow player which still calls initZoomImage.
function initZoomImage(stage) { /* no-op — images now use native browser viewer */ }

// ─── FLOW PLAYER (PNG sequence — controllable GIF replacement) ──
function renderFlowPlayer(node) {
  const frames = node.frames;
  return `
    <figure class="image-block flow-block" data-frames='${JSON.stringify(frames)}'>
      <div class="image-toolbar">
        <a class="btn open-btn" href="/asset/${node.file}" target="_blank" rel="noopener">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
          Open GIF in Chrome
        </a>
        <a class="btn frame-link" data-href-template="/asset/__FRAME__" href="/asset/${frames[0]}" target="_blank" rel="noopener" title="Open the current frame in Chrome">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
          Open current frame
        </a>
        <a class="btn" href="/asset/${node.file}" download="${node.file.split('/').pop()}" title="Download GIF">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Download
        </a>
      </div>
      <a class="image-link frame-image-link" href="/asset/${frames[0]}" target="_blank" rel="noopener">
        <img class="static-img flow-img" src="/asset/${frames[0]}" loading="lazy">
      </a>
      <div class="player-controls">
        <button class="play-btn" data-action="play" title="Play/Pause">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" class="icon-play"><path d="M6 4l14 8-14 8z"/></svg>
          <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" class="icon-pause" style="display:none"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>
        </button>
        <button class="frame-btn" data-action="prev" title="Previous frame">‹</button>
        <span class="frame-counter">1 / ${frames.length}</span>
        <button class="frame-btn" data-action="next" title="Next frame">›</button>
        <input type="range" class="scrubber" min="0" max="${frames.length - 1}" step="1" value="0">
        <span class="speed-control">
          Speed
          <select>
            <option value="1500">slow (1.5s)</option>
            <option value="3000" selected>normal (3s)</option>
            <option value="500">fast (0.5s)</option>
            <option value="200">very fast</option>
          </select>
        </span>
      </div>
      <figcaption>Paused on frame 1. Click ▶ or "Open current frame" to view it full-size in Chrome.</figcaption>
    </figure>
  `;
}

function initFlowPlayer(player) {
  if (player.__playerInited) return;
  player.__playerInited = true;

  const frames = JSON.parse(player.dataset.frames);
  const img = player.querySelector(".flow-img");
  const link = player.querySelector(".frame-image-link");
  const frameLink = player.querySelector(".frame-link");
  const playBtn = player.querySelector(".play-btn");
  const playIcon = playBtn.querySelector(".icon-play");
  const pauseIcon = playBtn.querySelector(".icon-pause");
  const counter = player.querySelector(".frame-counter");
  const scrubber = player.querySelector(".scrubber");
  const speedSelect = player.querySelector(".speed-control select");

  const state = { idx: 0, playing: false, timer: null, speed: 3000 };

  const showFrame = (i) => {
    state.idx = ((i % frames.length) + frames.length) % frames.length;
    const url = "/asset/" + frames[state.idx];
    img.src = url;
    if (link) link.href = url;
    if (frameLink) frameLink.href = url;
    counter.textContent = `${state.idx + 1} / ${frames.length}`;
    scrubber.value = state.idx;
  };

  const advance = () => {
    showFrame(state.idx + 1);
    if (state.playing) state.timer = setTimeout(advance, state.speed);
  };

  const play = () => {
    state.playing = true;
    playIcon.style.display = "none";
    pauseIcon.style.display = "";
    state.timer = setTimeout(advance, state.speed);
  };
  const pause = () => {
    state.playing = false;
    playIcon.style.display = "";
    pauseIcon.style.display = "none";
    clearTimeout(state.timer);
  };
  const togglePlay = () => state.playing ? pause() : play();

  player.querySelector(".player-controls").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    switch (btn.dataset.action) {
      case "play": togglePlay(); break;
      case "prev": pause(); showFrame(state.idx - 1); break;
      case "next": pause(); showFrame(state.idx + 1); break;
    }
  });

  scrubber.addEventListener("input", (e) => {
    pause();
    showFrame(parseInt(e.target.value, 10));
  });

  speedSelect.addEventListener("change", (e) => {
    state.speed = parseInt(e.target.value, 10);
    if (state.playing) { pause(); play(); }
  });

  // Start paused (so user can read each frame)
  showFrame(0);
}

// ─── GLOBAL EVENT DELEGATION ────────────────────────────────────
function bindGlobal() {
  document.body.addEventListener("click", (e) => {
    // Index/home link
    if (e.target.closest("[data-action='index']")) {
      e.preventDefault();
      window.location.hash = "";
      showIndex();
      return;
    }

    // Any data-id link (TOC entry, pager link)
    const idLink = e.target.closest("[data-id]");
    if (idLink) {
      e.preventDefault();
      const n = findNode(idLink.dataset.id);
      if (n) {
        window.location.hash = n.id;
        showSection(n);
      }
      return;
    }

    // Path copy button
    const copyBtn = e.target.closest(".path-copy");
    if (copyBtn) {
      e.preventDefault();
      const path = copyBtn.closest(".path-bar").dataset.path;
      navigator.clipboard.writeText(path).then(() => {
        copyBtn.classList.add("copied");
        const original = copyBtn.innerHTML;
        copyBtn.innerHTML = `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="20 6 9 17 4 12"/></svg> Copied`;
        setTimeout(() => {
          copyBtn.classList.remove("copied");
          copyBtn.innerHTML = original;
        }, 1500);
      });
      return;
    }

    // Cross-references inside rendered markdown
    const a = e.target.closest(".md a[href]");
    if (a) {
      const href = a.getAttribute("href");
      if (!href) return;
      if (/^(https?:|mailto:)/.test(href)) return;
      if (href.startsWith("#")) return; // anchor jump
      const file = href.replace(/^\.\//, "").split("#")[0];
      const target = GRAPH.nodes.find(n => n.file === file || n.file.endsWith("/" + file));
      if (target) {
        e.preventDefault();
        window.location.hash = target.id;
        showSection(target);
        return;
      }
      if (/\.(md|png|gif|svg)$/i.test(file)) {
        e.preventDefault();
        window.open(`/asset/${file}`, "_blank");
        return;
      }
    }

    // Inline image click → open original in new tab (Chrome's native viewer)
    const inlineImg = e.target.closest(".md img");
    if (inlineImg && !inlineImg.closest("a")) {
      e.preventDefault();
      window.open(inlineImg.src, "_blank", "noopener");
      return;
    }
  });
}

// ─── UTILS ──────────────────────────────────────────────────────
function rewriteRelative(md) {
  return md.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (m, alt, src) => {
    if (src.startsWith("http") || src.startsWith("/asset/")) return m;
    return `![${alt}](/asset/${src})`;
  });
}

function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

init();
