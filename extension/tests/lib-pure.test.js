import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { tokenize, tokenSet, overlapCount } from '../lib/normalize.js';
import { decideProposable } from '../lib/policy.js';
import {
  normalizeAutofill,
  normalizeField,
  applyReviewedGate,
  ContractError,
} from '../lib/packschema.js';
import {
  normalizeApplicationList,
  normalizePackMeta,
  buildNormalizedPack,
  isPositiveIntId,
} from '../lib/adapter.js';
import { validateMessage, isTrustedSender, isTrustedTopFrameSender } from '../lib/messages.js';
import { buildUrl, FETCH_OPTIONS, ALLOWED_ORIGINS } from '../lib/routes.js';
import {
  scorePair,
  buildProposals,
  resolveSelectOption,
  typeCompatible,
  MIN_CONFIDENCE,
} from '../lib/scoring.js';
import { mockListApplications, mockBuildPack, mockPackMeta } from '../lib/mockstore.js';

const FIXTURE = JSON.parse(
  readFileSync(new URL('../testbed/autofill.mock.json', import.meta.url), 'utf8'),
);

// ---------------------------------------------------------------------------
test('normalize: tokenize strips stopwords, punctuation, diacritics', () => {
  assert.deepEqual(tokenize('Your First-Name (café)'), ['first', 'name', 'cafe']);
  assert.equal(overlapCount(tokenSet('email address'), tokenSet('Email')), 1);
});

// ---------------------------------------------------------------------------
test('policy: sensitive:true never blocks proposability', () => {
  const r = decideProposable({ source: 'profile', answerKind: 'standard', status: 'sourced', sensitive: true });
  assert.equal(r.proposable, true);
});

test('policy: standard needs profile/user_supplied source', () => {
  assert.equal(decideProposable({ source: 'profile', answerKind: 'standard' }).proposable, true);
  assert.equal(decideProposable({ source: 'user_supplied', answerKind: 'standard' }).proposable, true);
  assert.equal(decideProposable({ source: '', answerKind: 'standard' }).proposable, false);
  assert.equal(decideProposable({ source: 'cv', answerKind: 'standard' }).proposable, false);
});

test('policy: declared answer requires user_supplied AND status sourced', () => {
  assert.equal(decideProposable({ source: 'user_supplied', answerKind: 'declared_answer', status: 'sourced' }).proposable, true);
  assert.equal(decideProposable({ source: 'user_supplied', answerKind: 'declared_answer', status: 'needs_input' }).proposable, false);
  assert.equal(decideProposable({ source: 'cv', answerKind: 'declared_answer', status: 'sourced' }).proposable, false);
  assert.equal(decideProposable({ source: 'profile', answerKind: 'declared_answer', status: 'sourced' }).proposable, false);
});

test('policy: narrative requires a reviewed pack', () => {
  assert.equal(decideProposable({ source: 'user_supplied', answerKind: 'narrative', status: 'sourced', reviewed: true }).proposable, true);
  assert.equal(decideProposable({ source: 'user_supplied', answerKind: 'narrative', status: 'sourced', reviewed: false }).proposable, false);
  assert.equal(decideProposable({ source: 'generated', answerKind: 'narrative', status: 'sourced', reviewed: true }).proposable, true);
});

test('policy: needs_input is never proposable', () => {
  assert.equal(decideProposable({ source: 'user_supplied', answerKind: 'standard', status: 'needs_input' }).proposable, false);
});

// ---------------------------------------------------------------------------
test('packschema: only integer schema_version 1 is accepted', () => {
  assert.throws(() => normalizeAutofill({ schema_version: 'autofill/v1', fields: [] }), ContractError);
  assert.throws(() => normalizeAutofill({ schema_version: 2, fields: [] }), ContractError);
  assert.doesNotThrow(() => normalizeAutofill({ schema_version: 1, reviewed: true, fields: [] }));
});

