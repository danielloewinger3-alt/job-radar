// Service worker: the ONLY component that performs network I/O.
//
// - Validates every inbound message against a fixed schema (lib/messages.js).
// - Builds request URLs from fixed templates (lib/routes.js); never accepts a
//   URL / path / method / header from a message. GET only, no credentials,
//   redirects refused.
// - Hands the pack to the content script exactly once, via an in-memory nonce
//   entry (lib/handoff.js). Pack data never touches chrome.storage.* / DOM.
// - Two data sources: "mock" (bundled fixture) and "live" (local backend). Mode
//   is a non-sensitive preference in chrome.storage.local.

import { validateMessage, isTrustedSender, isTrustedTopFrameSender } from './lib/messages.js';
import { createHandoffStore } from './lib/handoff.js';
import { buildUrl, FETCH_OPTIONS, DEFAULT_BASE } from './lib/routes.js';
import {
  ContractError,
  normalizeAutofill,
} from './lib/packschema.js';
import {
  normalizeApplicationList,
  normalizePackMeta,
  buildNormalizedPack,
} from './lib/adapter.js';
import {
  mockListApplications,
  mockPackMeta,
  mockAutofill,
  mockBuildPack,
} from './lib/mockstore.js';

const STORAGE_KEYS = Object.freeze(['mode', 'selectedApplicationId', 'selectedPackId']);

// ---------------------------------------------------------------------------
// nonce + one-time handoff
// ---------------------------------------------------------------------------

function randomNonce() {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  let s = '';
  for (const x of b) s += x.toString(16).padStart(2, '0');
  return s;
}

const handoff = createHandoffStore({
  randomNonce,
  ttlMs: 15000,
  setTimer: (fn, ms) => setTimeout(fn, ms),
  clearTimer: (h) => clearTimeout(h),
});

chrome.tabs.onRemoved.addListener((tabId) => handoff.clearFor(tabId));

// ---------------------------------------------------------------------------
// preferences (non-sensitive only)
// ---------------------------------------------------------------------------

async function getMode() {
  try {
    const got = await chrome.storage.local.get('mode');
    return got && got.mode === 'live' ? 'live' : 'mock';
  } catch {
    return 'mock';
  }
}

async function setPref(key, value) {
  if (!STORAGE_KEYS.includes(key)) return;
  try {
    await chrome.storage.local.set({ [key]: value });
  } catch {
    /* non-fatal */
  }
}

// ---------------------------------------------------------------------------
// data sources
// ---------------------------------------------------------------------------

let fixturePromise = null;
function getFixture() {
  if (!fixturePromise) {
    fixturePromise = fetch(chrome.runtime.getURL('testbed/autofill.mock.json'))
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
  }
  return fixturePromise;
}

async function apiGet(routeName, id) {
  let url;
  try {
    url = buildUrl(DEFAULT_BASE, routeName, id);
  } catch {
    return { error: 'INTERNAL' };
  }
  let res;
  try {
    res = await fetch(url, FETCH_OPTIONS);
  } catch {
    return { error: 'UPSTREAM_UNAVAILABLE' };
  }
  if (res.status === 404) return { error: 'NOT_FOUND' };
  if (!res.ok) return { error: 'UPSTREAM_UNAVAILABLE' };
  try {
    return { json: await res.json() };
  } catch {
    return { error: 'CONTRACT_MISMATCH' };
  }
}

function contractOrInternal(e) {
  return e instanceof ContractError ? 'CONTRACT_MISMATCH' : 'INTERNAL';
}

// ---------------------------------------------------------------------------
// message router
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((rawMsg, sender, sendResponse) => {
  if (!isTrustedSender(sender, chrome.runtime.id)) return false;

  const v = validateMessage(rawMsg);
  if (!v.ok) {
    sendResponse({ ok: false, error: 'BAD_REQUEST' });
    return false;
  }

  handle(v, sender)
    .then((res) => sendResponse(res))
    .catch(() => sendResponse({ ok: false, error: 'INTERNAL' }));
  return true; // async sendResponse
});

