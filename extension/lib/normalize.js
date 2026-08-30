// Pure text helpers shared by the scorer and the pack schema. No DOM, no deps.

// Deliberately small. Form-field words like "name", "id", "value" are meaningful
// signal here, so they are NOT stopwords.
const STOPWORDS = new Set([
  'the', 'a', 'an', 'your', 'you', 'please', 'enter', 'this',
  'and', 'or', 'of', 'to', 'in', 'for', 'is', 'are', 'if',
  'optional', 'required',
]);

const COMBINING_MARKS = /[̀-ͯ]/g;
const NON_ALNUM = /[^a-z0-9 ]+/g;

export function normalizeText(s) {
  return String(s == null ? '' : s)
    .toLowerCase()
    .normalize('NFKD')
    .replace(COMBINING_MARKS, '')
    .replace(/[_\-]+/g, ' ')
    .replace(NON_ALNUM, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function tokenize(s) {
  const n = normalizeText(s);
  if (!n) return [];
  return n.split(' ').filter((t) => t && !STOPWORDS.has(t));
}

export function tokenSet(...parts) {
  const set = new Set();
  for (const p of parts) {
    for (const t of tokenize(p)) set.add(t);
  }
  return set;
}

export function overlapCount(aSet, bSet) {
  let n = 0;
  for (const t of aSet) if (bSet.has(t)) n++;
  return n;
}
