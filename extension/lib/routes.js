// Fixed route templates for the local Job Radar API. IDs are validated as
// positive integers by lib/messages.js BEFORE they reach here. The service
// worker never accepts a URL, path, method, or header from a message -- it picks
// a template name and substitutes one validated id.

export const ALLOWED_ORIGINS = Object.freeze([
  'http://localhost:8000',
  'http://127.0.0.1:8000',
]);

export const DEFAULT_BASE = 'http://localhost:8000';

const POS_INT_STR = /^[1-9][0-9]{0,14}$/;

const TEMPLATES = Object.freeze({
  listApplications: () => '/api/tracked-applications',
  packForApplication: (id) => `/api/tracked-applications/${id}/pack`,
  packMeta: (id) => `/api/packs/${id}`,
  packAutofill: (id) => `/api/packs/${id}/autofill`,
});

export function isRouteName(name) {
  return Object.prototype.hasOwnProperty.call(TEMPLATES, name);
}

export function buildUrl(base, name, id) {
  if (!isRouteName(name)) throw new Error('unknown_route');
  if (!ALLOWED_ORIGINS.includes(base)) throw new Error('bad_base_origin');

  let path;
  if (TEMPLATES[name].length === 0) {
    path = TEMPLATES[name]();
  } else {
    const idStr = String(id);
    if (!POS_INT_STR.test(idStr)) throw new Error('bad_route_id');
    path = TEMPLATES[name](idStr);
  }

  const u = new URL(path, base);
  if (u.protocol !== 'http:' || !ALLOWED_ORIGINS.includes(u.origin)) {
    throw new Error('origin_not_allowed');
  }
  return u.toString();
}

// The only fetch options the service worker ever uses. GET only, no credentials,
// refuse redirects (so a 3xx to another origin cannot smuggle the request out).
export const FETCH_OPTIONS = Object.freeze({
  method: 'GET',
  credentials: 'omit',
  cache: 'no-store',
  redirect: 'error',
  headers: Object.freeze({ Accept: 'application/json' }),
});