async function handle(v, sender) {
  const mode = await getMode();

  switch (v.type) {
    case 'GET_STATUS': {
      const fx = mode === 'mock' ? await getFixture() : null;
      return { ok: true, mode, fixtureLoaded: mode === 'mock' ? !!fx : undefined };
    }

    case 'SET_MODE': {
      await setPref('mode', v.params.mode);
      return { ok: true, mode: v.params.mode };
    }

    case 'LIST_APPLICATIONS': {
      if (mode === 'mock') {
        const fx = await getFixture();
        if (!fx) return { ok: false, error: 'UPSTREAM_UNAVAILABLE' };
        return { ok: true, mode, applications: mockListApplications(fx) };
      }
      const r = await apiGet('listApplications');
      if (r.error) return { ok: false, error: r.error };
      try {
        return { ok: true, mode, applications: normalizeApplicationList(r.json) };
      } catch (e) {
        return { ok: false, error: contractOrInternal(e) };
      }
    }

    case 'GET_PACK_META': {
      if (mode === 'mock') {
        const fx = await getFixture();
        if (!fx) return { ok: false, error: 'UPSTREAM_UNAVAILABLE' };
        const res = mockPackMeta(fx, v.params.packId);
        return res.error ? { ok: false, error: res.error } : { ok: true, meta: res.meta };
      }
      const r = await apiGet('packMeta', v.params.packId);
      if (r.error) return { ok: false, error: r.error };
      try {
        return { ok: true, meta: normalizePackMeta(r.json) };
      } catch (e) {
        return { ok: false, error: contractOrInternal(e) };
      }
    }

    case 'GET_PACK_AUTOFILL': {
      // Non-sensitive summary only. Field values are handed over solely through
      // the one-time SCAN_PAGE handoff.
      let af;
      if (mode === 'mock') {
        const fx = await getFixture();
        if (!fx) return { ok: false, error: 'UPSTREAM_UNAVAILABLE' };
        const res = mockAutofill(fx, v.params.packId);
        if (res.error) return { ok: false, error: res.error };
        af = res.autofill;
      } else {
        const r = await apiGet('packAutofill', v.params.packId);
        if (r.error) return { ok: false, error: r.error };
        try {
          af = normalizeAutofill(r.json);
        } catch (e) {
          return { ok: false, error: contractOrInternal(e) };
        }
      }
      return {
        ok: true,
        summary: {
          fieldCount: af.fields.length,
          proposableCount: af.fields.filter((f) => f.proposable).length,
          omittedCount: af.omittedCount,
          reviewed: af.reviewed,
        },
      };
    }

    case 'SCAN_PAGE': {
      let built;
      if (mode === 'mock') {
        const fx = await getFixture();
        if (!fx) return { ok: false, error: 'UPSTREAM_UNAVAILABLE' };
        built = mockBuildPack(fx, v.params.packId);
      } else {
        const [metaRes, afRes] = await Promise.all([
          apiGet('packMeta', v.params.packId),
          apiGet('packAutofill', v.params.packId),
        ]);
        if (metaRes.error) return { ok: false, error: metaRes.error };
        if (afRes.error) return { ok: false, error: afRes.error };
        try {
          const meta = normalizePackMeta(metaRes.json);
          built = { pack: buildNormalizedPack(meta, afRes.json) };
        } catch (e) {
          built = { error: contractOrInternal(e) };
        }
      }
      if (built.error) return { ok: false, error: built.error };

      const tabId = v.params.tabId;
      const nonce = handoff.put(tabId, built.pack);

      try {
        await chrome.scripting.executeScript({
          target: { tabId }, // TOP FRAME ONLY -- no frameIds, no allFrames
          files: ['content.js'],
        });
        await chrome.tabs.sendMessage(tabId, { type: 'START', nonce }, { frameId: 0 });
      } catch {
        handoff.clearFor(tabId);
        return { ok: false, error: 'INJECTION_FAILED' };
      }
      return { ok: true };
    }

    case 'CONSUME_PACK': {
      const tabId = sender && sender.tab ? sender.tab.id : undefined;
      const trusted = isTrustedTopFrameSender(sender, chrome.runtime.id, tabId);
      const res = handoff.consume(tabId, v.params.nonce, trusted);
      return res.ok ? { ok: true, pack: res.pack } : { ok: false, error: res.error };
    }

    default:
      return { ok: false, error: 'BAD_REQUEST' };
  }
}
