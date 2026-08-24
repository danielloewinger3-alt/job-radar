const map = L.map("map", { worldCopyJump: true }).setView([35, -30], 3);

L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; OpenStreetMap contributors',
  maxZoom: 19,
}).addTo(map);

const state = {
  cities: [],
  markers: {},
  currentJobs: [],
  mode: { type: "all" }, // {type: "all"} | {type: "city", key} | {type: "remote"}
  showEu: true,
};

const els = {
  search: document.getElementById("search"),
  toggleEu: document.getElementById("toggle-eu"),
  toggleRemote: document.getElementById("toggle-remote"),
  refreshBtn: document.getElementById("refresh-btn"),
  refreshStatus: document.getElementById("refresh-status"),
  sidebarTitle: document.getElementById("sidebar-title"),
  sidebarCount: document.getElementById("sidebar-count"),
  jobList: document.getElementById("job-list"),
};

function cityIcon(city) {
  const count = city.unseen_jobs;
  const size = count > 0 ? Math.min(44, 26 + count * 2) : 22;
  const tierClass = city.tier === "primary" ? "primary" : "eu";
  const pinClass = city.total_jobs === 0 ? "empty" : tierClass;
  const ring = count > 0 ? `<div class="ping-ring"></div>` : "";
  const label = count > 0 ? count : "";

  return L.divIcon({
    className: "",
    html: `<div class="city-pin-wrapper" style="width:${size}px;height:${size}px;">
             ${ring}
             <div class="city-pin ${pinClass}" style="width:${size}px;height:${size}px;">${label}</div>
           </div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

async function loadCities() {
  const res = await fetch("/api/cities");
  state.cities = await res.json();

  Object.values(state.markers).forEach((m) => map.removeLayer(m));
  state.markers = {};

  for (const city of state.cities) {
    if (city.tier === "eu" && !state.showEu) continue;
    const marker = L.marker([city.lat, city.lon], { icon: cityIcon(city) }).addTo(map);
    marker.bindTooltip(`${city.label} — ${city.total_jobs} job${city.total_jobs === 1 ? "" : "s"}`);
    marker.on("click", () => selectCity(city.key, city.label));
    state.markers[city.key] = marker;
  }
}

function selectCity(key, label) {
  state.mode = { type: "city", key };
  els.toggleRemote.checked = false;
  els.sidebarTitle.textContent = label;
  loadJobs();
}

function selectRemote() {
  state.mode = { type: "remote" };
  els.sidebarTitle.textContent = "Remote roles";
  loadJobs();
}

function selectAll() {
  state.mode = { type: "all" };
  els.sidebarTitle.textContent = "All target cities";
  loadJobs();
}

async function loadJobs() {
  let url = "/api/jobs?";
  if (state.mode.type === "city") url += `city=${encodeURIComponent(state.mode.key)}`;
  else if (state.mode.type === "remote") url += `remote=true`;
  // "all" mode: no filter params, fetch everything and let sidebar show latest

  const res = await fetch(url);
  state.currentJobs = await res.json();
  renderJobs();
}

function renderJobs() {
  const query = els.search.value.trim().toLowerCase();
  let jobs = state.currentJobs;
  if (query) {
    jobs = jobs.filter(
      (j) => j.title.toLowerCase().includes(query) || j.company.toLowerCase().includes(query)
    );
  }
  jobs = [...jobs].sort((a, b) => new Date(b.first_seen_at) - new Date(a.first_seen_at));

  els.sidebarCount.textContent = `${jobs.length} job${jobs.length === 1 ? "" : "s"}`;
  els.jobList.innerHTML = "";

  if (jobs.length === 0) {
    els.jobList.innerHTML = `<div class="empty-state">No jobs yet. Try "Refresh now", or wait for the next scheduled poll.</div>`;
    return;
  }

  for (const job of jobs) {
    const card = document.createElement("div");
    card.className = `job-card ${job.seen ? "" : "unseen"}`;
    card.innerHTML = `
      <div class="job-title">${escapeHtml(job.title)}</div>
      <div class="job-meta">${escapeHtml(job.company)} — ${escapeHtml(job.location_text || "Remote")}</div>
      <div class="job-source">${job.source}</div>
    `;
    card.addEventListener("click", () => openJob(job));
    els.jobList.appendChild(card);
  }
}

async function openJob(job) {
  if (!job.seen) {
    await fetch(`/api/jobs/${encodeURIComponent(job.id)}/seen`, { method: "POST" });
    job.seen = true;
    renderJobs();
    loadCities();
  }
  window.open(job.url, "_blank", "noopener");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

async function refresh() {
  els.refreshBtn.disabled = true;
  els.refreshStatus.textContent = "Refreshing...";
  try {
    const res = await fetch("/api/refresh", { method: "POST" });
    const data = await res.json();
    els.refreshStatus.textContent = `+${data.total_new} new`;
  } catch (e) {
    els.refreshStatus.textContent = "Refresh failed";
  }
  await loadCities();
  await loadJobs();
  els.refreshBtn.disabled = false;
  setTimeout(() => (els.refreshStatus.textContent = ""), 4000);
}

els.search.addEventListener("input", renderJobs);
els.refreshBtn.addEventListener("click", refresh);
els.toggleEu.addEventListener("change", () => {
  state.showEu = els.toggleEu.checked;
  loadCities();
});
els.toggleRemote.addEventListener("change", () => {
  if (els.toggleRemote.checked) selectRemote();
  else selectAll();
});

(async function init() {
  await loadCities();
  await loadJobs();
})();
