// Job Radar - integrations workstream + shared front-end infrastructure.
//
// Loaded as the first ordered classic <script> after app.js, so window.JobRadar
// (frozen bridge: version, speak, registerAlfredDispatcher) already exists. This
// module publishes window.JobRadarUI: the shared helpers, fetch wrapper, honest
// state renderer, accessible modal/layer system, and integration gating that the
// tracker / projectfiles / outreach / alfred modules consume.
//
// It also owns the Integrations panel and the unobtrusive HUD status dot.
//
// No mock data. Endpoints that do not exist yet resolve to honest "unavailable"
// or "error" states, classified by structured backend error codes.
(function () {
  "use strict";

  // ------------------------------------------------------------------ helpers

  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let uidSeq = 0;
  function uid(prefix) { uidSeq += 1; return (prefix || "jr") + "-" + uidSeq + "-" + Date.now().toString(36); }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  // Externally supplied links only. Absolute http(s) URL string, or "#" for
  // anything else. Parsed with NO base URL so relative/garbage can never be
  // coerced into a same-origin link. Mirrors frontend/app.js safeUrl().
  function safeUrl(raw) {
    if (typeof raw !== "string") return "#";
    const trimmed = raw.trim();
    if (!trimmed) return "#";
    let parsed;
    try { parsed = new URL(trimmed); } catch (e) { return "#"; }
    return (parsed.protocol === "http:" || parsed.protocol === "https:") ? parsed.href : "#";
  }

  // True only for a string whose scheme is exactly mailto: (case-insensitive).
  // Full recipient/query validation lives in outreach.js.
  function isMailtoUrl(raw) {
    return typeof raw === "string" && /^mailto:/i.test(raw.trim());
  }

  function fmtBytes(n) {
    const b = Number(n);
    if (!isFinite(b) || b < 0) return "—";
    if (b < 1024) return b + " B";
    const units = ["KB", "MB", "GB", "TB"];
    let v = b / 1024, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
    return (v < 10 ? v.toFixed(1) : Math.round(v)) + " " + units[i];
  }

  function timeAgo(iso) {
    if (!iso) return "";
    const t = new Date(iso).getTime();
    if (isNaN(t)) return "";
    const mins = Math.round((Date.now() - t) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return mins + "m ago";
    const hours = Math.round(mins / 60);
    if (hours < 24) return hours + "h ago";
    return Math.round(hours / 24) + "d ago";
  }

  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d.getTime()) ? "" : d.toLocaleDateString();
  }

  // Minimal DOM builder. Never assigns innerHTML; children are text nodes or
  // elements. Unknown props become attributes.
  function el(tag, props) {
    const node = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach((k) => {
        const v = props[k];
        if (v == null) return;
        if (k === "className") node.className = v;
        else if (k === "text" || k === "textContent") node.textContent = v;
        else if (k === "dataset") Object.keys(v).forEach((d) => { node.dataset[d] = v[d]; });
        else if (k === "onclick") node.addEventListener("click", v);
        else if (k in node) { try { node[k] = v; } catch (e) { node.setAttribute(k, v); } }
        else node.setAttribute(k, v);
      });
    }
    for (let i = 2; i < arguments.length; i++) {
      const kids = arguments[i];
      (Array.isArray(kids) ? kids : [kids]).forEach((c) => {
        if (c == null || c === false) return;
        node.append(c.nodeType ? c : document.createTextNode(String(c)));
      });
    }
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  // Raised when a canonical response is missing a required field. Callers turn
  // this into a controlled on-screen error rather than limping on with guesses.
  function ContractError(message) { this.name = "ContractError"; this.message = message; }
  ContractError.prototype = Object.create(Error.prototype);

  // ------------------------------------------------------- error-envelope parse

  function extractErrorCode(data) {
    if (!data || typeof data !== "object") return null;
    if (typeof data.code === "string") return data.code;                      // Agent A explicit
    const d = data.detail;
    if (d && typeof d === "object" && !Array.isArray(d) && typeof d.code === "string") return d.code; // Agent B nested
    return null;
  }

  function safeDetailString(data) {
    let s = null;
    if (data && typeof data === "object") {
      if (typeof data.detail === "string") s = data.detail;
      else if (data.detail && typeof data.detail === "object" && typeof data.detail.message === "string") s = data.detail.message;
      else if (typeof data.message === "string") s = data.message;
    }
    if (!s || typeof s !== "string") return null;
    if (s.length > 160) return null;
    if (/[<>]/.test(s)) return null;
    if (/Traceback|File "| line \d+|Exception/.test(s)) return null;
    return s;
  }

  function isFastapiValidation(data) {
    return !!(data && typeof data === "object" && Array.isArray(data.detail));
  }

  // ----------------------------------------------------------- fetch wrapper

  // kind: "collection" (a base list route), "record" (a specific /{id}),
  // or "action" (a mutation). Never throws. Returns one of:
  //   { ok, status, data }
  //   { aborted:true }
  //   { unavailable:true }              feature not in this build
  //   { notFound:true }                 a specific record is gone
  //   { forbidden:true }                401 / 403
  //   { validation:true, code, message }400/409/413/415/422 - actionable
  //   { error:true, retryable?, message }
  async function fetchJSON(url, opts) {
    opts = opts || {};
    const kind = opts.kind || "collection";
    const init = { method: opts.method || "GET", headers: Object.assign({}, opts.headers) };
    if (opts.signal) init.signal = opts.signal;
    if (opts.body !== undefined) {
      if (opts.body instanceof FormData) init.body = opts.body;
      else { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    }

    let res;
    try {
      res = await fetch(url, init);
    } catch (e) {
      if (e && e.name === "AbortError") return { aborted: true };
      return { error: true, retryable: true, message: "Network error — check your connection and retry." };
    }

    let data = null;
    const ct = res.headers.get("content-type") || "";
    try {
      if (ct.indexOf("application/json") !== -1) data = await res.json();
      else await res.text();
    } catch (e) { /* leave data null */ }

    if (res.ok) return { ok: true, status: res.status, data };

    const code = extractErrorCode(data);
    const detail = safeDetailString(data);
    // Starlette / FastAPI's default "no such route" body. On a base list route
    // that is the signature of a feature whose router carries no endpoints yet.
    const frameworkDetail = (data && typeof data === "object" && typeof data.detail === "string")
      ? data.detail.trim().toLowerCase() : null;
    const isFrameworkDefault404 = data == null || frameworkDetail === "not found";
    // A mutation to an /api/ path with no mounted route falls through to the
    // static-file mount, which 405s the method. That default 405 (and a bodyless
    // one) means "feature not in this build", same as a bodyless GET 404.
    const isFrameworkDefault405 = frameworkDetail === "method not allowed" || (data == null && !code);

    if (res.status === 404) {
      if (code === "feature_unavailable") return { unavailable: true, status: 404, code };
      if (kind === "collection") {
        if (code === "not_found") return { error: true, status: 404, code, message: detail || "That wasn't found." };
        if (isFrameworkDefault404) return { unavailable: true, status: 404 };
        return { error: true, status: 404, code, message: detail || "That wasn't found." };
      }
      return { notFound: true, status: 404, code, message: (isFrameworkDefault404 ? null : detail) };
    }
    if (res.status === 501) return { unavailable: true, status: 501, code };
    if (res.status === 405 && isFrameworkDefault405) return { unavailable: true, status: 405 };
    if (res.status === 401 || res.status === 403) return { forbidden: true, status: res.status, code };
    if (res.status === 422) {
      return { validation: true, status: 422, code: code || "invalid",
        message: isFastapiValidation(data) ? "Some fields weren't valid." : (detail || "That didn't pass validation.") };
    }
    if (res.status === 400 || res.status === 409 || res.status === 413 || res.status === 415) {
      return { validation: true, status: res.status, code, message: detail, data };
    }
    if (res.status >= 500) return { error: true, retryable: true, status: res.status, message: "Server error — try again." };
    return { error: true, status: res.status, code, message: detail || ("Request failed (" + res.status + ").") };
  }

  // ------------------------------------------------------------- state view

  const DEFAULT_STATE_MSG = {
    loading: "Loading…",
    empty: "Nothing here yet.",
    unavailable: "This feature isn't in this build yet.",
    forbidden: "You don't have access to this.",
    error: "Something went wrong.",
  };

  function renderState(container, opts) {
    opts = opts || {};
    const kind = opts.kind || "error";
    clear(container);
    const box = el("div", { className: "jr-state jr-state--" + kind });
    box.setAttribute("role", (kind === "error" || kind === "forbidden") ? "alert" : "status");
    if (kind === "loading" && !prefersReducedMotion) {
      box.appendChild(el("span", { className: "jr-spinner", "aria-hidden": "true" }));
    }
    box.appendChild(el("p", { className: "jr-state-msg", text: opts.message || DEFAULT_STATE_MSG[kind] || "" }));
    if (typeof opts.onRetry === "function" && (kind === "error")) {
      const btn = el("button", { className: "hud-btn", type: "button", text: "Retry" });
      btn.addEventListener("click", opts.onRetry);
      box.appendChild(btn);
    }
    container.appendChild(box);
  }

  // Map a fetchJSON failure envelope onto a renderState() call.
  function renderFetchFailure(container, res, onRetry) {
    if (res.unavailable) return renderState(container, { kind: "unavailable" });
    if (res.forbidden) return renderState(container, { kind: "forbidden" });
    if (res.notFound) return renderState(container, { kind: "error", message: res.message || "That record no longer exists." });
    return renderState(container, { kind: "error", message: res.message || "Couldn't load this.", onRetry: res.retryable ? onRetry : onRetry });
  }

  // --------------------------------------------------------- layer + a11y

  // Ordered stack of open dialog-like layers. Only the top layer is interactive;
  // everything else is inert. Escape / backdrop dismiss only the top.
  const layers = [];
  const trapHandlers = new WeakMap();
  const MAX_DEPTH = 3;

  function focusables(root) {
    return Array.prototype.slice.call(root.querySelectorAll(
      'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'
    )).filter((n) => n.offsetWidth > 0 || n.offsetHeight > 0 || n === document.activeElement);
  }

  function applyInert(topEl, store, exceptEl) {
    let node = topEl;
    while (node && node.parentElement && node !== document.body) {
      const parent = node.parentElement;
      Array.prototype.forEach.call(parent.children, (child) => {
        if (child === node || child === exceptEl || child.tagName === "SCRIPT" || child.tagName === "STYLE") return;
        if (child.hasAttribute("data-jr-inert")) return;
        child.setAttribute("data-jr-inert", "1");
        child.__jrPrev = { hadInert: child.hasAttribute("inert"), ariaHidden: child.getAttribute("aria-hidden") };
        if ("inert" in HTMLElement.prototype) child.inert = true;
        else { child.setAttribute("aria-hidden", "true"); child.style.pointerEvents = "none"; }
        store.push(child);
      });
      node = parent;
    }
  }

  function releaseInert(store) {
    (store || []).forEach((child) => {
      const prev = child.__jrPrev || {};
      child.removeAttribute("data-jr-inert");
      if ("inert" in HTMLElement.prototype) { if (!prev.hadInert) child.inert = false; }
      else {
        if (prev.ariaHidden == null) child.removeAttribute("aria-hidden");
        else child.setAttribute("aria-hidden", prev.ariaHidden);
        child.style.pointerEvents = "";
      }
      delete child.__jrPrev;
    });
  }

  function trapFocus(layerEl) {
    const handler = (e) => {
      if (e.key !== "Tab") return;
      if (!layers.length || layers[layers.length - 1].el !== layerEl) return;
      const f = focusables(layerEl);
      if (!f.length) { e.preventDefault(); layerEl.focus(); return; }
      const first = f[0], last = f[f.length - 1];
      if (!layerEl.contains(document.activeElement)) { e.preventDefault(); first.focus(); return; }
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handler, true);
    trapHandlers.set(layerEl, handler);
  }

  function pushLayer(entry) {
    if (layers.length >= MAX_DEPTH) return false;
    entry.trigger = entry.trigger || document.activeElement;
    entry.inertStore = [];
    // Never inert the layer's own backdrop, or click-to-dismiss stops working.
    applyInert(entry.el, entry.inertStore, entry.backdrop);
    layers.push(entry);
    trapFocus(entry.el);
    const target = entry.initialFocus ||
      (entry.type === "confirm" ? entry.el.querySelector("[data-jr-confirm-cancel]") : null) ||
      focusables(entry.el)[0] || entry.el;
    try { target.focus({ preventScroll: true }); } catch (e) { try { target.focus(); } catch (e2) {} }
    return true;
  }

  function popLayer(layerEl) {
    const idx = layers.findIndex((l) => l.el === layerEl);
    if (idx === -1) return;
    for (let i = layers.length - 1; i >= idx; i--) {
      const l = layers[i];
      const h = trapHandlers.get(l.el);
      if (h) document.removeEventListener("keydown", h, true);
      trapHandlers.delete(l.el);
      releaseInert(l.inertStore);
      layers.pop();
      if (typeof l.onClosed === "function") { try { l.onClosed(); } catch (e) {} }
      const t = l.trigger;
      if (t && document.contains(t) && typeof t.focus === "function") {
        try { t.focus({ preventScroll: true }); } catch (e) { t.focus(); }
      }
    }
  }

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !layers.length) return;
    const top = layers[layers.length - 1];
    if (top.onEscape) { e.stopPropagation(); e.preventDefault(); top.onEscape(); }
  });

  // Registry of the workstream panels so opening one closes its siblings.
  const jrPanels = [];

  // Build a full-screen overlay panel shell into a mount point. The caller fills
  // `body`. Returns { card, body, backdrop, setEyebrow, setTitle, open, close, isOpen }.
  function makePanel(cfg) {
    cfg = cfg || {};
    const mount = cfg.mount || document.body;
    const titleId = uid("jrpt");
    const backdrop = el("div", { className: "overlay-backdrop jr-panel-backdrop" });
    const card = el("div", { className: "overlay-card jr-panel " + (cfg.className || ""), role: "dialog" });
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", titleId);
    card.hidden = true;

    const eyebrow = el("div", { className: "panel-eyebrow", text: cfg.eyebrow || "" });
    const h2 = el("h2", { id: titleId, text: cfg.title || "" });
    const closeBtn = el("button", { className: "icon-btn", type: "button", "aria-label": "Close " + (cfg.title || "panel"), text: "✕" });
    const head = el("div", { className: "panel-head" }, el("div", null, eyebrow, h2), closeBtn);
    const body = el("div", { className: "jr-panel-body" });
    card.append(head, body);
    mount.append(backdrop, card);
    // The mount points ship with a `hidden` attribute; the panel manages its own
    // visibility via `card.hidden` + the `.open` class, so clear it once here.
    mount.hidden = false;

    let open = false;
    function doClose() {
      if (!open) return;
      open = false;
      popLayer(card);
      card.classList.remove("open");
      backdrop.classList.remove("open");
      card.hidden = true;
      if (typeof cfg.onClose === "function") { try { cfg.onClose(); } catch (e) {} }
    }
    function doOpen(triggerEl) {
      if (open) return;
      // Close any sibling workstream panel first (one at a time on screen).
      jrPanels.forEach((p) => { if (p !== api && p.isOpen()) p.close(); });
      open = true;
      card.hidden = false;
      backdrop.classList.add("open");
      card.classList.add("open");
      pushLayer({ el: card, backdrop: backdrop, type: "panel", trigger: triggerEl, onEscape: doClose });
      if (typeof cfg.onOpen === "function") { try { cfg.onOpen(); } catch (e) {} }
    }
    closeBtn.addEventListener("click", doClose);
    backdrop.addEventListener("click", doClose);

    const api = {
      card, body, backdrop,
      setEyebrow: (t) => { eyebrow.textContent = t || ""; },
      setTitle: (t) => { h2.textContent = t || ""; },
      open: doOpen, close: doClose, isOpen: () => open,
    };
    jrPanels.push(api);
    return api;
  }

  // Accessible confirmation. Resolves { confirmed:boolean, note:string }.
  // Single-instance: a second call while one is open resolves {confirmed:false}.
  let confirmOpen = false;
  function confirmModal(opts) {
    opts = opts || {};
    return new Promise((resolve) => {
      if (confirmOpen) { resolve({ confirmed: false, note: "" }); return; }
      confirmOpen = true;
      const danger = !!opts.danger;
      const titleId = uid("jrct"), bodyId = uid("jrcb");
      const backdrop = el("div", { className: "overlay-backdrop jr-confirm-backdrop open" });
      const card = el("div", {
        className: "jr-confirm" + (danger ? " jr-confirm--danger" : ""),
        role: danger ? "alertdialog" : "dialog",
      });
      card.setAttribute("aria-modal", "true");
      card.setAttribute("aria-labelledby", titleId);
      card.setAttribute("aria-describedby", bodyId);

      card.appendChild(el("h3", { id: titleId, className: "jr-confirm-title", text: opts.title || "Are you sure?" }));
      card.appendChild(el("p", { id: bodyId, className: "jr-confirm-body", text: opts.body || "" }));

      let noteField = null;
      if (opts.note && typeof opts.note === "object") {
        const nId = uid("jrcn");
        card.appendChild(el("label", { className: "jr-confirm-note-label", htmlFor: nId, text: opts.note.label || "Note (optional)" }));
        noteField = el("textarea", {
          id: nId, className: "jr-confirm-note", rows: 3,
          maxLength: opts.note.maxLength || 500,
          placeholder: opts.note.placeholder || "",
        });
        card.appendChild(noteField);
      }

      const cancelBtn = el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: opts.cancelLabel || "Cancel" });
      cancelBtn.setAttribute("data-jr-confirm-cancel", "1");
      const okBtn = el("button", {
        className: "hud-btn " + (danger ? "hud-btn--danger" : "hud-btn--accent"),
        type: "button", text: opts.confirmLabel || "Confirm",
      });
      card.appendChild(el("div", { className: "jr-confirm-actions" }, cancelBtn, okBtn));

      function finish(confirmed) {
        if (!confirmOpen) return;
        confirmOpen = false;
        popLayer(card);
        backdrop.remove();
        card.remove();
        resolve({ confirmed: confirmed, note: noteField ? noteField.value.trim() : "" });
      }
      cancelBtn.addEventListener("click", () => finish(false));
      okBtn.addEventListener("click", () => finish(true));
      backdrop.addEventListener("click", () => finish(false));

      document.body.append(backdrop, card);
      pushLayer({
        el: card, backdrop: backdrop, type: "confirm", trigger: opts.triggerEl || document.activeElement,
        onEscape: () => finish(false),
        initialFocus: (danger || !noteField) ? cancelBtn : noteField,
      });
    });
  }

  // Layer a caller-built nested dialog (its own card + backdrop) on top of an
  // open panel: applies inert to the parent, traps focus, restores focus on
  // close, and closes with the parent. Returns { close }.
  function openNested(cfg) {
    const card = cfg.card, backdrop = cfg.backdrop;
    let torndown = false;
    // Runs exactly once, whether closed directly or popped by a parent layer.
    function teardown() {
      if (torndown) return;
      torndown = true;
      if (card && card.isConnected) card.remove();
      if (backdrop && backdrop.isConnected) backdrop.remove();
      if (typeof cfg.onClose === "function") { try { cfg.onClose(); } catch (e) {} }
    }
    function close() {
      if (torndown) return;
      popLayer(card); // triggers onClosed -> teardown
    }
    if (backdrop) backdrop.addEventListener("click", close);
    pushLayer({
      el: card, backdrop: backdrop, type: cfg.type || "dialog",
      trigger: cfg.triggerEl || document.activeElement,
      initialFocus: cfg.initialFocus || null,
      onEscape: close,
      onClosed: teardown,
    });
    return { close: close };
  }

  // ------------------------------------------------------------- toast

  let toastEl = null, toastTimer = null;
  function toast(message, kind) {
    if (!toastEl) {
      toastEl = el("div", { className: "jr-toast", role: "status", "aria-live": "polite" });
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = message || "";
    toastEl.dataset.kind = kind || "info";
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 4200);
  }

  // ---------------------------------------------------- section registry

  const sections = {};
  function registerSection(name, openFn) { if (typeof openFn === "function") sections[name] = openFn; }

  // ------------------------------------------------------ integrations state

  const INT_LABELS = {
    job_sources: "Job sources", ai: "AI features", prospects: "Prospects",
    outreach: "Outreach", github_repos: "GitHub repositories", news: "News feeds",
    outreach_mailto: "Outreach mailto", anthropic: "Claude (Anthropic)",
    openai: "OpenAI", pack_generation: "Application pack generation",
    pack_revision: "Application pack revision", pack_autofill: "Autofill export",
    reed: "Reed", adzuna: "Adzuna", usajobs: "USAJobs", companies_house: "Companies House",
    greenhouse: "Greenhouse", lever: "Lever", remoteok: "RemoteOK",
  };
  const INT_HINTS = {
    "ai.anthropic": "Add ANTHROPIC_API_KEY to your .env file.",
    "ai.openai": "Add OPENAI_API_KEY to your .env file.",
    "ai.pack_generation": "Needs ANTHROPIC_API_KEY (and OPENAI_API_KEY for review).",
    "ai.pack_revision": "Needs ANTHROPIC_API_KEY.",
    "ai.pack_autofill": "Local feature — enabled once a reviewed pack exists.",
    "job_sources.reed": "Add REED_API_KEY to your .env file.",
    "job_sources.adzuna": "Add ADZUNA_APP_ID and ADZUNA_APP_KEY to your .env file.",
    "job_sources.usajobs": "Add USAJOBS_API_KEY and USAJOBS_USER_AGENT to your .env file.",
    "prospects.companies_house": "Add COMPANIES_HOUSE_API_KEY to your .env file.",
    "github_repos": "Add GITHUB_USERNAME (public) or GITHUB_TOKEN (private) to your .env file.",
  };
  const SKIP_KEYS = { "__proto__": 1, "constructor": 1, "prototype": 1 };

  let intCache = null;      // last SUCCESSFUL payload (object). Failures never cached.
  let intInflight = null;
  let capabilityCache = null;

  function labelFor(key) { return INT_LABELS[key] || key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()); }

  function setDot(state) {
    const dot = document.querySelector("#integrations-indicator .integrations-dot");
    const btn = document.getElementById("integrations-indicator");
    if (dot) dot.setAttribute("data-state", state);
    if (btn) btn.title = state === "ok" ? "Integrations — reachable" : "Integrations — status unavailable";
  }

  async function getIntegrations(force) {
    if (!force && intCache) return intCache;
    if (intInflight && !force) return intInflight;
    intInflight = (async () => {
      const r = await fetchJSON("/api/integrations", { kind: "collection" });
      intInflight = null;
      if (r.ok && r.data && typeof r.data === "object" && !Array.isArray(r.data)) {
        intCache = r.data;
        setDot("ok");
        return intCache;
      }
      setDot("bad");
      return null; // NOT cached - next call retries
    })();
    return intInflight;
  }

  function integrationsReady(dottedKey) {
    if (!intCache || !dottedKey) return false;
    let node = intCache;
    const parts = String(dottedKey).split(".");
    for (let i = 0; i < parts.length; i++) {
      const p = parts[i];
      if (SKIP_KEYS[p] || !node || typeof node !== "object" || !Object.prototype.hasOwnProperty.call(node, p)) return false;
      node = node[p];
    }
    return node === true;
  }

  function featureEnabled(name) {
    if (!capabilityCache || typeof capabilityCache !== "object") return null; // unknown
    if (!Object.prototype.hasOwnProperty.call(capabilityCache, name)) return null;
    return capabilityCache[name] === true;
  }

  // Recursive boolean renderer. Own enumerable keys only, depth-capped, boolean
  // leaves become rows, nested plain objects become titled groups, everything
  // else is ignored. Keys are only ever used as textContent.
  function renderIntTree(obj, depth, pathPrefix) {
    const frag = document.createDocumentFragment();
    if (depth > 4 || !obj || typeof obj !== "object" || Array.isArray(obj)) return frag;
    Object.keys(obj).forEach((key) => {
      if (SKIP_KEYS[key] || !Object.prototype.hasOwnProperty.call(obj, key)) return;
      const val = obj[key];
      const path = pathPrefix ? pathPrefix + "." + key : key;
      if (typeof val === "boolean") {
        frag.appendChild(renderBoolRow(key, path, val));
      } else if (val && typeof val === "object" && !Array.isArray(val)) {
        const group = el("div", { className: "int-group" });
        group.appendChild(el("h4", { className: "int-group-title", text: labelFor(key) }));
        group.appendChild(renderIntTree(val, depth + 1, path));
        frag.appendChild(group);
      }
      // arrays / strings / numbers / null: ignored on purpose
    });
    return frag;
  }

  function renderBoolRow(key, path, connected) {
    const row = el("div", { className: "int-row" });
    row.appendChild(el("span", { className: "int-row-label", text: labelFor(key) }));
    row.appendChild(el("span", {
      className: "int-chip " + (connected ? "int-chip--on" : "int-chip--off"),
      text: connected ? "Connected" : "Not connected",
    }));
    if (!connected && INT_HINTS[path]) {
      row.appendChild(el("p", { className: "int-hint", text: INT_HINTS[path] }));
    }
    return row;
  }

  // ------------------------------------------------------ integrations panel

  const panel = makePanel({
    mount: document.getElementById("integrations-root") || document.body,
    className: "jr-panel--narrow",
    eyebrow: "Connected services",
    title: "Integrations",
  });

  async function renderIntPanel() {
    renderState(panel.body, { kind: "loading" });
    const data = await getIntegrations(true);
    clear(panel.body);
    if (!data) {
      renderState(panel.body, {
        kind: "error",
        message: "Couldn't reach the integrations endpoint.",
        onRetry: renderIntPanel,
      });
      return;
    }
    const intro = el("p", { className: "int-intro" });
    intro.textContent = "Green means the service is configured. Optional services left unconfigured are fine — set them up in your .env file only if you want that source.";
    panel.body.appendChild(intro);
    const tree = renderIntTree(data, 0, "");
    if (!tree.childNodes.length) {
      panel.body.appendChild(el("p", { className: "int-hint", text: "The endpoint is reachable but reported no services." }));
    } else {
      panel.body.appendChild(tree);
    }
  }

  function openIntegrations(triggerEl, reason) {
    panel.open(triggerEl || document.getElementById("integrations-indicator"));
    if (reason) toast("Set up " + reason + " to use that feature.", "info");
    renderIntPanel();
  }

  // Returns true when `dottedKey` is ready; otherwise opens the panel and
  // returns false so the caller can abort the gated action.
  async function gate(dottedKey, label, triggerEl) {
    await getIntegrations();
    if (integrationsReady(dottedKey)) return true;
    openIntegrations(triggerEl, label);
    return false;
  }

  // -------------------------------------------------------------- publish

  window.JobRadarUI = {
    prefersReducedMotion,
    uid, escapeHtml, safeUrl, isMailtoUrl, fmtBytes, fmtDate, timeAgo, el, clear,
    ContractError,
    fetchJSON, renderState, renderFetchFailure,
    makePanel, openNested, confirm: confirmModal, toast,
    layerDepth: () => layers.length,
    sections, registerSection,
    integrations: {
      get: getIntegrations,
      ready: integrationsReady,
      reachable: () => !!intCache,
      open: openIntegrations,
      gate: gate,
    },
    features: { enabled: featureEnabled, set: (map) => { capabilityCache = map; } },
  };

  // ---------------------------------------------------------------- wire-up

  const indicator = document.getElementById("integrations-indicator");
  if (indicator) {
    indicator.addEventListener("click", () => openIntegrations(indicator));
  }
  registerSection("integrations", () => openIntegrations(indicator));

  // Warm the dot without opening anything (booleans only; no values shown).
  getIntegrations().catch(() => setDot("bad"));
})();
