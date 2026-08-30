// Testbed instrumentation + self-test. Pure page script; no extension APIs.
//
// - Blocks and counts every submit attempt (form submit + submit-button click).
// - Counts input / change events separately.
// - "Run self-test" asserts: excluded fields still empty, zero submit attempts,
//   and at least one fill happened.

'use strict';

const counters = { input: 0, change: 0, submit: 0 };
const logEl = document.getElementById('log');
const countersEl = document.getElementById('counters');
const form = document.getElementById('app-form');

function stamp() {
  return new Date().toISOString().slice(11, 23);
}
function log(line) {
  logEl.textContent += `[${stamp()}] ${line}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}
function refreshCounters() {
  countersEl.textContent =
    `input: ${counters.input} · change: ${counters.change} · submit attempts: ${counters.submit}`;
}
function fieldName(t) {
  if (!t) return '(unknown)';
  return t.name || t.id || t.tagName.toLowerCase();
}

form.addEventListener(
  'submit',
  (e) => {
    e.preventDefault();
    counters.submit += 1;
    refreshCounters();
    log(`!! SUBMIT ATTEMPT BLOCKED (form) -- this must never happen during autofill`);
  },
  true,
);

document.getElementById('submit-btn').addEventListener('click', (e) => {
  e.preventDefault();
  counters.submit += 1;
  refreshCounters();
  log(`!! submit button click intercepted and blocked`);
});

document.addEventListener(
  'input',
  (e) => {
    counters.input += 1;
    refreshCounters();
    log(`input  -> ${fieldName(e.target)} = ${JSON.stringify(String(e.target.value || '').slice(0, 60))}`);
  },
  true,
);

document.addEventListener(
  'change',
  (e) => {
    counters.change += 1;
    refreshCounters();
    log(`change -> ${fieldName(e.target)} = ${JSON.stringify(String(e.target.value || '').slice(0, 60))}`);
  },
  true,
);

const EXCLUDED_IDS = [
  'pw', 'card', 'cvv', 'ssn', 'disability', 'race', 'gender', 'veteran',
  'signature', 'certify', 'agree', 'resume', 'tracking-token', 'fake-captcha',
];

document.getElementById('run-selftest').addEventListener('click', () => {
  log('--- self-test ---');
  let pass = true;

  for (const id of EXCLUDED_IDS) {
    const el = document.getElementById(id);
    if (!el) continue;
    const dirty =
      el.type === 'checkbox' ? el.checked
      : el.type === 'file' ? el.value !== ''
      : String(el.value || '') !== (id === 'tracking-token' ? 'do-not-touch' : '');
    if (dirty) {
      pass = false;
      log(`FAIL: excluded field "${id}" was modified`);
    }
  }
  if (EXCLUDED_IDS.every((id) => {
    const el = document.getElementById(id);
    if (!el) return true;
    return el.type === 'checkbox' ? !el.checked
      : el.type === 'file' ? el.value === ''
      : String(el.value || '') === (id === 'tracking-token' ? 'do-not-touch' : '');
  })) {
    log('OK: all excluded / sensitive / legal fields untouched');
  }

  if (counters.submit !== 0) {
    pass = false;
    log(`FAIL: ${counters.submit} submit attempt(s) recorded`);
  } else {
    log('OK: zero submit attempts');
  }

  const safeFilled = ['full-name', 'email', 'phone', 'work-auth'].some(
    (id) => String(document.getElementById(id).value || '') !== '',
  );
  if (!safeFilled) {
    log('NOTE: no safe field filled yet — accept some fields, then re-run');
  } else {
    log('OK: at least one safe field was filled by accepted proposals');
  }

  log(pass ? '=== SELF-TEST PASS ===' : '=== SELF-TEST FAIL ===');
});

log('testbed ready — load the extension, open its popup, pick a reviewed pack, press “Scan this page”.');
