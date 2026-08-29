(function () {
  "use strict";

  const SRC_LABEL = {
    greenhouse: "Greenhouse", lever: "Lever", remoteok: "RemoteOK",
    adzuna: "Adzuna", reed: "Reed", usajobs: "USAJobs",
  };
  function srcColor(source) { return getComputedStyle(document.documentElement).getPropertyValue("--src-" + source).trim() || "#2fe7c4"; }
  function srcVar(source) { return "var(--src-" + source + ")"; }

  const map = L.map("map", { worldCopyJump: true, zoomControl: true }).setView([35, -30], 3);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; OpenStreetMap contributors',
    maxZoom: 19,
  }).addTo(map);

  const els = {
    search: document.getElementById("search"),
    toggleEu: document.getElementById("toggle-eu"),
    refreshBtn: document.getElementById("refresh-btn"),
    refreshStatus: document.getElementById("refresh-status"),
    statTotal: document.getElementById("stat-total"),
    statNew: document.getElementById("stat-new"),
    clock: document.getElementById("clock"),
    remotePin: document.getElementById("remote-pin"),
    remoteCount: document.getElementById("remote-count"),
    panel: document.getElementById("panel"),
    panelBackdrop: document.getElementById("panel-backdrop"),
    panelCity: document.getElementById("panel-city"),
    panelCount: document.getElementById("panel-count"),
    panelList: document.getElementById("panel-list"),
    panelNetwork: document.getElementById("panel-network"),
    networkCanvas: document.getElementById("network-canvas"),
    networkReadout: document.getElementById("network-readout"),
    networkLegend: document.getElementById("network-legend"),
    panelClose: document.getElementById("panel-close"),

    hubBtn: document.getElementById("hub-btn"),
    hubPanel: document.getElementById("hub-panel"),
    hubBackdrop: document.getElementById("hub-backdrop"),
    hubClose: document.getElementById("hub-close"),
    hubCount: document.getElementById("hub-count"),
    cvForm: document.getElementById("cv-form"),
    cvFile: document.getElementById("cv-file"),
    cvLabel: document.getElementById("cv-label"),
    cvRoleType: document.getElementById("cv-role-type"),
    cvFormStatus: document.getElementById("cv-form-status"),
    cvGrid: document.getElementById("cv-grid"),
    projectForm: document.getElementById("project-form"),
    projectTitle: document.getElementById("project-title"),
    projectTags: document.getElementById("project-tags"),
    projectLink: document.getElementById("project-link"),
    projectDescription: document.getElementById("project-description"),
    projectFormStatus: document.getElementById("project-form-status"),
    projectGrid: document.getElementById("project-grid"),
    hubCvsTab: document.getElementById("hub-cvs"),
    hubProjectsTab: document.getElementById("hub-projects"),
    hubGithubTab: document.getElementById("hub-github"),
    hubProfileTab: document.getElementById("hub-profile"),
    hubTabs: document.getElementById("hub-tabs"),
    hubDialIndicator: document.getElementById("hub-dial-indicator"),
    githubStatus: document.getElementById("github-status"),
    githubGrid: document.getElementById("github-grid"),
    profileForm: document.getElementById("profile-form"),
    profileFullName: document.getElementById("profile-full-name"),
    profileEmail: document.getElementById("profile-email"),
    profilePhone: document.getElementById("profile-phone"),
    profileLinkedin: document.getElementById("profile-linkedin"),
    profileLocation: document.getElementById("profile-location"),
    profileFormStatus: document.getElementById("profile-form-status"),

    jobModal: document.getElementById("job-modal"),
    jobBackdrop: document.getElementById("job-backdrop"),
    jobModalClose: document.getElementById("job-modal-close"),
    jobModalCompany: document.getElementById("job-modal-company"),
    jobModalTitle: document.getElementById("job-modal-title"),
    jobModalMeta: document.getElementById("job-modal-meta"),
    jobModalDescription: document.getElementById("job-modal-description"),
    jobModalNotesInput: document.getElementById("job-modal-notes-input"),
    jobModalNotesSave: document.getElementById("job-modal-notes-save"),
    jobModalNotesStatus: document.getElementById("job-modal-notes-status"),
    jobModalView: document.getElementById("job-modal-view"),
    jobModalApplyBtn: document.getElementById("job-modal-apply-btn"),
    jobModalApply: document.getElementById("job-modal-apply"),
    jobModalApplyCv: document.getElementById("job-modal-apply-cv"),
    jobModalApplyStatus: document.getElementById("job-modal-apply-status"),
    jobModalApplyNote: document.getElementById("job-modal-apply-note"),
    jobModalGenerateBtn: document.getElementById("job-modal-generate-btn"),
    jobModalApplyResult: document.getElementById("job-modal-apply-result"),
    jobModalCoverLetter: document.getElementById("job-modal-cover-letter"),
    jobModalReviewNotesText: document.getElementById("job-modal-review-notes-text"),
    jobModalFeedback: document.getElementById("job-modal-feedback"),
    jobModalReviseBtn: document.getElementById("job-modal-revise-btn"),

    prospectsBtn: document.getElementById("prospects-btn"),
    prospectsPanel: document.getElementById("prospects-panel"),
    prospectsBackdrop: document.getElementById("prospects-backdrop"),
    prospectsClose: document.getElementById("prospects-close"),
    prospectsStats: document.getElementById("prospects-stats"),
    prospectsBackBtn: document.getElementById("prospects-back-btn"),
    prospectsCityControls: document.getElementById("prospects-city-controls"),
    prospectsSidebar: document.getElementById("prospects-sidebar"),
    prospectsScanBtn: document.getElementById("prospects-scan-btn"),
    prospectsAnalyzeBtn: document.getElementById("prospects-analyze-btn"),
    prospectsStatus: document.getElementById("prospects-status"),
    prospectsMapEl: document.getElementById("prospects-map"),

    newsBtn: document.getElementById("news-btn"),
    newsPanel: document.getElementById("news-panel"),
    newsBackdrop: document.getElementById("news-backdrop"),
    newsClose: document.getElementById("news-close"),
    newsStats: document.getElementById("news-stats"),
    newsTabs: document.getElementById("news-tabs"),
    newsDialIndicator: document.getElementById("news-dial-indicator"),
    newsList: document.getElementById("news-list"),

    alfredBtn: document.getElementById("alfred-btn"),
    alfredReadout: document.getElementById("alfred-readout"),
    alfredReadoutLabel: document.getElementById("alfred-readout-label"),
    alfredReadoutText: document.getElementById("alfred-readout-text"),
  };

  const state = {
    cities: [],          // raw /api/cities rows
    markers: {},         // city.key -> Leaflet marker
    showEu: true,
    openBubble: null,    // { key, label, jobs }
    mode: "list",
    filterText: "",
    cvs: [],
    projects: [],
    githubConfigured: false,
    githubRepos: [],
    currentJob: null,
    currentApplication: null,
    prospectAreas: [],
    prospectSectors: {},
    newsCategories: [],
    newsCurrentCategory: "",
    prospectCategories: [],
    prospectsCurrentArea: null,
    businesses: [],
  };

  let net = null;
  let minimap = null;

  // ---------- utility ----------

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }

  // Externally supplied links only. Returns an absolute http(s) URL string, or
  // "#" for anything else: other schemes (javascript:, data:, blob:, file:,
  // mailto:, tel:), malformed strings, ordinary relative text, root-relative
  // paths, scheme-relative URLs, empty/whitespace, and non-strings.
  // Parsed with NO base URL on purpose, so "not a url" or "/path" can never be
  // coerced into a same-origin link — they throw and collapse to "#".
  function safeUrl(raw) {
    if (typeof raw !== "string") return "#";
    const trimmed = raw.trim();
    if (!trimmed) return "#";
    let parsed;
    try {
      parsed = new URL(trimmed); // no second arg — relative/garbage throws here
    } catch (e) {
      return "#";
    }
    return (parsed.protocol === "http:" || parsed.protocol === "https:") ? parsed.href : "#";
  }

  // Normalize an externally supplied website field before validation: accept an
  // existing absolute http(s) URL as-is, prepend https:// only when the value
  // plausibly looks like a bare hostname/domain, and reject everything else
  // (non-strings, empty, other schemes, arbitrary text) via safeUrl.
  function safeWebsiteUrl(raw) {
    if (typeof raw !== "string") return "#";
    const trimmed = raw.trim();
    if (!trimmed) return "#";
    if (/^https?:\/\//i.test(trimmed)) return safeUrl(trimmed);
    // Bare hostname/domain: letters/digits/hyphen labels, a dot, a TLD, then an
    // optional path/query/fragment. No scheme, no whitespace, no colon.
    if (/^[a-z0-9-]+(\.[a-z0-9-]+)+([\/?#].*)?$/i.test(trimmed)) return safeUrl("https://" + trimmed);
    return "#";
  }

  function truncate(str, n) { return str.length > n ? str.slice(0, n - 1) + "…" : str; }

  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const animateNumberTokens = new WeakMap();

  function animateNumber(el, newValue) {
    const to = Number(newValue) || 0;
    if (prefersReducedMotion) { el.textContent = to; return; }
    const from = parseInt(el.textContent, 10) || 0;
    if (from === to) { el.textContent = to; return; }
    const token = (animateNumberTokens.get(el) || 0) + 1;
    animateNumberTokens.set(el, token);
    const duration = 450;
    const start = performance.now();
    function step(now) {
      if (animateNumberTokens.get(el) !== token) return; // a newer update superseded this one
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = Math.round(from + (to - from) * eased);
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function groupCompanies(jobs) {
    const order = [];
    const map = new Map();
    jobs.forEach(job => {
      if (!map.has(job.company)) { map.set(job.company, []); order.push(job.company); }
      map.get(job.company).push(job);
    });
    return order.map(company => ({ company, jobs: map.get(company) }));
  }

  // ---------- bubble minimap ----------

  function mountMinimap(lat, lon) {
    destroyMinimap();
    minimap = L.map("panel-minimap", {
      zoomControl: false, attributionControl: false,
      dragging: false, scrollWheelZoom: false, doubleClickZoom: false,
      boxZoom: false, keyboard: false, touchZoom: false, tap: false,
    }).setView([lat, lon], 12);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", { maxZoom: 19 }).addTo(minimap);
    L.circleMarker([lat, lon], { radius: 5, color: "#2fe7c4", weight: 1.5, fillColor: "#2fe7c4", fillOpacity: 0.9 }).addTo(minimap);
  }

  function destroyMinimap() {
    if (minimap) { minimap.remove(); minimap = null; }
    const el = document.getElementById("panel-minimap");
    if (el) el._leaflet_id = null;
  }

  // ---------- markers ----------

  function pinIcon(city) {
    const unseen = city.unseen_jobs;
    const size = city.total_jobs === 0 ? 14 : Math.min(38, 16 + city.total_jobs * 2.4);
    const classes = ["pin-icon"];
    if (unseen > 0) classes.push("has-new");
    if (city.total_jobs === 0) classes.push("pin-dim");
    if (city.tier === "eu") classes.push("tier-eu");
    return L.divIcon({
      className: "",
      html:
        '<div class="' + classes.join(" ") + '" style="width:' + size + 'px;height:' + size + 'px;">' +
          '<span class="pin-pulse"></span>' +
          '<span class="pin-dot"></span>' +
          (unseen > 0 ? '<span class="pin-count">' + unseen + '</span>' : '') +
        '</div>',
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
    });
  }

  async function loadCities() {
    let rows;
    try {
      const res = await fetch("/api/cities");
      if (!res.ok) throw new Error("HTTP " + res.status);
      rows = await res.json();
    } catch (e) {
      console.error("loadCities failed", e);
      els.refreshStatus.textContent = "Map data failed to load";
      return; // keep existing markers, state.cities, and stats intact
    }
    state.cities = rows;

    Object.values(state.markers).forEach(m => map.removeLayer(m));
    state.markers = {};

    state.cities.forEach(city => {
      if (city.tier === "eu" && !state.showEu) return;
      const marker = L.marker([city.lat, city.lon], { icon: pinIcon(city) }).addTo(map);
      marker.bindTooltip(city.label + " — " + city.total_jobs + " job" + (city.total_jobs === 1 ? "" : "s"));
      marker.on("click", () => openBubble(city.key, city.label));
      state.markers[city.key] = marker;
    });

    renderStats();
  }

  async function loadRemoteSummary() {
    let jobs;
    try {
      const res = await fetch("/api/jobs?remote=true");
      if (!res.ok) throw new Error("HTTP " + res.status);
      jobs = await res.json();
    } catch (e) {
      console.error("loadRemoteSummary failed", e);
      els.refreshStatus.textContent = "Remote data failed to load";
      return; // keep the existing remote pin/count and totals intact
    }
    const unseen = jobs.filter(j => !j.seen).length;
    els.remotePin.classList.toggle("has-new", unseen > 0);
    if (unseen > 0) { els.remoteCount.hidden = false; els.remoteCount.textContent = unseen; }
    else { els.remoteCount.hidden = true; }
    state.remoteTotal = jobs.length;
    state.remoteUnseen = unseen;
    renderStats();
  }

  function renderStats() {
    const citiesTotal = state.cities.reduce((s, c) => s + c.total_jobs, 0);
    const citiesUnseen = state.cities.reduce((s, c) => s + c.unseen_jobs, 0);
    animateNumber(els.statTotal, citiesTotal + (state.remoteTotal || 0));
    animateNumber(els.statNew, citiesUnseen + (state.remoteUnseen || 0));
  }

  // ---------- bubble open/close ----------

  async function openBubble(key, label) {
    closeHub();
    closeProspects();
    closeNews();
    state.mode = "list";
    document.querySelectorAll("[data-mode]").forEach(b => {
      const active = b.dataset.mode === "list";
      b.classList.toggle("active", active);
      b.setAttribute("aria-selected", String(active));
    });
    els.panelList.hidden = false;
    els.panelNetwork.hidden = true;
    stopNetwork();

    const url = key === "remote" ? "/api/jobs?remote=true" : "/api/jobs?city=" + encodeURIComponent(key);
    let jobs = [], loadError = false;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error("HTTP " + res.status);
      jobs = await res.json();
    } catch (e) {
      console.error("openBubble fetch failed", e);
      loadError = true;
    }
    state.openBubble = { key, label, jobs, loadError };

    renderCasefile();
    const city = state.cities.find(c => c.key === key);
    if (city) mountMinimap(city.lat, city.lon);
    else destroyMinimap();

    els.panel.classList.add("open");
    els.panelBackdrop.classList.add("open");
  }

  function closeBubble() {
    state.openBubble = null;
    els.panel.classList.remove("open");
    els.panelBackdrop.classList.remove("open");
    stopNetwork();
    destroyMinimap();
  }

  // ---------- list view ----------

  function matchesFilter(job) {
    if (!state.filterText) return true;
    const q = state.filterText;
    return job.title.toLowerCase().includes(q) || job.company.toLowerCase().includes(q);
  }

  function renderCasefile() {
    const bubble = state.openBubble;
    if (!bubble) return;
    els.panelCity.textContent = bubble.label;
    const unseen = bubble.jobs.filter(j => !j.seen).length;
    els.panelCount.textContent = bubble.jobs.length + " signal" + (bubble.jobs.length === 1 ? "" : "s") + (unseen ? " · " + unseen + " new" : "");
    renderList();
  }

  function renderList() {
    const bubble = state.openBubble;
    if (!bubble) return;
    els.panelList.innerHTML = "";

    const visibleJobs = bubble.jobs.filter(matchesFilter);
    if (visibleJobs.length === 0) {
      const msg = bubble.loadError
        ? "Couldn't load roles for this city. Close and try again."
        : (bubble.jobs.length === 0 ? "No signals detected yet." : "No roles match that filter.");
      els.panelList.innerHTML = '<div class="empty-state">' + msg + '</div>';
      return;
    }

    groupCompanies(visibleJobs).forEach(group => {
      const unseenN = group.jobs.filter(j => !j.seen).length;
      const card = document.createElement("div");
      card.className = "company-card";
      card.innerHTML =
        '<button class="company-head" type="button">' +
          '<span class="company-name">' + escapeHtml(group.company) + '</span>' +
          '<span class="company-sub"><span class="chevron">▾</span>' + group.jobs.length + ' role' + (group.jobs.length === 1 ? "" : "s") + (unseenN ? " · " + unseenN + " new" : "") + '</span>' +
        '</button>' +
        '<div class="company-roles"></div>';
      const rolesEl = card.querySelector(".company-roles");
      group.jobs.forEach(job => {
        const row = document.createElement("div");
        row.className = "job-row" + (job.seen ? " is-seen" : "");
        row.innerHTML =
          '<span class="job-title">' + escapeHtml(job.title) + '</span>' +
          (job.seen ? '<span></span>' : '<span class="job-new-tag">New</span>') +
          '<span class="job-meta"><span class="src-dot" style="background:' + srcVar(job.source) + '"></span>' +
            escapeHtml(job.location_text || "Remote") + ' &middot; ' + SRC_LABEL[job.source] + '</span>';
        row.addEventListener("click", () => openJobModal(job));
        rolesEl.appendChild(row);
      });
      card.querySelector(".company-head").addEventListener("click", () => card.classList.toggle("collapsed"));
      els.panelList.appendChild(card);
    });
  }

  async function markSeen(job) {
    if (job.seen) return;
    await fetch("/api/jobs/" + encodeURIComponent(job.id) + "/seen", { method: "POST" });
    job.seen = true;
    renderCasefile();
    if (state.mode === "network" && net) initNetworkCanvas();
    if (state.openBubble) updateLocalUnseenCount(state.openBubble.key, -1);
  }

  function updateLocalUnseenCount(key, delta) {
    if (key === "remote") {
      state.remoteUnseen = Math.max(0, (state.remoteUnseen || 0) + delta);
      els.remotePin.classList.toggle("has-new", state.remoteUnseen > 0);
      if (state.remoteUnseen > 0) { els.remoteCount.hidden = false; els.remoteCount.textContent = state.remoteUnseen; }
      else els.remoteCount.hidden = true;
    } else {
      const city = state.cities.find(c => c.key === key);
      if (city) {
        city.unseen_jobs = Math.max(0, city.unseen_jobs + delta);
        const marker = state.markers[key];
        if (marker) marker.setIcon(pinIcon(city));
      }
    }
    renderStats();
  }

  // ---------- job detail modal ----------

  function openJobModal(job) {
    state.currentJob = job;
    markSeen(job);

    els.jobModalTitle.textContent = job.title;
    els.jobModalCompany.textContent = job.company;

    let posted = null;
    if (job.posted_at) {
      const d = new Date(job.posted_at);
      if (!isNaN(d.getTime())) posted = d.toLocaleDateString();
    }
    const metaParts = [
      job.remote ? "Remote" : (job.location_text || null),
      SRC_LABEL[job.source] || job.source,
      posted ? "Posted " + posted : null,
    ].filter(Boolean);
    els.jobModalMeta.innerHTML = metaParts.map(p => "<span>" + escapeHtml(p) + "</span>").join("");

    els.jobModalDescription.textContent = job.description_full || job.description_snippet || "No description was provided by the source.";
    els.jobModalNotesInput.value = job.notes || "";
    els.jobModalNotesStatus.textContent = "";
    const viewUrl = safeUrl(job.url);
    if (viewUrl !== "#") {
      els.jobModalView.href = viewUrl;
      els.jobModalView.removeAttribute("aria-disabled");
      els.jobModalView.style.opacity = "";
      els.jobModalView.style.pointerEvents = "";
      els.jobModalView.removeAttribute("title");
    } else {
      // No usable link: turn the anchor into an inert, visibly-dimmed non-link.
      // Removing href prevents navigation and any "#" fragment write; there is
      // no click handler on this element, so the modal is unaffected.
      els.jobModalView.removeAttribute("href");
      els.jobModalView.setAttribute("aria-disabled", "true");
      els.jobModalView.style.pointerEvents = "none";
      els.jobModalView.style.opacity = "0.5";
      els.jobModalView.title = "No valid link for this posting";
    }
    els.jobModalApply.hidden = true;
    els.jobModalApplyStatus.textContent = "";
    state.currentApplication = null;
    els.jobModalApplyResult.hidden = true;
    renderApplyCvOptions();
    loadExistingApplication(job.id);

    els.jobModal.classList.add("open");
    els.jobBackdrop.classList.add("open");
  }

  function closeJobModal() {
    els.jobModal.classList.remove("open");
    els.jobBackdrop.classList.remove("open");
    state.currentJob = null;
  }

  function renderApplyCvOptions() {
    const hasCv = state.cvs.length > 0;
    els.jobModalGenerateBtn.disabled = !hasCv;
    if (!hasCv) {
      els.jobModalApplyCv.innerHTML = '<option value="">No CVs yet — add one in the Dossier</option>';
    } else {
      els.jobModalApplyCv.innerHTML = state.cvs.map(cv =>
        '<option value="' + cv.id + '">' + escapeHtml(cv.label) + (cv.role_type ? " — " + escapeHtml(cv.role_type) : "") + '</option>'
      ).join("");
    }
  }

  async function loadExistingApplication(jobId) {
    try {
      const res = await fetch("/api/jobs/" + encodeURIComponent(jobId) + "/applications");
      const applications = await res.json();
      if (applications.length > 0 && state.currentJob && state.currentJob.id === jobId) {
        renderApplication(applications[0]);
      }
    } catch (e) { /* no existing draft — fine */ }
  }

  function renderApplication(application) {
    state.currentApplication = application;
    els.jobModalCoverLetter.value = application.cover_letter;
    els.jobModalReviewNotesText.textContent = application.review_notes || "Nothing flagged.";
    els.jobModalApplyResult.hidden = false;
    els.jobModalGenerateBtn.textContent = "Regenerate";
  }

  els.jobModalClose.addEventListener("click", closeJobModal);
  els.jobBackdrop.addEventListener("click", closeJobModal);

  els.jobModalNotesSave.addEventListener("click", async () => {
    const job = state.currentJob;
    if (!job) return;
    els.jobModalNotesStatus.textContent = "Saving…";
    try {
      await fetch("/api/jobs/" + encodeURIComponent(job.id) + "/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes: els.jobModalNotesInput.value }),
      });
      job.notes = els.jobModalNotesInput.value;
      els.jobModalNotesStatus.textContent = "Saved";
    } catch (e) {
      els.jobModalNotesStatus.textContent = "Failed to save";
    }
    setTimeout(() => (els.jobModalNotesStatus.textContent = ""), 2500);
  });

  els.jobModalApplyBtn.addEventListener("click", () => {
    els.jobModalApply.hidden = !els.jobModalApply.hidden;
  });

  els.jobModalGenerateBtn.addEventListener("click", async () => {
    const job = state.currentJob;
    const cvId = els.jobModalApplyCv.value;
    if (!job || !cvId) return;
    els.jobModalGenerateBtn.disabled = true;
    els.jobModalApplyStatus.textContent = "Drafting… (Claude → GPT review → Claude revise, ~15s)";
    try {
      const res = await fetch("/api/jobs/" + encodeURIComponent(job.id) + "/applications", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cv_id: Number(cvId) }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "generation failed");
      renderApplication(data);
      els.jobModalApplyStatus.textContent = "Ready";
    } catch (err) {
      els.jobModalApplyStatus.textContent = err.message;
    }
    els.jobModalGenerateBtn.disabled = false;
    setTimeout(() => (els.jobModalApplyStatus.textContent = ""), 4000);
  });

  els.jobModalReviseBtn.addEventListener("click", async () => {
    const application = state.currentApplication;
    const feedback = els.jobModalFeedback.value.trim();
    if (!application || !feedback) return;
    els.jobModalReviseBtn.disabled = true;
    els.jobModalApplyStatus.textContent = "Revising…";
    try {
      const res = await fetch("/api/applications/" + application.id + "/revise", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feedback }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "revision failed");
      state.currentApplication = data;
      els.jobModalCoverLetter.value = data.cover_letter;
      els.jobModalFeedback.value = "";
      els.jobModalApplyStatus.textContent = "Updated";
    } catch (err) {
      els.jobModalApplyStatus.textContent = err.message;
    }
    els.jobModalReviseBtn.disabled = false;
    setTimeout(() => (els.jobModalApplyStatus.textContent = ""), 4000);
  });

  // ---------- dossier hub (CVs + projects + github) ----------

  function openHub() {
    closeBubble();
    closeProspects();
    closeNews();
    els.hubPanel.classList.add("open");
    els.hubBackdrop.classList.add("open");
    loadCVs();
    loadProjects();
    loadGithub();
    loadProfile();
    positionHubDialIndicator(document.querySelector('[data-hub-tab].active'));
  }

  function closeHub() {
    els.hubPanel.classList.remove("open");
    els.hubBackdrop.classList.remove("open");
  }

  function positionHubDialIndicator(btn) {
    if (!btn) return;
    els.hubDialIndicator.style.width = btn.offsetWidth + "px";
    els.hubDialIndicator.style.transform = "translateX(" + (btn.offsetLeft - 2) + "px)";
  }

  els.hubBtn.addEventListener("click", openHub);
  els.hubClose.addEventListener("click", closeHub);
  els.hubBackdrop.addEventListener("click", closeHub);

  document.querySelectorAll("[data-hub-tab]").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-hub-tab]").forEach(b => {
        const active = b === btn;
        b.classList.toggle("active", active);
        b.setAttribute("aria-selected", String(active));
      });
      const tab = btn.dataset.hubTab;
      els.hubCvsTab.hidden = tab !== "cvs";
      els.hubProjectsTab.hidden = tab !== "projects";
      els.hubGithubTab.hidden = tab !== "github";
      els.hubProfileTab.hidden = tab !== "profile";
      positionHubDialIndicator(btn);
    });
  });

  window.addEventListener("resize", () => {
    if (els.hubPanel.classList.contains("open")) {
      positionHubDialIndicator(document.querySelector('[data-hub-tab].active'));
    }
    if (els.newsPanel.classList.contains("open")) {
      positionNewsDialIndicator(els.newsTabs.querySelector("[data-news-tab].active"));
    }
  });

  async function loadGithub() {
    let data;
    try {
      const res = await fetch("/api/github/repos");
      if (!res.ok) throw new Error("HTTP " + res.status);
      data = await res.json();
    } catch (e) {
      console.error("loadGithub failed", e);
      els.githubStatus.textContent = "Couldn't load GitHub data — check your connection and reopen the Dossier.";
      return; // leave state.githubConfigured/githubRepos and the grid intact
    }
    state.githubConfigured = data.configured;
    state.githubRepos = data.repos || [];
    renderGithubGrid(data);
    updateHubCount();
  }

  function renderGithubGrid(data) {
    els.githubGrid.innerHTML = "";
    if (!data.configured) {
      els.githubStatus.innerHTML = "GitHub isn't connected yet. Add <code>GITHUB_USERNAME</code> (public repos) or <code>GITHUB_TOKEN</code> (public + private) to your <code>.env</code> file, then reopen the Dossier.";
      return;
    }
    if (data.error) {
      els.githubStatus.textContent = "Couldn't reach GitHub: " + data.error;
      return;
    }
    if (data.repos.length === 0) {
      els.githubStatus.textContent = "No repositories found.";
      return;
    }
    els.githubStatus.textContent = "";
    data.repos.forEach(repo => {
      const card = document.createElement("div");
      card.className = "hub-card";
      const tag = (repo.private ? "Private" : "Public") + (repo.language ? " · " + repo.language : "");
      const updated = repo.updated_at ? new Date(repo.updated_at).toLocaleDateString() : "";
      card.innerHTML =
        '<div class="hub-card-title">' + escapeHtml(repo.name) + '</div>' +
        '<span class="hub-card-tag' + (repo.private ? " hub-card-tag--private" : "") + '">' + escapeHtml(tag) + '</span>' +
        (repo.description ? '<div class="hub-card-desc">' + escapeHtml(repo.description) + '</div>' : "") +
        '<div class="hub-card-meta">' + (updated ? "Updated " + updated : "") + (repo.stars ? " &middot; &#9733; " + repo.stars : "") + '</div>' +
        '<div class="hub-card-actions"><a href="' + escapeHtml(safeUrl(repo.url)) + '" target="_blank" rel="noopener">Open</a></div>';
      els.githubGrid.appendChild(card);
    });
  }

  async function loadCVs() {
    let cvs;
    try {
      const res = await fetch("/api/cvs");
      if (!res.ok) throw new Error("HTTP " + res.status);
      cvs = await res.json();
    } catch (e) {
      console.error("loadCVs failed", e);
      els.cvGrid.innerHTML = '<div class="hub-empty">Couldn\'t load your CVs. Reopen the Dossier to retry.</div>';
      return; // keep any previously loaded CVs in state and the apply dropdown
    }
    state.cvs = cvs;
    renderCvGrid();
    renderApplyCvOptions();
    updateHubCount();
  }

  function renderCvGrid() {
    if (state.cvs.length === 0) {
      els.cvGrid.innerHTML = '<div class="hub-empty">No CVs uploaded yet.</div>';
      return;
    }
    els.cvGrid.innerHTML = "";
    state.cvs.forEach(cv => {
      const card = document.createElement("div");
      card.className = "hub-card";
      card.innerHTML =
        '<div class="hub-card-title">' + escapeHtml(cv.label) + '</div>' +
        (cv.role_type ? '<span class="hub-card-tag">' + escapeHtml(cv.role_type) + '</span>' : "") +
        '<div class="hub-card-meta">' + escapeHtml(cv.original_name) + ' &middot; ' + new Date(cv.uploaded_at).toLocaleDateString() + '</div>' +
        '<div class="hub-card-actions">' +
          '<a href="/api/cvs/' + cv.id + '/file" target="_blank" rel="noopener">View</a>' +
          '<button type="button" data-delete-cv="' + cv.id + '">Delete</button>' +
        '</div>';
      card.querySelector("[data-delete-cv]").addEventListener("click", () => deleteCv(cv.id));
      els.cvGrid.appendChild(card);
    });
  }

  async function deleteCv(id) {
    await fetch("/api/cvs/" + id, { method: "DELETE" });
    await loadCVs();
  }

  els.cvForm.addEventListener("submit", async e => {
    e.preventDefault();
    const file = els.cvFile.files[0];
    if (!file) return;
    els.cvFormStatus.textContent = "Uploading…";
    const formData = new FormData();
    formData.append("file", file);
    formData.append("label", els.cvLabel.value);
    formData.append("role_type", els.cvRoleType.value);
    try {
      const res = await fetch("/api/cvs", { method: "POST", body: formData });
      if (!res.ok) throw new Error((await res.json()).detail || "upload failed");
      els.cvForm.reset();
      els.cvFormStatus.textContent = "Uploaded";
      await loadCVs();
    } catch (err) {
      els.cvFormStatus.textContent = err.message || "Upload failed";
    }
    setTimeout(() => (els.cvFormStatus.textContent = ""), 2500);
  });

  async function loadProjects() {
    let projects;
    try {
      const res = await fetch("/api/projects");
      if (!res.ok) throw new Error("HTTP " + res.status);
      projects = await res.json();
    } catch (e) {
      console.error("loadProjects failed", e);
      els.projectGrid.innerHTML = '<div class="hub-empty">Couldn\'t load your projects. Reopen the Dossier to retry.</div>';
      return; // keep any previously loaded projects in state
    }
    state.projects = projects;
    renderProjectGrid();
    updateHubCount();
  }

  function renderProjectGrid() {
    if (state.projects.length === 0) {
      els.projectGrid.innerHTML = '<div class="hub-empty">No projects added yet.</div>';
      return;
    }
    els.projectGrid.innerHTML = "";
    state.projects.forEach(project => {
      const tags = (project.tags || "").split(",").map(t => t.trim()).filter(Boolean);
      const card = document.createElement("div");
      card.className = "hub-card";
      card.innerHTML =
        '<div class="hub-card-title">' + escapeHtml(project.title) + '</div>' +
        tags.map(t => '<span class="hub-card-tag">' + escapeHtml(t) + '</span>').join(" ") +
        (project.description ? '<div class="hub-card-desc">' + escapeHtml(project.description) + '</div>' : "") +
        '<div class="hub-card-actions">' +
          (project.link ? '<a href="' + escapeHtml(safeUrl(project.link)) + '" target="_blank" rel="noopener">Open link</a>' : "") +
          '<button type="button" data-delete-project="' + project.id + '">Delete</button>' +
        '</div>';
      card.querySelector("[data-delete-project]").addEventListener("click", () => deleteProject(project.id));
      els.projectGrid.appendChild(card);
    });
  }

  async function deleteProject(id) {
    await fetch("/api/projects/" + id, { method: "DELETE" });
    await loadProjects();
  }

  els.projectForm.addEventListener("submit", async e => {
    e.preventDefault();
    els.projectFormStatus.textContent = "Saving…";
    try {
      const res = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: els.projectTitle.value,
          description: els.projectDescription.value,
          tags: els.projectTags.value,
          link: els.projectLink.value,
        }),
      });
      if (!res.ok) throw new Error("save failed");
      els.projectForm.reset();
      els.projectFormStatus.textContent = "Added";
      await loadProjects();
    } catch (err) {
      els.projectFormStatus.textContent = "Failed to save";
    }
    setTimeout(() => (els.projectFormStatus.textContent = ""), 2500);
  });

  async function loadProfile() {
    let profile;
    try {
      const res = await fetch("/api/profile");
      if (!res.ok) throw new Error("HTTP " + res.status);
      profile = await res.json();
    } catch (e) {
      console.error("loadProfile failed", e);
      els.profileFormStatus.textContent = "Couldn't load your saved profile.";
      return; // leave whatever is already in the form fields untouched
    }
    els.profileFullName.value = profile.full_name || "";
    els.profileEmail.value = profile.email || "";
    els.profilePhone.value = profile.phone || "";
    els.profileLinkedin.value = profile.linkedin || "";
    els.profileLocation.value = profile.location || "";
  }

  els.profileForm.addEventListener("submit", async e => {
    e.preventDefault();
    els.profileFormStatus.textContent = "Saving…";
    try {
      await fetch("/api/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          full_name: els.profileFullName.value,
          email: els.profileEmail.value,
          phone: els.profilePhone.value,
          linkedin: els.profileLinkedin.value,
          location: els.profileLocation.value,
        }),
      });
      els.profileFormStatus.textContent = "Saved";
    } catch (err) {
      els.profileFormStatus.textContent = "Failed to save";
    }
    setTimeout(() => (els.profileFormStatus.textContent = ""), 2500);
  });

  function updateHubCount() {
    let text = state.cvs.length + " CV" + (state.cvs.length === 1 ? "" : "s") + " · " + state.projects.length + " project" + (state.projects.length === 1 ? "" : "s");
    if (state.githubConfigured) {
      const n = (state.githubRepos || []).length;
      text += " · " + n + " repo" + (n === 1 ? "" : "s");
    }
    els.hubCount.textContent = text;
  }

  // ---------- mode toggle ----------

  document.querySelectorAll("[data-mode]").forEach(btn => {
    btn.addEventListener("click", () => {
      state.mode = btn.dataset.mode;
      document.querySelectorAll("[data-mode]").forEach(b => {
        const active = b === btn;
        b.classList.toggle("active", active);
        b.setAttribute("aria-selected", String(active));
      });
      if (state.mode === "network") {
        els.panelList.hidden = true;
        els.panelNetwork.hidden = false;
        startNetwork();
      } else {
        els.panelNetwork.hidden = true;
        els.panelList.hidden = false;
        stopNetwork();
      }
    });
  });

  els.panelClose.addEventListener("click", closeBubble);
  els.panelBackdrop.addEventListener("click", closeBubble);
  els.remotePin.addEventListener("click", () => openBubble("remote", "Remote"));

  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if (els.jobModal.classList.contains("open")) closeJobModal();
    else if (els.hubPanel.classList.contains("open")) closeHub();
    else if (els.prospectsPanel.classList.contains("open")) closeProspects();
    else if (els.newsPanel.classList.contains("open")) closeNews();
    else if (state.openBubble) closeBubble();
  });

  window.addEventListener("resize", () => {
    if (!state.openBubble) return;
    if (minimap) minimap.invalidateSize();
    if (state.mode === "network" && net) initNetworkCanvas();
  });

  els.search.addEventListener("input", () => {
    state.filterText = els.search.value.trim().toLowerCase();
    if (state.openBubble) renderList();
  });

  els.toggleEu.addEventListener("change", () => {
    state.showEu = els.toggleEu.checked;
    loadCities();
  });

  els.refreshBtn.addEventListener("click", refresh);

  async function refresh() {
    els.refreshBtn.disabled = true;
    els.refreshStatus.textContent = "Refreshing…";
    try {
      const res = await fetch("/api/refresh", { method: "POST" });
      if (!res.ok) {
        els.refreshStatus.textContent = "Refresh failed";
      } else {
        // /api/refresh is asynchronous (HTTP 202): it kicks off a background
        // poll and cannot report a completed-job count. Report the state only.
        const data = await res.json();
        els.refreshStatus.textContent =
          data.status === "already_running"
            ? "Refresh already running"
            : "Refresh running…";
      }
    } catch (e) {
      els.refreshStatus.textContent = "Refresh failed";
    }
    // Immediate reload of the current DB state. The background poll may land
    // after this returns; its results will show on the next refresh/reload.
    await loadCities();
    await loadRemoteSummary();
    if (state.openBubble) closeBubble();
    els.refreshBtn.disabled = false;
    setTimeout(() => (els.refreshStatus.textContent = ""), 4000);
  }

  // ---------- network view (city -> company -> role) ----------

  function startNetwork() {
    initNetworkCanvas();
    els.networkCanvas.addEventListener("pointermove", onNetPointerMove);
    els.networkCanvas.addEventListener("pointerdown", onNetPointerDown);
    window.addEventListener("pointerup", onNetPointerUp);
    window.addEventListener("pointermove", onNetPointerDrag);
  }

  function stopNetwork() {
    net = null;
    els.networkCanvas.removeEventListener("pointermove", onNetPointerMove);
    els.networkCanvas.removeEventListener("pointerdown", onNetPointerDown);
    window.removeEventListener("pointerup", onNetPointerUp);
    window.removeEventListener("pointermove", onNetPointerDrag);
  }

  function initNetworkCanvas() {
    const bubble = state.openBubble;
    if (!bubble) return;
    const rect = els.panelNetwork.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    els.networkCanvas.width = rect.width * dpr;
    els.networkCanvas.height = rect.height * dpr;
    const ctx = els.networkCanvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const cx = rect.width / 2, cy = rect.height / 2;
    const jobs = bubble.jobs.filter(matchesFilter);
    const groups = groupCompanies(jobs);
    const companyTargetR = Math.max(50, Math.min(rect.width, rect.height) / 2 - 74);
    const roleTargetR = 32;

    const prevCollapsed = new Map();
    if (net) net.companyNodes.forEach(cn => prevCollapsed.set(cn.group.company, cn.collapsed));

    const companyNodes = groups.map((g, i) => {
      const angle = (i / Math.max(1, groups.length)) * Math.PI * 2 - Math.PI / 2;
      return {
        type: "company", group: g, angle,
        collapsed: prevCollapsed.get(g.company) || false,
        x: cx + Math.cos(angle) * companyTargetR * 0.6,
        y: cy + Math.sin(angle) * companyTargetR * 0.6,
        vx: 0, vy: 0, r: companyTargetR, dragging: false,
      };
    });

    const roleNodes = [];
    companyNodes.forEach(cn => {
      const k = cn.group.jobs.length;
      cn.group.jobs.forEach((job, j) => {
        const spread = 0.55;
        const roleAngle = cn.angle + (k > 1 ? (j - (k - 1) / 2) * (spread / (k - 1)) : 0);
        roleNodes.push({
          type: "role", job, parent: cn, angle: roleAngle,
          x: cn.x + Math.cos(roleAngle) * roleTargetR,
          y: cn.y + Math.sin(roleAngle) * roleTargetR,
          vx: 0, vy: 0, r: roleTargetR, dragging: false,
        });
      });
    });

    net = { ctx, w: rect.width, h: rect.height, cx, cy, companyNodes, roleNodes, hovered: null, dragNode: null, dragStartX: 0, dragStartY: 0, roleByJob: new Map(roleNodes.map(rn => [rn.job, rn])) };

    // Settle the layout once up front so it reads as a static diagram, not a jittering simulation.
    for (let i = 0; i < 160; i++) stepNetwork();

    renderLegend(jobs);
    drawNetwork();
  }

  function renderLegend(jobs) {
    const sources = Array.from(new Set(jobs.map(j => j.source)));
    els.networkLegend.innerHTML = sources.map(s => '<span><i style="background:' + srcVar(s) + '"></i>' + SRC_LABEL[s] + '</span>').join("");
  }

  function visibleRoleNodes() { return net ? net.roleNodes.filter(n => !n.parent.collapsed) : []; }
  function allNodes() { return net ? net.companyNodes.concat(visibleRoleNodes()) : []; }

  function stepNetwork() {
    if (!net) return;
    const nodes = allNodes();
    nodes.forEach(node => {
      if (node.dragging) return;
      const centerX = node.type === "company" ? net.cx : node.parent.x;
      const centerY = node.type === "company" ? net.cy : node.parent.y;
      const dx = node.x - centerX, dy = node.y - centerY;
      const dist = Math.hypot(dx, dy) || 0.001;
      const diff = (node.r - dist) * 0.05;
      node.vx += (dx / dist) * diff;
      node.vy += (dy / dist) * diff;
    });
    nodes.forEach((node, i) => {
      if (node.dragging) return;
      nodes.forEach((other, j) => {
        if (i === j) return;
        const ox = node.x - other.x, oy = node.y - other.y;
        const od = Math.hypot(ox, oy) || 0.001;
        const bothCompany = node.type === "company" && other.type === "company";
        const minD = bothCompany ? 76 : (node.type === "company" || other.type === "company") ? 44 : 38;
        if (od < minD) {
          const push = (minD - od) * 0.025;
          node.vx += (ox / od) * push;
          node.vy += (oy / od) * push;
        }
      });
      node.vx *= 0.82;
      node.vy *= 0.82;
      node.x += node.vx;
      node.y += node.vy;
    });
  }

  function drawCurve(ctx, x1, y1, x2, y2, color, bend) {
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    const midx = (x1 + x2) / 2 + (y2 - y1) * bend;
    const midy = (y1 + y2) / 2 - (x2 - x1) * bend;
    ctx.quadraticCurveTo(midx, midy, x2, y2);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  function drawDot(ctx, x, y, r, fill, glow) {
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = fill;
    ctx.shadowColor = fill;
    ctx.shadowBlur = glow || 0;
    ctx.fill();
    ctx.shadowBlur = 0;
  }

  function drawNetwork() {
    if (!net) return;
    const { ctx, w, h, cx, cy, companyNodes, hovered } = net;
    ctx.clearRect(0, 0, w, h);

    companyNodes.forEach(cn => {
      drawCurve(ctx, cx, cy, cn.x, cn.y, hovered === cn ? "rgba(47,231,196,0.55)" : "rgba(47,231,196,0.2)", 0.06);
    });
    companyNodes.forEach(cn => {
      if (cn.collapsed) return;
      cn.group.jobs.forEach(job => {
        const rn = net.roleByJob.get(job);
        drawCurve(ctx, cn.x, cn.y, rn.x, rn.y, hovered === rn ? "rgba(47,231,196,0.5)" : "rgba(47,231,196,0.14)", 0.04);
      });
    });

    drawDot(ctx, cx, cy, 9, "#2fe7c4", 12);

    const labelsAlwaysOn = companyNodes.length <= 8;
    companyNodes.forEach(cn => {
      const isHover = hovered === cn;
      ctx.beginPath();
      ctx.arc(cn.x, cn.y, isHover ? 9 : 7.5, 0, Math.PI * 2);
      ctx.fillStyle = "#0d1719";
      ctx.strokeStyle = isHover ? "#d8f3ee" : "#6f9296";
      ctx.lineWidth = 1.4;
      ctx.fill();
      ctx.stroke();
      if (labelsAlwaysOn || isHover) {
        ctx.font = "600 10px 'IBM Plex Mono', monospace";
        ctx.fillStyle = isHover ? "#d8f3ee" : "#9db8ba";
        ctx.textAlign = "center";
        ctx.fillText(truncate(cn.group.company, 12), cn.x, cn.y - 14);
      }
      if (cn.collapsed) {
        ctx.font = "600 9px 'IBM Plex Mono', monospace";
        ctx.fillStyle = "#2fe7c4";
        ctx.fillText("+" + cn.group.jobs.length, cn.x, cn.y + 3.5);
      }
    });

    visibleRoleNodes().forEach(rn => {
      const isHover = hovered === rn;
      const color = srcColor(rn.job.source);
      drawDot(ctx, rn.x, rn.y, isHover ? 7 : 5, color, isHover ? 12 : 4);
      if (!rn.job.seen) {
        ctx.beginPath();
        ctx.arc(rn.x + 5, rn.y - 5, 2.4, 0, Math.PI * 2);
        ctx.fillStyle = "#ff4d4d";
        ctx.fill();
      }
      if (isHover) {
        ctx.font = "9px 'IBM Plex Mono', monospace";
        ctx.fillStyle = "#d8f3ee";
        ctx.textAlign = "center";
        ctx.fillText(truncate(rn.job.title, 22), rn.x, rn.y + 16);
      }
    });
  }

  function nodeAt(x, y) {
    const nodes = allNodes();
    for (let i = nodes.length - 1; i >= 0; i--) {
      const node = nodes[i];
      const threshold = node.type === "company" ? 13 : 11;
      if (Math.hypot(node.x - x, node.y - y) < threshold) return node;
    }
    return null;
  }

  function canvasPoint(e) {
    const rect = els.networkCanvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function onNetPointerMove(e) {
    if (!net) return;
    const p = canvasPoint(e);
    const node = nodeAt(p.x, p.y);
    net.hovered = node;
    els.networkCanvas.style.cursor = node ? "pointer" : "default";
    if (node && node.type === "role") {
      const job = node.job;
      els.networkReadout.textContent = job.title + " — " + job.company + " — " + SRC_LABEL[job.source] + (!job.seen ? " — new" : "");
    } else if (node && node.type === "company") {
      els.networkReadout.textContent = node.group.company + " — " + node.group.jobs.length + " role" + (node.group.jobs.length === 1 ? "" : "s") + (node.collapsed ? " (collapsed, click to expand)" : " (click to collapse)");
    } else {
      els.networkReadout.textContent = "Hover a node to inspect. Click a company to expand or collapse it.";
    }
    drawNetwork();
  }

  function onNetPointerDown(e) {
    if (!net) return;
    const p = canvasPoint(e);
    const node = nodeAt(p.x, p.y);
    if (node) {
      net.dragNode = node;
      node.dragging = true;
      net.dragStartX = node.x;
      net.dragStartY = node.y;
    }
  }

  function onNetPointerDrag(e) {
    if (!net || !net.dragNode) return;
    const node = net.dragNode;
    const p = canvasPoint(e);
    const newX = Math.max(10, Math.min(net.w - 10, p.x));
    const newY = Math.max(10, Math.min(net.h - 10, p.y));
    const dx = newX - node.x, dy = newY - node.y;
    node.x = newX;
    node.y = newY;
    if (node.type === "company") {
      // Roles are anchored to their company's live position, so move them along rigidly.
      net.roleNodes.filter(rn => rn.parent === node).forEach(rn => { rn.x += dx; rn.y += dy; });
    }
    drawNetwork();
  }

  function onNetPointerUp() {
    if (!net || !net.dragNode) return;
    const node = net.dragNode;
    node.dragging = false;
    const moved = Math.hypot(node.x - net.dragStartX, node.y - net.dragStartY);
    if (moved < 3) {
      if (node.type === "company") {
        node.collapsed = !node.collapsed;
        drawNetwork();
        renderCasefile();
      } else {
        openJobModal(node.job);
      }
    }
    net.dragNode = null;
  }

  // ---------- prospects (UK map -> city map -> business pins) ----------

  let prospectsMap = null;
  let prospectsAreaMarkers = {};
  let prospectsBizMarkers = [];
  let prospectsBizMarkerById = {};

  function openProspects() {
    closeBubble();
    closeHub();
    closeNews();
    els.prospectsPanel.classList.add("open");
    els.prospectsBackdrop.classList.add("open");
    if (!prospectsMap) initProspectsMap();
    setTimeout(() => prospectsMap.invalidateSize(), 20);
    if (state.prospectsCurrentArea) showCityView(state.prospectsCurrentArea);
    else showUkView();
  }

  function closeProspects() {
    els.prospectsPanel.classList.remove("open");
    els.prospectsBackdrop.classList.remove("open");
  }

  els.prospectsBtn.addEventListener("click", openProspects);
  els.prospectsClose.addEventListener("click", closeProspects);
  els.prospectsBackdrop.addEventListener("click", closeProspects);
  els.prospectsBackBtn.addEventListener("click", showUkView);

  function initProspectsMap() {
    prospectsMap = L.map(els.prospectsMapEl, { zoomControl: false, attributionControl: false }).setView([54.5, -3.5], 6);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", { maxZoom: 19 }).addTo(prospectsMap);
  }

  async function loadProspectMeta() {
    let data;
    try {
      const res = await fetch("/api/prospects/areas");
      if (!res.ok) throw new Error("HTTP " + res.status);
      data = await res.json();
    } catch (e) {
      console.error("loadProspectMeta failed", e);
      els.prospectsStats.textContent = "Couldn't load prospect areas. Close and reopen.";
      return false; // keep any previously loaded areas/sectors/categories
    }
    state.prospectAreas = data.areas;
    state.prospectSectors = data.sectors || {};
    state.prospectCategories = data.categories;
    return true;
  }

  function categoryLabel(key) {
    const cat = state.prospectCategories.find(c => c.key === key);
    return cat ? cat.label : key;
  }

  function sectorColor(categoryKey) {
    const cat = state.prospectCategories.find(c => c.key === categoryKey);
    const sector = cat && state.prospectSectors[cat.sector];
    return sector ? sector.color : "#6f9296";
  }

  function checkedCategories() {
    return new Set(Array.from(els.prospectsSidebar.querySelectorAll("input:checked")).map(i => i.dataset.category));
  }

  function groupByCategory(businesses) {
    const map = {};
    businesses.forEach(b => {
      (map[b.category] = map[b.category] || []).push(b);
    });
    return map;
  }

  function renderSidebar() {
    const grouped = groupByCategory(state.businesses);
    const prevChecked = checkedCategories();
    const prevExpanded = new Set(Array.from(els.prospectsSidebar.querySelectorAll(".prospect-category-section.expanded")).map(s => s.dataset.category));

    els.prospectsSidebar.innerHTML = state.prospectCategories.map(cat => {
      const businesses = grouped[cat.key] || [];
      const isChecked = prevChecked.size > 0 ? prevChecked.has(cat.key) : cat.osm_coverage === "good";
      const color = sectorColor(cat.key);
      const rows = businesses.map(b => {
        const detail = b.analyzed_at
          ? '<div class="prospect-business-desc">' + escapeHtml(b.description || "No description.") + '</div>'
          : '<div class="prospect-business-pending">Not analyzed yet</div>';
        return '<div class="prospect-business-row" data-business-id="' + escapeHtml(b.id) + '">' +
          '<div class="prospect-business-name">' + escapeHtml(b.name) + '</div>' + detail +
        '</div>';
      }).join("") || '<div class="prospect-business-pending" style="padding:0.3rem 0.75rem;">No businesses discovered yet.</div>';

      return (
        '<div class="prospect-category-section' + (prevExpanded.has(cat.key) ? " expanded" : "") + '" data-category="' + cat.key + '">' +
          '<div class="prospect-category-header">' +
            '<input type="checkbox" data-category="' + cat.key + '"' + (isChecked ? " checked" : "") + ' />' +
            '<button type="button" class="prospect-category-toggle">' +
              '<span class="sector-dot" style="background:' + color + '"></span>' +
              '<span class="prospect-category-name">' + escapeHtml(cat.label) + '</span>' +
              '<span class="prospect-category-count">' + businesses.length + '</span>' +
              '<span class="chevron">&#9656;</span>' +
            '</button>' +
          '</div>' +
          '<div class="prospect-category-businesses">' + rows + '</div>' +
        '</div>'
      );
    }).join("");
  }

  els.prospectsSidebar.addEventListener("change", e => {
    if (e.target.matches('input[type="checkbox"]')) renderBizMarkers();
  });

  els.prospectsSidebar.addEventListener("click", e => {
    const toggle = e.target.closest(".prospect-category-toggle");
    if (toggle) {
      toggle.closest(".prospect-category-section").classList.toggle("expanded");
      return;
    }
    const row = e.target.closest(".prospect-business-row");
    if (row) {
      const business = state.businesses.find(b => b.id === row.dataset.businessId);
      if (business) focusBusiness(business);
    }
  });

  function focusBusiness(business) {
    const marker = prospectsBizMarkerById[business.id];
    if (!marker) return;
    prospectsMap.flyTo([business.lat, business.lon], Math.max(prospectsMap.getZoom(), 15), { duration: 0.6 });
    marker.openPopup();
  }

  async function showUkView() {
    state.prospectsCurrentArea = null;
    els.prospectsBackBtn.hidden = true;
    els.prospectsCityControls.hidden = true;
    els.prospectsSidebar.hidden = true;
    clearBizMarkers();
    if (!(await loadProspectMeta())) return; // loader already surfaced the failure in prospects-stats
    renderAreaMarkers();
    prospectsMap.setView([54.5, -3.5], 6);
    const n = state.prospectAreas.length;
    els.prospectsStats.textContent = "United Kingdom · " + n + " area" + (n === 1 ? "" : "s") + " tracked";
  }

  function renderAreaMarkers() {
    Object.values(prospectsAreaMarkers).forEach(m => prospectsMap.removeLayer(m));
    prospectsAreaMarkers = {};
    state.prospectAreas.forEach(area => {
      const hasSignal = area.unanalyzed_businesses > 0;
      const size = Math.min(36, 16 + area.total_businesses * 0.12);
      const icon = L.divIcon({
        className: "",
        html:
          '<div class="area-pin-icon' + (hasSignal ? " has-signal" : "") + '" style="width:' + size + 'px;height:' + size + 'px;">' +
            '<span class="area-pin-pulse"></span>' +
            '<span class="area-pin-dot"></span>' +
            '<span class="area-pin-label">' + escapeHtml(area.label) + '</span>' +
          '</div>',
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
      });
      const marker = L.marker([area.lat, area.lon], { icon }).addTo(prospectsMap);
      marker.bindTooltip(area.label + " — " + area.total_businesses + " business" + (area.total_businesses === 1 ? "" : "es"));
      marker.on("click", () => showCityView(area.key));
      prospectsAreaMarkers[area.key] = marker;
    });
  }

  async function showCityView(areaKey) {
    state.prospectsCurrentArea = areaKey;
    if (state.prospectAreas.length === 0) await loadProspectMeta();
    const area = state.prospectAreas.find(a => a.key === areaKey);
    els.prospectsBackBtn.hidden = false;
    els.prospectsCityControls.hidden = false;
    els.prospectsSidebar.hidden = false;
    Object.values(prospectsAreaMarkers).forEach(m => prospectsMap.removeLayer(m));
    if (area) prospectsMap.setView([area.lat, area.lon], 12);
    await loadBusinesses();
  }

  els.prospectsScanBtn.addEventListener("click", async () => {
    const areaKey = state.prospectsCurrentArea;
    const checked = Array.from(checkedCategories());
    if (!areaKey || checked.length === 0) return;
    els.prospectsScanBtn.disabled = true;
    els.prospectsStatus.textContent = "Scanning " + checked.length + " categor" + (checked.length === 1 ? "y" : "ies") + " via OpenStreetMap…";
    try {
      const res = await fetch("/api/prospects/" + areaKey + "/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ categories: checked }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "scan failed");
      els.prospectsStatus.textContent = "+" + data.total_new + " new businesses found";
      await loadBusinesses();
    } catch (err) {
      els.prospectsStatus.textContent = err.message;
    }
    els.prospectsScanBtn.disabled = false;
    setTimeout(() => (els.prospectsStatus.textContent = ""), 5000);
  });

  els.prospectsAnalyzeBtn.addEventListener("click", async () => {
    const areaKey = state.prospectsCurrentArea;
    if (!areaKey) return;
    els.prospectsAnalyzeBtn.disabled = true;
    els.prospectsStatus.textContent = "Analyzing… (fetches each website, then Claude scores it — can take a minute)";
    try {
      const res = await fetch("/api/prospects/" + areaKey + "/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 10 }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "analysis failed");
      els.prospectsStatus.textContent = "Analyzed " + data.analyzed + " businesses";
      await loadBusinesses();
    } catch (err) {
      els.prospectsStatus.textContent = err.message;
    }
    els.prospectsAnalyzeBtn.disabled = false;
    setTimeout(() => (els.prospectsStatus.textContent = ""), 5000);
  });

  async function loadBusinesses() {
    const areaKey = state.prospectsCurrentArea;
    if (!areaKey) return;
    let businesses;
    try {
      const res = await fetch("/api/prospects/" + areaKey + "/businesses");
      if (!res.ok) throw new Error("HTTP " + res.status);
      businesses = await res.json();
    } catch (e) {
      console.error("loadBusinesses failed", e);
      els.prospectsStatus.textContent = "Couldn't load businesses for this area.";
      return; // keep the current sidebar and markers in place
    }
    state.businesses = businesses;
    renderSidebar();
    renderBizMarkers();
    updateProspectsStats();
  }

  function clearBizMarkers() {
    prospectsBizMarkers.forEach(m => prospectsMap.removeLayer(m));
    prospectsBizMarkers = [];
    prospectsBizMarkerById = {};
  }

  function renderBizMarkers() {
    clearBizMarkers();
    const checked = checkedCategories();
    const visible = checked.size === 0 ? state.businesses : state.businesses.filter(b => checked.has(b.category));
    visible.forEach(business => {
      const color = sectorColor(business.category);
      const cls = (business.analyzed_at ? "analyzed " : "") + (!business.website ? "no-website" : "");
      const icon = L.divIcon({
        className: "",
        html: '<div class="biz-pin-wrap ' + cls + '" style="width:12px;height:12px;color:' + color + ';"><span class="biz-pin" style="background:' + color + '"></span></div>',
        iconSize: [12, 12],
        iconAnchor: [6, 6],
      });
      const marker = L.marker([business.lat, business.lon], { icon }).addTo(prospectsMap);
      marker.bindPopup(renderBusinessPopup(business), { maxWidth: 300 });
      prospectsBizMarkers.push(marker);
      prospectsBizMarkerById[business.id] = marker;
    });
  }

  function renderBusinessPopup(business) {
    const links = [
      business.phone ? escapeHtml(business.phone) : "",
      business.website ? '<a href="' + escapeHtml(safeWebsiteUrl(business.website)) + '" target="_blank" rel="noopener">Website</a>' : "no website found",
    ].filter(Boolean).join(" &middot; ");

    let chStatus = "";
    if (business.companies_house_status) {
      const overdue = business.companies_house_status.includes("overdue");
      chStatus = '<div class="ch-status' + (overdue ? " overdue" : "") + '">Companies House: ' + escapeHtml(business.companies_house_status) + '</div>';
    }

    let opportunitySection;
    if (business.analyzed_at) {
      const tags = (business.opportunity_tags || "").split(",").map(t => t.trim()).filter(Boolean);
      opportunitySection =
        '<div class="opportunity-summary">' + escapeHtml(business.opportunity_summary || "No summary.") + '</div>' +
        (tags.length ? '<div class="opportunity-tags">' + tags.map(t => '<span class="opportunity-tag">' + escapeHtml(t.replace(/_/g, " ")) + '</span>').join("") + '</div>' : "");
    } else {
      opportunitySection = '<div class="opportunity-summary">Not analyzed yet — click "Analyze next 10" above.</div>';
    }

    const description = business.description ? '<div class="biz-popup-desc">' + escapeHtml(business.description) + '</div>' : "";

    return (
      '<div class="biz-popup-title">' + escapeHtml(business.name) + '</div>' +
      '<div class="biz-popup-category">' + escapeHtml(categoryLabel(business.category)) + '</div>' +
      '<div class="biz-popup-meta">' + escapeHtml(business.address || "") + '<br>' + links + '</div>' +
      description +
      chStatus +
      opportunitySection
    );
  }

  function updateProspectsStats() {
    const area = state.prospectAreas.find(a => a.key === state.prospectsCurrentArea);
    const analyzed = state.businesses.filter(b => b.analyzed_at).length;
    els.prospectsStats.textContent = (area ? area.label : "") + ", UK · " + state.businesses.length + " businesses · " + analyzed + " analyzed";
  }

  // ---------- news ----------

  const NEWS_CAT_COLOR = {
    world: "var(--src-reed)", tech: "var(--accent)", business: "var(--src-remoteok)",
    israel: "var(--src-lever)", uk: "var(--src-usajobs)", usa: "var(--src-adzuna)", europe: "#5ad1e6",
  };

  function openNews() {
    closeBubble();
    closeHub();
    closeProspects();
    els.newsPanel.classList.add("open");
    els.newsBackdrop.classList.add("open");
    loadNewsCategories().then(() => loadNews(state.newsCurrentCategory));
  }

  function closeNews() {
    els.newsPanel.classList.remove("open");
    els.newsBackdrop.classList.remove("open");
  }

  els.newsBtn.addEventListener("click", openNews);
  els.newsClose.addEventListener("click", closeNews);
  els.newsBackdrop.addEventListener("click", closeNews);

  async function loadNewsCategories() {
    if (state.newsCategories.length > 0) return;
    let cats;
    try {
      const res = await fetch("/api/news/categories");
      if (!res.ok) throw new Error("HTTP " + res.status);
      cats = await res.json();
    } catch (e) {
      console.error("loadNewsCategories failed", e);
      return; // leave tabs at the static "All" tab; loadNews still runs
    }
    state.newsCategories = cats;
    const tabsHtml = state.newsCategories.map(cat =>
      '<button class="mode-btn" data-news-tab="' + cat.key + '" role="tab" aria-selected="false">' + escapeHtml(cat.label) + '</button>'
    ).join("");
    els.newsTabs.insertAdjacentHTML("beforeend", tabsHtml);
    positionNewsDialIndicator(els.newsTabs.querySelector('[data-news-tab=""]'));
  }

  function positionNewsDialIndicator(btn) {
    if (!btn) return;
    els.newsDialIndicator.style.width = btn.offsetWidth + "px";
    els.newsDialIndicator.style.transform = "translateX(" + (btn.offsetLeft - 2) + "px)";
  }

  els.newsTabs.addEventListener("click", e => {
    const btn = e.target.closest("[data-news-tab]");
    if (!btn) return;
    els.newsTabs.querySelectorAll("[data-news-tab]").forEach(b => {
      const active = b === btn;
      b.classList.toggle("active", active);
      b.setAttribute("aria-selected", String(active));
    });
    positionNewsDialIndicator(btn);
    state.newsCurrentCategory = btn.dataset.newsTab;
    loadNews(state.newsCurrentCategory);
  });

  let newsRequestId = 0;

  async function loadNews(category) {
    const requestId = ++newsRequestId;
    els.newsList.innerHTML = '<div class="loading-state"><span class="loading-ring"></span>Loading&hellip;</div>';
    const url = "/api/news" + (category ? "?category=" + encodeURIComponent(category) : "");
    let data;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error("HTTP " + res.status);
      data = await res.json();
    } catch (e) {
      if (requestId !== newsRequestId) return; // a newer tab click won — stay silent
      console.error("loadNews failed", e);
      els.newsList.innerHTML = '<div class="hub-empty">Couldn\'t load the news feed — try again shortly.</div>';
      els.newsStats.textContent = "—";
      return;
    }
    if (requestId !== newsRequestId) return; // a newer tab click superseded this fetch
    renderNewsList(data.articles || []);
  }

  function timeAgo(iso) {
    if (!iso) return "";
    const diffMs = Date.now() - new Date(iso).getTime();
    const mins = Math.round(diffMs / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return mins + "m ago";
    const hours = Math.round(mins / 60);
    if (hours < 24) return hours + "h ago";
    return Math.round(hours / 24) + "d ago";
  }

  function renderNewsList(articles) {
    if (articles.length === 0) {
      els.newsList.innerHTML = '<div class="hub-empty">No articles right now — try again shortly.</div>';
      els.newsStats.textContent = "0 stories";
      return;
    }
    els.newsStats.textContent = articles.length + " stor" + (articles.length === 1 ? "y" : "ies");
    els.newsList.innerHTML = articles.map(a => {
      const color = NEWS_CAT_COLOR[a.category] || "var(--panel-edge)";
      return (
        '<a class="news-card" style="--news-cat-color:' + color + '" href="' + escapeHtml(safeUrl(a.link)) + '" target="_blank" rel="noopener">' +
          '<div class="news-card-head">' +
            '<span class="news-source">' + escapeHtml(a.source) + '</span>' +
            '<span class="news-time">' + timeAgo(a.published_at) + '</span>' +
          '</div>' +
          '<div class="news-title">' + escapeHtml(a.title) + '</div>' +
          (a.summary ? '<div class="news-summary">' + escapeHtml(a.summary) + '</div>' : "") +
        '</a>'
      );
    }).join("");
  }

  // ---------- alfred (voice input/output) ----------

  const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis;
  let alfredVoice = null;
  let alfredRecognizer = null;
  let alfredListening = false;
  let alfredHideTimer = null;
  // Set only via window.JobRadar.registerAlfredDispatcher(). Stays null (and the
  // built-in handler stays fully in charge) until a future module registers one.
  let alfredDispatcher = null;

  // Name fragments that mark a platform's higher-quality "enhanced"/neural voice.
  // pickAlfredVoice() prefers these variants (its first two tiers), and
  // alfredSpeak() uses the same list to skip the butler pitch drop on them —
  // keep the two in sync.
  const ENHANCED_VOICE_RE = /enhanced|premium|natural|online/i;

  function pickAlfredVoice() {
    if (!synth) return null;
    const voices = synth.getVoices();
    if (voices.length === 0) return null;
    // Prefer a measured British male voice for the butler persona. Try each
    // platform's higher-quality "enhanced"/neural variant first, since those
    // sound far less robotic than the default compact voices, then fall back
    // gracefully through progressively looser matches.
    const byName = re => voices.find(v => re.test(v.name));
    return (
      byName(/daniel.*(enhanced|premium)/i) ||                        // macOS/Safari high-quality Daniel
      byName(/ryan.*online.*natural/i) ||                              // Edge/Windows neural voice
      byName(/^google uk english male/i) ||                            // Chrome network voice
      byName(/daniel/i) ||                                             // macOS/Safari/Chrome default Daniel
      voices.find(v => /male/i.test(v.name) && /gb|uk|british/i.test(v.lang + " " + v.name)) ||
      voices.find(v => /gb|uk/i.test(v.lang)) ||
      voices.find(v => v.lang.startsWith("en")) ||
      voices[0]
    );
  }

  if (synth) {
    alfredVoice = pickAlfredVoice();
    synth.addEventListener("voiceschanged", () => { alfredVoice = pickAlfredVoice(); });
  }

  function showAlfredReadout(label, text) {
    clearTimeout(alfredHideTimer);
    els.alfredReadoutLabel.textContent = label;
    els.alfredReadoutText.textContent = text;
    els.alfredReadout.hidden = false;
    requestAnimationFrame(() => els.alfredReadout.classList.add("show"));
  }

  function hideAlfredReadoutSoon(delay) {
    clearTimeout(alfredHideTimer);
    alfredHideTimer = setTimeout(() => {
      els.alfredReadout.classList.remove("show");
      setTimeout(() => { els.alfredReadout.hidden = true; }, 220);
    }, delay);
  }

  function alfredSpeak(text) {
    showAlfredReadout("ALFRED", text);
    if (!synth) { hideAlfredReadoutSoon(3200); return; }
    synth.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    if (alfredVoice) utterance.voice = alfredVoice;
    // High-quality neural voices already sound natural; detuning their pitch
    // makes them sound worse, so only apply the "butler" pitch drop to the
    // lower-quality compact voices that need it.
    const isEnhancedVoice = alfredVoice && ENHANCED_VOICE_RE.test(alfredVoice.name);
    utterance.rate = isEnhancedVoice ? 0.99 : 0.96;
    utterance.pitch = isEnhancedVoice ? 0.97 : 0.85;
    els.alfredBtn.classList.add("speaking");
    utterance.onend = utterance.onerror = () => {
      els.alfredBtn.classList.remove("speaking");
      hideAlfredReadoutSoon(1400);
    };
    synth.speak(utterance);
  }

  function currentOpenStat() {
    const total = els.statTotal.textContent;
    const unreviewed = els.statNew.textContent;
    return "You have " + total + " signals tracked, " + unreviewed + " still unreviewed, sir.";
  }

  function closeWhicheverIsOpen() {
    if (els.jobModal.classList.contains("open")) { closeJobModal(); return true; }
    if (els.hubPanel.classList.contains("open")) { closeHub(); return true; }
    if (els.prospectsPanel.classList.contains("open")) { closeProspects(); return true; }
    if (els.newsPanel.classList.contains("open")) { closeNews(); return true; }
    if (state.openBubble) { closeBubble(); return true; }
    return false;
  }

  // dispatchAlfredCommand is the single entry point the recognizer calls. A
  // future Alfred module can take over parsing by registering a dispatcher via
  // window.JobRadar.registerAlfredDispatcher(); when none is registered, or the
  // registered one declines (returns a falsy value) or throws, this falls back
  // to the built-in command set with identical behavior.
  function dispatchAlfredCommand(heard) {
    if (alfredDispatcher) {
      let handled = false;
      try {
        handled = alfredDispatcher(heard, { speak: alfredSpeak, builtin: builtinAlfredDispatch });
      } catch (err) {
        console.error("Alfred dispatcher threw; using built-in handler", err);
      }
      if (handled) return;
    }
    builtinAlfredDispatch(heard);
  }

  function builtinAlfredDispatch(heard) {
    const text = heard.trim().toLowerCase();
    let match;

    if (/^open (the )?dossier/.test(text)) {
      openHub();
      alfredSpeak("Opening the Dossier, sir.");
    } else if (/^open (the )?(uk )?prospects/.test(text)) {
      openProspects();
      alfredSpeak("Pulling up UK Prospects.");
    } else if (/^open (the )?news/.test(text)) {
      openNews();
      alfredSpeak("Here is the news, sir.");
    } else if (/^(close|dismiss|hide)/.test(text)) {
      const closed = closeWhicheverIsOpen();
      alfredSpeak(closed ? "Very good." : "There is nothing open at present, sir.");
    } else if (/^refresh/.test(text)) {
      refresh();
      alfredSpeak("Refreshing the board now.");
    } else if ((match = text.match(/^search for (.+)/))) {
      els.search.value = match[1];
      els.search.dispatchEvent(new Event("input"));
      alfredSpeak('Filtering for "' + match[1] + '".');
    } else if (/status|briefing|what.?s (going on|happening)|how are things/.test(text)) {
      alfredSpeak(currentOpenStat());
    } else {
      alfredSpeak("I'm afraid I didn't quite catch that, sir.");
    }
  }

  function initAlfredRecognizer() {
    const recognizer = new SpeechRecognitionCtor();
    recognizer.lang = "en-GB";
    recognizer.continuous = false;
    recognizer.interimResults = true;

    recognizer.onresult = e => {
      let transcript = "";
      for (let i = e.resultIndex; i < e.results.length; i++) transcript += e.results[i][0].transcript;
      showAlfredReadout("LISTENING", transcript || "…");
      if (e.results[e.results.length - 1].isFinal) dispatchAlfredCommand(transcript);
    };
    recognizer.onerror = () => { alfredListening = false; els.alfredBtn.classList.remove("listening"); };
    recognizer.onend = () => { alfredListening = false; els.alfredBtn.classList.remove("listening"); };
    return recognizer;
  }

  if (!SpeechRecognitionCtor) {
    els.alfredBtn.disabled = true;
    els.alfredBtn.title = "Voice input isn't supported in this browser (try Chrome or Edge)";
  } else {
    els.alfredBtn.addEventListener("click", () => {
      if (alfredListening) {
        alfredRecognizer.stop();
        return;
      }
      if (synth) synth.cancel();
      if (!alfredRecognizer) alfredRecognizer = initAlfredRecognizer();
      alfredListening = true;
      els.alfredBtn.classList.add("listening");
      showAlfredReadout("LISTENING", "…");
      try {
        alfredRecognizer.start();
      } catch (e) {
        alfredListening = false;
        els.alfredBtn.classList.remove("listening");
      }
    });
  }

  // ---------- clock ----------

  function tickClock() {
    const now = new Date();
    const pad = n => String(n).padStart(2, "0");
    els.clock.textContent = pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds());
  }

  // ---------- window.JobRadar bridge ----------
  // Minimal, frozen surface for the ordered feature modules loaded after this
  // file. Only what the revised plan needs: a version marker, the existing
  // Alfred speech helper, and the Alfred dispatcher registration hook. No
  // internal mutable state is exposed -- alfredDispatcher lives in this closure
  // and is only settable through registerAlfredDispatcher().
  window.JobRadar = Object.freeze({
    version: 1,
    speak: alfredSpeak,
    registerAlfredDispatcher: function (fn) {
      if (typeof fn !== "function") {
        throw new TypeError("registerAlfredDispatcher expects a function");
      }
      alfredDispatcher = fn;
    },
  });

  // ---------- init ----------

  (async function init() {
    // Guard each loader independently so one failure can't stop the others or
    // the clock. (Each loader also swallows its own errors internally.)
    try { await loadCities(); } catch (e) { console.error(e); }
    try { await loadRemoteSummary(); } catch (e) { console.error(e); }
    try { await loadCVs(); } catch (e) { console.error(e); }
    tickClock();
    setInterval(tickClock, 1000);
  })();
})();
