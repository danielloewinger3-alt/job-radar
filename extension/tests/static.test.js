import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildAll } from '../build.js';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const read = (p) => readFileSync(join(ROOT, p), 'utf8');

function walk(dir, acc = []) {
  for (const name of readdirSync(join(ROOT, dir))) {
    if (name === 'node_modules' || name === 'tests') continue;
    const rel = join(dir, name);
    const st = statSync(join(ROOT, rel));
    if (st.isDirectory()) walk(rel, acc);
    else acc.push(rel);
  }
  return acc;
}

// Strip line then block comments so "no eval" style checks test real code, not
// prose. Line-first avoids a stray "/*" inside a // comment (e.g. a path glob)
// swallowing the rest of the file.
function stripComments(src) {
  return src
    .replace(/(^|[^:'"`\\])\/\/.*$/gm, '$1')
    .replace(/\/\*[\s\S]*?\*\//g, '');
}

const ALL_FILES = walk('.').filter((f) => !f.startsWith('.git'));
const JS_FILES = ALL_FILES.filter((f) => f.endsWith('.js'));
const SHIPPED_JS = JS_FILES.filter(
  (f) => !f.startsWith('build.js') && !f.startsWith('verify'),
);

// ---------------------------------------------------------------------------
// manifest
// ---------------------------------------------------------------------------

test('manifest: exact permissions, host_permissions, CSP, no broad grants', () => {
  const m = JSON.parse(read('manifest.json'));
  assert.deepEqual(m.permissions.sort(), ['activeTab', 'scripting', 'storage']);
  assert.deepEqual(m.host_permissions.sort(), [
    'http://127.0.0.1:8000/*',
    'http://localhost:8000/*',
  ]);
  assert.equal(m.content_security_policy.extension_pages, "script-src 'self'; object-src 'none'");
  assert.equal(m.background.type, 'module');
  assert.equal('content_scripts' in m, false);
  assert.equal('web_accessible_resources' in m, false);

  const json = read('manifest.json');
  assert.doesNotMatch(json, /<all_urls>/);
  assert.doesNotMatch(json, /\*:\/\/\*/);
  assert.doesNotMatch(json, /"tabs"|"cookies"|"webRequest"|"<all_urls>"|"downloads"|"history"|"bookmarks"/);
  assert.doesNotMatch(m.content_security_policy.extension_pages, /unsafe-eval|wasm-unsafe-eval|http:/);
});

// ---------------------------------------------------------------------------
// storage: only non-sensitive preference keys; never storage.session
// ---------------------------------------------------------------------------

test('no chrome.storage.session anywhere in shipped code', () => {
  // shipped runtime code only -- not docs, not the audit script, not build.js
  for (const f of SHIPPED_JS) {
    assert.doesNotMatch(read(f), /storage\.session/, f);
  }
  assert.doesNotMatch(read('manifest.json'), /storage\.session/);
});

test('sw.js only ever writes the mode / selected-id preference keys to storage.local', () => {
  const sw = read('sw.js');
  const setCalls = [...sw.matchAll(/storage\.local\.set\(([^)]*)\)/g)].map((m) => m[1]);
  assert.ok(setCalls.length >= 1);
  // the only set is `chrome.storage.local.set({ [key]: value })` guarded by STORAGE_KEYS
  assert.match(sw, /STORAGE_KEYS\s*=\s*Object\.freeze\(\['mode', 'selectedApplicationId', 'selectedPackId'\]\)/);
  for (const c of setCalls) assert.match(c, /\[key\]:\s*value/);
});

// ---------------------------------------------------------------------------
// no dynamic code execution anywhere
// ---------------------------------------------------------------------------

test('no eval / new Function / WebAssembly codegen in shipped JS', () => {
  for (const f of SHIPPED_JS) {
    const src = stripComments(read(f));
    assert.doesNotMatch(src, /\beval\s*\(/, f);
    assert.doesNotMatch(src, /new\s+Function\s*\(/, f);
    assert.doesNotMatch(src, /WebAssembly\./, f);
  }
});

// ---------------------------------------------------------------------------
// rendering: textContent only, never innerHTML
// ---------------------------------------------------------------------------

test('no innerHTML / outerHTML / insertAdjacentHTML / document.write in shipped JS', () => {
  for (const f of SHIPPED_JS) {
    const src = stripComments(read(f));
    assert.doesNotMatch(src, /\.innerHTML\b/, f);
    assert.doesNotMatch(src, /\.outerHTML\b/, f);
    assert.doesNotMatch(src, /insertAdjacentHTML/, f);
    assert.doesNotMatch(src, /document\.write\s*\(/, f);
  }
});

// ---------------------------------------------------------------------------
// the injected bundles never submit / click / navigate, and never send page or
// pack values back to the worker (the ONLY outbound message is CONSUME_PACK)
// ---------------------------------------------------------------------------

for (const bundle of ['content.js', 'bookmarklet.js']) {
  test(`${bundle}: no form submission, no button click, no navigation`, () => {
    const src = stripComments(read(bundle));
    assert.doesNotMatch(src, /\.submit\s*\(/, bundle);
    assert.doesNotMatch(src, /requestSubmit/, bundle);
    assert.doesNotMatch(src, /\bHTMLFormElement\b/, bundle);
    assert.doesNotMatch(src, /\.click\s*\(/, bundle);
    assert.doesNotMatch(src, /location\s*\.\s*(href|assign|replace)/, bundle);
    assert.doesNotMatch(src, /\bwindow\.open\s*\(/, bundle);
  });

  test(`${bundle}: never focuses/blurs page controls (no focus/blur calls at all)`, () => {
    const src = stripComments(read(bundle));
    assert.doesNotMatch(src, /\.focus\s*\(/, bundle);
    assert.doesNotMatch(src, /\.blur\s*\(/, bundle);
  });

  test(`${bundle}: no pack/field value stored in DOM attributes or dataset`, () => {
    const src = stripComments(read(bundle));
    assert.doesNotMatch(src, /dataset\s*\[/, bundle);
    assert.doesNotMatch(src, /dataset\.\w+\s*=/, bundle);
    // setAttribute is allowed, but only with literal safe attr names
    for (const m of src.matchAll(/setAttribute\(\s*([^,]+),/g)) {
      assert.match(
        m[1].trim(),
        /^'(data-jobradar|disabled|placeholder|aria-[a-z-]+|hidden|type)'$/,
        bundle + ' :: ' + m[1],
      );
    }
  });
}

test('content.js: the only runtime.sendMessage is CONSUME_PACK (nonce only)', () => {
  const src = stripComments(read('content.js'));
  const sends = [...src.matchAll(/runtime\.sendMessage\(\s*(\{[^}]*\})/g)].map((m) => m[1]);
  assert.equal(sends.length, 1);
  assert.match(sends[0], /type:\s*'CONSUME_PACK'/);
  assert.match(sends[0], /nonce:\s*nonce/);
  assert.doesNotMatch(sends[0], /value|field|pack|document|innerText|\.el\b/);
});

test('content.js: injected via files, reacts to START, has an isolated-world sentinel', () => {
  const src = read('content.js');
  assert.match(src, /__JOBRADAR_AUTOFILL_ACTIVE__/);
  assert.match(src, /'START'/);
  assert.match(src, /handledNonces/);
  assert.match(src, /state\.generation/);
});

// ---------------------------------------------------------------------------
// no injection into subframes; top frame only
// ---------------------------------------------------------------------------

test('sw.js injects into the top frame only (no allFrames / frameIds)', () => {
  const sw = stripComments(read('sw.js'));
  assert.doesNotMatch(sw, /allFrames/);
  assert.doesNotMatch(sw, /frameIds/);
  assert.match(sw, /target:\s*\{\s*tabId\s*\}/);
  assert.match(sw, /sendMessage\([^)]*\{\s*frameId:\s*0\s*\}\s*\)/);
});

test('no MutationObserver / auto-rescan timer in the injected bundles', () => {
  for (const bundle of ['content.js', 'bookmarklet.js']) {
    const src = stripComments(read(bundle));
    assert.doesNotMatch(src, /MutationObserver/, bundle);
    assert.doesNotMatch(src, /setInterval/, bundle);
  }
});

// ---------------------------------------------------------------------------
// bookmarklet safety
// ---------------------------------------------------------------------------

test('bookmarklet.js: JSON.parse only, no personal data, labelled a fallback', () => {
  const raw = read('bookmarklet.js');
  const src = stripComments(raw);
  assert.match(src, /JSON\.parse\(/);
  assert.doesNotMatch(src, /\beval\s*\(/);
  assert.doesNotMatch(src, /new\s+Function\s*\(/);
  assert.doesNotMatch(src, /\bPACK\s*=/); // no embedded pack literal
  assert.doesNotMatch(src, /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/); // no email literal
  assert.doesNotMatch(src, /\+\d[\d ().-]{6,}\d|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b/); // no phone-like literal
  assert.doesNotMatch(src, /localStorage|sessionStorage|indexedDB|document\.cookie/);
  assert.match(raw, /less isolated/i); // the "fallback" warning lives in a comment + UI string
});

test('bookmarklet source (src/bookmarklet.bootstrap.js) contains no pack values', () => {
  const src = stripComments(read('src/bookmarklet.bootstrap.js'));
  assert.doesNotMatch(src, /\bPACK\s*=/);
  assert.doesNotMatch(src, /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/);
});

// ---------------------------------------------------------------------------
// no telemetry / analytics / non-localhost network
// ---------------------------------------------------------------------------

test('no analytics endpoints or non-localhost fetch targets', () => {
  for (const f of SHIPPED_JS) {
    const src = read(f);
    assert.doesNotMatch(src, /google-analytics|googletagmanager|segment\.io|sentry\.io|mixpanel|amplitude/i, f);
    for (const m of src.matchAll(/https?:\/\/[^\s'"`)]+/g)) {
      const url = m[0];
      const ok = /^http:\/\/(localhost|127\.0\.0\.1):8000/.test(url) || /w3\.org|schema\.org/.test(url);
      assert.ok(ok, `${f}: unexpected URL ${url}`);
    }
  }
});

// ---------------------------------------------------------------------------
// popup: external assets, no inline script
// ---------------------------------------------------------------------------

test('popup.html uses external css/js and has no inline script', () => {
  const html = read('popup.html');
  assert.match(html, /<link rel="stylesheet" href="popup\.css"/);
  assert.match(html, /<script src="popup\.js"><\/script>/);
  const inline = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>/g)];
  assert.equal(inline.length, 0);
  assert.doesNotMatch(html, /\son(click|load|submit|change|input|error|focus|blur|mouse\w+|key\w+)\s*=/i);
});

// ---------------------------------------------------------------------------
// generated bundles are in sync with source
// ---------------------------------------------------------------------------

test('content.js / bookmarklet.js are up to date with build.js', () => {
  const built = buildAll();
  for (const [name, content] of Object.entries(built)) {
    assert.equal(read(name), content, `${name} is stale -- run: node build.js`);
  }
});
