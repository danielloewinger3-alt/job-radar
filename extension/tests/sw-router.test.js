import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const RUNTIME_ID = 'abcdefghijklmnopabcdefghijklmnop';
const FIXTURE_TEXT = readFileSync(new URL('../testbed/autofill.mock.json', import.meta.url), 'utf8');

let swCounter = 0;

async function loadSw({ fetchImpl, executeScriptThrows = false, tabsSendMessageThrows = false, localStore = {} } = {}) {
  const calls = { executeScript: [], tabsSendMessage: [], storageSet: [] };
  const listeners = { message: [], tabRemoved: [] };
  const store = { ...localStore };

  const chrome = {
    runtime: {
      id: RUNTIME_ID,
      lastError: null,
      getURL: (p) => 'chrome-extension://' + RUNTIME_ID + '/' + p,
      onMessage: { addListener: (fn) => listeners.message.push(fn) },
    },
    tabs: {
      onRemoved: { addListener: (fn) => listeners.tabRemoved.push(fn) },
      sendMessage: async (tabId, msg, opts) => {
        calls.tabsSendMessage.push({ tabId, msg, opts });
        if (tabsSendMessageThrows) throw new Error('no receiver');
      },
    },
    scripting: {
      executeScript: async (args) => {
        calls.executeScript.push(args);
        if (executeScriptThrows) throw new Error('cannot inject');
        return [{ result: null }];
      },
    },
    storage: {
      local: {
        get: async (key) =>
          typeof key === 'string' ? { [key]: store[key] } : { ...store },
        set: async (obj) => {
          calls.storageSet.push(obj);
          Object.assign(store, obj);
        },
      },
      // deliberately NO `session` -- sw.js must never touch it
    },
  };

  const defaultFetch = async (url) => {
    if (String(url).startsWith('chrome-extension://')) {
      return { ok: true, status: 200, json: async () => JSON.parse(FIXTURE_TEXT) };
    }
    throw new Error('unexpected fetch: ' + url);
  };

  globalThis.chrome = chrome;
  globalThis.fetch = fetchImpl || defaultFetch;

  await import('../sw.js?swload=' + ++swCounter);

  function send(msg, sender = { id: RUNTIME_ID }) {
    return new Promise((resolve, reject) => {
      let done = false;
      for (const fn of listeners.message) {
        const ret = fn(msg, sender, (r) => {
          done = true;
          resolve(r);
        });
        if (ret !== true && !done) resolve(undefined); // sync, no response
      }
      setTimeout(() => !done && reject(new Error('timeout')), 1000);
    });
  }

  return { chrome, calls, listeners, store, send, fireTabRemoved: (id) => listeners.tabRemoved.forEach((f) => f(id)) };
}

test('sw: ignores untrusted senders', async () => {
  const sw = await loadSw();
  const res = await sw.send({ type: 'LIST_APPLICATIONS' }, { id: 'someone-else' });
  assert.equal(res, undefined);
});

test('sw: invalid message -> BAD_REQUEST', async () => {
  const sw = await loadSw();
  assert.deepEqual(await sw.send({ type: 'WAT' }), { ok: false, error: 'BAD_REQUEST' });
  assert.deepEqual(await sw.send({ type: 'GET_PACK_META', packId: '../x' }), { ok: false, error: 'BAD_REQUEST' });
  assert.deepEqual(await sw.send({ type: 'SCAN_PAGE', tabId: 1, packId: 2, url: 'http://evil' }), { ok: false, error: 'BAD_REQUEST' });
});

test('sw: SET_MODE persists; GET_STATUS reports it', async () => {
  const sw = await loadSw();
  assert.deepEqual(await sw.send({ type: 'SET_MODE', mode: 'live' }), { ok: true, mode: 'live' });
  assert.deepEqual(sw.calls.storageSet, [{ mode: 'live' }]);
  const status = await sw.send({ type: 'GET_STATUS' });
  assert.equal(status.mode, 'live');
});

test('sw: mock LIST_APPLICATIONS + GET_PACK_META from the fixture', async () => {
  const sw = await loadSw();
  const list = await sw.send({ type: 'LIST_APPLICATIONS' });
  assert.equal(list.ok, true);
  assert.equal(list.mode, 'mock');
  assert.ok(list.applications.find((a) => a.applicationId === '7'));

  const meta = await sw.send({ type: 'GET_PACK_META', packId: 42 });
  assert.equal(meta.ok, true);
  assert.equal(meta.meta.reviewed, true);

  const missing = await sw.send({ type: 'GET_PACK_META', packId: 999 });
  assert.deepEqual(missing, { ok: false, error: 'NOT_FOUND' });
});

