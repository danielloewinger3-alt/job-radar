// Answers the same queries the live adapter does, from the bundled fixture
// testbed/autofill.mock.json. Used only when mode === "mock". The fixture bundles
// all four canonical endpoint responses per application so the service worker
// code path matches the live path.

import { ContractError, normalizeAutofill } from './packschema.js';
import {
  isPositiveIntId,
  normalizeApplicationList,
  normalizePackMeta,
  buildNormalizedPack,
} from './adapter.js';

function findByPackId(mock, packId) {
  const arr = mock && Array.isArray(mock.tracked_applications) ? mock.tracked_applications : [];
  return arr.find((a) => a && isPositiveIntId(a.pack_id) && String(a.pack_id) === String(packId)) || null;
}

export function mockListApplications(mock) {
  // Reuse the real adapter so mock + live output shapes are identical.
  return normalizeApplicationList({
    schema_version: 1,
    tracked_applications: (mock && mock.tracked_applications) || [],
  });
}

export function mockPackMeta(mock, packId) {
  const a = findByPackId(mock, packId);
  if (!a || !a.pack) return { error: 'NOT_FOUND' };
  try {
    return { meta: normalizePackMeta(a.pack) };
  } catch (e) {
    return { error: e instanceof ContractError ? 'CONTRACT_MISMATCH' : 'INTERNAL' };
  }
}

export function mockAutofill(mock, packId) {
  const a = findByPackId(mock, packId);
  if (!a || !a.autofill) return { error: 'NOT_FOUND' };
  try {
    return { autofill: normalizeAutofill(a.autofill) };
  } catch (e) {
    return { error: e instanceof ContractError ? 'CONTRACT_MISMATCH' : 'INTERNAL' };
  }
}

export function mockBuildPack(mock, packId) {
  const a = findByPackId(mock, packId);
  if (!a || !a.pack || !a.autofill) return { error: 'NOT_FOUND' };
  try {
    const meta = normalizePackMeta(a.pack);
    return { pack: buildNormalizedPack(meta, a.autofill) };
  } catch (e) {
    return { error: e instanceof ContractError ? 'CONTRACT_MISMATCH' : 'INTERNAL' };
  }
}
