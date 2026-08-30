import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { classifyDescriptor, scanFields, hasCaptcha } from '../lib/classify.js';
import { renderOverlay, removeOverlay, writeValue, RefusedWrite, HOST_ID } from '../lib/overlay.js';
import { makeDom, withDomGlobals } from './helpers.js';

const TESTBED_HTML = readFileSync(new URL('../testbed/form.html', import.meta.url), 'utf8');

// ---------------------------------------------------------------------------
// classifyDescriptor (pure)
// ---------------------------------------------------------------------------

const d = (o) => ({
  tag: o.tag || 'input', inputType: o.inputType || 'text', label: o.label || '',
  name: o.name || '', id: o.id || '', placeholder: o.placeholder || '',
  ariaLabel: o.ariaLabel || '', autocomplete: o.autocomplete || '',
  disabled: !!o.disabled, readOnly: !!o.readOnly, insideCaptcha: !!o.insideCaptcha,
});

test('classify: structural exclusions by input type', () => {
  for (const t of ['password', 'hidden', 'file', 'submit', 'reset', 'button', 'image', 'checkbox', 'radio']) {
    const v = classifyDescriptor(d({ inputType: t }));
    assert.equal(v.fillable, false, t);
    assert.match(v.excludedReason, /field_type/);
  }
});

test('classify: unknown input types are excluded, textarea/select allowed', () => {
  assert.equal(classifyDescriptor(d({ inputType: 'range' })).fillable, false);
  assert.equal(classifyDescriptor(d({ inputType: 'color' })).fillable, false);
  assert.equal(classifyDescriptor(d({ tag: 'textarea', inputType: 'textarea' })).fillable, true);
  assert.equal(classifyDescriptor(d({ tag: 'select', inputType: 'select' })).fillable, true);
});

test('classify: sensitive / legal / EEO / gov-id / payment / medical / signature patterns', () => {
  const cases = {
    payment: 'Card number', payment2: 'CVV', payment3: 'IBAN', payment4: 'Sort code',
    gov_id: 'Social Security Number', gov_id2: 'National Insurance number', gov_id3: 'Passport number',
    medical: 'Do you have a disability?', medical2: 'medical condition',
    eeo: 'Race / ethnicity', eeo2: 'Gender identity', eeo3: 'Protected veteran status',
    signature: 'Type your full name to sign', signature2: 'Electronic signature',
    legal: 'I certify that the information is true', legal2: 'I agree to the terms and conditions',
  };
  for (const [k, label] of Object.entries(cases)) {
    const v = classifyDescriptor(d({ label }));
    assert.equal(v.fillable, false, k + ' :: ' + label);
    assert.match(v.excludedReason, /^sensitive:/, k);
  }
});

test('classify: safe fields pass', () => {
  assert.equal(classifyDescriptor(d({ label: 'Full name', autocomplete: 'name' })).fillable, true);
  assert.equal(classifyDescriptor(d({ label: 'Email address', inputType: 'email' })).fillable, true);
  assert.equal(classifyDescriptor(d({ label: 'Notice period' })).fillable, true);
  assert.equal(classifyDescriptor(d({ label: 'Disabled field', disabled: true })).fillable, false);
  assert.equal(classifyDescriptor(d({ label: 'In a captcha', insideCaptcha: true })).fillable, false);
});

// ---------------------------------------------------------------------------
// scanFields on the actual testbed form
// ---------------------------------------------------------------------------

