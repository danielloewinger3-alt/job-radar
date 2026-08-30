// Page-field scanning + exclusion taxonomy.
//
// The security-critical part -- deciding whether a field is off-limits -- is the
// pure function classifyDescriptor(descriptor). scanFields(root) is a thin DOM
// walk that builds descriptors and calls it.

// input types we will ever write a value into
export const FILLABLE_INPUT_TYPES = new Set([
  'text', 'email', 'tel', 'url', 'number', 'search', 'date', '',
]);

// input types we hard-exclude by type alone
export const EXCLUDED_INPUT_TYPES = new Set([
  'password', 'hidden', 'file', 'submit', 'reset', 'button', 'image',
  'checkbox', 'radio',
]);

// label/name/id/placeholder/aria text patterns that make a field off-limits
// regardless of the pack. Categories mirror lib/policy.HARD_EXCLUDED_CATEGORIES.
export const SENSITIVE_PATTERNS = [
  {
    cat: 'payment',
    re: /\b(card\s*number|cardnumber|cc[-\s]?num(ber)?|cvv|cvc2?|security\s*code|iban|sort[-\s]?code|routing\s*(number|no)|account\s*(number|no)|bank\s*account|billing\s*address)\b/i,
  },
  {
    cat: 'gov_id',
    re: /\b(national\s*insurance|ni\s*number|social\s*security|ssn|passport\s*(no|number)|driver'?s?\s*licen[cs]e|tax\s*(id|identification|file\s*number)|itin|\bein\b)\b/i,
  },
  {
    cat: 'medical',
    re: /\b(disab(led|ility)|medical\s*(condition|history|information)|health\s*condition|impairment|chronic\s*illness|accommodation\s*request|mental\s*health)\b/i,
  },
  {
    cat: 'eeo',
    re: /(\brace\b|ethnic(ity)?|gender\s*identity|\bsex\b|sexual\s*orientation|\bveteran\b|disability\s*status|protected\s*(class|group|veteran)|equal\s*(employment\s*)?opportunity|\beeo\b|self[-\s]?identif|hispanic\s*or\s*latino)/i,
  },
  {
    cat: 'signature',
    re: /\b(e[-\s]?signature|electronic\s*signature|digital\s*signature|sign\s*here|type\s*your\s*(full\s*)?name\s*to\s*sign|^signature$)\b/i,
  },
  {
    cat: 'legal',
    re: /(i\s*(certify|attest|declare|acknowledge|consent|agree)\b|terms\s*(and|&)\s*conditions|privacy\s*policy|legal\s*(declaration|attestation)|penalty\s*of\s*perjury|under\s*penalty|authoriz(e|ation)\s*to\s*(check|verify|contact)|background\s*check\s*consent)/i,
  },
];

export const CAPTCHA_SELECTORS = [
  '.g-recaptcha',
  'iframe[src*="recaptcha"]',
  'iframe[src*="google.com/recaptcha"]',
  '.h-captcha',
  'iframe[src*="hcaptcha"]',
  '.cf-turnstile',
  '#px-captcha',
  '[data-sitekey]',
];

/**
 * Pure classification.
 * @param {object} d descriptor: { tag, inputType, label, name, id, placeholder,
 *   ariaLabel, autocomplete, disabled, readOnly, insideCaptcha }
 * @returns {{fillable:boolean, excludedReason:(string|null)}}
 */
export function classifyDescriptor(d) {
  const tag = String(d.tag || '').toLowerCase();
  const inputType = String(d.inputType || '').toLowerCase();

  if (tag === 'input' && EXCLUDED_INPUT_TYPES.has(inputType)) {
    return excl('field_type:' + inputType);
  }
  if (tag === 'input' && !FILLABLE_INPUT_TYPES.has(inputType)) {
    return excl('unsupported_type:' + (inputType || 'unknown'));
  }
  if (tag !== 'input' && tag !== 'textarea' && tag !== 'select') {
    return excl('not_fillable_tag:' + tag);
  }
  if (d.disabled) return excl('disabled');
  if (d.readOnly) return excl('readonly');
  if (d.insideCaptcha) return excl('captcha');

  const hay = [d.label, d.name, d.id, d.placeholder, d.ariaLabel]
    .map((x) => String(x || ''))
    .join('  ');
  for (const p of SENSITIVE_PATTERNS) {
    if (p.re.test(hay)) return excl('sensitive:' + p.cat);
  }

  return { fillable: true, excludedReason: null };

  function excl(reason) {
    return { fillable: false, excludedReason: reason };
  }
}

function cssEscapeAttr(s) {
  if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') return CSS.escape(s);
  return String(s).replace(/["\\\]]/g, '\\$&');
}

function labelTextFor(el, root) {
  const bits = [];
  const id = el.getAttribute && el.getAttribute('id');
  if (id) {
    try {
      const labels = root.querySelectorAll(`label[for="${cssEscapeAttr(id)}"]`);
      labels.forEach((l) => bits.push(l.textContent || ''));
    } catch {
      /* bad id -> skip */
    }
  }
  const wrap = el.closest ? el.closest('label') : null;
  if (wrap) bits.push(wrap.textContent || '');

  const lb = el.getAttribute && el.getAttribute('aria-labelledby');
  if (lb && root.getElementById) {
    lb.split(/\s+/).forEach((rid) => {
      const n = root.getElementById(rid);
      if (n) bits.push(n.textContent || '');
    });
  }
  return bits.join(' ').replace(/\s+/g, ' ').trim();
}

function insideCaptcha(el) {
  if (!el.closest) return false;
  return CAPTCHA_SELECTORS.some((s) => {
    try {
      return !!el.closest(s);
    } catch {
      return false;
    }
  });
}

/**
 * Walk a document/root and return { fillable: descriptor[], excluded: descriptor[] }.
 * Descriptors keep a live `el` reference for later writing.
 */
export function scanFields(root) {
  const doc = root || (typeof document !== 'undefined' ? document : null);
  if (!doc) return { fillable: [], excluded: [] };

  const fillable = [];
  const excluded = [];
  const nodes = doc.querySelectorAll('input, textarea, select');

  nodes.forEach((el, index) => {
    const tag = el.tagName.toLowerCase();
    let inputType = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && inputType === '') inputType = 'text';

    const descriptor = {
      el,
      index,
      tag,
      inputType: tag === 'input' ? inputType : tag,
      label: labelTextFor(el, doc),
      name: el.getAttribute('name') || '',
      id: el.getAttribute('id') || '',
      placeholder: el.getAttribute('placeholder') || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      autocomplete: (el.getAttribute('autocomplete') || '').toLowerCase(),
      disabled: !!el.disabled,
      readOnly: !!el.readOnly,
      insideCaptcha: insideCaptcha(el),
      options:
        tag === 'select'
          ? Array.from(el.options || []).map((o) => ({
              value: o.value,
              text: (o.textContent || '').trim(),
              disabled: !!o.disabled,
            }))
          : null,
    };

    const verdict = classifyDescriptor(descriptor);
    if (verdict.fillable) {
      fillable.push(descriptor);
    } else {
      descriptor.excludedReason = verdict.excludedReason;
      excluded.push(descriptor);
    }
  });

  return { fillable, excluded };
}

export function hasCaptcha(root) {
  const doc = root || (typeof document !== 'undefined' ? document : null);
  if (!doc) return false;
  return CAPTCHA_SELECTORS.some((s) => {
    try {
      return !!doc.querySelector(s);
    } catch {
      return false;
    }
  });
}