test('packschema: fields missing or non-array is a contract error; [] is valid', () => {
  assert.throws(() => normalizeAutofill({ schema_version: 1 }), ContractError);
  assert.throws(() => normalizeAutofill({ schema_version: 1, fields: {} }), ContractError);
  const ok = normalizeAutofill({ schema_version: 1, reviewed: true, fields: [] });
  assert.deepEqual(ok.fields, []);
  assert.equal(ok.omittedCount, 0);
});

test('packschema: malformed field entries dropped, valid siblings kept, counted', () => {
  const res = normalizeAutofill({
    schema_version: 1,
    reviewed: true,
    fields: [
      { key: 'a', label: 'A', value: 'x', type: 'text', source: 'profile', answer_kind: 'standard', status: 'sourced' },
      { key: 'b', label: 'B', value: '', type: 'text' }, // empty value
      { key: 'c', label: 'C', value: 'y', type: 'color' }, // bad type
      null,
      { label: '', value: 'z', type: 'text' }, // no label
    ],
  });
  assert.equal(res.fields.length, 1);
  assert.equal(res.omittedCount, 4);
});

test('packschema: textarea is a valid type', () => {
  const f = normalizeField({ label: 'Why', value: 'hi', type: 'textarea', source: 'user_supplied', answer_kind: 'narrative', status: 'sourced' }, true);
  assert.equal(f.type, 'textarea');
  assert.equal(f.proposable, true);
});

test('packschema: prototype-pollution keys are ignored; control chars stripped; length capped', () => {
  const f = normalizeField(
    JSON.parse('{"__proto__":{"polluted":1},"label":"L","value":"a\\u0007b\\tc","type":"text","source":"profile","answer_kind":"standard","status":"sourced"}'),
    true,
  );
  assert.equal({}.polluted, undefined);
  assert.equal(f.value, 'ab\tc');
  const long = normalizeField({ label: 'L', value: 'z'.repeat(50000), type: 'text', source: 'profile', answer_kind: 'standard', status: 'sourced' }, true);
  assert.equal(long.value.length, 20000);
});

test('packschema: applyReviewedGate flips narrative off when finally unreviewed', () => {
  const fields = [
    { key: 'n', answerKind: 'narrative', proposable: true, policyReason: 'narrative_reviewed_pack' },
    { key: 's', answerKind: 'standard', proposable: true, policyReason: 'standard_sourced' },
  ];
  const gated = applyReviewedGate(fields, false);
  assert.equal(gated[0].proposable, false);
  assert.equal(gated[1].proposable, true);
  assert.equal(applyReviewedGate(fields, true), fields);
});

// ---------------------------------------------------------------------------
test('adapter: positive-int id validation', () => {
  assert.equal(isPositiveIntId(7), true);
  assert.equal(isPositiveIntId('42'), true);
  assert.equal(isPositiveIntId(0), false);
  assert.equal(isPositiveIntId(-3), false);
  assert.equal(isPositiveIntId('07'), false);
  assert.equal(isPositiveIntId('7a'), false);
  assert.equal(isPositiveIntId('../etc'), false);
});

test('adapter: normalizeApplicationList uses canonical names, drops bad rows', () => {
  const list = normalizeApplicationList({
    schema_version: 1,
    tracked_applications: [
      { id: 7, role_title: 'SWE', stage: 'preparing', pack_id: 42 },
      { id: 8, role_title: 'DE', stage: 'preparing', pack_id: null },
      { id: 0, role_title: 'bad', stage: 'x', pack_id: 1 },
      { role_title: 'missing id' },
    ],
  });
  assert.deepEqual(list, [
    { applicationId: '7', roleTitle: 'SWE', stage: 'preparing', packId: '42' },
    { applicationId: '8', roleTitle: 'DE', stage: 'preparing', packId: null },
  ]);
});

test('adapter: normalizeApplicationList rejects wrong schema_version', () => {
  assert.throws(() => normalizeApplicationList({ schema_version: 2, tracked_applications: [] }), ContractError);
});

