import test from 'node:test';
import assert from 'node:assert/strict';
import { createHandoffStore } from '../lib/handoff.js';

const NONCE_A = 'a'.repeat(32);
const NONCE_B = 'b'.repeat(32);

function fixedNonces(seq) {
  let i = 0;
  return () => seq[i++ % seq.length];
}

function makeStore(over = {}) {
  let clock = 1_000_000;
  const timers = [];
  const store = createHandoffStore({
    now: () => clock,
    ttlMs: 15000,
    randomNonce: over.randomNonce || fixedNonces([NONCE_A, NONCE_B]),
    setTimer: (fn, ms) => {
      const h = { fn, at: clock + ms };
      timers.push(h);
      return h;
    },
    clearTimer: (h) => {
      const idx = timers.indexOf(h);
      if (idx >= 0) timers.splice(idx, 1);
    },
    ...over,
  });
  return {
    store,
    advance: (ms) => {
      clock += ms;
    },
    runDueTimers: () => {
      for (const h of [...timers]) if (h.at <= clock) h.fn();
    },
  };
}

const trustedSender = { id: 'ext', tab: { id: 5 }, frameId: 0 };

test('handoff: happy path -- pack delivered once, then entry gone', () => {
  const { store } = makeStore();
  const nonce = store.put(5, { fields: [{ key: 'x' }] });
  assert.equal(nonce, NONCE_A);
  assert.equal(store.size(), 1);

  const r1 = store.consume(5, nonce, true);
  assert.equal(r1.ok, true);
  assert.deepEqual(r1.pack, { fields: [{ key: 'x' }] });
  assert.equal(store.size(), 0);

  const r2 = store.consume(5, nonce, true);
  assert.equal(r2.ok, false);
  assert.equal(r2.error, 'PACK_UNAVAILABLE_RETRY');
});

test('handoff: pack value survives the entry deletion that happens on consume', () => {
  const { store } = makeStore();
  const original = { fields: [{ key: 'secret', value: 'v' }] };
  store.put(5, original);
  const r = store.consume(5, NONCE_A, true);
  assert.equal(store.size(), 0); // deleted
  assert.equal(r.pack.fields[0].value, 'v'); // but still returned
});

test('handoff: rejects bad nonce, wrong nonce, untrusted sender', () => {
  const { store } = makeStore();
  store.put(5, { fields: [] });
  assert.equal(store.consume(5, 'short', true).error, 'INTERNAL');
  assert.equal(store.consume(5, NONCE_B, true).error, 'INTERNAL'); // wrong nonce
  assert.equal(store.size(), 1); // untouched on bad-nonce
  assert.equal(store.consume(5, NONCE_A, false).error, 'INTERNAL'); // untrusted
  assert.equal(store.consume(5, NONCE_A, true).ok, true); // still consumable
});

test('handoff: TTL expiry drops the entry', () => {
  const h = makeStore();
  h.store.put(5, { fields: [] });
  h.advance(15001);
  const r = h.store.consume(5, NONCE_A, true);
  assert.equal(r.error, 'PACK_UNAVAILABLE_RETRY');
});

test('handoff: timer fires and clears entry (simulated SW alarm)', () => {
  const h = makeStore();
  h.store.put(5, { fields: [] });
  h.advance(15000);
  h.runDueTimers();
  assert.equal(h.store.size(), 0);
});

test('handoff: clearFor removes an entry (tab closed / injection failed)', () => {
  const { store } = makeStore();
  store.put(5, { fields: [] });
  store.clearFor(5);
  assert.equal(store.has(5), false);
  assert.equal(store.consume(5, NONCE_A, true).error, 'PACK_UNAVAILABLE_RETRY');
});

test('handoff: a fresh put for the same tab replaces the previous entry + nonce', () => {
  const { store } = makeStore();
  store.put(5, { fields: [{ key: 'old' }] });
  const nonce2 = store.put(5, { fields: [{ key: 'new' }] });
  assert.equal(nonce2, NONCE_B);
  assert.equal(store.consume(5, NONCE_A, true).error, 'INTERNAL'); // old nonce dead
  const r = store.consume(5, NONCE_B, true);
  assert.equal(r.pack.fields[0].key, 'new');
});

test('handoff: entry keyed per tab; sender-tab mismatch cannot consume another tab', () => {
  const { store } = makeStore();
  store.put(5, { fields: [] });
  // simulate CONSUME arriving with sender.tab.id = 6
  const r = store.consume(6, NONCE_A, true);
  assert.equal(r.error, 'PACK_UNAVAILABLE_RETRY'); // no entry for tab 6
  assert.equal(store.has(5), true); // tab 5 entry intact
});