test('scanFields: testbed form -> safe fields fillable, sensitive/legal excluded', () => {
  const win = makeDom(TESTBED_HTML);
  const restore = withDomGlobals(win);
  try {
    const { fillable, excluded } = scanFields(win.document);
    const fillableIds = fillable.map((f) => f.id).sort();
    const excludedIds = excluded.map((f) => f.id).sort();

    for (const id of ['full-name', 'email', 'phone', 'linkedin', 'portfolio', 'city', 'years', 'start-date', 'notice', 'work-auth', 'sponsorship', 'cover']) {
      assert.ok(fillableIds.includes(id), 'expected fillable: ' + id);
    }
    for (const id of ['pw', 'card', 'cvv', 'ssn', 'disability', 'race', 'gender', 'veteran', 'signature', 'certify', 'agree', 'resume', 'tracking-token', 'fake-captcha']) {
      assert.ok(excludedIds.includes(id), 'expected excluded: ' + id);
      assert.ok(!fillableIds.includes(id), 'must not be fillable: ' + id);
    }
    assert.equal(hasCaptcha(win.document), true);
  } finally {
    restore();
  }
});

// ---------------------------------------------------------------------------
// writeValue runtime guard
// ---------------------------------------------------------------------------

test('writeValue: refuses non-fillable elements even if classification is bypassed', () => {
  const win = makeDom();
  const restore = withDomGlobals(win);
  try {
    const doc = win.document;
    const form = doc.createElement('form');
    const btn = doc.createElement('button');
    btn.type = 'submit';
    const submit = doc.createElement('input');
    submit.type = 'submit';
    const file = doc.createElement('input');
    file.type = 'file';
    const pw = doc.createElement('input');
    pw.type = 'password';
    const cb = doc.createElement('input');
    cb.type = 'checkbox';
    for (const el of [btn, submit, file, pw, cb]) form.appendChild(el);
    doc.body.appendChild(form);

    assert.throws(() => writeValue(form, 'x'), RefusedWrite);
    assert.throws(() => writeValue(btn, 'x'), RefusedWrite);
    assert.throws(() => writeValue(submit, 'x'), RefusedWrite);
    assert.throws(() => writeValue(file, 'x'), RefusedWrite);
    assert.throws(() => writeValue(pw, 'x'), RefusedWrite);
    assert.throws(() => writeValue(cb, 'x'), RefusedWrite);
  } finally {
    restore();
  }
});

test('writeValue: fills a text input and dispatches ONLY input + change', () => {
  const win = makeDom();
  const restore = withDomGlobals(win);
  try {
    const doc = win.document;
    const input = doc.createElement('input');
    input.type = 'text';
    doc.body.appendChild(input);

    const seen = [];
    for (const type of ['input', 'change', 'focus', 'blur', 'click', 'keydown', 'submit']) {
      input.addEventListener(type, () => seen.push(type));
    }
    let clicked = false;
    input.click = () => { clicked = true; };
    let focused = false;
    input.focus = () => { focused = true; };

    writeValue(input, 'hello');
    assert.equal(input.value, 'hello');
    assert.deepEqual(seen, ['input', 'change']);
    assert.equal(clicked, false);
    assert.equal(focused, false);
  } finally {
    restore();
  }
});

test('writeValue: select only accepts an existing enabled option', () => {
  const win = makeDom();
  const restore = withDomGlobals(win);
  try {
    const doc = win.document;
    const sel = doc.createElement('select');
    for (const [v, dis] of [['', false], ['Yes', false], ['No', true]]) {
      const o = doc.createElement('option');
      o.value = v;
      o.textContent = v || 'Select';
      o.disabled = dis;
      sel.appendChild(o);
    }
    doc.body.appendChild(sel);

    assert.throws(() => writeValue(sel, 'Maybe'), RefusedWrite); // no such option
    assert.throws(() => writeValue(sel, 'No'), RefusedWrite); // disabled option
    writeValue(sel, 'Yes');
    assert.equal(sel.value, 'Yes');
  } finally {
    restore();
  }
});

// ---------------------------------------------------------------------------
// overlay rendering
// ---------------------------------------------------------------------------

function baseController() {
  return { onAccept: () => ({ ok: true }), onSkip() {}, onPickTarget() {}, onClose() {} };
}

