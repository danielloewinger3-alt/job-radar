// Wire -> internal shape. The ONLY module that names the live API's fields.
// Downstream code (popup, scanner, overlay) consumes the normalized shapes and
// is unaffected by wire changes.
//
// Agent A's contract (final):
//   GET /api/tracked-applications
//     { schema_version: 1, tracked_applications: [ { id, role_title, stage,
//       pack_id } ] }                         // id, pack_id: positive ints
//   GET /api/tracked-applications/{id}/pack
//   GET /api/packs/{pack_id}
//   GET /api/packs/{pack_id}/autofill
//     { schema_version: 1, pack_id, tracked_application_id, reviewed, fields:[…] }

import { ContractError, normalizeAutofill, applyReviewedGate } from './packschema.js';

const POS_INT_STR = /^[1-9][0-9]{0,14}$/;

export function isPositiveIntId(v) {
  if (typeof v === 'number') return Number.isInteger(v) && v > 0 && v <= Number.MAX_SAFE_INTEGER;
  return typeof v === 'string' && POS_INT_STR.test(v);
}

function idToString(v) {
  return isPositiveIntId(v) ? String(v) : null;
}

const asStr = (v) => (typeof v === 'string' ? v : '');

/**
 * @returns {{applicationId:string, roleTitle:string, stage:string,
 *            packId:(string|null)}[]}
 */
export function normalizeApplicationList(raw) {
  const arr =
    raw && typeof raw === 'object' && Array.isArray(raw.tracked_applications)
      ? raw.tracked_applications
      : null;
  if (!arr) throw new ContractError('tracked-applications payload missing list');
  if (raw.schema_version !== 1) throw new ContractError('unsupported schema_version');

  const out = [];
  for (const a of arr) {
    if (!a || typeof a !== 'object') continue;
    const applicationId = idToString(a.id);
    if (!applicationId) continue; // drop malformed rows
    out.push({
      applicationId,
      roleTitle: asStr(a.role_title),
      stage: asStr(a.stage),
      packId: a.pack_id == null ? null : idToString(a.pack_id),
    });
  }
  return out;
}

/**
 * GET /api/packs/{pack_id} -- flat object.
 * @returns {{packId:string, applicationId:(string|null), reviewed:boolean,
 *            updatedAt:(string|null)}}
 */
export function normalizePackMeta(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new ContractError('pack payload is not an object');
  }
  if (raw.schema_version !== undefined && raw.schema_version !== 1) {
    throw new ContractError('unsupported schema_version');
  }
  const packId = idToString(raw.pack_id);
  if (!packId) throw new ContractError('pack_id is not a positive integer');
  return {
    packId,
    applicationId: idToString(raw.tracked_application_id),
    reviewed: raw.reviewed === true, // fail closed
    updatedAt: typeof raw.updated_at === 'string' ? raw.updated_at : null,
  };
}

/**
 * Combine normalized pack metadata with a raw /autofill payload.
 * `reviewed` fails closed: reviewed only if BOTH payloads say so.
 */
export function buildNormalizedPack(meta, autofillRaw) {
  const af = normalizeAutofill(autofillRaw); // throws ContractError on drift

  if (
    idToString(autofillRaw.pack_id) &&
    meta.packId &&
    idToString(autofillRaw.pack_id) !== meta.packId
  ) {
    throw new ContractError('pack_id mismatch between pack and autofill');
  }

  const reviewed = meta.reviewed === true && af.reviewed === true;
  const fields = applyReviewedGate(af.fields, reviewed);

  return {
    schemaVersion: af.schemaVersion,
    packId: meta.packId,
    applicationId: meta.applicationId,
    reviewed,
    updatedAt: meta.updatedAt,
    fields,
    omittedCount: af.omittedCount,
  };
}
