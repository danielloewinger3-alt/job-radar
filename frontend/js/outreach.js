// Job Radar - outreach workstream.
//
// Discovery + contact status, outreach thread pipeline, draft / revise / approve
// via dedicated endpoints, and a guarded "Open in email app" (mailto) flow.
// Consumes Agent B's canonical contract.
//
// Hard rules honoured here:
//  - The generic /stage selector ONLY exposes Agent B's administrative
//    transitions (replied->meeting, meeting->closed_won|closed_lost,
//    any active->closed_lost). draft / approve / mailto / reopen / opt-out use
//    their own dedicated endpoints and never go through /stage.
//  - mailto: explicit confirmation, then strict validation of the returned
//    mailto_url before navigating. Never claims an email was sent.
//  - No "sent" stage. The post-mailto stage is Agent B's `contacted`.
(function () {
  "use strict";

  const UI = window.JobRadarUI;
  if (!UI) { console.error("outreach.js: JobRadarUI missing"); return; }
  const { el, clear, fetchJSON, renderState, renderFetchFailure, confirm, toast } = UI;

  // Canonical stage set (Agent B). Order is display order.
  const STAGES = ["identified", "contacted", "replied", "meeting", "closed_won", "closed_lost", "opted_out"];
  const STAGE_LABEL = {
    identified: "Identified", contacted: "Contacted", replied: "Replied", meeting: "Meeting",
    closed_won: "Closed — won", closed_lost: "Closed — lost", opted_out: "Opted out",
  };
  const TERMINAL = { closed_won: 1, closed_lost: 1, opted_out: 1 };
  function isActive(stage) { return !TERMINAL[stage]; }

  // Administrative transitions the generic selector is allowed to offer.
  function adminTargets(current) {
    const out = [];
    if (current === "replied") out.push("meeting");
    if (current === "meeting") { out.push("closed_won"); out.push("closed_lost"); }
    if (isActive(current) && out.indexOf("closed_lost") === -1) out.push("closed_lost");
    return out;
  }

  const ERROR_COPY = {
    suppressed: "This business is on the suppression list — no outreach allowed.",
    opted_out: "This business has opted out of outreach.",
    duplicate: "You've already contacted this business.",
    already_contacted: "You've already contacted this business.",
  };
  function errorMessage(res) {
    const code = res.code || (res.data && res.data.code) || (res.data && res.data.detail && res.data.detail.code);
    if (code && ERROR_COPY[code]) return ERROR_COPY[code];
    if (res.unavailable) return "The outreach API isn't available in this build yet.";
    if (res.forbidden) return "You don't have access to outreach.";
    return res.message || "That outreach action didn't go through.";
  }

  function threadBusiness(t) {
    if (t.business && typeof t.business === "object") return t.business.name || t.business.title || "Business";
    return t.business_name || t.name || "Business";
  }
  function threadContactEmail(t) {
    if (t.contact && typeof t.contact === "object") return t.contact.email || "";
    return t.contact_email || "";
  }
  function threadContactName(t) {
    if (t.contact && typeof t.contact === "object") return t.contact.name || t.contact.email || "";
    return t.contact_name || t.contact_email || "";
  }
  function threadDraftApproved(t) {
    if (t.draft && typeof t.draft === "object") return t.draft.approved === true;
    return t.draft_approved === true || t.approved === true;
  }
  function threadDraftBody(t) {
    if (t.draft && typeof t.draft === "object") return t.draft.body || t.draft.text || "";
    return t.draft_body || "";
  }
  function threadHasDraft(t) {
    return !!(threadDraftBody(t) || (t.draft && typeof t.draft === "object"));
  }

  const state = {
    threads: [],
    pipelineReq: 0,
    detailReq: 0,
    areas: [],
    area: "",
    inflight: new Set(),
  };
  function busy(k) { return state.inflight.has(k); }
  function setBusy(k, on, btn) {
    if (on) state.inflight.add(k); else state.inflight.delete(k);
    if (btn) { btn.disabled = on; if (on) btn.setAttribute("aria-busy", "true"); else btn.removeAttribute("aria-busy"); }
  }

  const panel = UI.makePanel({
    mount: document.getElementById("outreach-root") || document.body,
    eyebrow: "Local business outreach",
    title: "Outreach",
    onOpen: () => { loadAreas(); loadPipeline(); },
  });

  const discovery = el("div", { className: "or-discovery" });
  const liveStatus = el("div", { className: "jr-live", role: "status", "aria-live": "polite" });
  const pipelineArea = el("div", { className: "or-pipeline" });
  panel.body.append(discovery, liveStatus, pipelineArea);

  // ---------------------------------------------------- discovery / contacts

  async function loadAreas() {
    const res = await fetchJSON("/api/prospects/areas", { kind: "collection" });
    clear(discovery);
    const sel = el("select", { className: "or-area-select", "aria-label": "Prospect area" });
    if (res.ok && res.data && Array.isArray(res.data.areas) && res.data.areas.length) {
      state.areas = res.data.areas;
      res.data.areas.forEach((a) => sel.appendChild(el("option", { value: a.key, text: a.label || a.key })));
      state.area = state.area || res.data.areas[0].key;
      sel.value = state.area;
    } else {
      sel.appendChild(el("option", { value: "", text: "No areas configured" }));
      sel.disabled = true;
    }
    sel.addEventListener("change", () => { state.area = sel.value; });

    const discoverBtn = el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: "Run discovery" });
    const contactsBtn = el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: "Collect contacts" });
    const viewBtn = el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: "View contact status" });
    discoverBtn.addEventListener("click", () => runAreaAction("discover", discoverBtn));
    contactsBtn.addEventListener("click", () => runAreaAction("contacts/collect", contactsBtn));
    viewBtn.addEventListener("click", () => viewContacts(viewBtn));

    discovery.append(
      el("label", { className: "pf-flabel", text: "Area" }), sel,
      el("div", { className: "or-discovery-actions" }, discoverBtn, contactsBtn, viewBtn)
    );
  }

  async function runAreaAction(path, btn) {
    if (!state.area) return;
    const key = "area:" + path + ":" + state.area;
    if (busy(key)) return;
    setBusy(key, true, btn);
    liveStatus.textContent = "Working…";
    const res = await fetchJSON("/api/prospects/" + encodeURIComponent(state.area) + "/" + path, { method: "POST", kind: "action", body: {} });
    setBusy(key, false, btn);
    if (res.ok) { liveStatus.textContent = "Done."; toast("Finished. Refreshing pipeline.", "info"); loadPipeline(); }
    else { liveStatus.textContent = ""; toast(errorMessage(res), "error"); }
  }

  async function viewContacts(triggerEl) {
    if (!state.area) return;
    const content = el("div", { className: "or-contacts" });
    const dlg = openSubDialog("Contact status — " + state.area, content, triggerEl);
    renderState(content, { kind: "loading" });
    const res = await fetchJSON("/api/prospects/" + encodeURIComponent(state.area) + "/contacts", { kind: "collection" });
    clear(content);
    if (!res.ok) { renderFetchFailure(content, res, () => { dlg.close(); viewContacts(triggerEl); }); return; }
    const rows = Array.isArray(res.data) ? res.data : (res.data && res.data.items) || [];
    if (!rows.length) { renderState(content, { kind: "empty", message: "No contacts collected for this area yet." }); return; }
    const ul = el("ul", { className: "tk-sec-list" });
    rows.forEach((c) => {
      const name = c.name || c.business_name || "Business";
      const status = c.contact_status || c.discovery_status || c.status || "";
      const email = c.email || (c.contact && c.contact.email) || "";
      ul.appendChild(el("li", { text: name + (status ? " — " + status : "") + (email ? " · " + email : "") }));
    });
    content.appendChild(ul);
  }

  // ---------------------------------------------------- pipeline

  async function loadPipeline() {
    const my = ++state.pipelineReq;
    renderState(pipelineArea, { kind: "loading" });
    const res = await fetchJSON("/api/outreach/pipeline", { kind: "collection" });
    if (my !== state.pipelineReq || !panel.isOpen()) return;
    if (!res.ok) { renderFetchFailure(pipelineArea, res, loadPipeline); return; }
    state.threads = flattenThreads(res.data);
    renderPipeline();
  }

  function flattenThreads(data) {
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.threads)) return data.threads;
    if (data && typeof data === "object") {
      const out = [];
      STAGES.forEach((s) => { if (Array.isArray(data[s])) data[s].forEach((t) => out.push(Object.assign({ stage: s }, t))); });
      if (out.length) return out;
    }
    return [];
  }

  function renderPipeline() {
    clear(pipelineArea);
    if (!state.threads.length) { renderState(pipelineArea, { kind: "empty", message: "No outreach threads yet. Run discovery, then create a thread." }); return; }
    const byStage = {};
    STAGES.forEach((s) => { byStage[s] = []; });
    const unknown = [];
    state.threads.forEach((t) => { (byStage[t.stage] || unknown).push(t); });

    const board = el("div", { className: "or-board" });
    STAGES.forEach((s) => board.appendChild(renderColumn(s, STAGE_LABEL[s], byStage[s])));
    if (unknown.length) board.appendChild(renderColumn("__unknown", "Unknown stage", unknown));
    pipelineArea.appendChild(board);
    liveStatus.textContent = state.threads.length + " thread" + (state.threads.length === 1 ? "" : "s") + ".";
  }

  function renderColumn(stage, label, threads) {
    const id = UI.uid("orcol");
    const col = el("section", { className: "or-col", "aria-labelledby": id });
    if (TERMINAL[stage] || stage === "__unknown") col.classList.add("or-col--terminal");
    col.appendChild(el("h3", { id: id, className: "or-col-head", text: label + " — " + threads.length }));
    threads.forEach((t) => {
      const openBtn = el("button", { className: "or-card", type: "button", text: threadBusiness(t) });
      openBtn.addEventListener("click", () => openThread(t.id, openBtn));
      col.appendChild(openBtn);
    });
    return col;
  }

  // ---------------------------------------------------- thread detail

  function openSubDialog(title, contentEl, triggerEl) {
    const titleId = UI.uid("orsub");
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
    const first = card.querySelector("input,select,textarea,button:not(.icon-btn)");
    const layer = UI.openNested({ card, backdrop, triggerEl, initialFocus: first || closeBtn });
    closeBtn.addEventListener("click", layer.close);
    return { close: layer.close, card: card };
  }

  const detail = { id: null, render: null, dlg: null };

  async function openThread(id, triggerEl) {
    detail.id = id;
    const content = el("div", { className: "or-thread" });
    detail.render = content;
    detail.dlg = openSubDialog("Outreach thread", content, triggerEl);
    loadThread(id);
  }

  async function loadThread(id) {
    if (!detail.render) return;
    const my = ++state.detailReq;
    renderState(detail.render, { kind: "loading" });
    const res = await fetchJSON("/api/outreach/threads/" + encodeURIComponent(id), { kind: "record" });
    if (my !== state.detailReq || !detail.render || !detail.render.isConnected) return;
    if (!res.ok) { renderFetchFailure(detail.render, res, () => loadThread(id)); return; }
    renderThread(res.data || {});
  }

  function renderThread(t) {
    const box = detail.render;
    clear(box);
    const email = threadContactEmail(t);
    box.appendChild(el("h4", { className: "tk-sec-title", text: threadBusiness(t) }));
    box.appendChild(el("p", { className: "or-thread-meta", text: "Stage: " + (STAGE_LABEL[t.stage] || t.stage || "—") +
      (threadContactName(t) ? " · Contact: " + threadContactName(t) : "") + (email ? " <" + email + ">" : "") }));
    if (t.discovery_status || t.contact_status) {
      box.appendChild(el("p", { className: "or-thread-meta", text:
        (t.discovery_status ? "Discovery: " + t.discovery_status + "  " : "") + (t.contact_status ? "Contact: " + t.contact_status : "") }));
    }

    // draft area
    box.appendChild(el("h5", { className: "tk-pack-sub", text: "Draft" }));
    const approved = threadDraftApproved(t);
    box.appendChild(el("span", {
      className: "tk-badge " + (approved ? "tk-badge--reviewed" : "tk-badge--draft"),
      text: approved ? "Approved by you" : "AI draft — not reviewed",
    }));
    box.appendChild(el("pre", { className: "tk-pack-letter", text: threadDraftBody(t) || "No draft yet." }));

    const actions = el("div", { className: "or-actions" });

    // Draft / Revise - dedicated endpoints, gated on ai.anthropic
    const hasDraft = threadHasDraft(t);
    const draftBtn = el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: hasDraft ? "Revise draft" : "Draft outreach" });
    draftBtn.addEventListener("click", () => hasDraft ? openRevise(t, draftBtn) : runDraft(t, draftBtn));
    UI.integrations.get().then(() => { if (!UI.integrations.ready("ai.anthropic")) { draftBtn.disabled = true; draftBtn.title = "Connect Claude (ai.anthropic) to enable"; } });
    actions.appendChild(draftBtn);

    // Approve - dedicated endpoint
    if (hasDraft && !approved) {
      const approveBtn = el("button", { className: "hud-btn hud-btn--accent", type: "button", text: "Approve draft" });
      approveBtn.addEventListener("click", () => runSimple(t, "approve", approveBtn, "Draft approved."));
      actions.appendChild(approveBtn);
    }

    // Open in email app - dedicated /mailto, guarded
    const mailBtn = el("button", { className: "hud-btn hud-btn--accent", type: "button", text: "Open in email app" });
    if (!approved || !email) { mailBtn.disabled = true; mailBtn.title = !email ? "No contact address on this thread" : "Approve the draft first"; }
    mailBtn.addEventListener("click", () => openInEmailApp(t, mailBtn));
    actions.appendChild(mailBtn);

    // Reopen (terminal only) / Opt-out - dedicated endpoints
    if (TERMINAL[t.stage] && t.stage !== "opted_out") {
      const reopenBtn = el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: "Reopen" });
      reopenBtn.addEventListener("click", () => runSimple(t, "reopen", reopenBtn, "Thread reopened."));
      actions.appendChild(reopenBtn);
    }
    if (t.stage !== "opted_out") {
      const optBtn = el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: "Opt this business out" });
      optBtn.addEventListener("click", async () => {
        const r = await confirm({
          title: "Opt this business out?", body: threadBusiness(t) + " will be excluded from all future outreach.",
          confirmLabel: "Opt out", danger: true, triggerEl: optBtn,
        });
        if (r.confirmed) runSimple(t, "opt-out", optBtn, "Business opted out.");
      });
      actions.appendChild(optBtn);
    }
    box.appendChild(actions);

    // Administrative /stage selector - restricted set only
    const targets = adminTargets(t.stage);
    if (targets.length) {
      const row = el("div", { className: "or-stage-row" });
      const sel = el("select", { className: "or-stage-select", "aria-label": "Administrative stage change" });
      sel.appendChild(el("option", { value: "", text: "Administrative stage change…" }));
      targets.forEach((s) => sel.appendChild(el("option", { value: s, text: STAGE_LABEL[s] || s })));
      const moveBtn = el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: "Apply" });
      moveBtn.addEventListener("click", async () => {
        const to = sel.value;
        if (!to) return;
        const r = await confirm({
          title: "Change thread stage?", body: threadBusiness(t) + ": " + (STAGE_LABEL[t.stage] || t.stage) + " → " + (STAGE_LABEL[to] || to) + ".",
          confirmLabel: "Move to " + (STAGE_LABEL[to] || to), danger: TERMINAL[to] === 1, triggerEl: moveBtn,
        });
        if (!r.confirmed) return;
        if (busy("orstage:" + t.id)) return;
        setBusy("orstage:" + t.id, true, moveBtn);
        const res = await fetchJSON("/api/outreach/threads/" + encodeURIComponent(t.id) + "/stage", { method: "POST", kind: "action", body: { to_stage: to } });
        setBusy("orstage:" + t.id, false, moveBtn);
        if (res.ok) { toast("Stage updated.", "info"); loadThread(t.id); loadPipeline(); }
        else toast(errorMessage(res), "error");
      });
      row.append(sel, moveBtn);
      box.appendChild(row);
      box.appendChild(el("p", { className: "tk-pack-hint", text: "Contacting, replying, approving and reopening use their own buttons above — not this selector." }));
    }
  }

  async function runDraft(t, btn) {
    if (!(await UI.integrations.gate("ai.anthropic", "outreach drafting", btn))) return;
    if (busy("ordraft:" + t.id)) return;
    setBusy("ordraft:" + t.id, true, btn);
    const res = await fetchJSON("/api/outreach/threads/" + encodeURIComponent(t.id) + "/draft", { method: "POST", kind: "action", body: {} });
    setBusy("ordraft:" + t.id, false, btn);
    if (res.ok) { toast("Draft created.", "info"); loadThread(t.id); }
    else toast(errorMessage(res), "error");
  }

  function openRevise(t, triggerEl) {
    const form = el("form", { className: "tk-revise-form" });
    const fb = el("textarea", { className: "tk-revise-fb", rows: 4, required: true, placeholder: "What should change?", "aria-label": "Feedback" });
    const status = el("span", { className: "refresh-status", role: "status", "aria-live": "polite" });
    const submit = el("button", { className: "hud-btn hud-btn--accent", type: "submit", text: "Revise draft" });
    form.append(el("label", { className: "tk-pack-flabel", text: "Feedback" }), fb, el("div", { className: "tk-create-actions" }, submit, status));
    const dlg = openSubDialog("Revise draft", form, triggerEl);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!fb.value.trim()) { status.textContent = "Feedback is required."; return; }
      if (!(await UI.integrations.gate("ai.anthropic", "outreach drafting", submit))) return;
      if (busy("orrev:" + t.id)) return;
      setBusy("orrev:" + t.id, true, submit);
      status.textContent = "Revising…";
      const res = await fetchJSON("/api/outreach/threads/" + encodeURIComponent(t.id) + "/revise", { method: "POST", kind: "action", body: { feedback: fb.value.trim() } });
      setBusy("orrev:" + t.id, false, submit);
      if (res.ok) { dlg.close(); toast("Draft revised.", "info"); loadThread(t.id); }
      else status.textContent = errorMessage(res);
    });
  }

  async function runSimple(t, path, btn, okMsg) {
    if (busy("or:" + path + ":" + t.id)) return;
    setBusy("or:" + path + ":" + t.id, true, btn);
    const res = await fetchJSON("/api/outreach/threads/" + encodeURIComponent(t.id) + "/" + path, { method: "POST", kind: "action", body: {} });
    setBusy("or:" + path + ":" + t.id, false, btn);
    if (res.ok) { toast(okMsg, "info"); loadThread(t.id); loadPipeline(); }
    else toast(errorMessage(res), "error");
  }

  // ---------------------------------------------------- mailto (guarded)

  // Strict validation of the server's mailto_url before navigating.
  function validateMailto(url, contactEmail) {
    if (typeof url !== "string") return { ok: false, reason: "The server didn't return a mailto link." };
    const trimmed = url.trim();
    if (!/^mailto:/i.test(trimmed)) return { ok: false, reason: "That link isn't a mailto: link." };
    const rest = trimmed.slice(trimmed.indexOf(":") + 1);
    const qIdx = rest.indexOf("?");
    const recipientRaw = qIdx === -1 ? rest : rest.slice(0, qIdx);
    const queryRaw = qIdx === -1 ? "" : rest.slice(qIdx + 1);
    let recipient;
    try { recipient = decodeURIComponent(recipientRaw); } catch (e) { return { ok: false, reason: "The recipient address couldn't be decoded." }; }
    if (!recipient) return { ok: false, reason: "The mailto link has no recipient." };
    if (/[\r\n,;\s]/.test(recipient)) return { ok: false, reason: "The recipient address looks malformed or contains more than one address." };
    if (!contactEmail) return { ok: false, reason: "This thread has no contact address to check against." };
    if (recipient.toLowerCase() !== String(contactEmail).trim().toLowerCase()) return { ok: false, reason: "The mailto recipient doesn't match this thread's contact." };
    if (queryRaw) {
      const pairs = queryRaw.split("&").filter(Boolean);
      const seen = {};
      for (let i = 0; i < pairs.length; i++) {
        const eq = pairs[i].indexOf("=");
        const key = (eq === -1 ? pairs[i] : pairs[i].slice(0, eq)).toLowerCase();
        if (key !== "subject" && key !== "body") return { ok: false, reason: "The mailto link has a disallowed parameter (" + key + ")." };
        if (seen[key]) return { ok: false, reason: "The mailto link repeats a parameter." };
        seen[key] = true;
      }
    }
    return { ok: true };
  }

  async function openInEmailApp(t, btn) {
    const email = threadContactEmail(t);
    if (!threadDraftApproved(t)) { toast("Approve the draft first.", "error"); return; }
    if (!email) { toast("This thread has no contact address.", "error"); return; }

    const r = await confirm({
      title: "Open in email app",
      body: "Open this draft in your email app? It will be addressed to " + threadContactName(t) + " <" + email + "> at " + threadBusiness(t) + ". Nothing is sent automatically.",
      confirmLabel: "Open in email app", triggerEl: btn,
    });
    if (!r.confirmed) return;
    if (busy("ormailto:" + t.id)) return;
    setBusy("ormailto:" + t.id, true, btn);
    const res = await fetchJSON("/api/outreach/threads/" + encodeURIComponent(t.id) + "/mailto", { method: "POST", kind: "action", body: {} });
    setBusy("ormailto:" + t.id, false, btn);
    if (!res.ok) { toast(errorMessage(res), "error"); return; }

    const url = res.data && res.data.mailto_url;
    const check = validateMailto(url, email);
    if (!check.ok) { toast("Not opening the email app: " + check.reason, "error"); return; }

    // Reliable after an async fetch (no popup that could be blocked).
    window.location.href = url;
    toast("Draft opened in your email client — nothing was sent.", "info");
    loadThread(t.id);
    loadPipeline();
  }

  // ---------------------------------------------------- wire-up

  const btn = document.getElementById("outreach-btn");
  if (btn) btn.addEventListener("click", () => panel.open(btn));
  UI.registerSection("outreach", () => panel.open(btn));

  UI.outreach = {
    openPanel: (trig) => panel.open(trig || btn),
    openThreadById: (id, trig) => { panel.open(trig || btn); setTimeout(() => openThread(id, trig || btn), 60); },
    listThreads: async () => {
      const res = await fetchJSON("/api/outreach/threads", { kind: "collection" });
      if (!res.ok) return { error: res };
      return { threads: flattenThreads(res.data) };
    },
    businessName: threadBusiness,
  };
})();