test('overlay: exactly one host; re-render replaces rather than duplicates', () => {
  const win = makeDom();
  const restore = withDomGlobals(win);
  try {
    const doc = win.document;
    const args = {
      doc, pack: { applicationId: '7' }, proposals: [], pageFields: [], excluded: [],
      previewOnly: false, omittedCount: 0, controller: baseController(),
    };
    renderOverlay(args);
    renderOverlay(args);
    renderOverlay(args);
    assert.equal(doc.querySelectorAll('#' + HOST_ID).length, 1);
    removeOverlay(doc);
    assert.equal(doc.querySelectorAll('#' + HOST_ID).length, 0);
  } finally {
    restore();
  }
});

test('overlay: hostile pack text renders inert (textContent, no script execution)', () => {
  const win = makeDom();
  const restore = withDomGlobals(win);
  try {
    const doc = win.document;
    const evil = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>';
    renderOverlay({
      doc,
      pack: { heading: evil },
      proposals: [{
        packIndex: 0, packKey: 'k', packLabel: evil, value: evil, type: 'text',
        source: 'profile', answerKind: 'standard', status: 'sourced', provenance: '',
        sensitive: false, policyReason: 'x', state: 'preview', acceptableIfPicked: false,
        confidence: 0.5, targetPageIndex: 0, selectResolution: null, candidates: [],
      }],
      pageFields: [{ label: evil }],
      excluded: [], previewOnly: false, omittedCount: 0, controller: baseController(),
    });
    const host = doc.getElementById(HOST_ID);
    assert.equal(host.shadowRoot.querySelectorAll('img,script').length, 0);
    assert.equal(win.__pwned, undefined);
    assert.ok(host.shadowRoot.textContent.includes('onerror'));
  } finally {
    restore();
  }
});

test('overlay: previewOnly disables every Accept control', () => {
  const win = makeDom();
  const restore = withDomGlobals(win);
  try {
    const doc = win.document;
    renderOverlay({
      doc, pack: { applicationId: '9' },
      proposals: [{
        packIndex: 0, packKey: 'full_name', packLabel: 'Full name', value: 'Jordan', type: 'text',
        source: 'profile', answerKind: 'standard', status: 'sourced', provenance: '',
        sensitive: false, policyReason: 'x', state: 'proposable', acceptableIfPicked: true,
        confidence: 0.9, targetPageIndex: 0, selectResolution: null, candidates: [],
      }],
      pageFields: [{ label: 'Full name' }],
      excluded: [], previewOnly: true, omittedCount: 0, controller: baseController(),
    });
    const host = doc.getElementById(HOST_ID);
    const accepts = [...host.shadowRoot.querySelectorAll('button.accept')];
    assert.ok(accepts.length >= 1);
    assert.ok(accepts.every((b) => b.hasAttribute('disabled')));
  } finally {
    restore();
  }
});

test('overlay: Accept calls controller once and marks the row filled', () => {
  const win = makeDom();
  const restore = withDomGlobals(win);
  try {
    const doc = win.document;
    let calls = 0;
    renderOverlay({
      doc, pack: { applicationId: '7' },
      proposals: [{
        packIndex: 0, packKey: 'full_name', packLabel: 'Full name', value: 'Jordan', type: 'text',
        source: 'profile', answerKind: 'standard', status: 'sourced', provenance: '',
        sensitive: false, policyReason: 'x', state: 'proposable', acceptableIfPicked: true,
        confidence: 0.9, targetPageIndex: 0, selectResolution: null, candidates: [],
      }],
      pageFields: [{ label: 'Full name' }],
      excluded: [], previewOnly: false, omittedCount: 0,
      controller: { ...baseController(), onAccept: () => { calls += 1; return { ok: true }; } },
    });
    const host = doc.getElementById(HOST_ID);
    const btn = host.shadowRoot.querySelector('button.accept');
    btn.dispatchEvent(new win.Event('click'));
    btn.dispatchEvent(new win.Event('click'));
    assert.equal(calls, 1); // disabled after first accept
    assert.ok(host.shadowRoot.querySelector('.row').classList.contains('filled'));
  } finally {
    restore();
  }
});
