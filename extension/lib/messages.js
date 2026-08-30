// Message-type + parameter validation for chrome.runtime / chrome.tabs messaging.
//
// The service worker NEVER reads a URL, path, HTTP method, or header from a
// message. Every message is one of a fixed set of types, each with an exact
// allow-list of parameter keys and a strict type check per key. Application and
// pack ids must be positive integers.

export const NONCE_RE = /^[0-9a-f]{32}$/;
const POS_INT_STR = /^[1-9][0-9]{0,14}$/;

// type -> exact set of allowed keys (besides "type"). Anything else is rejected.
const SCHEMA = Object.freeze({
  LIST_APPLICATIONS: [],
  GET_PACK_META: ['packId'],
  GET_PACK_AUTOFILL: ['packId'],
  SCAN_PAGE: ['tabId', 'packId'],
  CONSUME_PACK: ['nonce'],
  GET_STATUS: [],
  SET_MODE: ['mode'],
});

export function isKnownType(type) {
  return typeof type === 'string' && Object.prototype.hasOwnProperty.call(SCHEMA, type);
}

function coercePositiveIntId(v) {
  if (typeof v === 'number') {
    if (!Number.isInteger(v) || v <= 0 || v > Number.MAX_SAFE_INTEGER) return null;
    return String(v);
  }
  if (typeof v === 'string' && POS_INT_STR.test(v)) return v;
  return null;
}

/**
 * @returns {{ok:true, type:string, params:object} | {ok:false, reason:string}}
 */
export function validateMessage(msg) {
  if (!msg || typeof msg !== 'object' || Array.isArray(msg)) return fail('not_object');
  if (!isKnownType(msg.type)) return fail('unknown_type');

  const allowed = SCHEMA[msg.type];
  for (const k of Object.keys(msg)) {
    if (k === 'type') continue;
    if (!allowed.includes(k)) return fail('unexpected_key:' + k);
  }

  const params = {};

  if (allowed.includes('packId')) {
    const id = coercePositiveIntId(msg.packId);
    if (!id) return fail('bad_packId');
    params.packId = id;
  }
  if (allowed.includes('tabId')) {
    const v = msg.tabId;
    if (!Number.isInteger(v) || v < 0) return fail('bad_tabId');
    params.tabId = v;
  }
  if (allowed.includes('nonce')) {
    const v = msg.nonce;
    if (typeof v !== 'string' || !NONCE_RE.test(v)) return fail('bad_nonce');
    params.nonce = v;
  }
  if (allowed.includes('mode')) {
    if (msg.mode !== 'mock' && msg.mode !== 'live') return fail('bad_mode');
    params.mode = msg.mode;
  }

  return { ok: true, type: msg.type, params };
}

export function isTrustedSender(sender, runtimeId) {
  return !!sender && typeof runtimeId === 'string' && sender.id === runtimeId;
}

// For CONSUME_PACK: must come from the content script in the top frame of the
// exact tab we injected into.
export function isTrustedTopFrameSender(sender, runtimeId, expectedTabId) {
  return (
    isTrustedSender(sender, runtimeId) &&
    !!sender.tab &&
    sender.tab.id === expectedTabId &&
    sender.frameId === 0
  );
}

function fail(reason) {
  return { ok: false, reason };
}