test('adapter: buildNormalizedPack fail-closed reviewed + pack_id mismatch', () => {
  const meta = { packId: '42', applicationId: '7', reviewed: true, updatedAt: null };
  const af = { schema_version: 1, pack_id: 42, tracked_application_id: 7, reviewed: false, fields: [] };
  assert.equal(buildNormalizedPack(meta, af).reviewed, false); // one says false -> false

  const afMismatch = { schema_version: 1, pack_id: 99, reviewed: true, fields: [] };
  assert.throws(() => buildNormalizedPack(meta, afMismatch), ContractError);
});

// ---------------------------------------------------------------------------
test('messages: rejects unknown types and unexpected keys', () => {
  assert.equal(validateMessage({ type: 'NOPE' }).ok, false);
  assert.equal(validateMessage({ type: 'LIST_APPLICATIONS', extra: 1 }).ok, false);
  assert.equal(validateMessage('string').ok, false);
});

test('messages: ids must be positive integers, no path strings', () => {
  assert.equal(validateMessage({ type: 'GET_PACK_META', packId: 42 }).ok, true);
  assert.equal(validateMessage({ type: 'GET_PACK_META', packId: '42' }).params.packId, '42');
  assert.equal(validateMessage({ type: 'GET_PACK_META', packId: 0 }).ok, false);
  assert.equal(validateMessage({ type: 'GET_PACK_META', packId: -1 }).ok, false);
  assert.equal(validateMessage({ type: 'GET_PACK_META', packId: '../secret' }).ok, false);
  assert.equal(validateMessage({ type: 'GET_PACK_META', packId: '7/pack' }).ok, false);
});

test('messages: no url/path/method/header keys are ever accepted', () => {
  for (const bad of ['url', 'path', 'method', 'headers', 'body', 'origin']) {
    assert.equal(validateMessage({ type: 'SCAN_PAGE', tabId: 1, packId: 2, [bad]: 'x' }).ok, false);
  }
});

test('messages: SCAN_PAGE tabId + nonce + mode validation', () => {
  assert.equal(validateMessage({ type: 'SCAN_PAGE', tabId: 5, packId: 2 }).ok, true);
  assert.equal(validateMessage({ type: 'SCAN_PAGE', tabId: -1, packId: 2 }).ok, false);
  assert.equal(validateMessage({ type: 'CONSUME_PACK', nonce: 'abc' }).ok, false);
  assert.equal(validateMessage({ type: 'CONSUME_PACK', nonce: 'a'.repeat(32) }).ok, true);
  assert.equal(validateMessage({ type: 'SET_MODE', mode: 'prod' }).ok, false);
  assert.equal(validateMessage({ type: 'SET_MODE', mode: 'live' }).ok, true);
});

test('messages: sender trust helpers', () => {
  assert.equal(isTrustedSender({ id: 'x' }, 'x'), true);
  assert.equal(isTrustedSender({ id: 'y' }, 'x'), false);
  assert.equal(isTrustedTopFrameSender({ id: 'x', tab: { id: 9 }, frameId: 0 }, 'x', 9), true);
  assert.equal(isTrustedTopFrameSender({ id: 'x', tab: { id: 9 }, frameId: 1 }, 'x', 9), false);
  assert.equal(isTrustedTopFrameSender({ id: 'x', tab: { id: 8 }, frameId: 0 }, 'x', 9), false);
});

// ---------------------------------------------------------------------------
test('routes: only localhost origins, positive-int ids, fixed options', () => {
  assert.equal(buildUrl('http://localhost:8000', 'listApplications'), 'http://localhost:8000/api/tracked-applications');
  assert.equal(buildUrl('http://127.0.0.1:8000', 'packAutofill', '42'), 'http://127.0.0.1:8000/api/packs/42/autofill');
  assert.throws(() => buildUrl('http://evil.example', 'listApplications'));
  assert.throws(() => buildUrl('http://localhost:8000', 'nope'));
  assert.throws(() => buildUrl('http://localhost:8000', 'packMeta', '../x'));
  assert.throws(() => buildUrl('http://localhost:8000', 'packMeta', '7 OR 1'));
  assert.equal(FETCH_OPTIONS.method, 'GET');
  assert.equal(FETCH_OPTIONS.credentials, 'omit');
  assert.equal(FETCH_OPTIONS.redirect, 'error');
  assert.deepEqual(ALLOWED_ORIGINS, ['http://localhost:8000', 'http://127.0.0.1:8000']);
});

