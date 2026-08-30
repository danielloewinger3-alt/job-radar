# Job Radar Autofill (MV3)

A review-first browser extension that proposes values for job-application form
fields from a **pack** you have prepared in Job Radar. Every field is filled only
after you individually accept it. The extension **never** submits a form, clicks a
button, focuses a page field, navigates, uploads a file, or touches a CAPTCHA.

---

## Loading the unpacked extension

1. `cd extension && npm install` (dev-only; pulls in `jsdom` for the test suite).
2. `node build.js` — regenerates `content.js` and `bookmarklet.js` from `lib/*`
   plus `src/*.bootstrap.js`. (They are committed; run this after editing `lib/`.)
3. Chrome/Chromium → `chrome://extensions` → enable **Developer mode** →
   **Load unpacked** → select this `extension/` directory.
4. The action icon opens the popup. No manifest errors should appear.

## Running the local backend (live mode)

Live mode talks to the Job Radar API on `http://localhost:8000`:

```
# from the repo root
uvicorn backend.main:app --reload --port 8000
```

Then in the popup switch **Source** to **Local backend**. With no backend running,
live mode shows *“Can’t reach the Job Radar backend …”* and you can fall back to
**Mock fixture** (bundled `testbed/autofill.mock.json`), which needs nothing
running and is the default.

## Testbed

Open `testbed/form.html` in the same browser. It has ~13 safe fields, a full set
of excluded sensitive/legal fields, a fake reCAPTCHA, a file input, and a submit
button wired to **block and count** every submission attempt. Press **Scan this
page** in the popup (pick the reviewed pack), accept a few fields, then click
**Run self-test** on the page.

---

## Security model

- **The service worker is the only component that touches the network.** It
  performs `GET` only, with `credentials: 'omit'` and `redirect: 'error'`, to URLs
  it builds from fixed templates (`lib/routes.js`). It never accepts a URL, path,
  method, or header from any message.
- **Message validation.** Every inbound message is checked against a fixed schema
  (`lib/messages.js`): known type, exact allow-list of keys, ids must be positive
  integers, nonce must match `^[0-9a-f]{32}$`, mode ∈ {`mock`,`live`}. Messages
  from senders other than this extension are ignored.
- **One-time pack handoff.** On **Scan this page** the worker builds the
  normalized pack, stores it in an in-memory `Map` keyed by tab id with a random
  32-hex nonce and a 15 s TTL, injects `content.js` into the **top frame only**,
  then sends `START { nonce }` to `frameId: 0`. The content script exchanges the
  nonce for the pack via `CONSUME_PACK`. The worker verifies the nonce (constant
  time), `sender.id`, `sender.tab.id`, and `sender.frameId === 0`, returns the
  pack **once**, and deletes the entry. A second attempt fails. The entry is also
  dropped on injection failure, TTL expiry, and tab close. If the worker was
  suspended and the entry is gone, the content script shows a retry message —
  **there is no persistent-storage fallback**. `chrome.storage.session` is never
  used.
- **No sensitive data at rest.** `chrome.storage.local` holds only
  `{ mode, selectedApplicationId, selectedPackId }`. Pack values, cover letters,
  and page field values are never stored or logged, and never sent back to the
  worker after the handoff (the content script’s only outbound message is
  `CONSUME_PACK`, carrying just the nonce).
- **CSP.** `script-src 'self'; object-src 'none'` — no remote code, no `eval`, no
  `new Function`, no WebAssembly codegen. Popup CSS/JS are external files; no
  inline scripts. All dynamic text is rendered with `textContent`.
- **Overlay isolation.** The review overlay lives in an open shadow root; its
  styles never leak to or from the page. Re-scanning removes the old overlay
  first — there is always exactly one.
- **No telemetry, no analytics, no credential storage.**

## Permissions rationale

| Permission | Why |
|---|---|
| `activeTab` | Grants temporary access to the current tab **only when you invoke the extension**, so it can be the injection target for a scan. |
| `scripting` | `chrome.scripting.executeScript` to inject `content.js` — only on your explicit “Scan this page” press, top frame only. |
| `storage` | Persist the three non-sensitive preference keys above. |
| `host_permissions: http://localhost:8000/*`, `http://127.0.0.1:8000/*` | The service worker’s `GET`s to the local Job Radar API. Nothing else. |

Not requested: `<all_urls>`, `*://*/*`, `tabs`, `cookies`, `webRequest`,
`downloads`, `history`, `bookmarks`. No `content_scripts` declaration (injection
is programmatic). No `web_accessible_resources`.

## Matching algorithm and confidence threshold

