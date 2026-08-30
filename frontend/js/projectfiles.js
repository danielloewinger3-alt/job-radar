// Job Radar - project-file / file-management workstream.
//
// Per-project file store: upload (with progress), list, describe, toggle AI
// context, download, delete. Consumes Agent A's canonical nested routes under
// /api/projects/{project_id}/files. The server is authoritative for extension,
// count and storage limits; the client only does a broad accept hint and a
// 50 MB convenience pre-check.
//
// Uploaded file bytes are never rendered. Names are text only. No SVG/HTML/CAD/
// archive preview. Backend `detail` is never shown raw.
(function () {
  "use strict";

  const UI = window.JobRadarUI;
  if (!UI) { console.error("projectfiles.js: JobRadarUI missing"); return; }
  const { el, clear, fetchJSON, renderState, renderFetchFailure, confirm, toast } = UI;

  const MAX_CLIENT_BYTES = 50 * 1024 * 1024;
  const ACCEPT_HINT = ".pdf,.txt,.md,.csv,.tsv,.json,.rtf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.odt," +
    ".png,.jpg,.jpeg,.gif,.webp,.svg,.zip,.tar,.gz,.tgz," +
    ".step,.stp,.stl,.iges,.igs,.dwg,.dxf,.3mf,.f3d,.sldprt,.ipt";

  const STATUS_COPY = {
    400: "That request or filename wasn't valid.",
    404: "That project or file no longer exists.",
    409: "This project already has the maximum number of files.",
    413: "That file is over the size or storage limit.",
    415: "That file type isn't accepted.",
    422: "The file didn't pass validation.",
  };
  function statusMessage(res) {
    if (res.status && STATUS_COPY[res.status]) {
      let msg = STATUS_COPY[res.status];
      if (res.message && typeof res.message === "string") msg += " " + res.message; // res.message is already safe-filtered
      return msg;
    }
    if (res.unavailable) return "The file store isn't available in this build yet.";
    if (res.forbidden) return "You don't have access to this project's files.";
    return res.message || "Something went wrong with that file.";
  }

  // Agent A's canonical extract_status set: "ok" | "truncated" (readable) and
  // "unsupported" | "empty" | "error" (stored, not read by AI).
  function extractionLabel(status) {
    const s = String(status || "").toLowerCase();
    if (/^(ok|truncated)$/.test(s)) return { text: "Text extraction available", cls: "ok" };
    return { text: "Stored attachment — not read by AI", cls: "off" };
  }
  function isReadable(status) { return /^(ok|truncated)$/i.test(String(status || "")); }

  function normalizeFile(raw) {
    if (!raw || typeof raw !== "object") throw new UI.ContractError("project-file was not an object");
    // Agent A's serialiser keys the id as `file_id`.
    const fid = raw.file_id != null ? raw.file_id : raw.id;
    if (fid == null) throw new UI.ContractError("project-file missing required 'file_id'");
    return {
      id: fid,
      original_name: typeof raw.original_name === "string" ? raw.original_name : "file",
      extension: raw.extension || "",
      byte_size: raw.byte_size != null ? raw.byte_size : null,
      extract_status: raw.extract_status || "",
      ai_context_enabled: raw.ai_context_enabled === true,
      description: typeof raw.description === "string" ? raw.description : "",
      created_at: raw.created_at || null,
      raw: raw,
    };
  }

  const state = {
    projects: [],
    projectId: "",
    files: [],
    filesReq: 0,
    inflight: new Set(),
  };
  function busy(k) { return state.inflight.has(k); }
  function setBusy(k, on, btn) {
    if (on) state.inflight.add(k); else state.inflight.delete(k);
    if (btn) { btn.disabled = on; if (on) btn.setAttribute("aria-busy", "true"); else btn.removeAttribute("aria-busy"); }
  }

  const panel = UI.makePanel({
    mount: document.getElementById("projectfiles-root") || document.body,
    eyebrow: "Attachments",
    title: "Project files",
    onOpen: loadProjects,
  });

  const picker = el("div", { className: "pf-picker" });
  const uploadZone = el("div", { className: "pf-upload" });
  const liveStatus = el("div", { className: "jr-live", role: "status", "aria-live": "polite" });
  const listArea = el("div", { className: "pf-list" });
  panel.body.append(picker, uploadZone, liveStatus, listArea);

  async function loadProjects() {
    clear(picker);
    renderState(listArea, { kind: "loading" });
    const res = await fetchJSON("/api/projects", { kind: "collection" });
    if (!panel.isOpen()) return;
    if (!res.ok) { renderFetchFailure(listArea, res, loadProjects); return; }
    state.projects = Array.isArray(res.data) ? res.data : [];
    if (!state.projects.length) {
      picker.appendChild(el("p", { className: "tk-sec-empty", text: "No projects yet. Add one in the Dossier first." }));
      clear(listArea); clear(uploadZone);
      return;
    }
    const sel = el("select", { className: "pf-project-select", "aria-label": "Project" });
    sel.appendChild(el("option", { value: "", text: "Choose a project…" }));
    state.projects.forEach((p) => sel.appendChild(el("option", { value: String(p.id), text: p.title || ("Project " + p.id) })));
    sel.value = state.projectId;
    sel.addEventListener("change", () => { state.projectId = sel.value; renderUploadZone(); loadFiles(); });
    picker.append(el("label", { className: "pf-flabel", text: "Project" }), sel);
    renderUploadZone();
    if (state.projectId) loadFiles();
    else { clear(listArea); }
  }

  function renderUploadZone() {
    clear(uploadZone);
    if (!state.projectId) { return; }
    const input = el("input", { type: "file", accept: ACCEPT_HINT, "aria-label": "File to upload" });
    const btn = el("button", { className: "hud-btn hud-btn--accent", type: "button", text: "Upload" });
    const bar = el("div", { className: "pf-progress", hidden: true }, el("span", { className: "pf-progress-fill" }));
    const err = el("p", { className: "pf-error", role: "alert", hidden: true });
    btn.addEventListener("click", () => doUpload(input, btn, bar, err));
    uploadZone.append(input, btn, bar, err);
  }

  function doUpload(input, btn, bar, err) {
    err.hidden = true; err.textContent = "";
    const file = input.files && input.files[0];
    if (!file) { err.hidden = false; err.textContent = "Choose a file first."; return; }
    if (file.size > MAX_CLIENT_BYTES) { err.hidden = false; err.textContent = "That file is larger than 50 MB."; return; }
    if (busy("upload:" + state.projectId)) return;
    setBusy("upload:" + state.projectId, true, btn);

    const fd = new FormData();
    fd.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/projects/" + encodeURIComponent(state.projectId) + "/files");
    const fill = bar.querySelector(".pf-progress-fill");
    bar.hidden = false; fill.style.width = "0%";
    let lastAnnounced = -1;
    xhr.upload.addEventListener("progress", (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      fill.style.width = pct + "%";
      const milestone = pct >= 100 ? 100 : Math.floor(pct / 25) * 25;
      if (milestone !== lastAnnounced) { lastAnnounced = milestone; liveStatus.textContent = "Uploading… " + milestone + "%"; }
    });
    xhr.addEventListener("load", () => {
      setBusy("upload:" + state.projectId, false, btn);
      bar.hidden = true;
      if (xhr.status >= 200 && xhr.status < 300) {
        input.value = "";
        liveStatus.textContent = "Upload complete.";
        toast("File uploaded.", "info");
        loadFiles();
      } else {
        err.hidden = false;
        err.textContent = statusMessage(classifyXhr(xhr));
        liveStatus.textContent = "Upload failed.";
      }
    });
    xhr.addEventListener("error", () => {
      setBusy("upload:" + state.projectId, false, btn);
      bar.hidden = true;
      err.hidden = false; err.textContent = "Upload failed on the network — try again.";
    });
    xhr.send(fd);
  }

  // Reduce an XHR error to the same shape statusMessage() expects, applying the
  // same safe-detail filtering as fetchJSON.
  function classifyXhr(xhr) {
    let data = null;
    try { if ((xhr.getResponseHeader("content-type") || "").indexOf("application/json") !== -1) data = JSON.parse(xhr.responseText); } catch (e) {}
    let msg = null;
    if (data && typeof data === "object") {
      const cand = typeof data.detail === "string" ? data.detail
        : (data.detail && typeof data.detail === "object" && typeof data.detail.message === "string" ? data.detail.message
          : (typeof data.message === "string" ? data.message : null));
      if (cand && cand.length <= 160 && !/[<>]/.test(cand) && !/Traceback|File "| line \d+|Exception/.test(cand)) msg = cand;
    }
    const detail = (data && typeof data.detail === "string") ? data.detail.trim().toLowerCase() : null;
    const isDefault404 = !data || detail === "not found";
    const isDefault405 = detail === "method not allowed" || (!data && xhr.status === 405);
    if (xhr.status === 404) return { unavailable: isDefault404, status: 404, message: isDefault404 ? null : msg };
    if (xhr.status === 405 && isDefault405) return { unavailable: true, status: 405, message: null };
    if (xhr.status >= 500) return { error: true, retryable: true, message: "Server error — try again." };
    return { status: xhr.status, message: msg };
  }

  async function loadFiles() {
    if (!state.projectId) return;
    const my = ++state.filesReq;
    renderState(listArea, { kind: "loading" });
    const res = await fetchJSON("/api/projects/" + encodeURIComponent(state.projectId) + "/files", { kind: "collection" });
    if (my !== state.filesReq || !panel.isOpen()) return;
    if (!res.ok) { renderFetchFailure(listArea, res, loadFiles); return; }
    try {
      const list = res.data && Array.isArray(res.data.files) ? res.data.files : null;
      if (!list) throw new UI.ContractError("project-files collection missing 'files' array");
      state.files = list.map(normalizeFile);
    } catch (e) {
      console.error(e);
      renderState(listArea, { kind: "error", message: "The file list wasn't in the expected shape (contract mismatch)." });
      return;
    }
    renderFiles();
  }

  function renderFiles() {
    clear(listArea);
    if (!state.files.length) { renderState(listArea, { kind: "empty", message: "No files in this project yet." }); return; }
    state.files.forEach((f) => listArea.appendChild(renderFileRow(f)));
    liveStatus.textContent = state.files.length + " file" + (state.files.length === 1 ? "" : "s") + ".";
  }

  function renderFileRow(f) {
    const row = el("div", { className: "pf-row" });
    row.appendChild(el("div", { className: "pf-name", text: f.original_name }));
    const meta = el("div", { className: "pf-meta" });
    meta.appendChild(el("span", { text: (f.extension || "?") + " · " + UI.fmtBytes(f.byte_size) }));
    const ex = extractionLabel(f.extract_status);
    meta.appendChild(el("span", { className: "pf-extract pf-extract--" + ex.cls, text: ex.text }));
    row.appendChild(meta);

    // description + ai context toggle
    const form = el("div", { className: "pf-editrow" });
    const desc = el("input", { type: "text", className: "pf-desc", maxLength: 300, value: f.description, placeholder: "Description", "aria-label": "Description for " + f.original_name });
    const aiWrap = el("label", { className: "pf-aitoggle" });
    const aiCb = el("input", { type: "checkbox" });
    aiCb.checked = f.ai_context_enabled;
    if (!isReadable(f.extract_status)) { aiCb.disabled = true; aiWrap.title = "Only AI-readable files can be used as context"; }
    aiWrap.append(aiCb, " Use as AI context");
    const saveBtn = el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: "Save" });
    const st = el("span", { className: "refresh-status", role: "status", "aria-live": "polite" });
    saveBtn.addEventListener("click", async () => {
      const key = "patch:" + f.id;
      if (busy(key)) return;
      setBusy(key, true, saveBtn);
      st.textContent = "Saving…";
      const res = await fetchJSON("/api/projects/" + encodeURIComponent(state.projectId) + "/files/" + encodeURIComponent(f.id), {
        method: "PATCH", kind: "action", body: { description: desc.value, ai_context_enabled: aiCb.checked },
      });
      setBusy(key, false, saveBtn);
      if (res.ok) { st.textContent = "Saved"; f.description = desc.value; f.ai_context_enabled = aiCb.checked; }
      else st.textContent = statusMessage(res);
    });
    form.append(desc, aiWrap, saveBtn, st);
    row.appendChild(form);

    // actions
    const actions = el("div", { className: "pf-actions" });
    const dl = el("a", {
      className: "hud-btn hud-btn--ghost", text: "Download",
      href: UI.safeUrl(location.origin + "/api/projects/" + encodeURIComponent(state.projectId) + "/files/" + encodeURIComponent(f.id) + "/download"),
    });
    dl.setAttribute("rel", "noopener");
    const del = el("button", { className: "hud-btn hud-btn--ghost", type: "button", text: "Delete" });
    del.addEventListener("click", async () => {
      const r = await confirm({
        title: "Delete this file?", body: f.original_name + " will be removed from this project.",
        confirmLabel: "Delete file", danger: true, triggerEl: del,
      });
      if (!r.confirmed) return;
      if (busy("del:" + f.id)) return;
      setBusy("del:" + f.id, true, del);
      const res = await fetchJSON("/api/projects/" + encodeURIComponent(state.projectId) + "/files/" + encodeURIComponent(f.id), {
        method: "DELETE", kind: "action",
      });
      setBusy("del:" + f.id, false, del);
      if (res.ok) { toast("File deleted.", "info"); loadFiles(); }
      else toast(statusMessage(res), "error");
    });
    actions.append(dl, del);
    row.appendChild(actions);
    return row;
  }

  const btn = document.getElementById("projectfiles-btn");
  if (btn) btn.addEventListener("click", () => panel.open(btn));
  UI.registerSection("files", () => panel.open(btn));
})();
