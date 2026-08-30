// Popup UI. Talks to the service worker only through chrome.runtime messaging.
// All dynamic text is written with textContent -- never innerHTML.

'use strict';

const ERROR_TEXT = {
  UPSTREAM_UNAVAILABLE: 'Can’t reach the Job Radar backend at localhost:8000. Start it, or switch to the mock fixture.',
  NOT_FOUND: 'That pack no longer exists on the backend.',
  CONTRACT_MISMATCH: 'The backend returned data this extension version does not understand. Update the extension.',
  INJECTION_FAILED: 'Could not run on this page. Browser-internal pages (chrome://, the Web Store) and PDFs are not scannable.',
  BAD_REQUEST: 'The extension sent an invalid request. This is a bug.',
  LIVE_DISABLED: 'Live mode is not available in this build.',
  INTERNAL: 'Something went wrong inside the extension.',
};

const state = {
  mode: 'mock',
  apps: [], // { applicationId, roleTitle, stage, packId }
  metaByPack: new Map(), // packId -> { reviewed } | { error }
  selectedPackId: null,
  listGen: 0, // bumped on every (re)load so stale pack-meta responses are ignored
};

const els = {
  status: document.getElementById('status'),
  list: document.getElementById('app-list'),
  scan: document.getElementById('scan'),
  scanResult: document.getElementById('scan-result'),
};

function send(message) {
  return chrome.runtime.sendMessage(message).catch(() => ({ ok: false, error: 'INTERNAL' }));
}

function setStatus(node, text, kind) {
  node.textContent = text || '';
  node.classList.remove('error', 'ok');
  if (kind) node.classList.add(kind);
}

async function init() {
  const status = await send({ type: 'GET_STATUS' });
  if (status && status.ok) state.mode = status.mode;
  for (const radio of document.querySelectorAll('input[name="mode"]')) {
    radio.checked = radio.value === state.mode;
    radio.addEventListener('change', onModeChange);
  }
  await loadApplications();
  els.scan.addEventListener('click', onScan);
}

async function onModeChange(e) {
  const mode = e.target.value;
  const res = await send({ type: 'SET_MODE', mode });
  if (!res || !res.ok) {
    setStatus(els.status, ERROR_TEXT[res && res.error] || ERROR_TEXT.INTERNAL, 'error');
    return;
  }
  state.mode = mode;
  state.metaByPack.clear();
  state.selectedPackId = null;
  els.scan.disabled = true;
  await loadApplications();
}

async function loadApplications() {
  const gen = ++state.listGen;
  setStatus(els.status, 'Loading tracked applications…');
  els.list.textContent = '';
  const res = await send({ type: 'LIST_APPLICATIONS' });
  if (!res || !res.ok) {
    setStatus(els.status, ERROR_TEXT[res && res.error] || ERROR_TEXT.INTERNAL, 'error');
    return;
  }
  state.apps = (res.applications || []).filter((a) => a.stage !== 'archived');
  setStatus(els.status, `${state.apps.length} application(s) · source: ${state.mode}`, 'ok');

  if (!state.apps.length) {
    const li = document.createElement('li');
    li.textContent = 'No non-archived tracked applications.';
    els.list.appendChild(li);
    return;
  }

  for (const app of state.apps) renderAppRow(app);
  // fetch reviewed-state for packs that exist
  for (const app of state.apps) {
    if (app.packId) refreshPackMeta(app.packId, gen);
  }
}

function renderAppRow(app) {
  const li = document.createElement('li');
  li.dataset.packId = app.packId || '';

  const row = document.createElement('div');
  row.className = 'app-row';

  const radio = document.createElement('input');
  radio.type = 'radio';
  radio.name = 'app';
  radio.value = app.packId || '';
  radio.disabled = !app.packId;
  radio.addEventListener('change', () => selectPack(app.packId, li));

  const main = document.createElement('div');
  main.className = 'app-main';
  const role = document.createElement('div');
  role.className = 'role';
  role.textContent = app.roleTitle || `Application #${app.applicationId}`;
  const meta = document.createElement('div');
  meta.textContent = `#${app.applicationId} · ${app.stage || 'unknown stage'}`;
  main.appendChild(role);
  main.appendChild(meta);

  const badge = document.createElement('span');
  if (!app.packId) {
    badge.className = 'badge nopack';
    badge.textContent = 'No pack yet';
  } else {
    badge.className = 'badge unreviewed';
    badge.textContent = 'checking…';
  }
  badge.dataset.role = 'badge';
  main.appendChild(badge);

  row.appendChild(radio);
  row.appendChild(main);
  li.appendChild(row);
  els.list.appendChild(li);
}

async function refreshPackMeta(packId, gen) {
  const res = await send({ type: 'GET_PACK_META', packId: Number(packId) });
  if (gen !== undefined && gen !== state.listGen) return; // a newer load superseded this
  const li = els.list.querySelector(`li[data-pack-id="${CSS.escape(String(packId))}"]`);
  const badge = li && li.querySelector('[data-role="badge"]');
  if (!badge) return;

  if (!res || !res.ok) {
    state.metaByPack.set(String(packId), { error: res && res.error });
    badge.className = 'badge err';
    badge.textContent =
      (res && res.error === 'NOT_FOUND') ? 'pack missing'
      : (res && res.error === 'CONTRACT_MISMATCH') ? 'unsupported'
      : 'backend error';
    if (state.selectedPackId === String(packId)) els.scan.disabled = true;
    return;
  }

  const reviewed = res.meta && res.meta.reviewed === true;
  state.metaByPack.set(String(packId), { reviewed });
  badge.className = reviewed ? 'badge reviewed' : 'badge unreviewed';
  badge.textContent = reviewed ? 'Reviewed — fillable' : 'Not reviewed — preview only';
}

function selectPack(packId, li) {
  if (!packId) return;
  state.selectedPackId = String(packId);
  for (const el of els.list.querySelectorAll('li')) el.classList.remove('selected');
  li.classList.add('selected');
  const meta = state.metaByPack.get(String(packId));
  els.scan.disabled = !meta || !!meta.error ? true : false;
  setStatus(els.scanResult, '');
}

async function onScan() {
  if (!state.selectedPackId) return;
  els.scan.disabled = true;
  setStatus(els.scanResult, 'Scanning…');

  let tab;
  try {
    [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  } catch {
    tab = null;
  }
  if (!tab || typeof tab.id !== 'number') {
    setStatus(els.scanResult, ERROR_TEXT.INJECTION_FAILED, 'error');
    els.scan.disabled = false;
    return;
  }

  const res = await send({
    type: 'SCAN_PAGE',
    tabId: tab.id,
    packId: Number(state.selectedPackId),
  });
  els.scan.disabled = false;

  if (res && res.ok) {
    setStatus(els.scanResult, 'Review overlay opened on the page. Accept fields individually.', 'ok');
  } else {
    setStatus(els.scanResult, ERROR_TEXT[res && res.error] || ERROR_TEXT.INTERNAL, 'error');
  }
}

init();