Full detail in [`MATCHING.md`](./MATCHING.md). In short: each pack field is scored
against each compatible page field using label / `name` / `id` / `placeholder` /
`aria-label` / `autocomplete`. A candidate needs a **meaningful** token or
`autocomplete` match — score alone never qualifies. Thresholds:

- `< 0.45` → omitted
- `0.45 – 0.62` → shown **preview-only** (cannot be accepted)
- close top-2 candidates, or two pack fields contesting one page field → **ambiguous** (you pick the target)
- `≥ 0.62` and unambiguous → **acceptable** (still one click per field)

Unreviewed packs are always preview-only. `<select>` fields are matched only
against the pack **value** (never the field label), exactly one enabled option
must match, disabled options are never chosen, and the first option is never a
fallback.

## Which pack answers can be filled

| Answer kind | Fillable when |
|---|---|
| Standard (name, email, phone, location, LinkedIn, portfolio) | `source` is `profile` or `user_supplied` |
| Declared (work authorization, sponsorship, salary, start date, notice period, years of experience) | `source === "user_supplied"` **and** `status === "sourced"` |
| Narrative (cover note, “why us”) | the pack is **reviewed** |
| Anything with `status === "needs_input"` | never |

`sensitive: true` never blocks a field — it just means “always confirm”, which is
already true for everything. Legal attestations, e-signatures, government IDs,
payment/banking, medical, and demographic/EEO fields are excluded both by the
backend (it omits them from the pack) and by the page-field classifier
(`lib/classify.js`).

## Unsupported sites and fields (v1)

- **Cross-origin / embedded (iframe) application forms** — never scanned or
  accessed. Injection is top-frame only.
- **Closed shadow DOM** and **custom non-native widgets** (anything that is not a
  real `<input>` / `<textarea>` / `<select>`).
- **Multi-page / wizard forms** that require navigation between steps.
- **Fields inserted after the scan** — no auto-refill; press *Scan this page*
  again.
- **Pages that block script injection** (`chrome://*`, the Chrome Web Store,
  PDF viewer, `view-source:`) — the popup reports this.
- `contenteditable` rich-text editors.

## CAPTCHA and file-upload limitations

- reCAPTCHA / hCaptcha / Turnstile widgets are **detected and left completely
  untouched**. The extension will never tick a checkbox, solve a challenge, or
  interact with a CAPTCHA iframe.
- `<input type="file">` is **listed as “left untouched”** and never receives a
  value. Attach your résumé/portfolio manually.

## Review-before-submit guarantee

- Scanning does not modify the page.
- Every proposed field starts **unaccepted**. Only that field’s own **Accept**
  button writes it, and only `input` + `change` events are dispatched.
- There is **no “Accept all”**.
- A hard runtime guard (`writeValue` in `lib/overlay.js`) refuses to write to
  anything that is not a plain `INPUT` (safe types) / `TEXTAREA` / `SELECT`, even
  if field classification were bypassed, and refuses submit-like controls and
  elements inside a `<button>`.
- The extension never calls `submit()`, `requestSubmit()`, `.click()` on buttons,
  `.focus()` / `.blur()` on page controls, and never navigates.

## Bookmarklet (limited fallback — not the production route)

`bookmarklet.js` is a **fallback** for browsers where the extension can’t be
installed. It runs in the **page’s own JavaScript context** and is therefore
**less isolated than the extension** — prefer the extension whenever possible.

- It contacts **no** backend and contains **no** pack data.
- On invocation it opens an overlay with a textarea. Paste the autofill JSON you
  exported for that session. It is parsed with `JSON.parse` (never `eval`) and
  validated against the same `schema_version: 1` + field contract.
- The textarea is cleared immediately after a successful parse; the parsed pack
  lives only in the bookmarklet’s in-memory closure; **Clear and close** drops
  every reference. No `localStorage` / `sessionStorage` / IndexedDB / cookies /
  URL params / DOM attributes are used.
- Same review overlay, same per-field Accept, same no-submit guarantees.

To install: run `node build.js`, open `bookmarklet.js`, and wrap its contents as a
single `javascript:` URL (any minifier) saved as a bookmark. The readable source
is the source of truth; keep it in sync with `content.js` via `lib/`.

## Tests

```
npm test          # node --test — 80 checks: policy, schema, adapter, messages,
                  # routes, scoring, classify, overlay, handoff, sw router, static
bash verify/audit.sh   # fast syntax + build-freshness + static security sweep
```

DOM behaviour uses `jsdom`. A full Chrome load test is manual — see **Testbed**.