test('sw: SCAN_PAGE handoff -- top-frame inject, START with hex nonce, CONSUME once', async () => {
  const sw = await loadSw();
  const scan = await sw.send({ type: 'SCAN_PAGE', tabId: 77, packId: 42 });
  assert.deepEqual(scan, { ok: true });

  assert.equal(sw.calls.executeScript.length, 1);
  assert.deepEqual(sw.calls.executeScript[0].target, { tabId: 77 });
  assert.ok(!('allFrames' in sw.calls.executeScript[0].target));
  assert.equal(sw.calls.executeScript[0].files[0], 'content.js');

  const started = sw.calls.tabsSendMessage[0];
  assert.equal(started.msg.type, 'START');
  assert.match(started.msg.nonce, /^[0-9a-f]{32}$/);
  assert.deepEqual(started.opts, { frameId: 0 });

  const sender = { id: RUNTIME_ID, tab: { id: 77 }, frameId: 0 };
  const consumed = await sw.send({ type: 'CONSUME_PACK', nonce: started.msg.nonce }, sender);
  assert.equal(consumed.ok, true);
  assert.equal(consumed.pack.reviewed, true);
  assert.ok(consumed.pack.fields.length > 0);

  const again = await sw.send({ type: 'CONSUME_PACK', nonce: started.msg.nonce }, sender);
  assert.equal(again.ok, false);
  assert.equal(again.error, 'PACK_UNAVAILABLE_RETRY');
});

test('sw: CONSUME_PACK from wrong frame / wrong tab is refused', async () => {
  const sw = await loadSw();
  await sw.send({ type: 'SCAN_PAGE', tabId: 5, packId: 42 });
  const nonce = sw.calls.tabsSendMessage[0].msg.nonce;

  const wrongFrame = await sw.send({ type: 'CONSUME_PACK', nonce }, { id: RUNTIME_ID, tab: { id: 5 }, frameId: 2 });
  assert.equal(wrongFrame.error, 'INTERNAL');
  const wrongTab = await sw.send({ type: 'CONSUME_PACK', nonce }, { id: RUNTIME_ID, tab: { id: 9 }, frameId: 0 });
  assert.equal(wrongTab.error, 'PACK_UNAVAILABLE_RETRY');

  // legit consume still works afterwards
  const ok = await sw.send({ type: 'CONSUME_PACK', nonce }, { id: RUNTIME_ID, tab: { id: 5 }, frameId: 0 });
  assert.equal(ok.ok, true);
});

test('sw: injection failure clears the pending entry', async () => {
  const sw = await loadSw({ executeScriptThrows: true });
  const scan = await sw.send({ type: 'SCAN_PAGE', tabId: 5, packId: 42 });
  assert.deepEqual(scan, { ok: false, error: 'INJECTION_FAILED' });
  // no START was sent, so we cannot know the nonce; but any consume must fail
  const c = await sw.send({ type: 'CONSUME_PACK', nonce: 'f'.repeat(32) }, { id: RUNTIME_ID, tab: { id: 5 }, frameId: 0 });
  assert.equal(c.ok, false);
});

test('sw: tab close clears a pending entry', async () => {
  const sw = await loadSw();
  await sw.send({ type: 'SCAN_PAGE', tabId: 5, packId: 42 });
  const nonce = sw.calls.tabsSendMessage[0].msg.nonce;
  sw.fireTabRemoved(5);
  const c = await sw.send({ type: 'CONSUME_PACK', nonce }, { id: RUNTIME_ID, tab: { id: 5 }, frameId: 0 });
  assert.equal(c.error, 'PACK_UNAVAILABLE_RETRY');
});

test('sw: live mode -- happy fetch, unavailable, contract mismatch, 404', async () => {
  const routes = {
    '/api/tracked-applications': {
      ok: true,
      status: 200,
      json: async () => ({
        schema_version: 1,
        tracked_applications: [{ id: 7, role_title: 'SWE', stage: 'preparing', pack_id: 42 }],
      }),
    },
  };
  const sw = await loadSw({
    localStore: { mode: 'live' },
    fetchImpl: async (url) => {
      const u = new URL(url);
      assert.ok(u.origin === 'http://localhost:8000');
      if (routes[u.pathname]) return routes[u.pathname];
      if (u.pathname === '/api/packs/999') return { ok: false, status: 404, json: async () => ({}) };
      if (u.pathname === '/api/packs/500') return { ok: true, status: 200, json: async () => { throw new Error('not json'); } };
      if (u.pathname === '/api/packs/1') return Promise.reject(new Error('conn refused'));
      throw new Error('unrouted ' + u.pathname);
    },
  });

  const list = await sw.send({ type: 'LIST_APPLICATIONS' });
  assert.equal(list.ok, true);
  assert.equal(list.applications[0].roleTitle, 'SWE');

  assert.deepEqual(await sw.send({ type: 'GET_PACK_META', packId: 999 }), { ok: false, error: 'NOT_FOUND' });
  assert.deepEqual(await sw.send({ type: 'GET_PACK_META', packId: 500 }), { ok: false, error: 'CONTRACT_MISMATCH' });
  assert.deepEqual(await sw.send({ type: 'GET_PACK_META', packId: 1 }), { ok: false, error: 'UPSTREAM_UNAVAILABLE' });
});

test('sw: fake chrome exposes no storage.session and nothing threw on load', async () => {
  const sw = await loadSw();
  assert.equal(sw.chrome.storage.session, undefined);
});