// ---------------------------------------------------------------------------
const pf = (o) => ({
  key: o.key || 'k', label: o.label || 'L', value: o.value ?? 'v', type: o.type || 'text',
  autocompleteHint: o.autocompleteHint || null, source: 'profile', answerKind: 'standard',
  status: 'sourced', provenance: '', sensitive: false, proposable: o.proposable !== false,
  policyReason: 'x',
});
const gf = (o) => ({
  el: { tagName: (o.tag || 'input').toUpperCase() }, index: o.index ?? 0, tag: o.tag || 'input',
  inputType: o.inputType || 'text', label: o.label || '', name: o.name || '', id: o.id || '',
  placeholder: '', ariaLabel: '', autocomplete: o.autocomplete || '', disabled: false,
  options: o.options || null,
});

test('scoring: type compatibility incl. textarea', () => {
  assert.equal(typeCompatible('textarea', 'textarea'), true);
  assert.equal(typeCompatible('textarea', 'text'), false);
  assert.equal(typeCompatible('email', 'text'), true);
  assert.equal(typeCompatible('email', 'tel'), false);
});

test('scoring: score alone without a meaningful token/autocomplete match does not propose', () => {
  const r = scorePair(pf({ key: 'zzz', label: 'Zzz' }), gf({ label: 'Totally unrelated' }));
  assert.equal(r.meaningful, false);
  const proposals = buildProposals([pf({ key: 'zzz', label: 'Zzz' })], [gf({ label: 'Totally unrelated' })]);
  assert.equal(proposals.length, 0);
});

test('scoring: strong label match -> proposable; order-independent', () => {
  const packFields = [pf({ key: 'email', label: 'Email address', type: 'email', autocompleteHint: 'email' })];
  const g1 = gf({ label: 'Email address', inputType: 'email', autocomplete: 'email', index: 0 });
  const g2 = gf({ label: 'Phone', inputType: 'tel', index: 1 });
  const a = buildProposals(packFields, [g1, g2]);
  const b = buildProposals(packFields, [g2, g1]);
  assert.equal(a.length, 1);
  assert.equal(a[0].state, 'proposable');
  assert.ok(a[0].confidence >= MIN_CONFIDENCE);
  assert.equal(JSON.stringify(a.map((p) => p.packKey)), JSON.stringify(b.map((p) => p.packKey)));
});

test('scoring: two page fields equally matching -> ambiguous, no target', () => {
  const packFields = [pf({ key: 'name', label: 'Name' })];
  const g1 = gf({ label: 'Name', name: 'name', index: 0 });
  const g2 = gf({ label: 'Name', name: 'name', index: 1 });
  const [p] = buildProposals(packFields, [g1, g2]);
  assert.equal(p.state, 'ambiguous');
  assert.equal(p.targetPageIndex, null);
});

test('scoring: no two proposals are simultaneously acceptable for one page field', () => {
  const packFields = [
    pf({ key: 'first_name', label: 'First name', autocompleteHint: 'name' }),
    pf({ key: 'last_name', label: 'Last name', autocompleteHint: 'name' }),
  ];
  const g = gf({ label: 'Name', name: 'name', autocomplete: 'name', index: 0 });
  const proposals = buildProposals(packFields, [g]);
  const acceptable = proposals.filter((p) => p.state === 'proposable' && p.targetPageIndex != null);
  const targets = acceptable.map((p) => p.targetPageIndex);
  assert.equal(new Set(targets).size, targets.length); // no duplicate accept targets
  assert.ok(proposals.every((p) => p.state !== 'proposable')); // contested -> not silently accepted
});

