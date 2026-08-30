// One-time, in-memory pack handoff between the service worker and the content
// script. Pack data is NEVER written to chrome.storage.* / DOM / page globals /
// postMessage. It lives only in this Map for the few hundred ms between injection
// and CONSUME_PACK.

import { NONCE_RE } from './messages.js';

function constantTimeEq(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

/**
 * @param {object} deps
 * @param {() => number} [deps.now]
 * @param {number} [deps.ttlMs]
 * @param {() => string} deps.randomNonce  32-hex-char nonce generator
 * @param {(fn:Function, ms:number) => any} [deps.setTimer]
 * @param {(handle:any) => void} [deps.clearTimer]
 */
export function createHandoffStore({
  now = () => Date.now(),
  ttlMs = 15000,
  randomNonce,
  setTimer,
  clearTimer,
} = {}) {
  if (typeof randomNonce !== 'function') throw new Error('randomNonce required');
  const entries = new Map(); // tabId -> {nonce, pack, targetTabId, createdAt, consumed}
  const timers = new Map(); // tabId -> timer handle

  function dropTimer(tabId) {
    const h = timers.get(tabId);
    if (h !== undefined) {
      timers.delete(tabId);
      if (typeof clearTimer === 'function') clearTimer(h);
    }
  }

  function clearFor(tabId) {
    entries.delete(tabId);
    dropTimer(tabId);
  }

  function put(tabId, pack) {
    clearFor(tabId);
    const nonce = randomNonce();
    if (!NONCE_RE.test(nonce)) throw new Error('randomNonce produced bad value');
    entries.set(tabId, { nonce, pack, targetTabId: tabId, createdAt: now(), consumed: false });
    if (typeof setTimer === 'function') {
      timers.set(tabId, setTimer(() => clearFor(tabId), ttlMs));
    }
    return nonce;
  }

  /**
   * @param {number} tabId  from sender.tab.id
   * @param {string} nonce  from the CONSUME_PACK message
   * @param {boolean} senderTrusted  result of isTrustedTopFrameSender(...)
   * @returns {{ok:true, pack:any} | {ok:false, error:string}}
   */
  function consume(tabId, nonce, senderTrusted) {
    const e = entries.get(tabId);
    if (!e) return { ok: false, error: 'PACK_UNAVAILABLE_RETRY' };
    if (e.consumed) {
      clearFor(tabId);
      return { ok: false, error: 'INTERNAL' };
    }
    if (now() - e.createdAt > ttlMs) {
      clearFor(tabId);
      return { ok: false, error: 'PACK_UNAVAILABLE_RETRY' };
    }
    if (typeof nonce !== 'string' || !NONCE_RE.test(nonce) || !constantTimeEq(nonce, e.nonce)) {
      return { ok: false, error: 'INTERNAL' };
    }
    if (!senderTrusted) return { ok: false, error: 'INTERNAL' };

    e.consumed = true;
    const pack = e.pack;
    clearFor(tabId);
    return { ok: true, pack };
  }

  return {
    put,
    consume,
    clearFor,
    has: (tabId) => entries.has(tabId),
    size: () => entries.size,
  };
}
