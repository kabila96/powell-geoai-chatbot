/* Powell Ndlovu consultancy assistant — frontend logic (no framework, no secrets). */
(() => {
  "use strict";

  const API_BASE = ((window.CHATBOT_CONFIG && window.CHATBOT_CONFIG.apiBase) || "").replace(/\/$/, "");
  const STORAGE_KEY = "pn-chat-history-v2";
  const MAX_HISTORY_SENT = 12; // messages (6 turns); the server enforces its own limit too
  const REQUEST_TIMEOUT_MS = 45000;

  const $ = (id) => document.getElementById(id);
  const els = {
    messages: $("messages"),
    welcome: $("welcome"),
    form: $("composer"),
    input: $("input"),
    send: $("sendBtn"),
    count: $("charCount"),
    starters: $("starters"),
    clear: $("clearBtn"),
    contact: $("contactLinks"),
    portfolios: $("portfolioLinks"),
    coords: $("coordReadout"),
    status: $("kbStatus"),
    statusText: $("kbStatusText"),
    jump: $("jumpBtn"),
    layers: $("layerList"),
  };

  const ICONS = {
    mark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.5 17 20l-5-3.6L7 20z" fill="#47b07c"/></svg>',
    copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>',
    check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5 10 17l9-10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  let maxChars = 1000;
  let history = loadHistory(); // [{role, content, sources?}]
  let busy = false;
  let msgCounter = 0;

  // ------------------------------------------------------------------ storage (per tab session)
  function loadHistory() {
    try {
      const parsed = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]");
      return Array.isArray(parsed) ? parsed.filter((m) => m && typeof m.content === "string") : [];
    } catch { return []; }
  }
  function saveHistory() {
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(history.slice(-40))); } catch { /* storage unavailable */ }
  }

  // ------------------------------------------------------------------ safe rendering
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // Minimal Markdown: paragraphs, headings, bullet/numbered lists, **bold**, *italic*, `code`,
  // [label](https://...) and bare links, and [S1] citation markers. Everything is escaped first.
  function renderMarkdown(text, msgId) {
    const links = [];
    const stash = (html) => `\u0000${links.push(html) - 1}\u0000`;

    const inline = (line) => {
      let h = escapeHtml(line);
      h = h.replace(/`([^`]+)`/g, (_, code) => stash(`<code>${code}</code>`));
      h = h.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) =>
        stash(`<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`));
      h = h.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?'"])/g, (_, pre, url) =>
        pre + stash(`<a href="${url}" target="_blank" rel="noopener noreferrer">${url.replace(/^https?:\/\//, "").replace(/\/$/, "")}</a>`));
      h = h.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
      h = h.replace(/(^|[^*\w])\*(?!\s)([^*]+?)\*(?!\*)/g, "$1<em>$2</em>");
      h = h.replace(/\[S(\d{1,2})\]/g, (_, n) =>
        `<a class="cite" href="#${msgId}-s${n}" aria-label="Source ${n}" data-src="${n}">${n}</a>`);
      return h.replace(/\u0000(\d+)\u0000/g, (_, i) => links[Number(i)]);
    };

    const out = [];
    let list = null;
    const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };

    for (const raw of String(text).split(/\n/)) {
      const line = raw.trim();
      const bullet = line.match(/^[-*•]\s+(.*)$/);
      const numbered = line.match(/^\d+[.)]\s+(.*)$/);
      if (bullet || numbered) {
        const type = bullet ? "ul" : "ol";
        if (list !== type) { closeList(); out.push(`<${type}>`); list = type; }
        out.push(`<li>${inline((bullet || numbered)[1])}</li>`);
        continue;
      }
      closeList();
      if (!line) continue;
      const heading = line.match(/^#{1,4}\s+(.*)$/);
      out.push(heading ? `<h4>${inline(heading[1])}</h4>` : `<p>${inline(line)}</p>`);
    }
    closeList();
    return out.join("");
  }

  function sourceLabel(s) {
    const section = s.section && !/^links on this page$/i.test(s.section) ? s.section : "";
    if (s.type === "document") return section ? `Consultancy profile: ${section.replace(/^\d+(\.\d+)*\.?\s+/, "")}` : "Consultancy profile";
    return section ? `${s.title}: ${section}` : s.title;
  }

  function renderSources(sources, msgId) {
    if (!sources || !sources.length) return "";
    const items = sources.map((s) => {
      const n = String(s.id).replace(/^S/, "");
      const label = escapeHtml(sourceLabel(s));
      const isWeb = /^https?:\/\//.test(s.url);
      const body = isWeb
        ? `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">${label}</a>`
        : `<span>${label}</span>`;
      return `<li id="${msgId}-s${n}"><span class="tag">${n}</span><span>${body}${isWeb ? '<span class="kind">website</span>' : ""}</span></li>`;
    }).join("");
    const noun = sources.length === 1 ? "source" : "sources";
    return `<details class="sources"><summary>${sources.length} ${noun}</summary><ol>${items}</ol></details>`;
  }

  function avatar() {
    const a = document.createElement("div");
    a.className = "avatar";
    a.innerHTML = ICONS.mark;
    return a;
  }

  function addMessage(role, content, { sources = [], error = false, retryText = null, scroll = "bottom" } = {}) {
    const msgId = `m${++msgCounter}`;
    const wrap = document.createElement("div");
    wrap.className = `msg ${role === "user" ? "user" : "bot"}${error ? " error" : ""}`;
    const body = document.createElement("div");
    body.className = "msg-body";
    const bubble = document.createElement("div");
    bubble.className = "bubble";

    if (role === "user") {
      bubble.textContent = content;
      body.appendChild(bubble);
    } else {
      wrap.appendChild(avatar());
      bubble.innerHTML = renderMarkdown(content, msgId);
      body.appendChild(bubble);

      if (!error) {
        const tools = document.createElement("div");
        tools.className = "msg-tools";
        const copy = document.createElement("button");
        copy.type = "button";
        copy.className = "tool";
        copy.innerHTML = `${ICONS.copy}<span>Copy answer</span>`;
        copy.addEventListener("click", () => copyAnswer(copy, content));
        tools.appendChild(copy);
        body.appendChild(tools);
        if (sources.length) {
          body.insertAdjacentHTML("beforeend", renderSources(sources, msgId));
        }
      }
      if (retryText) {
        const btn = document.createElement("button");
        btn.className = "btn btn-quiet retry";
        btn.type = "button";
        btn.textContent = "Try again";
        btn.addEventListener("click", () => { wrap.remove(); ask(retryText, { retry: true }); });
        body.appendChild(btn);
      }
    }
    wrap.appendChild(body);
    els.messages.appendChild(wrap);

    if (scroll === "start") scrollMessagesTo(wrap);
    else if (scroll === "bottom") scrollToBottom();
    return wrap;
  }

  async function copyAnswer(btn, content) {
    const plain = content.replace(/\s?\[S\d{1,2}\]/g, "").trim();
    try {
      await navigator.clipboard.writeText(plain);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = plain; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch { /* ignore */ }
      ta.remove();
    }
    btn.innerHTML = `${ICONS.check}<span>Copied</span>`;
    setTimeout(() => { btn.innerHTML = `${ICONS.copy}<span>Copy answer</span>`; }, 1800);
  }

  // Open the sources list when a citation marker is clicked, then jump to the entry.
  els.messages.addEventListener("click", (e) => {
    const cite = e.target.closest(".cite");
    if (!cite) return;
    const target = document.getElementById(cite.getAttribute("href").slice(1));
    if (!target) return;
    e.preventDefault();
    const details = target.closest("details");
    if (details) details.open = true;
    scrollMessagesTo(target, true);
    target.classList.add("flash");
    setTimeout(() => target.classList.remove("flash"), 1600);
  });

  function showTyping() {
    const wrap = document.createElement("div");
    wrap.className = "msg bot typing";
    wrap.setAttribute("aria-label", "The assistant is searching Powell's material");
    wrap.appendChild(avatar());
    const body = document.createElement("div");
    body.className = "msg-body";
    body.innerHTML = '<div class="bubble"><span></span><span></span><span></span><em>Searching Powell\'s profile and portfolios…</em></div>';
    wrap.appendChild(body);
    els.messages.appendChild(wrap);
    scrollToBottom();
    return wrap;
  }

  // ------------------------------------------------------------------ scrolling
  const smoothBehaviour = () =>
    window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  const nearBottom = () =>
    els.messages.scrollHeight - els.messages.scrollTop - els.messages.clientHeight < 80;
  // Scroll only the message list (scrollIntoView would also scroll the page on mobile).
  function scrollMessagesTo(el, onlyIfHidden = false) {
    const box = els.messages.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    if (onlyIfHidden && r.top >= box.top && r.bottom <= box.bottom) return;
    const top = els.messages.scrollTop + (r.top - box.top) - 12;
    els.messages.scrollTo({ top, behavior: smoothBehaviour() });
  }
  function scrollToBottom() {
    els.messages.scrollTo({ top: els.messages.scrollHeight, behavior: smoothBehaviour() });
  }
  els.messages.addEventListener("scroll", () => { els.jump.hidden = nearBottom(); }, { passive: true });
  els.jump.addEventListener("click", () => { scrollToBottom(); els.input.focus(); });

  // ------------------------------------------------------------------ welcome state
  function setConversationStarted(started) {
    els.welcome.classList.toggle("compact", started);
    els.clear.hidden = !started;
  }

  // ------------------------------------------------------------------ API
  async function request(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const res = await fetch(`${API_BASE}${path}`, { ...options, signal: controller.signal });
      let data = {};
      try { data = await res.json(); } catch { /* non-JSON error page */ }
      if (!res.ok) {
        let message = data.error || `The assistant returned an error (${res.status}). Please try again.`;
        if (res.status === 429 && data.retry_after) {
          message += ` You can send another question in about ${Math.max(1, Math.ceil(data.retry_after))} seconds.`;
        }
        const err = new Error(message);
        err.status = res.status;
        throw err;
      }
      return data;
    } catch (e) {
      if (e.name === "AbortError") throw new Error("The assistant took too long to answer. Please try again.");
      if (e instanceof TypeError) throw new Error("The assistant can't be reached. Check your connection, then try again.");
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }

  async function ask(text, { retry = false } = {}) {
    const question = (text || "").trim();
    if (!question || busy) return;
    if (question.length > maxChars) {
      addMessage("bot", `Questions can be up to ${maxChars} characters. Please shorten yours and send it again.`, { error: true });
      return;
    }

    setBusy(true);
    setConversationStarted(true);
    // History sent to the server never includes the question being asked; the server adds it.
    const historyForServer = history.slice(-MAX_HISTORY_SENT).map(({ role, content }) => ({ role, content }));
    const userEl = retry ? null : addMessage("user", question);
    const typing = showTyping();

    try {
      const data = await request("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question, history: historyForServer }),
      });
      typing.remove();
      // Long answers: show the start of the answer rather than its last line.
      addMessage("bot", data.answer, { sources: data.sources || [], scroll: "start" });
      history.push({ role: "user", content: question });
      history.push({ role: "assistant", content: data.answer, sources: data.sources || [] });
      saveHistory();
    } catch (err) {
      typing.remove();
      addMessage("bot", err.message || "Something went wrong. Please try again.", { error: true, retryText: question });
    } finally {
      setBusy(false);
      if (userEl === null || window.matchMedia("(hover: hover)").matches) els.input.focus({ preventScroll: true });
    }
  }

  function setBusy(state) {
    busy = state;
    els.send.disabled = state;
    els.input.setAttribute("aria-busy", String(state));
    els.layers.querySelectorAll(".layer").forEach((b) => { b.disabled = state; });
    els.starters.querySelectorAll(".starter").forEach((b) => { b.disabled = state; });
  }

  // ------------------------------------------------------------------ public config + health
  async function loadConfig() {
    try {
      const cfg = await request("/api/config");
      maxChars = cfg.max_message_chars || maxChars;
      els.input.maxLength = maxChars;
      updateCount();
      renderContact(cfg.contact || {});
      if (Array.isArray(cfg.portfolios) && cfg.portfolios.some((p) => p.url)) {
        els.portfolios.innerHTML = cfg.portfolios
          .filter((p) => /^https?:\/\//.test(p.url))
          .map((p) => `<li><a href="${escapeHtml(p.url)}" target="_blank" rel="noopener">${escapeHtml(p.label)}</a></li>`)
          .join("");
      }
    } catch {
      els.contact.innerHTML = '<li><span class="muted small">Contact details are listed on the portfolio websites.</span></li>';
    }
  }

  async function loadHealth() {
    try {
      const h = await request("/api/health");
      if (h.knowledge_base_ready) {
        const built = h.knowledge_base && h.knowledge_base.built_at ? new Date(h.knowledge_base.built_at) : null;
        const when = built && !isNaN(built)
          ? built.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })
          : "";
        setStatus("ready", "Online", when ? `knowledge base updated ${when}` : "");
      } else {
        setStatus("down", "The knowledge base isn't built yet, so answers may be unavailable");
      }
    } catch {
      setStatus("down", "The assistant is offline right now. You can still contact Powell directly");
    }
  }

  function setStatus(state, text, detail = "") {
    els.status.dataset.state = state;
    els.statusText.textContent = text;
    if (detail) {
      const d = document.createElement("span");
      d.className = "status-detail";
      d.textContent = `, ${detail}`;
      els.statusText.appendChild(d);
    }
  }

  function splitList(v) { return String(v || "").split(",").map((x) => x.trim()).filter(Boolean); }

  function renderContact(c) {
    const isPlaceholder = (v) => !v || /^\[.*\]$/.test(v);
    const groups = [];
    if (!isPlaceholder(c.email)) {
      const items = splitList(c.email).map((e) => `<a href="mailto:${escapeHtml(e)}">${escapeHtml(e)}</a>`);
      groups.push(`<li class="group"><span class="label">Email</span>${items.join("")}</li>`);
    }
    if (!isPlaceholder(c.phone)) {
      const items = splitList(c.phone).map((p) => `<a href="tel:${escapeHtml(p.replace(/[^\d+]/g, ""))}">${escapeHtml(p)}</a>`);
      groups.push(`<li class="group"><span class="label">Phone</span>${items.join("")}</li>`);
    }
    if (!isPlaceholder(c.linkedin) && /^https?:\/\//.test(c.linkedin)) {
      const handle = c.linkedin.split("/").filter(Boolean).pop();
      groups.push(`<li class="group"><span class="label">LinkedIn</span><a href="${escapeHtml(c.linkedin)}" target="_blank" rel="noopener">${escapeHtml(handle)}</a></li>`);
    }
    if (c.github && /^https?:\/\//.test(c.github)) {
      groups.push(`<li class="group"><span class="label">GitHub</span><a href="${escapeHtml(c.github)}" target="_blank" rel="noopener">${escapeHtml(c.github.replace(/^https?:\/\/(www\.)?/, ""))}</a></li>`);
    }
    els.contact.innerHTML = groups.length ? groups.join("") : '<li><span class="muted small">Contact details are listed on the portfolio websites.</span></li>';

    const firstEmail = !isPlaceholder(c.email) && splitList(c.email)[0];
    if (firstEmail) {
      const mailto = `mailto:${firstEmail}?subject=${encodeURIComponent("Consultation enquiry")}`;
      $("topContact").href = mailto;
      const btn = document.createElement("a");
      btn.className = "btn btn-primary";
      btn.href = mailto;
      btn.textContent = "Email Powell about a project";
      els.contact.after(btn);
    }
  }

  // ------------------------------------------------------------------ input UX
  function updateCount() {
    const n = els.input.value.length;
    els.count.textContent = `${n} / ${maxChars}`;
    els.count.classList.toggle("near", n > maxChars * 0.9);
  }
  function autoGrow() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 180) + "px";
  }

  els.form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (busy) return;
    const text = els.input.value;
    if (!text.trim()) return;
    els.input.value = "";
    updateCount(); autoGrow();
    ask(text);
  });
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      els.form.requestSubmit();
    }
  });
  els.input.addEventListener("input", () => { updateCount(); autoGrow(); });

  els.starters.addEventListener("click", (e) => {
    const s = e.target.closest(".starter");
    if (s) ask(s.dataset.q);
  });
  els.layers.addEventListener("click", (e) => {
    const layer = e.target.closest(".layer");
    if (!layer) return;
    if (window.matchMedia("(max-width: 860px)").matches) {
      document.querySelector(".chat").scrollIntoView({ behavior: smoothBehaviour() });
    }
    ask(layer.dataset.q);
  });

  els.clear.addEventListener("click", () => {
    if (busy) return;
    history = [];
    saveHistory();
    els.messages.querySelectorAll(".msg").forEach((m) => m.remove());
    setConversationStarted(false);
    els.messages.scrollTop = 0;
    els.input.focus();
  });

  // Decorative pointer "coordinates": treats the window as a world map.
  let raf = 0;
  if (els.coords && window.matchMedia("(hover: hover)").matches) {
    window.addEventListener("pointermove", (e) => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const lat = 90 - (e.clientY / window.innerHeight) * 180;
        const lon = (e.clientX / window.innerWidth) * 360 - 180;
        els.coords.textContent =
          `${Math.abs(lat).toFixed(4)}° ${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(4)}° ${lon >= 0 ? "E" : "W"}`;
      });
    }, { passive: true });
  }

  // ------------------------------------------------------------------ restore session
  function restore() {
    if (!history.length) return;
    setConversationStarted(true);
    for (const m of history) addMessage(m.role, m.content, { sources: m.sources || [], scroll: "none" });
    els.messages.scrollTop = els.messages.scrollHeight;
  }

  if (window.matchMedia("(max-width: 560px)").matches) {
    els.input.placeholder = "Ask about Powell's services";
  }
  restore();
  updateCount();
  loadConfig();
  loadHealth();
})();