test('scoring: non-proposable pack field surfaces as preview only', () => {
  const packFields = [pf({ key: 'email', label: 'Email address', type: 'email', autocompleteHint: 'email', proposable: false })];
  const g = gf({ label: 'Email address', inputType: 'email', autocomplete: 'email' });
  const [p] = buildProposals(packFields, [g]);
  assert.equal(p.state, 'preview');
});

test('scoring: a preview-only pack field does not push a fillable one into ambiguity', () => {
  const packFields = [
    pf({ key: 'email', label: 'Email address', type: 'email', autocompleteHint: 'email' }),
    pf({ key: 'alt_email', label: 'Alternate email', type: 'email', autocompleteHint: 'email', proposable: false }),
  ];
  const g = gf({ label: 'Email address', inputType: 'email', autocomplete: 'email', index: 0 });
  const proposals = buildProposals(packFields, [g]);
  const email = proposals.find((p) => p.packKey === 'email');
  const alt = proposals.find((p) => p.packKey === 'alt_email');
  assert.equal(email.state, 'proposable'); // not forced ambiguous by the preview field
  assert.equal(email.targetPageIndex, 0);
  assert.equal(alt.state, 'preview');
});

test('scoring: resolveSelectOption -- exact value, ambiguous, missing, disabled; NO label fallback', () => {
  const base = { inputType: 'select' };
  assert.equal(
    resolveSelectOption({ value: 'Yes', label: 'Work authorization' }, { ...base, options: [{ value: 'Yes', text: 'Yes', disabled: false }, { value: 'No', text: 'No', disabled: false }] }).status,
    'ok',
  );
  // label is "Work authorization" but no option matches the *value* -> no_option
  assert.equal(
    resolveSelectOption({ value: 'Authorized', label: 'Work authorization' }, { ...base, options: [{ value: 'Yes', text: 'Yes', disabled: false }] }).status,
    'no_option',
  );
  assert.equal(
    resolveSelectOption({ value: 'Yes', label: 'x' }, { ...base, options: [{ value: 'Yes', text: 'Yes', disabled: false }, { value: 'yes', text: 'Yes', disabled: false }] }).status,
    'ambiguous',
  );
  assert.equal(
    resolveSelectOption({ value: 'Yes', label: 'x' }, { ...base, options: [{ value: 'Yes', text: 'Yes', disabled: true }] }).status,
    'disabled_only',
  );
});

// ---------------------------------------------------------------------------
test('mockstore: list + buildPack against the shipped fixture', () => {
  const apps = mockListApplications(FIXTURE);
  assert.ok(apps.find((a) => a.applicationId === '7' && a.packId === '42'));
  assert.ok(apps.find((a) => a.packId === null)); // application 12 has no pack

  const p42 = mockBuildPack(FIXTURE, '42');
  assert.equal(p42.pack.reviewed, true);
  assert.equal(p42.pack.omittedCount, 1); // the color-typed field
  const byKey = Object.fromEntries(p42.pack.fields.map((f) => [f.key, f.proposable]));
  assert.equal(byKey.full_name, true);
  assert.equal(byKey.email, true); // sensitive:true still proposable
  assert.equal(byKey.work_authorization, true);
  assert.equal(byKey.notice_period, false); // needs_input
  assert.equal(byKey.clearance_from_cv, false); // declared, source=cv
  assert.equal(byKey.unsourced_email, false); // standard, no source
  assert.equal(byKey.cover_note, true); // narrative + reviewed
  assert.equal(byKey.auto_summary, true); // generated narrative + reviewed

  const p55 = mockBuildPack(FIXTURE, '55');
  assert.equal(p55.pack.reviewed, false);
  const n = p55.pack.fields.find((f) => f.key === 'cover_note');
  assert.equal(n.proposable, false); // narrative in an unreviewed pack

  assert.deepEqual(mockPackMeta(FIXTURE, '999'), { error: 'NOT_FOUND' });
});
