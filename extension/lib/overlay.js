// In-page review overlay. Shadow-DOM isolated, idempotent, text-only rendering.
//
// This module NEVER submits, never calls requestSubmit, never clicks a button,
// never focuses/blurs, never navigates, never touches file or CAPTCHA controls.
// On Accept it dispatches exactly one 'input' and one 'change' event.

export const HOST_ID = '__jobradar_autofill_overlay_host__';

const SAFE_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
const SAFE_INPUT_TYPES = new Set(['text', 'email', 'tel', 'url', 'number', 'search', 'date', '']);

export class RefusedWrite extends Error {
  constructor(msg) {
    super(msg || 'refused');
    this.name = 'RefusedWrite';
  }
}

/**
 * Hard runtime guard. Refuses anything that is not a plain fillable control,
 * even if upstream classification failed. Dispatches only input + change.
 */
export function writeValue(el, value) {
  if (!el || !SAFE_TAGS.has(el.tagName)) throw new RefusedWrite('not a fillable element');
  if (typeof el.closest === 'function' && el.closest('button')) {
    throw new RefusedWrite('inside a <button>');
  }
  if (el.disabled || el.readOnly) throw new RefusedWrite('disabled or readonly');

  if (el.tagName === 'INPUT') {
    const t = String(el.getAttribute && el.getAttribute('type') || '').toLowerCase();
    if (!SAFE_INPUT_TYPES.has(t)) throw new RefusedWrite('input type: ' + (t || 'unknown'));
  }

  if (el.tagName === 'SELECT') {
    const opts = Array.from(el.options || []);
    const match = opts.find((o) => !o.disabled && o.value === value);
    if (!match) throw new RefusedWrite('no matching enabled <option>');
    el.value = value;
  } else {
    el.value = value;
  }

  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

export function removeOverlay(doc) {
  const d = doc || (typeof document !== 'undefined' ? document : null);
  if (!d) return;
  const prev = d.getElementById(HOST_ID);
  if (prev && prev.parentNode) prev.parentNode.removeChild(prev);
}

function mk(doc, tag, className, text) {
  const n = doc.createElement(tag);
  if (className) n.className = className;
  if (text != null) n.textContent = String(text);
  return n;
}

const OVERLAY_CSS = `
:host { all: initial; }
.wrap {
  position: fixed; top: 12px; right: 12px; width: 340px; max-height: 82vh;
  overflow: auto; z-index: 2147483647;
  font: 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: #1a1a1a; background: #fff; border: 1px solid #c9c9c9; border-radius: 10px;
  box-shadow: 0 8px 30px rgba(0,0,0,.22); padding: 12px;
}
.warn { background: #fff4e5; border: 1px solid #f0c27b; border-radius: 6px;
  padding: 8px; margin-bottom: 10px; font-weight: 600; }
.head { font-size: 14px; margin-bottom: 8px; }
.role { color: #555; font-weight: 400; }
.badge { border-radius: 6px; padding: 6px 8px; margin-bottom: 8px; font-weight: 600; }
.badge.preview { background: #eef2ff; border: 1px solid #c7d2fe; }
.note { color: #555; margin-bottom: 8px; }
.row { border: 1px solid #e3e3e3; border-radius: 8px; padding: 8px; margin-bottom: 8px; }
.row .k { font-weight: 600; }
.row .v { color: #333; word-break: break-word; margin: 3px 0; }
.row .meta { color: #666; font-size: 12px; }
.row.filled { background: #ecfdf3; border-color: #a6e9c5; }
.row.skipped { opacity: .55; }
.row.previewonly { background: #f7f7f8; }
.row .err { color: #b42318; font-size: 12px; margin-top: 4px; }
.btns { margin-top: 6px; display: flex; gap: 6px; flex-wrap: wrap; }
button { font: inherit; padding: 4px 10px; border-radius: 6px; border: 1px solid #b9b9b9;
  background: #f6f6f6; cursor: pointer; }
button.accept { background: #1f6feb; border-color: #1f6feb; color: #fff; }
button.accept[disabled] { background: #c7c7c7; border-color: #c7c7c7; cursor: not-allowed; }
button.pick { background: #eef2ff; }
.excluded { margin-top: 6px; }
.exrow { color: #666; font-size: 12px; padding: 2px 0; }
.foot { margin-top: 10px; text-align: right; }
`;

/**
 * @param {object} args
 * @param {Document} [args.doc]
 * @param {object} args.pack  NormalizedPack (metadata only used here)
 * @param {object[]} args.proposals
 * @param {object[]} args.pageFields  fillable descriptors (for candidate labels)
 * @param {object[]} args.excluded  excluded descriptors
 * @param {boolean} args.previewOnly  pack unreviewed -> nothing fillable
 * @param {number} args.omittedCount
 * @param {object} args.controller  { onAccept(proposal, pageIndex)->{ok,error?},
 *   onSkip(proposal), onPickTarget(proposal, pageIndex), onClose() }
 */
export function renderOverlay(args) {
  const doc = args.doc || document;
  const {
    pack, proposals, pageFields, excluded, previewOnly, omittedCount, controller,
  } = args;

  removeOverlay(doc); // idempotent: exactly one overlay at a time

  const host = doc.createElement('div');
  host.id = HOST_ID;
  host.setAttribute('data-jobradar', 'autofill-overlay');
  const shadow = typeof host.attachShadow === 'function'
    ? host.attachShadow({ mode: 'open' })
    : host;

  shadow.appendChild(mk(doc, 'style', null, OVERLAY_CSS));

  const wrap = mk(doc, 'div', 'wrap');
  wrap.appendChild(mk(doc, 'div', 'warn',
    'Review every field before you submit. This tool never submits forms, never clicks buttons, and never solves CAPTCHAs.'));

  const heading =
    pack.heading ||
    (pack.applicationId ? 'Tracked application #' + pack.applicationId : 'Autofill pack');
  const head = mk(doc, 'div', 'head');
  head.appendChild(mk(doc, 'strong', null, heading));
  wrap.appendChild(head);

  if (previewOnly) {
    wrap.appendChild(mk(doc, 'div', 'badge preview',
      'This pack is not reviewed yet — preview only. Nothing can be filled.'));
  }
  if (omittedCount > 0) {
    wrap.appendChild(mk(doc, 'div', 'note',
      omittedCount + ' unusable answer' + (omittedCount === 1 ? '' : 's') + ' were omitted.'));
  }

  const filledPageIndexes = new Set();

  if (!proposals.length) {
    wrap.appendChild(mk(doc, 'div', 'note', 'No confident matches on this page.'));
  }
  proposals.forEach((p) => {
    wrap.appendChild(buildRow(doc, p, { pageFields, previewOnly, controller, filledPageIndexes }));
  });

  if (excluded && excluded.length) {
    const det = mk(doc, 'details', 'excluded');
    det.appendChild(mk(doc, 'summary', null,
      excluded.length + ' field(s) left untouched (sensitive, file, CAPTCHA, buttons)'));
    excluded.forEach((x) => {
      det.appendChild(mk(doc, 'div', 'exrow',
        (x.label || x.name || x.id || 'field') + ' — ' + (x.excludedReason || 'excluded')));
    });
    wrap.appendChild(det);
  }

  const foot = mk(doc, 'div', 'foot');
  const closeBtn = mk(doc, 'button', 'close', 'Clear and close');
  closeBtn.addEventListener('click', () => controller.onClose());
  foot.appendChild(closeBtn);
  wrap.appendChild(foot);

  shadow.appendChild(wrap);
  (doc.body || doc.documentElement).appendChild(host);
  return { host, shadow };
}

function buildRow(doc, p, ctx) {
  const { pageFields, previewOnly, controller, filledPageIndexes } = ctx;
  const row = mk(doc, 'div', 'row');
  const previewOnlyRow = previewOnly || p.state === 'preview';
  if (previewOnlyRow) row.classList.add('previewonly');

  row.appendChild(mk(doc, 'div', 'k', p.packLabel));
  row.appendChild(mk(doc, 'div', 'v', p.value));

  const src = p.provenance || sourceLabel(p.source, p.answerKind);
  row.appendChild(mk(doc, 'div', 'meta',
    'Source: ' + src + '  ·  confidence ' + p.confidence
    + (p.sensitive ? '  ·  sensitive — always confirm' : '')
    + (previewOnlyRow ? '  ·  preview only' : '')));

  const errLine = mk(doc, 'div', 'err');
  errLine.hidden = true;
  row.appendChild(errLine);

  const btns = mk(doc, 'div', 'btns');

  if (p.state === 'ambiguous') {
    row.appendChild(mk(doc, 'div', 'meta', 'Ambiguous — choose the field to fill:'));
    p.candidates.forEach((c) => {
      const b = mk(doc, 'button', 'pick', truncate(c.label, 40) + '  (' + c.score + ')');
      b.addEventListener('click', () => {
        p.state = p.acceptableIfPicked ? 'proposable' : 'preview';
        p.targetPageIndex = c.pageIndex;
        controller.onPickTarget(p, c.pageIndex);
        rerenderRow(row, p, ctx);
      });
      btns.appendChild(b);
    });
    row.appendChild(btns);
    return row;
  }

  const acceptBtn = mk(doc, 'button', 'accept', 'Accept');
  const skipBtn = mk(doc, 'button', null, 'Skip');

  const canAccept = !previewOnlyRow && p.state === 'proposable' && p.targetPageIndex != null;
  if (!canAccept) acceptBtn.setAttribute('disabled', 'disabled');

  acceptBtn.addEventListener('click', () => {
    if (acceptBtn.hasAttribute('disabled')) return;
    if (filledPageIndexes.has(p.targetPageIndex)) {
      showErr(errLine, 'That field was already filled by another answer.');
      return;
    }
    const res = controller.onAccept(p, p.targetPageIndex);
    if (res && res.ok) {
      filledPageIndexes.add(p.targetPageIndex);
      row.classList.add('filled');
      acceptBtn.setAttribute('disabled', 'disabled');
      skipBtn.setAttribute('disabled', 'disabled');
      errLine.hidden = true;
    } else {
      showErr(errLine, (res && res.error) || 'Could not fill that field.');
    }
  });

  skipBtn.addEventListener('click', () => {
    row.classList.add('skipped');
    acceptBtn.setAttribute('disabled', 'disabled');
    skipBtn.setAttribute('disabled', 'disabled');
    controller.onSkip(p);
  });

  btns.appendChild(acceptBtn);
  btns.appendChild(skipBtn);
  row.appendChild(btns);
  return row;
}

function rerenderRow(oldRow, p, ctx) {
  const doc = oldRow.ownerDocument || document;
  const fresh = buildRow(doc, p, ctx);
  if (oldRow.parentNode) oldRow.parentNode.replaceChild(fresh, oldRow);
}

function showErr(errLine, text) {
  errLine.textContent = text;
  errLine.hidden = false;
}

function sourceLabel(source, kind) {
  if (source === 'profile') return 'your saved profile';
  if (source === 'user_supplied') return 'an answer you supplied';
  if (source === 'cv') return 'your CV (not user-confirmed)';
  if (source === 'generated' || source === 'derived') return 'assistant-generated draft';
  return source || 'unknown';
}

function truncate(s, n) {
  s = String(s || '');
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}
