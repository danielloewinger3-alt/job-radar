// Job Radar - application-tracker workstream.
//
// Board (10 canonical stages) + application detail + calendar/upcoming view +
// application-pack preparation (generate / view / edit answers / revise /
// review / autofill export). Also owns the job-modal "Track application" button.
//
// Consumes Agent A's canonical contracts. All list/detail loads are honest:
// loading / empty / unavailable / forbidden / error. No mock data. Nothing here
// ever claims an application was submitted.
(function () {
  "use strict";

  const UI = window.JobRadarUI;
  if (!UI) { console.error("tracker.js: JobRadarUI missing"); return; }
  const { el, clear, escapeHtml, fetchJSON, renderState, renderFetchFailure, confirm, toast } = UI;

  // ---------------------------------------------------- canonical stages

  const STAGES = [
    { key: "interested", label: "Interested" },
    { key: "preparing", label: "Preparing" },
    { key: "applied", label: "Applied" },
    { key: "assessment", label: "Assessment" },
    { key: "recruiter_screen", label: "Recruiter screen" },
    { key: "interview", label: "Interview" },
    { key: "final_interview", label: "Final interview" },
    { key: "offer", label: "Offer" },
    { key: "rejected", label: "Rejected" },
    { key: "withdrawn", label: "Withdrawn" },
  ];
  const STAGE_INDEX = {};
  STAGES.forEach((s, i) => { STAGE_INDEX[s.key] = i; });
  const STAGE_LABEL = {};
  STAGES.forEach((s) => { STAGE_LABEL[s.key] = s.label; });
  const ALWAYS_CONFIRM = { rejected: 1, withdrawn: 1 };

  function stageNeedsConfirm(from, to) {
    if (ALWAYS_CONFIRM[to]) return true;
    const fi = STAGE_INDEX[from], ti = STAGE_INDEX[to];
    return typeof fi === "number" && typeof ti === "number" && ti < fi;
  }

  // ---------------------------------------------------- adapters

  function normalizeTrackedApp(raw) {
    if (!raw || typeof raw !== "object") throw new UI.ContractError("tracked-application response was not an object");
    if (raw.id == null) throw new UI.ContractError("tracked-application missing required 'id'");
    if (typeof raw.stage !== "string") throw new UI.ContractError("tracked-application missing required 'stage'");
    return {
      id: raw.id,
      job_id: raw.job_id != null ? String(raw.job_id) : null,
      company: typeof raw.company === "string" ? raw.company : "",
      role_title: typeof raw.role_title === "string" ? raw.role_title : "",
      stage: raw.stage,
      archived: raw.archived === true,
      created_at: raw.created_at || raw.updated_at || null,
      updated_at: raw.updated_at || null,
      contacts: Array.isArray(raw.contacts) ? raw.contacts : [],
      events: Array.isArray(raw.events) ? raw.events : [],
      deadlines: Array.isArray(raw.deadlines) ? raw.deadlines : [],
      next_actions: Array.isArray(raw.next_actions) ? raw.next_actions : [],
      projects: Array.isArray(raw.projects) ? raw.projects
        : (Array.isArray(raw.linked_project_ids) ? raw.linked_project_ids : []),
      cv_id: raw.cv_id != null ? raw.cv_id : null,
      pack_id: raw.pack_id != null ? raw.pack_id : (raw.pack && raw.pack.pack_id) || null,
      raw: raw,
    };
  }

  function normalizeCalendarItem(raw) {
    if (!raw || typeof raw !== "object") return null;
    return {
      when: raw.when || raw.date || raw.due || raw.at || null,
      kind: raw.kind || raw.type || "item",
      title: typeof raw.title === "string" ? raw.title : (raw.label || ""),
      application_id: raw.application_id != null ? raw.application_id : (raw.tracked_application_id != null ? raw.tracked_application_id : null),
      company: typeof raw.company === "string" ? raw.company : "",
    };
  }

  function normalizeAnswer(a) {
    return {
      key: a.key,
      label: typeof a.label === "string" && a.label ? a.label : String(a.key || ""),
      category: a.category || "",
      answer_kind: a.answer_kind || "",
      autofill_exportable: a.autofill_exportable === true,
      value: a.value == null ? "" : String(a.value),
      source: a.source || "",
      status: a.status || "",
      provenance: a.provenance || null,
      edited_by_user: a.edited_by_user === true,
    };
  }

  function normalizePack(env) {
    if (!env || typeof env !== "object") throw new UI.ContractError("pack response was not an object");
    const p = env.pack && typeof env.pack === "object" ? env.pack : null;
    if (!p) throw new UI.ContractError("pack response missing 'pack'");
    if (p.pack_id == null) throw new UI.ContractError("pack missing required 'pack_id'");
    return {
      schema_version: env.schema_version,
      pack_id: p.pack_id,
      tracked_application_id: p.tracked_application_id != null ? p.tracked_application_id : null,
      cover_letter: typeof p.cover_letter === "string" ? p.cover_letter : "",
      answers: Array.isArray(p.answers) ? p.answers.map(normalizeAnswer) : [],
      reviewed: p.reviewed === true,
      review_valid: p.review_valid === true,
      reviewed_at: p.reviewed_at || null,
      references: env.references || null,
      raw: env,
    };
  }

  const ANSWER_SOURCE_LABEL = {
    generated: "AI suggestion", user_supplied: "Your answer", profile: "From your profile",
  };
  function packIsAutofillReady(pack) { return pack.reviewed && pack.review_valid; }

  // ---------------------------------------------------- module state

  const state = {
    apps: [],
    view: "board",             // "board" | "calendar"
    filterStage: "",           // narrow-screen single-column filter
    listReq: 0,
    detailReq: 0,
    calReq: 0,
    packReq: 0,
    pendingJob: null,          // {id, company, title} from job-modal bridge
    inflight: new Set(),       // resource keys with a mutation in flight
  };

  function busy(key) { return state.inflight.has(key); }
  function setBusy(key, on, btn) {
    if (on) state.inflight.add(key); else state.inflight.delete(key);
    if (btn) { btn.disabled = on; if (on) btn.setAttribute("aria-busy", "true"); else btn.removeAttribute("aria-busy"); }
  }

  // ---------------------------------------------------- panel scaffold

  const panel = UI.makePanel({
    mount: document.getElementById("tracker-root") || document.body,
    eyebrow: "Applications",
    title: "Tracker",
    onOpen: () => { if (state.view === "board") loadBoard(); else loadCalendar(); },
  });

  // panel.body gets: a toolbar (view toggle + new + stage filter) and a scroll area.
  const toolbar = el("div", { className: "tk-toolbar" });
  const scrollArea = el("div", { className: "tk-scroll" });
  const liveStatus = el("div", { className: "jr-live", role: "status", "aria-live": "polite" });
  panel.body.append(toolbar, liveStatus, scrollArea);

  function buildToolbar() {
    clear(toolbar);
    const seg = el("div", { className: "mode-toggle", role: "tablist" });
    ["board", "calendar"].forEach((v) => {
      const b = el("button", {
        className: "mode-btn" + (state.view === v ? " active" : ""),
        type: "button", role: "tab", "aria-selected": String(state.view === v),
        text: v === "board" ? "Board" : "Calendar",
      });
      b.addEventListener("click", () => {
        if (state.view === v) return;
        state.view = v;
        buildToolbar();
        if (v === "board") loadBoard(); else loadCalendar();
      });
      seg.appendChild(b);
    });
    toolbar.appendChild(seg);

    const newBtn = el("button", { className: "hud-btn hud-btn--accent", type: "button", text: "New application" });
    newBtn.addEventListener("click", () => openCreateForm(newBtn));
    toolbar.appendChild(newBtn);

    if (state.view === "board") {
      const filt = el("select", { className: "tk-stage-filter", "aria-label": "Show one stage" });
      filt.appendChild(el("option", { value: "", text: "All stages" }));
      STAGES.forEach((s) => filt.appendChild(el("option", { value: s.key, text: s.label })));
      filt.value = state.filterStage;
      filt.addEventListener("change", () => { state.filterStage = filt.value; renderBoard(); });
      toolbar.appendChild(filt);
    }
  }

  // ---------------------------------------------------- board

  async function loadBoard() {
    buildToolbar();
    const my = ++state.listReq;
    renderState(scrollArea, { kind: "loading" });
    const res = await fetchJSON("/api/tracked-applications", { kind: "collection" });
    if (my !== state.listReq || !panel.isOpen()) return;
    if (!res.ok) { renderFetchFailure(scrollArea, res, loadBoard); return; }
    try {
      state.apps = (Array.isArray(res.data) ? res.data : (res.data && res.data.items) || [])
        .map(normalizeTrackedApp).filter((a) => !a.archived);
    } catch (e) {
      console.error(e);
      renderState(scrollArea, { kind: "error", message: "The server sent tracker data this screen didn't recognise (contract mismatch)." });
      return;
    }
    renderBoard();
  }

  function renderBoard() {
    clear(scrollArea);
    if (!state.apps.length) {
      renderState(scrollArea, { kind: "empty", message: "No tracked applications yet. Use “New application”, or “Track application” from a job." });
      return;
    }
    const byStage = {};
    STAGES.forEach((s) => { byStage[s.key] = []; });
    const unknown = [];
    state.apps.forEach((a) => {
      if (byStage[a.stage]) byStage[a.stage].push(a);
      else unknown.push(a);
    });
    if (unknown.length) console.warn("tracker: unrecognised stage value(s)", unknown.map((a) => a.stage));

    const board = el("div", { className: "tk-board" });
    const cols = state.filterStage ? STAGES.filter((s) => s.key === state.filterStage) : STAGES;
    cols.forEach((s) => board.appendChild(renderColumn(s.key, s.label, byStage[s.key])));
    if (unknown.length && !state.filterStage) board.appendChild(renderColumn("__unknown", "Unknown stage", unknown));
    scrollArea.appendChild(board);
    liveStatus.textContent = state.apps.length + " application" + (state.apps.length === 1 ? "" : "s") + " tracked.";
  }

  function renderColumn(key, label, apps) {
    const labelId = UI.uid("tkcol");
    const col = el("section", { className: "tk-col", "aria-labelledby": labelId });
    if (key === "rejected" || key === "withdrawn" || key === "__unknown") col.classList.add("tk-col--terminal");
    col.appendChild(el("h3", { id: labelId, className: "tk-col-head", text: label + " — " + apps.length }));
    const list = el("div", { className: "tk-col-list" });
    apps.forEach((a) => list.appendChild(renderCard(a)));
    col.appendChild(list);
    return col;
  }

  function renderCard(a) {
    const card = el("div", { className: "tk-card" });
    const openBtn = el("button", {
      className: "tk-card-open", type: "button",
      text: (a.company || "—") + (a.role_title ? " · " + a.role_title : ""),
    });
    openBtn.addEventListener("click", () => openDetail(a.id, openBtn));
    card.appendChild(openBtn);

    const row = el("div", { className: "tk-card-move" });
    const sel = el("select", { className: "tk-move-select", "aria-label": "Move " + (a.company || "application") + " to stage" });
    STAGES.forEach((s) => {
      const o = el("option", { value: s.key, text: s.label });
      if (s.key === a.stage) o.selected = true;
      sel.appendChild(o);
    });
    const moveBtn = el("button", { className: "hud-btn hud-btn--ghost tk-move-btn", type: "button", text: "Move" });
    moveBtn.addEventListener("click", () => doStageMove(a, sel.value, moveBtn));
    row.append(sel, moveBtn);
    card.appendChild(row);
    return card;
  }

  async function doStageMove(app, toStage, btn) {
    if (toStage === app.stage) return;
    const key = "stage:" + app.id;
    if (busy(key)) return;
    if (!STAGE_INDEX.hasOwnProperty(toStage)) { toast("Unknown stage.", "error"); return; }

    let note = "";
    if (stageNeedsConfirm(app.stage, toStage)) {
      const r = await confirm({
        title: "Move this application?",
        body: (app.company || "This application") + (app.role_title ? " · " + app.role_title : "") +
              " — " + (STAGE_LABEL[app.stage] || app.stage) + " → " + (STAGE_LABEL[toStage] || toStage) + ".",
        confirmLabel: "Move to " + (STAGE_LABEL[toStage] || toStage),
        danger: !!ALWAYS_CONFIRM[toStage],
        note: { label: "Note (optional)", placeholder: "Why is it moving?" },
        triggerEl: btn,
      });
      if (!r.confirmed) return;
      note = r.note;
    }

    setBusy(key, true, btn);
    const body = { to_stage: toStage };
    if (note) body.note = note;
    const res = await fetchJSON("/api/tracked-applications/" + encodeURIComponent(app.id) + "/stage", {
      method: "POST", kind: "action", body: body,
    });
    setBusy(key, false, btn);
    if (res.aborted) return;
    if (res.ok) {
      app.stage = toStage;
      toast("Moved to " + (STAGE_LABEL[toStage] || toStage) + ".", "info");
      renderBoard();
      if (detail.isOpen() && detail.currentId === app.id) loadDetail(app.id);
    } else if (res.validation) {
      toast(res.message || "That move isn't allowed.", "error");
    } else if (res.unavailable) {
      toast("The tracker API isn't available in this build yet.", "error");
    } else {
      toast(res.message || "Couldn't move that application.", "error");
    }
  }

  // ---------------------------------------------------- create form

  function openCreateForm(triggerEl) {
    const form = el("form", { className: "tk-create-form" });
    const company = el("input", { type: "text", required: true, maxLength: 120, placeholder: "Company", "aria-label": "Company" });
    const role = el("input", { type: "text", required: true, maxLength: 160, placeholder: "Role title", "aria-label": "Role title" });
    const status = el("span", { className: "refresh-status", role: "status", "aria-live": "polite" });
    const submit = el("button", { className: "hud-btn hud-btn--accent", type: "submit", text: "Create" });
    form.append(
      el("p", { className: "tk-create-note", text: "Manual entry. To keep it linked to a posting, use “Track application” from the job modal instead." }),
      company, role, el("div", { className: "tk-create-actions" }, submit, status)
    );
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!company.value.trim() || !role.value.trim()) { status.textContent = "Company and role are required."; return; }
      if (busy("create")) return;
      setBusy("create", true, submit);
      status.textContent = "Creating…";
      const res = await fetchJSON("/api/tracked-applications", {
        method: "POST", kind: "action", body: { company: company.value.trim(), role_title: role.value.trim() },
      });
      setBusy("create", false, submit);
      if (res.ok) { modal.close(); toast("Application created.", "info"); loadBoard(); }
      else if (res.unavailable) status.textContent = "The tracker API isn't available in this build yet.";
      else status.textContent = res.message || "Couldn't create that application.";
    });
    const modal = openSubDialog("New application", form, triggerEl);
  }

  // ---------------------------------------------------- detail (nested dialog)

  const detail = { isOpen: () => !!detail._card, currentId: null, _card: null, _backdrop: null };

  function openSubDialog(title, contentEl, triggerEl) {
    const titleId = UI.uid("tksub");
    const backdrop = el("div", { className: "overlay-backdrop jr-sub-backdrop open" });
    const card = el("div", { className: "overlay-card jr-subdialog", role: "dialog" });
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", titleId);
    const closeBtn = el("button", { className: "icon-btn", type: "button", "aria-label": "Close", text: "✕" });
    card.append(
      el("div", { className: "panel-head" }, el("div", null, el("h2", { id: titleId, text: title })), closeBtn),
      el("div", { className: "jr-subdialog-body" }, contentEl)
    );
    document.body.append(backdrop, card);
    const firstField = card.querySelector("input,select,textarea,button:not(.icon-btn)");
    const layer = UI.openNested({ card, backdrop, triggerEl, initialFocus: firstField || closeBtn });
    closeBtn.addEventListener("click", layer.close);
    return { close: layer.close, card: card };
  }

  async function openDetail(id, triggerEl) {
    detail.currentId = id;
    const titleId = UI.uid("tkdt");
    const backdrop = el("div", { className: "overlay-backdrop jr-sub-backdrop open" });
    const card = el("div", { className: "overlay-card jr-detail", role: "dialog" });
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", titleId);
    const closeBtn = el("button", { className: "icon-btn", type: "button", "aria-label": "Close application", text: "✕" });
    const h2 = el("h2", { id: titleId, text: "Application" });
    const bodyEl = el("div", { className: "jr-detail-body" });
    card.append(el("div", { className: "panel-head" }, el("div", null, el("div", { className: "panel-eyebrow", text: "Application" }), h2), closeBtn),
      bodyEl);
    document.body.append(backdrop, card);
    detail._card = card; detail._backdrop = backdrop;

    const layer = UI.openNested({
      card, backdrop, triggerEl, initialFocus: closeBtn,
      onClose: () => {
        detail._card = null; detail._backdrop = null; detail._render = null;
        detail.currentId = null;
        state.detailReq++;
      },
    });
    detail._close = layer.close;
    closeBtn.addEventListener("click", layer.close);

    detail._render = bodyEl;
    detail._title = h2;
    loadDetail(id);
  }

  async function loadDetail(id) {
    const bodyEl = detail._render;
    if (!bodyEl) return;
    const my = ++state.detailReq;
    renderState(bodyEl, { kind: "loading" });
    const res = await fetchJSON("/api/tracked-applications/" + encodeURIComponent(id), { kind: "record" });
    if (my !== state.detailReq || !detail.isOpen()) return;
    if (!res.ok) { renderFetchFailure(bodyEl, res, () => loadDetail(id)); return; }
    let app;
    try { app = normalizeTrackedApp(res.data); }
    catch (e) { console.error(e); renderState(bodyEl, { kind: "error", message: "This application's data wasn't in the expected shape (contract mismatch)." }); return; }
    renderDetail(app);
  }

  function renderDetail(app) {
    const bodyEl = detail._render;
    clear(bodyEl);
    detail._title.textContent = (app.company || "Application") + (app.role_title ? " · " + app.role_title : "");

    // stage stepper (buttons, not colour-only)
    const stepper = el("div", { className: "tk-stepper", role: "group", "aria-label": "Stage" });
    STAGES.forEach((s) => {
      const b = el("button", {
        className: "tk-step" + (s.key === app.stage ? " is-current" : ""),
        type: "button", text: s.label, "aria-pressed": String(s.key === app.stage),
      });
      if (s.key === app.stage) b.disabled = true;
      b.addEventListener("click", () => doStageMove(app, s.key, b));
      stepper.appendChild(b);
    });
    bodyEl.append(el("h4", { className: "tk-sec-title", text: "Stage" }), stepper);

    if (app.job_id) bodyEl.appendChild(el("p", { className: "tk-linked", text: "Linked to job " + app.job_id }));

    // archive
    const archiveBtn = el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: "Archive application" });
    archiveBtn.addEventListener("click", async () => {
      const r = await confirm({
        title: "Archive this application?",
        body: "It leaves the board. You can still reach it later.",
        confirmLabel: "Archive application", danger: true, triggerEl: archiveBtn,
      });
      if (!r.confirmed) return;
      setBusy("arch:" + app.id, true, archiveBtn);
      const res = await fetchJSON("/api/tracked-applications/" + encodeURIComponent(app.id), { method: "DELETE", kind: "action" });
      setBusy("arch:" + app.id, false, archiveBtn);
      if (res.ok) { toast("Application archived.", "info"); if (detail._close) detail._close(); loadBoard(); }
      else toast(res.message || (res.unavailable ? "The tracker API isn't available yet." : "Couldn't archive that."), "error");
    });
    bodyEl.append(el("div", { className: "tk-detail-actions" }, archiveBtn));

    renderSimpleList(bodyEl, "Contacts", app.contacts, (c) => (c.name || c.email || "Contact") + (c.role ? " — " + c.role : ""));
    renderSimpleList(bodyEl, "Events", app.events, (e2) => (UI.fmtDate(e2.when || e2.at) ? UI.fmtDate(e2.when || e2.at) + " · " : "") + (e2.title || e2.kind || "Event"));
    renderSimpleList(bodyEl, "Deadlines", app.deadlines, (d) => (UI.fmtDate(d.when || d.due) ? UI.fmtDate(d.when || d.due) + " · " : "") + (d.title || "Deadline"));
    renderSimpleList(bodyEl, "Next actions", app.next_actions, (n) => (typeof n === "string" ? n : (n.title || "Action")));

    renderPackSection(bodyEl, app);
  }

  function renderSimpleList(container, title, items, fmt) {
    container.appendChild(el("h4", { className: "tk-sec-title", text: title }));
    if (!items || !items.length) {
      container.appendChild(el("p", { className: "tk-sec-empty", text: "None recorded." }));
      return;
    }
    const ul = el("ul", { className: "tk-sec-list" });
    items.forEach((it) => ul.appendChild(el("li", { text: fmt(it) })));
    container.appendChild(ul);
  }

  // ---------------------------------------------------- pack section

  async function renderPackSection(container, app) {
    const sec = el("div", { className: "tk-pack" });
    sec.appendChild(el("h4", { className: "tk-sec-title", text: "Application pack" }));
    container.appendChild(sec);

    const my = ++state.packReq;
    const box = el("div", { className: "tk-pack-box" });
    sec.appendChild(box);
    renderState(box, { kind: "loading" });

    const res = await fetchJSON("/api/tracked-applications/" + encodeURIComponent(app.id) + "/pack", { kind: "record" });
    if (my !== state.packReq || !detail.isOpen()) return;

    if (res.notFound) { renderPackEmpty(box, app); return; }
    if (res.unavailable) { renderState(box, { kind: "unavailable" }); return; }
    if (!res.ok) { renderFetchFailure(box, res, () => renderPackSection(container, app)); return; }

    let pack;
    try { pack = normalizePack(res.data); }
    catch (e) { console.error(e); renderState(box, { kind: "error", message: "The pack data wasn't in the expected shape (contract mismatch)." }); return; }
    renderPack(box, app, pack);
  }

  async function renderPackEmpty(box, app) {
    clear(box);
    box.appendChild(el("p", { className: "tk-pack-empty", text: "No pack yet. Generate one to draft a cover letter and answer the application questions." }));
    const genReady = await UI.integrations.get().then(() => UI.integrations.ready("ai.pack_generation"));
    const btn = el("button", {
      className: "hud-btn hud-btn--accent", type: "button", text: "Generate pack",
    });
    if (!genReady) {
      btn.disabled = true;
      const hint = el("button", { className: "tk-connect-link", type: "button", text: "Connect Claude to enable" });
      hint.addEventListener("click", () => UI.integrations.open(hint, "application pack generation"));
      box.append(btn, hint);
    } else {
      btn.addEventListener("click", () => openPackForm(app, false, btn));
      box.appendChild(btn);
    }
  }

  function renderPack(box, app, pack) {
    clear(box);
    const ready = packIsAutofillReady(pack);
    const badge = el("span", {
      className: "tk-badge " + (ready ? "tk-badge--reviewed" : "tk-badge--draft"),
      text: ready ? "Reviewed by you" : "AI suggestion — not reviewed",
    });
    box.appendChild(el("div", { className: "tk-pack-head" }, badge));

    // cover letter (read-only view; distinguishes generated content)
    box.appendChild(el("h5", { className: "tk-pack-sub", text: "Cover letter (AI-drafted)" }));
    box.appendChild(el("pre", { className: "tk-pack-letter", text: pack.cover_letter || "—" }));

    // answers
    box.appendChild(el("h5", { className: "tk-pack-sub", text: "Application answers" }));
    if (!pack.answers.length) box.appendChild(el("p", { className: "tk-sec-empty", text: "No answers in this pack." }));
    pack.answers.forEach((ans) => box.appendChild(renderAnswerRow(app, pack, ans)));

    // actions
    const actions = el("div", { className: "tk-pack-actions" });

    const reviseBtn = el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: "Revise with feedback" });
    reviseBtn.addEventListener("click", () => openReviseForm(app, pack, reviseBtn));
    UI.integrations.get().then(() => { if (!UI.integrations.ready("ai.pack_revision")) reviseBtn.disabled = true; });
    actions.appendChild(reviseBtn);

    const reviewBtn = el("button", { className: "hud-btn hud-btn--accent", type: "button", text: pack.reviewed && pack.review_valid ? "Reviewed" : "Mark reviewed" });
    if (pack.reviewed && pack.review_valid) reviewBtn.disabled = true;
    reviewBtn.addEventListener("click", async () => {
      setBusy("review:" + pack.pack_id, true, reviewBtn);
      const r = await fetchJSON("/api/packs/" + encodeURIComponent(pack.pack_id) + "/review", { method: "POST", kind: "action" });
      setBusy("review:" + pack.pack_id, false, reviewBtn);
      if (r.ok) { toast("Pack marked reviewed.", "info"); rehydratePack(box, app); }
      else toast(r.message || (r.unavailable ? "The pack API isn't available yet." : "Couldn't mark reviewed."), "error");
    });
    actions.appendChild(reviewBtn);

    const autofillBtn = el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: "Prepare autofill data" });
    if (!ready) { autofillBtn.disabled = true; autofillBtn.title = "Review the pack to enable autofill data"; }
    autofillBtn.addEventListener("click", () => openAutofill(pack, autofillBtn));
    actions.appendChild(autofillBtn);

    box.appendChild(actions);
    if (!ready) box.appendChild(el("p", { className: "tk-pack-hint", text: "Review the pack to enable autofill data. This app never fills an external site itself." }));
  }

  function rehydratePack(box, app) {
    // re-fetch just the pack into the same box
    const container = box.parentElement;
    if (!container) return;
    const my = ++state.packReq;
    renderState(box, { kind: "loading" });
    fetchJSON("/api/tracked-applications/" + encodeURIComponent(app.id) + "/pack", { kind: "record" }).then((res) => {
      if (my !== state.packReq || !detail.isOpen()) return;
      if (res.notFound) return renderPackEmpty(box, app);
      if (!res.ok) return renderFetchFailure(box, res, () => rehydratePack(box, app));
      try { renderPack(box, app, normalizePack(res.data)); }
      catch (e) { renderState(box, { kind: "error", message: "Pack contract mismatch." }); }
    });
  }

  function renderAnswerRow(app, pack, ans) {
    const row = el("div", { className: "tk-answer" });
    row.appendChild(el("div", { className: "tk-answer-label" },
      el("span", { text: ans.label }),
      el("span", { className: "tk-answer-src", text: ANSWER_SOURCE_LABEL[ans.source] || ans.source || "" }),
      ans.status === "needs_input" ? el("span", { className: "tk-answer-needs", text: "Needs input" }) : null
    ));
    const ta = el("textarea", { className: "tk-answer-input", rows: 2, value: ans.value, "aria-label": ans.label });
    const saveBtn = el("button", { className: "hud-btn hud-btn--ghost tk-answer-save", type: "button", text: "Save answer" });
    const st = el("span", { className: "refresh-status", role: "status", "aria-live": "polite" });
    saveBtn.addEventListener("click", async () => {
      const key = "ans:" + pack.pack_id + ":" + ans.key;
      if (busy(key)) return;
      setBusy(key, true, saveBtn);
      st.textContent = "Saving…";
      const r = await fetchJSON(
        "/api/packs/" + encodeURIComponent(pack.pack_id) + "/answers/" + encodeURIComponent(ans.key),
        { method: "PATCH", kind: "action", body: { value: ta.value } }
      );
      setBusy(key, false, saveBtn);
      if (r.ok) {
        st.textContent = "Saved — review cleared";
        toast("Answer saved. Review state cleared.", "info");
        const box = row.closest(".tk-pack-box");
        if (box) { try { renderPack(box, app, normalizePack(r.data)); } catch (e) { rehydratePack(box, app); } }
      } else if (r.validation) st.textContent = r.message || "That answer wasn't accepted.";
      else st.textContent = r.unavailable ? "The pack API isn't available yet." : (r.message || "Couldn't save.");
    });
    row.append(ta, el("div", { className: "tk-answer-row" }, saveBtn, st));
    return row;
  }

  function openPackForm(app, regenerate, triggerEl) {
    const form = el("form", { className: "tk-pack-form" });
    const cvSel = el("select", { className: "tk-pack-cv", "aria-label": "CV" });
    cvSel.appendChild(el("option", { value: "", text: "Loading CVs…" }));
    const projWrap = el("div", { className: "tk-pack-projects" }, el("p", { className: "tk-sec-empty", text: "Loading projects…" }));
    const fileWrap = el("div", { className: "tk-pack-files" });
    const jd = el("textarea", { className: "tk-pack-jd", rows: 4, placeholder: "Optional: paste the job description text", "aria-label": "Job description" });
    const regen = el("label", { className: "tk-pack-regen" },
      (() => { const c = el("input", { type: "checkbox" }); c.checked = !!regenerate; form._regen = c; return c; })(),
      " Regenerate (replace the current pack)"
    );
    const status = el("span", { className: "refresh-status", role: "status", "aria-live": "polite" });
    const submit = el("button", { className: "hud-btn hud-btn--accent", type: "submit", text: regenerate ? "Regenerate pack" : "Generate pack" });

    form.append(
      el("label", { className: "tk-pack-flabel", text: "CV" }), cvSel,
      el("label", { className: "tk-pack-flabel", text: "Linked projects" }), projWrap,
      el("label", { className: "tk-pack-flabel", text: "AI-context project files" }), fileWrap,
      el("label", { className: "tk-pack-flabel", text: "Job description (optional)" }), jd,
      regen,
      el("div", { className: "tk-create-actions" }, submit, status)
    );

    const dlg = openSubDialog(regenerate ? "Regenerate pack" : "Generate pack", form, triggerEl);

    // populate CVs
    fetchJSON("/api/cvs", { kind: "collection" }).then((r) => {
      clear(cvSel);
      if (r.ok && Array.isArray(r.data) && r.data.length) {
        cvSel.appendChild(el("option", { value: "", text: "— none —" }));
        r.data.forEach((cv) => cvSel.appendChild(el("option", { value: String(cv.id), text: cv.label + (cv.role_type ? " · " + cv.role_type : "") })));
      } else {
        cvSel.appendChild(el("option", { value: "", text: "No CVs — add one in the Dossier" }));
      }
    });

    // populate projects + their eligible files
    fetchJSON("/api/projects", { kind: "collection" }).then((r) => {
      clear(projWrap); clear(fileWrap);
      const projects = (r.ok && Array.isArray(r.data)) ? r.data : [];
      if (!projects.length) { projWrap.appendChild(el("p", { className: "tk-sec-empty", text: "No projects yet." })); return; }
      projects.forEach((p) => {
        const cb = el("input", { type: "checkbox", value: String(p.id), id: "tkpp-" + p.id });
        cb.addEventListener("change", () => loadFilesForProjects(fileWrap, projWrap));
        projWrap.appendChild(el("label", { className: "tk-pack-proj" }, cb, " " + (p.title || ("Project " + p.id))));
      });
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (busy("packgen:" + app.id)) return;
      const okKey = await UI.integrations.gate("ai.pack_generation", "application pack generation", submit);
      if (!okKey) return;
      setBusy("packgen:" + app.id, true, submit);
      status.textContent = "Generating… this can take a few seconds.";
      const project_ids = Array.prototype.map.call(projWrap.querySelectorAll("input:checked"), (n) => Number(n.value));
      const project_file_ids = Array.prototype.map.call(fileWrap.querySelectorAll("input:checked"), (n) => Number(n.value));
      const body = {
        cv_id: cvSel.value ? Number(cvSel.value) : null,
        project_ids: project_ids,
        project_file_ids: project_file_ids,
        job_description: jd.value.trim(),
        regenerate: !!form._regen.checked,
      };
      const res = await fetchJSON("/api/tracked-applications/" + encodeURIComponent(app.id) + "/pack", {
        method: "POST", kind: "action", body: body,
      });
      setBusy("packgen:" + app.id, false, submit);
      if (res.ok) { dlg.close(); toast("Pack generated.", "info"); loadDetail(app.id); }
      else if (res.validation) status.textContent = res.message || "The pack request wasn't accepted.";
      else status.textContent = res.unavailable ? "The pack API isn't available yet." : (res.message || "Couldn't generate the pack.");
    });
  }

  async function loadFilesForProjects(fileWrap, projWrap) {
    clear(fileWrap);
    const ids = Array.prototype.map.call(projWrap.querySelectorAll("input:checked"), (n) => n.value);
    if (!ids.length) { fileWrap.appendChild(el("p", { className: "tk-sec-empty", text: "Select a project to choose its files." })); return; }
    let any = false;
    for (const pid of ids) {
      const r = await fetchJSON("/api/projects/" + encodeURIComponent(pid) + "/files", { kind: "collection" });
      if (!r.ok || !Array.isArray(r.data)) continue;
      r.data.forEach((f) => {
        const readable = /^(available|done|ok|extracted|ready)$/i.test(String(f.extract_status || ""));
        const eligible = readable && f.ai_context_enabled === true;
        const cb = el("input", { type: "checkbox", value: String(f.id) });
        if (!eligible) { cb.disabled = true; }
        const why = eligible ? "" : (f.ai_context_enabled === true ? " (text not extractable)" : " (AI context off)");
        fileWrap.appendChild(el("label", { className: "tk-pack-file" }, cb, " " + (f.original_name || ("File " + f.id)) + why));
        any = true;
      });
    }
    if (!any) fileWrap.appendChild(el("p", { className: "tk-sec-empty", text: "No AI-readable, AI-context-enabled files in the selected project(s)." }));
  }

  function openReviseForm(app, pack, triggerEl) {
    const form = el("form", { className: "tk-revise-form" });
    const fb = el("textarea", { className: "tk-revise-fb", rows: 4, required: true, placeholder: "What should change? e.g. make it less formal", "aria-label": "Feedback" });
    const status = el("span", { className: "refresh-status", role: "status", "aria-live": "polite" });
    const submit = el("button", { className: "hud-btn hud-btn--accent", type: "submit", text: "Revise pack" });
    form.append(el("label", { className: "tk-pack-flabel", text: "Feedback" }), fb, el("div", { className: "tk-create-actions" }, submit, status));
    const dlg = openSubDialog("Revise pack", form, triggerEl);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!fb.value.trim()) { status.textContent = "Feedback is required."; return; }
      if (!(await UI.integrations.gate("ai.pack_revision", "application pack revision", submit))) return;
      if (busy("revise:" + pack.pack_id)) return;
      setBusy("revise:" + pack.pack_id, true, submit);
      status.textContent = "Revising…";
      const r = await fetchJSON("/api/packs/" + encodeURIComponent(pack.pack_id) + "/revise", {
        method: "POST", kind: "action", body: { feedback: fb.value.trim() },
      });
      setBusy("revise:" + pack.pack_id, false, submit);
      if (r.ok) { dlg.close(); toast("Pack revised. Review state cleared.", "info"); loadDetail(app.id); }
      else if (r.validation) status.textContent = r.message || "That wasn't accepted.";
      else status.textContent = r.unavailable ? "The pack API isn't available yet." : (r.message || "Couldn't revise.");
    });
  }

  // Deny-list is a DRIFT TRIPWIRE only: if the server's /autofill payload still
  // carries fields it should have filtered, we refuse to copy and warn - we do
  // NOT silently strip and then present the result as canonical.
  const AUTOFILL_FORBIDDEN = /^(legal_attestation|attestation|signature|e_signature|electronic_signature|demographic|eeo)$/i;
  function detectAutofillDrift(payload) {
    const answers = payload && (Array.isArray(payload.answers) ? payload.answers : (Array.isArray(payload.fields) ? payload.fields : null));
    if (!answers) return false;
    return answers.some((a) => a && (a.autofill_exportable === false || AUTOFILL_FORBIDDEN.test(String(a.category || "")) || AUTOFILL_FORBIDDEN.test(String(a.answer_kind || ""))));
  }

  async function openAutofill(pack, triggerEl) {
    if (!(await UI.integrations.gate("ai.pack_autofill", "autofill export", triggerEl))) return;
    const content = el("div", { className: "tk-autofill" });
    const dlg = openSubDialog("Autofill data", content, triggerEl);
    renderState(content, { kind: "loading" });
    const r = await fetchJSON("/api/packs/" + encodeURIComponent(pack.pack_id) + "/autofill", { kind: "record" });
    clear(content);
    if (!r.ok) { renderFetchFailure(content, r, () => { dlg.close(); openAutofill(pack, triggerEl); }); return; }

    content.appendChild(el("p", { className: "tk-autofill-warn", role: "alert",
      text: "This JSON contains your application answers. Only paste it into a browser helper you trust. Legal, signature and demographic fields are intentionally excluded — enter those yourself on the site." }));

    if (detectAutofillDrift(r.data)) {
      content.appendChild(el("p", { className: "jr-state jr-state--error", role: "alert",
        text: "The autofill export contained fields it should not (possible contract drift). Copy is disabled — please report this. Nothing has been modified." }));
      content.appendChild(el("pre", { className: "tk-autofill-json", text: safeJson(r.data) }));
      return;
    }

    const pre = el("pre", { className: "tk-autofill-json", text: safeJson(r.data) });
    content.appendChild(pre);
    const copyBtn = el("button", { className: "hud-btn hud-btn--accent", type: "button", text: "Copy autofill JSON" });
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(safeJson(r.data));
        toast("Autofill JSON copied — paste only into your trusted browser helper.", "info");
      } catch (e) {
        toast("Couldn't access the clipboard. Select the text and copy manually.", "error");
      }
    });
    content.appendChild(el("div", { className: "tk-create-actions" }, copyBtn));
    content.appendChild(el("p", { className: "tk-pack-hint",
      text: "Until the browser helper is connected, copy this JSON and paste it into the helper. This app never fills an external site itself and never puts pack data in a URL." }));
  }

  function safeJson(v) { try { return JSON.stringify(v, null, 2); } catch (e) { return "/* unserialisable */"; } }

  // ---------------------------------------------------- calendar view

  async function loadCalendar() {
    buildToolbar();
    const my = ++state.calReq;
    renderState(scrollArea, { kind: "loading" });
    const from = new Date(); from.setHours(0, 0, 0, 0);
    const to = new Date(from.getTime() + 30 * 864e5);
    const qs = "?from=" + encodeURIComponent(from.toISOString().slice(0, 10)) + "&to=" + encodeURIComponent(to.toISOString().slice(0, 10));
    const res = await fetchJSON("/api/tracked-applications/calendar" + qs, { kind: "collection" });
    if (my !== state.calReq || !panel.isOpen()) return;
    if (!res.ok) { renderFetchFailure(scrollArea, res, loadCalendar); return; }
    const items = (Array.isArray(res.data) ? res.data : (res.data && res.data.items) || [])
      .map(normalizeCalendarItem).filter(Boolean);
    renderCalendar(items);
  }

  function renderCalendar(items) {
    clear(scrollArea);
    if (!items.length) { renderState(scrollArea, { kind: "empty", message: "Nothing scheduled in the next 30 days." }); return; }
    const byDay = {};
    items.forEach((it) => {
      const d = it.when ? String(it.when).slice(0, 10) : "undated";
      (byDay[d] = byDay[d] || []).push(it);
    });
    const wrap = el("div", { className: "tk-calendar" });
    Object.keys(byDay).sort().forEach((day) => {
      const dayEl = el("div", { className: "tk-cal-day" });
      dayEl.appendChild(el("h4", { className: "tk-cal-date", text: day === "undated" ? "Undated" : UI.fmtDate(day) || day }));
      byDay[day].forEach((it) => {
        dayEl.appendChild(el("div", { className: "tk-cal-item" },
          el("span", { className: "tk-cal-kind", text: it.kind }),
          el("span", { className: "tk-cal-title", text: (it.company ? it.company + " · " : "") + (it.title || "") })
        ));
      });
      wrap.appendChild(dayEl);
    });
    scrollArea.appendChild(wrap);
    liveStatus.textContent = items.length + " upcoming item" + (items.length === 1 ? "" : "s") + ".";
  }

  // ---------------------------------------------------- job-modal bridge

  // app.js is frozen and currently emits no job-modal events. We add the button
  // and the listeners; until the `jobradar:jobmodalopen` / `jobradar:jobmodalclose`
  // emissions exist in app.js the button stays disabled. No DOM scraping.
  const trackBtn = document.getElementById("job-modal-track-btn");
  if (trackBtn) {
    trackBtn.disabled = true;
    trackBtn.title = "Waiting for the job-modal bridge (see frontend/js/README.md)";
    document.addEventListener("jobradar:jobmodalopen", (e) => {
      const d = (e && e.detail) || {};
      if (d.id == null) return;
      state.pendingJob = { id: String(d.id), company: d.company || "", title: d.title || "" };
      trackBtn.disabled = false;
      trackBtn.title = "Track this application";
    });
    document.addEventListener("jobradar:jobmodalclose", () => {
      state.pendingJob = null;
      trackBtn.disabled = true;
      trackBtn.title = "Waiting for the job-modal bridge (see frontend/js/README.md)";
    });
    trackBtn.addEventListener("click", onTrackClick);
  }

  async function onTrackClick() {
    const job = state.pendingJob;
    if (!job) return;
    if (busy("track:" + job.id)) return;
    setBusy("track:" + job.id, true, trackBtn);
    const res = await fetchJSON("/api/tracked-applications", {
      method: "POST", kind: "action", body: { job_id: job.id },
    });
    setBusy("track:" + job.id, false, trackBtn);

    if (res.ok) {
      toast("Now tracking this application.", "info");
      const open = el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: "Open in tracker" });
      // best-effort: open the board
      panel.open(trackBtn);
      state.view = "board"; loadBoard();
      return;
    }
    if (res.validation && res.status === 409) {
      const body = res.data || {};
      const code = body.code || (body.detail && body.detail.code);
      const existingId = body.tracked_application_id != null ? body.tracked_application_id
        : (body.detail && body.detail.tracked_application_id);
      const archived = body.archived === true || (body.detail && body.detail.archived === true);
      if (code === "already_tracked" && existingId != null) {
        if (archived) return promptArchivedDuplicate(existingId, trackBtn);
        return promptActiveDuplicate(existingId, trackBtn);
      }
    }
    if (res.unavailable) { toast("The tracker API isn't available in this build yet.", "error"); return; }
    toast(res.message || "Couldn't start tracking this job.", "error");
  }

  async function promptActiveDuplicate(existingId, triggerEl) {
    const r = await confirm({
      title: "Already tracked",
      body: "This job is already being tracked.",
      confirmLabel: "Open existing application", cancelLabel: "Close", triggerEl: triggerEl,
    });
    if (r.confirmed) { panel.open(triggerEl); state.view = "board"; loadBoard(); setTimeout(() => openDetail(existingId, triggerEl), 60); }
  }

  async function promptArchivedDuplicate(existingId, triggerEl) {
    const r = await confirm({
      title: "Previously archived",
      body: "This job was tracked before and then archived.",
      confirmLabel: "Open archived application", cancelLabel: "Close", triggerEl: triggerEl,
    });
    if (!r.confirmed) return;
    // Offer a deliberate restore
    const r2 = await confirm({
      title: "Restore to tracker?",
      body: "Bring this archived application back onto the board?",
      confirmLabel: "Restore to tracker", cancelLabel: "Just view it", triggerEl: triggerEl,
    });
    if (r2.confirmed) {
      const res = await fetchJSON("/api/tracked-applications/" + encodeURIComponent(existingId), {
        method: "PATCH", kind: "action", body: { archived: false },
      });
      if (res.ok) toast("Application restored to the tracker.", "info");
      else toast(res.message || "Couldn't restore it.", "error");
    }
    panel.open(triggerEl); state.view = "board"; loadBoard();
    setTimeout(() => openDetail(existingId, triggerEl), 60);
  }

  // ---------------------------------------------------- wire-up

  const trackerBtn = document.getElementById("tracker-btn");
  if (trackerBtn) trackerBtn.addEventListener("click", () => panel.open(trackerBtn));
  UI.registerSection("tracker", () => panel.open(trackerBtn));

  // Alfred hooks (used by alfred.js via JobRadarUI.tracker)
  UI.tracker = {
    STAGES: STAGES,
    openBoard: (trig) => { state.view = "board"; panel.open(trig || trackerBtn); },
    openCalendar: (trig) => { state.view = "calendar"; panel.open(trig || trackerBtn); if (panel.isOpen()) loadCalendar(); },
    openDetailById: (id, trig) => { state.view = "board"; panel.open(trig || trackerBtn); setTimeout(() => openDetail(id, trig || trackerBtn), 60); },
    listApps: async () => {
      const res = await fetchJSON("/api/tracked-applications", { kind: "collection" });
      if (!res.ok) return { error: res };
      try { return { apps: (Array.isArray(res.data) ? res.data : (res.data && res.data.items) || []).map(normalizeTrackedApp).filter((a) => !a.archived) }; }
      catch (e) { return { error: { error: true, message: "contract mismatch" } }; }
    },
    calendarSummary: async () => {
      const from = new Date(); from.setHours(0, 0, 0, 0);
      const to = new Date(from.getTime() + 30 * 864e5);
      const qs = "?from=" + from.toISOString().slice(0, 10) + "&to=" + to.toISOString().slice(0, 10);
      const res = await fetchJSON("/api/tracked-applications/calendar" + qs, { kind: "collection" });
      if (!res.ok) return { error: res };
      const items = (Array.isArray(res.data) ? res.data : (res.data && res.data.items) || []).map(normalizeCalendarItem).filter(Boolean);
      return { items: items };
    },
    stageKeys: STAGES.map((s) => s.key),
    stageLabel: (k) => STAGE_LABEL[k] || k,
    requestStageMove: (app, toStage, trig) => doStageMove(app, toStage, trig),
    generatePack: (app, trig) => openPackForm(app, false, trig),

    // Alfred: always-confirmed stage change (no implicit forward move).
    applyStage: async (appId, toStage, note) => {
      const body = { to_stage: toStage };
      if (note) body.note = note;
      const res = await fetchJSON("/api/tracked-applications/" + encodeURIComponent(appId) + "/stage", { method: "POST", kind: "action", body: body });
      if (res.ok) {
        toast("Moved to " + (STAGE_LABEL[toStage] || toStage) + ".", "info");
        if (panel.isOpen()) loadBoard();
        if (detail.isOpen() && detail.currentId === appId) loadDetail(appId);
        return { ok: true };
      }
      return { ok: false, message: res.unavailable ? "The tracker API isn't available yet." : (res.message || "Couldn't move that application.") };
    },

    // Alfred: open the pack-generation form for an application id. The form's
    // own Generate button is the on-screen confirmation.
    openPackFormForId: async (id, trig) => {
      state.view = "board";
      panel.open(trig || trackerBtn);
      const res = await fetchJSON("/api/tracked-applications/" + encodeURIComponent(id), { kind: "record" });
      if (!res.ok) { toast(res.unavailable ? "The tracker API isn't available yet." : "Couldn't open that application.", "error"); return; }
      let app;
      try { app = normalizeTrackedApp(res.data); }
      catch (e) { toast("That application's data wasn't in the expected shape.", "error"); return; }
      openDetail(id, trig || trackerBtn);
      setTimeout(() => openPackForm(app, !!app.pack_id, trig || trackerBtn), 90);
    },
  };
})();
