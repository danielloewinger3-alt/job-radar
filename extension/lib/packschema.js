// Pack-payload validation: schema-version gate, envelope shape, per-field
// normalization + hardening. Used by lib/adapter.js (service worker) and by the
// bookmarklet fallback (which parses pasted JSON).
//
// Agent A's contract is final:
//   - schema_version is the integer 1
//   - pack_id / tracked_application_id are positive integers
//   - reviewed is the fail-closed authoritative reviewed state
//   - fields is an array; each field object has exactly:
//       key, label, value, type, autocomplete, source, answer_kind,
//       status, provenance, sensitive
//   - field types include "textarea"

import { decideProposable } from './policy.js';

export const SUPPORTED_SCHEMA_VERSION = 1;

export class ContractError extends Error {
  constructor(detail) {
    super(detail || 'contract_mismatch');
    this.name = 'ContractError';
  }
}

const FIELD_TYPES = new Set([
  'text', 'email', 'tel', 'url', 'number', 'date', 'textarea', 'select',
]);
const MAX_VALUE_LEN = 20000;
const FORBIDDEN_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

function safePlainCopy(obj) {
  const out = {};
  for (const k of Object.keys(obj)) {
    if (FORBIDDEN_KEYS.has(k)) continue;
    out[k] = obj[k];
  }
  return out;
}

function hardenValue(raw) {
  if (typeof raw === 'number' && Number.isFinite(raw)) raw = String(raw);
  if (typeof raw !== 'string') return null;
  // drop C0/C1 control chars, keeping only TAB (\x09) and LF (\x0a)
  let v = raw.replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '');
  if (v.length > MAX_VALUE_LEN) v = v.slice(0, MAX_VALUE_LEN);
  return v;
}

/**
 * Normalize one raw pack-field entry. Returns null if unusable (caller counts
 * these as "omitted"). `packReviewed` is the authoritative reviewed state and
 * gates narrative fields.
 */
export function normalizeField(entry, packReviewed) {
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return null;
  const e = safePlainCopy(entry);

  const label = typeof e.label === 'string' ? e.label.trim() : '';
  const type = typeof e.type === 'string' ? e.type.trim().toLowerCase() : '';
  const value = hardenValue(e.value);

  if (!label || !FIELD_TYPES.has(type) || value == null || value.trim() === '') {
    return null;
  }

  const key = typeof e.key === 'string' && e.key.trim() ? e.key.trim() : null;
  const autocompleteHint =
    typeof e.autocomplete === 'string' && e.autocomplete.trim()
      ? e.autocomplete.trim().toLowerCase()
      : null;
  const source = typeof e.source === 'string' ? e.source.trim().toLowerCase() : '';
  const answerKind =
    typeof e.answer_kind === 'string' ? e.answer_kind.trim().toLowerCase() : 'standard';
  const status = typeof e.status === 'string' ? e.status.trim().toLowerCase() : '';
  const provenance = typeof e.provenance === 'string' ? e.provenance.trim() : '';
  const sensitive = e.sensitive === true;

  const decision = decideProposable({
    source,
    answerKind,
    status,
    sensitive,
    reviewed: packReviewed === true,
  });

  return {
    key,
    label,
    value,
    type,
    autocompleteHint,
    source,
    answerKind,
    status,
    provenance,
    sensitive,
    proposable: decision.proposable,
    policyReason: decision.reason,
  };
}

/**
 * Validate + normalize an /autofill payload (or the bookmarklet's pasted JSON).
 * Throws ContractError on envelope-level drift. An explicit empty `fields: []` is
 * valid; a missing or non-array `fields` is drift.
 *
 * @returns {{schemaVersion:number, reviewed:boolean, fields:object[],
 *            omittedCount:number}}
 */
export function normalizeAutofill(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new ContractError('autofill payload is not an object');
  }
  if (raw.schema_version !== SUPPORTED_SCHEMA_VERSION) {
    throw new ContractError('unsupported or missing schema_version');
  }
  if (!('fields' in raw) || !Array.isArray(raw.fields)) {
    throw new ContractError('fields is missing or not an array');
  }

  const reviewed = raw.reviewed === true; // fail closed
  const fields = [];
  let omittedCount = 0;
  for (const entry of raw.fields) {
    const nf = normalizeField(entry, reviewed);
    if (nf) fields.push(nf);
    else omittedCount += 1;
  }

  return { schemaVersion: raw.schema_version, reviewed, fields, omittedCount };
}

/**
 * Tighten proposability once the FINAL reviewed state is known (pack meta AND
 * autofill payload must agree; disagreement -> unreviewed). Narrative fields
 * flip to non-proposable if the final state is unreviewed. Monotonic: never
 * loosens a decision.
 */
export function applyReviewedGate(fields, finalReviewed) {
  if (finalReviewed === true) return fields;
  return fields.map((f) =>
    f.answerKind === 'narrative' && f.proposable
      ? { ...f, proposable: false, policyReason: 'narrative_unreviewed_pack' }
      : f,
  );
}
