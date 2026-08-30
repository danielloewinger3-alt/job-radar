# Job Radar frontend feature modules

Ordered classic `<script>`s loaded after `frontend/app.js` (which is frozen and
exposes only `window.JobRadar` = `{ version, speak, registerAlfredDispatcher }`).

Load order (fixed in `index.html`): `integrations.js` → `tracker.js` →
`projectfiles.js` → `outreach.js` → `alfred.js`.

`integrations.js` publishes **`window.JobRadarUI`** — the shared helpers every
other module consumes:

| member | purpose |
| --- | --- |
| `escapeHtml`, `safeUrl`, `isMailtoUrl`, `fmtBytes`, `fmtDate`, `timeAgo`, `el`, `clear`, `uid` | DOM + string helpers (app.js's copies are closure-private) |
| `ContractError` | thrown by a `normalize*` adapter when a **required** canonical field is missing → callers show a controlled "contract mismatch" error, never silent fallback |
| `fetchJSON(url, {method, body, headers, kind})` | never throws; returns a classified envelope (see **Error classification**) |
| `renderState(container, {kind, message, onRetry})` / `renderFetchFailure` | the five honest states: `loading` / `empty` / `unavailable` / `forbidden` / `error` |
| `confirm({title, body, confirmLabel, danger, note, triggerEl})` → `Promise<{confirmed, note}>` | accessible modal; single-instance; optional labelled note textarea (never collected by voice) |
| `makePanel({mount, eyebrow, title})` | full-screen overlay shell + layer/focus/inert wiring |
| `integrations.get / ready(dottedKey) / open / gate(dottedKey, label)` | cached `GET /api/integrations`; `gate` opens the panel + returns `false` when a key is missing |
| `features.enabled(name)` | capability map lookup (`null` = unknown), consulted before treating a 404 as "feature unavailable" |
| `sections`, `registerSection` | `{tracker, files, outreach, integrations}` open-fns for Alfred navigation |
| `toast(msg, kind)` | transient `role="status"` line |
| `tracker`, `outreach`, `alfred` | per-module hooks the other modules / tests call |

## Accessibility contract (all panels, nested detail, confirm modal, Alfred cards)

* `role="dialog"` (or `alertdialog` when destructive), `aria-modal="true"`,
  `aria-labelledby` → visible heading; confirm also `aria-describedby`.
* Layer stack: only the top layer is interactive; everything else gets `inert`
  (fallback `aria-hidden` + `pointer-events:none`). `Escape` / backdrop dismiss
  **only the top layer** (a confirm resolves `{confirmed:false, note:""}`).
* Focus enters the dialog on open (confirm → Cancel, or the note field when not
  `danger`); `Tab`/`Shift+Tab` trapped in the top layer; focus returns to the
  trigger on close.
* Live regions: `role="status" aria-live="polite"` for loading/success/progress,
  `role="alert"` for errors. Upload progress announced at 0/25/50/75/100 %.
  Alfred's spoken output is mirrored to an on-screen `role="status"` line.
* `prefers-reduced-motion`: no open/close transitions, static "Loading…", no
  number/progress animation.
* Board columns carry a text label **and** count; `rejected` / `withdrawn` and
  terminal outreach stages use a dashed border, never colour alone.
* Every mutating control disables + `aria-busy` while its request is in flight;
  an in-flight `Set` keyed by resource id blocks re-entry after a re-render.
* Every async surface has a monotonic request token; a late response is dropped
  if the selection changed or the panel closed.

## Error classification (`fetchJSON`)

`kind: "collection"` = a base list route; `"record"` = a specific `/{id}`;
`"action"` = a mutation.

| HTTP / condition | result | shown |
| --- | --- | --- |
| 2xx | `{ok, data}` | — |
| 404 + `{code:"feature_unavailable"}`, or bodyless 404 on a `collection`, or 501 | `{unavailable}` | "This feature isn't in this build yet." |
| 404 `{code:"not_found"}` on a `collection` (unknown area/project/parent) | `{error, message}` | server-approved safe message |
| 404 on a `record` | `{notFound}` | "That record no longer exists." |
| 401 / 403 | `{forbidden}` | "You don't have access…" (an error state, **not** "unavailable") |
| 400 / 409 / 413 / 415 / 422 | `{validation, code, message, data}` | fixed per-status copy; a backend `detail` **string** is appended only if ≤160 chars, no `<`/`>`, no stack markers |
| 5xx / network / non-JSON | `{error, retryable}` | "Server error — try again." + Retry |

Error envelopes handled: Agent A top-level `{code}`, Agent B nested
`detail.code`, FastAPI `detail: [...]` arrays, and bodyless/network failures.
No raw error body is ever rendered.

---

## `integrations.js`

* **Mount** `#integrations-root`; **HUD** `#integrations-indicator` dot.
* **Consumes** `GET /api/integrations` — a nested object of **boolean leaves**,
  e.g. `{ job_sources:{…}, ai:{ anthropic, pack_generation, pack_revision, pack_autofill }, prospects:{…}, github_repos, news, outreach_mailto }`.
* Recursive renderer: own enumerable keys only, `__proto__`/`constructor`/`prototype`
  skipped, **max depth 4**, boolean leaf → row, nested object → titled group,
  everything else ignored. Keys are only ever `textContent`.
* **Dot:** green whenever the endpoint returns a valid object (reachable) — even
  if every optional service is `false`; grey for unreachable / loading / error.
  No "N of M missing" text on the HUD or map. A failed fetch is **not cached**
  (next call / Retry re-fetches).
* **States:** loading (grey dot) · unavailable / error (grey dot, panel error +
  Retry) · populated.

## `tracker.js`

* **Mount** `#tracker-root`; **HUD** `#tracker-btn`; also owns
  `#job-modal-track-btn`.
* **Stages (canonical, 10):** `interested, preparing, applied, assessment,
  recruiter_screen, interview, final_interview, offer, rejected, withdrawn`.
  All ten are board columns (`rejected`/`withdrawn` included). An unrecognised
  stage value → trailing "Unknown stage" column, logged, never dropped.
* **Confirm required** to enter a stage: `to_stage ∈ {rejected, withdrawn}` **or**
  a backward move (`index(to) < index(from)`). Confirm modal carries an optional
  note textarea. Alfred-initiated stage moves **always** confirm.
* **Endpoints**
  * `GET /api/tracked-applications` (`?archived=` assumed — see below)
  * `POST /api/tracked-applications` — **job-linked body is exactly `{"job_id":"<id>"}`**
    (backend copies company + role title from the Job row); **manual create**
    body `{company, role_title}`.
  * `GET /api/tracked-applications/{id}` · `PATCH …/{id}` (`{archived:false}` to
    restore) · `DELETE …/{id}` = **archive**
  * `POST /api/tracked-applications/{id}/stage` — body **exactly `{to_stage, note?}`**
  * `GET /api/tracked-applications/calendar?from=&to=`
  * `…/{id}/events`, `…/{id}/contacts`, `…/{id}/projects` — read from the detail
    payload; write verbs assumed (see below)
* **Duplicate job tracking (confirmed):** `job_id` is unique even when archived.
  A duplicate → `409 { code:"already_tracked", tracked_application_id, archived }`.
  `archived=false` → "already being tracked" + "Open existing application".
  `archived=true` → "previously archived" + "Open archived application" + an
  optional deliberate "Restore to tracker" (`PATCH {archived:false}`).
* **Packs** (`normalizePack` on `{schema_version, pack, references}`; answers are
  rendered from the canonical objects — `key/label/category/answer_kind/
  autofill_exportable/value/source/status/provenance/edited_by_user`):
  * `POST /api/tracked-applications/{id}/pack` — body
    `{cv_id, project_ids, project_file_ids, job_description, regenerate}`.
    The form offers CV, linked projects, **only AI-readable + AI-context-enabled**
    project files, optional JD text, and a regenerate checkbox.
  * `GET /api/tracked-applications/{id}/pack` · `GET /api/packs/{pack_id}`
  * `PATCH /api/packs/{pack_id}/answers/{key}` — body `{value}`; returns the
    canonical representation and **clears review state** (badge → "not reviewed",
    autofill disabled).
  * `POST /api/packs/{pack_id}/revise` — `{feedback}` · `POST /api/packs/{pack_id}/review`
    (the **only** source of reviewed status) · `GET /api/packs/{pack_id}/autofill`
  * **Gating:** generate/regenerate → `ai.pack_generation`; revise →
    `ai.pack_revision`; autofill export → `ai.pack_autofill`. Viewing, answer
    editing and review are **never** key-gated once a pack exists.
  * **Autofill:** shown read-only using the server's `schema_version` + payload
    verbatim (no second schema). "Copy autofill JSON" is an explicit click with a
    visible privacy warning; nothing is written to a URL, `localStorage`,
    `sessionStorage`, or analytics. The `/autofill` payload is expected to already
    omit `autofill_exportable=false` fields (legal attestations, e-signatures,
    demographic/EEO). If such a field appears anyway (contract drift) the module
    **refuses to copy and warns** — it never silently strips and presents a
    modified payload as canonical.
* **States:** loading / empty / unavailable / forbidden / error — board, detail,
  each detail section, and pack section independently.

## `projectfiles.js`

* **Mount** `#projectfiles-root`; **HUD** `#projectfiles-btn`.
* **Endpoints** (nested, canonical): `GET /api/projects` (project picker, live) ·
  `GET/POST /api/projects/{project_id}/files` ·
  `GET/PATCH/DELETE /api/projects/{project_id}/files/{file_id}` ·
  `GET /api/projects/{project_id}/files/{file_id}/download`.
* `normalizeFile` canonical fields: `original_name`, `extension`, `byte_size`,
  `extract_status`, `ai_context_enabled`, `description`, `created_at`
  (accepts `uploaded_at`).
* **No allowlist in JS.** A broad `accept` hint + a 50 MB client size pre-check
  only; the server is authoritative for extension, count and storage limits.
* Upload uses `XMLHttpRequest` for a real progress %. Archive/CAD/image rows show
  "Stored attachment — not read by AI" and disable the AI-context toggle.
  Filenames are text only; **no inline preview of any kind**; download is an
  `<a href>` through `safeUrl`.
* **Errors** → fixed per-status copy (400/404/409/413/415/422/5xx); a safe
  backend `detail` string may be appended; never raw HTML / exception text.
* **States:** loading / empty (per project) / unavailable / forbidden / error /
  populated + per-upload progress + error rows.

## `outreach.js`

* **Mount** `#outreach-root`; **HUD** `#outreach-btn`. Agent B canonical contract.
* **Stages** `identified → contacted → replied → meeting → {closed_won,
  closed_lost}`, plus `opted_out`. `closed_won/closed_lost/opted_out` are
  terminal. **No `sent` stage** — post-mailto the stage is `contacted`.
* The generic `/stage` selector offers **only** Agent B's administrative
  transitions: `replied → meeting`, `meeting → closed_won|closed_lost`, and
  `any active → closed_lost`. Draft, approve, mailto, reopen and opt-out use
  their own dedicated endpoints and never go through `/stage`.
* **Endpoints:** `GET /api/outreach/pipeline` · `GET /api/outreach/threads` ·
  `GET /api/outreach/threads/{id}` · `POST /api/outreach/threads/{id}/{draft,
  revise, approve, mailto, stage, reopen, opt-out}` ·
  `POST /api/prospects/{area}/{discover, contacts/collect}` ·
  `GET /api/prospects/{area}/contacts` · `GET /api/prospects/areas` (live).
* Draft / revise gated on `ai.anthropic` (Agent B needs only the Anthropic key).
* **mailto:** button labelled **"Open in email app"**. Requires an approved draft
  and a single thread contact address. On confirm → `POST …/mailto` → response
  `{mailto_url, thread}`. `mailto_url` is validated: scheme exactly `mailto:`;
  exactly one recipient; percent-decoded; no CR/LF/comma/semicolon/whitespace;
  case-insensitively equal to the thread contact; query keys **only** `subject`
  / `body` (no `cc`/`bcc`/unknown, no duplicates). Then `window.location.href =
  mailto_url`. Message: **"Draft opened in your email client — nothing was sent."**
  Never claims an email was sent.
* **States:** loading / empty / unavailable / forbidden / error / populated.

## `alfred.js`

* Registers a dispatcher via `window.JobRadar.registerAlfredDispatcher`. A
  recognised command returns `true` synchronously (so app.js's built-in handler
  does not also run); anything else returns `false` → built-in handler + its own
  harmless no-op. Unknown input performs **no action** and no fabricated
  interpretation. No permanent "NLU unavailable" banner — that line shows only on
  an explicit request for free-form mode.
* Entity resolution: exact-unique → proceed; multiple → on-screen choice list
  (no auto-pick); none → honest message. Stage words map only to the 10-enum.
* A new recognised command is ignored while a prior command's on-screen prompt is
  still pending.

| id | risk | effect |
| --- | --- | --- |
| `nav.section` | low | open tracker / files / outreach / integrations |
| `nav.builtin` | low | delegate `open news/dossier/prospects` to app.js |
| `jobs.filter` | low | set `#search` + dispatch `input` (filter/navigation only — never opens a job) |
| `apps.list` | low | open board, speak the count |
| `apps.open` | low | resolve name → open detail |
| `apps.next` | low | `GET …/calendar` → open Calendar + speak summary |
| `apps.prepare` | **confirm** | resolve name → open the pack-generation form (its Generate button is the on-screen confirmation) |
| `apps.stage` | **confirm** | resolve name + map stage → `UI.confirm` (with note) → `POST …/stage {to_stage, note?}` |
| `outreach.open` | low | open the Outreach panel / a thread; never drafts, approves, or generates a mailto |
| `nlu.unsupported` | low | explains that free-form mode is unavailable |

Alfred never: opens/sends an outreach mailto, approves an outreach draft,
generates/regenerates a pack without an on-screen click, submits an application,
triggers autofill, uploads files, or archives/deletes/edits contacts. A spoken
"yes" never confirms anything.

---

## Job-modal bridge — open integration blocker

"Track application" needs the current job's **id**, which lives only in
`frontend/app.js`'s closure. `app.js` is frozen for this workstream and currently
emits nothing. `tracker.js` adds `#job-modal-track-btn` (disabled) and listens
for:

```js
document.dispatchEvent(new CustomEvent("jobradar:jobmodalopen",
  { detail: { id, company, title, url, source } }));   // in openJobModal(job)
document.dispatchEvent(new CustomEvent("jobradar:jobmodalclose"));  // in closeJobModal()
```

Until those two lines exist in `app.js`, the button stays disabled with an
explanatory tooltip. No MutationObserver / polling / DOM-scraping fallback is
used.

## Assumptions to verify after Agents A & B finalise

* `GET /api/tracked-applications` archived filter (`?archived=`) — param name.
* tracked-application **detail** JSON shape (contacts / events / deadlines /
  next_actions / linked projects) and the **write** verbs for
  `…/{id}/events`, `…/{id}/contacts`, `…/{id}/projects`.
* `GET …/calendar` item shape (`when` / `kind` / `title` / `company` /
  `application_id`).
* Manual-create body (`{company, role_title}`) and whether `PATCH` accepts a
  `role_title` display override.
* Pack answer-edit route (`PATCH /api/packs/{pack_id}/answers/{key}` `{value}`) —
  confirmed by this brief; verify the returned representation matches
  `{schema_version, pack, references}` and that it clears `review_valid`.
* `/autofill` payload shape and its `autofill_exportable` policy (must already
  exclude legal/signature/demographic fields).
* Outreach: full stage list / transition graph, discovery-status values, thread /
  contact / suppression response shapes, error codes, and the exact
  contacts/suppression endpoint paths (only the ones listed above are wired).
* Integration key names actually emitted: `ai.pack_generation`,
  `ai.pack_revision`, `ai.pack_autofill`, `ai.anthropic`, plus any capability /
  feature-enablement map for 404 classification.
