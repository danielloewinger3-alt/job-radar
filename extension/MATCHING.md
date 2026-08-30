# Field matching

How `lib/scoring.js` turns a normalized pack and the scanned page fields into the
proposals shown in the overlay. The design goal is **conservative**: a wrong
proposal that the user accepts is worse than a missing one.

## Inputs

**Pack field** (from `lib/packschema.js`, canonical Agent A contract):
`key`, `label`, `value`, `type` (`text|email|tel|url|number|date|textarea|select`),
`autocomplete` hint, `source`, `answer_kind`, `status`, `provenance`, `sensitive`,
plus a computed `proposable` (see *Policy* below).

**Page field** (from `lib/classify.js` `scanFields`): the live element plus
`inputType`, `label` (from `<label for>`, a wrapping `<label>`, or
`aria-labelledby`), `name`, `id`, `placeholder`, `ariaLabel`, `autocomplete`,
`disabled`, and for `<select>` the list of `{ value, text, disabled }` options.

## Step 1 — exclusions (page side)

`classifyDescriptor` drops a field entirely if it is:

- a structural non-target: `password`, `hidden`, `file`, `submit`, `reset`,
  `button`, `image`, `checkbox`, `radio`, or any unknown input type;
- `disabled` or `readonly`;
- inside a CAPTCHA container (`.g-recaptcha`, `.h-captcha`, `.cf-turnstile`,
  reCAPTCHA/hCaptcha iframes, `[data-sitekey]`);
- matched by a **sensitive/legal** pattern on its label/name/id/placeholder/aria:
  - `payment` — card number, CVV/CVC, IBAN, sort code, routing/account number, billing
  - `gov_id` — SSN, National Insurance, passport no., driver’s licence, tax/ITIN/EIN
  - `medical` — disability, medical/health condition, impairment, accommodation request
  - `eeo` — race, ethnicity, gender identity, sexual orientation, veteran status, self-identification
  - `signature` — “type your full name to sign”, electronic/digital signature
  - `legal` — “I certify/attest/agree/consent”, terms & conditions, penalty of perjury, background-check consent

Excluded fields are listed in the overlay’s “left untouched” section with the
reason; they are never scored or written.

## Step 2 — policy (pack side): can this answer be proposed at all?

`lib/policy.js` `decideProposable`:

| Condition | Result |
|---|---|
| `status === "needs_input"` | **not proposable** |
| `answer_kind === "declared_answer"` and (`source !== "user_supplied"` or `status !== "sourced"`) | **not proposable** |
| `answer_kind === "narrative"` and pack **not reviewed** | **not proposable** |
| `answer_kind === "standard"` and `source ∉ {profile, user_supplied}` | **not proposable** |
| otherwise | proposable |

`sensitive: true` has **no effect** here. After the final reviewed state is known
(`pack.reviewed && autofill.reviewed`), `applyReviewedGate` flips any narrative
field back to non-proposable if the pack ended up unreviewed. Non-proposable
fields can still appear as **preview-only** rows when they have a strong page
match, so you can see what would have been suggested.

## Step 3 — type compatibility

| pack `type` | accepts page `inputType` |
|---|---|
| `text` | `text`, `search` |
| `email` | `email`, `text` |
| `tel` | `tel`, `text` |
| `url` | `url`, `text` |
| `number` | `number`, `text` |
| `date` | `date`, `text` |
| `textarea` | `textarea` |
| `select` | `select` |

An incompatible pair scores 0 and is discarded.

## Step 4 — scoring a (pack field, page field) pair

Signals are normalized (lowercased, de-punctuated, diacritics stripped, a small
stopword list removed — `name`/`id`/`value` are **kept**) and compared as token
sets. `key` + `label` form the pack token set.

| Signal | Contribution | Meaningful? |
|---|---|---|
| `autocomplete` exact match to the pack hint | `+0.70` | yes |
| `autocomplete` known-token match to the pack `key` (equality, not substring) | `+0.55` | yes |
| token overlap with the page **label** | `+0.30` each, cap `+0.60` | yes |
| token overlap with page **name/id** | `+0.28` each, cap `+0.50` | yes |
| token overlap with **placeholder/aria-label** | `+0.15` each, cap `+0.30` | **no** |

Score is capped at `1.0`. **Meaningful gate:** a candidate is only kept if at
least one *meaningful* signal fired. A placeholder-only or aria-only resemblance
never produces a proposal.

## Step 5 — thresholds and ambiguity

Per pack field, candidates are ranked by score (ties broken by DOM order, so the
result is order-independent).

```
PREVIEW_FLOOR   = 0.45
MIN_CONFIDENCE  = 0.62
AMBIGUITY_DELTA = 0.12
```

- best `< PREVIEW_FLOOR` → **omit**.
- pack field not policy-proposable → **preview** (visible, not acceptable).
- top two candidates within `AMBIGUITY_DELTA`, **or** this page field is the #1
  pick of more than one pack field → **ambiguous**: no target is pre-selected,
  the overlay shows the candidate list, and you pick. After picking, the row is
  acceptable only if the underlying confidence was `≥ MIN_CONFIDENCE`
  (`acceptableIfPicked`); otherwise it stays preview.
- best `< MIN_CONFIDENCE` → **preview**.
- otherwise → **proposable** (Accept enabled).

Two pack fields never silently target the same page field: the contest forces
both to *ambiguous*. The overlay additionally blocks accepting a proposal whose
target was already filled by another accepted row.

## Step 6 — `<select>` resolution

`resolveSelectOption` compares each **enabled** option’s `value` and visible
`text` (normalized) to the pack field’s **`value`** only — never to the field
label (a label like “Work authorisation” is not an option like “Yes”).

| Outcome | Meaning |
|---|---|
| exactly one enabled option matches | propose it |
| two or more match | **ambiguous** — you pick the option |
| only a **disabled** option matches | **no proposal** |
| nothing matches | **no proposal** |

The first option is never used as a fallback. On Accept, `select.value` is set to
the resolved option’s `value` and `input` + `change` are dispatched.

## Tuning the threshold

`MIN_CONFIDENCE = 0.62` is deliberately above “one strong label word + one
name/id word” (`0.30 + 0.28 = 0.58`). Raising it makes the extension quieter and
pushes more matches to preview-only; lowering it surfaces more Accept buttons at
the cost of more marginal suggestions. `AMBIGUITY_DELTA = 0.12` treats a
near-tie as “ask the user”. Change these in `lib/scoring.js`; the scoring tests in
`tests/lib-pure.test.js` pin the current behaviour.

## Known false positives / negatives

- Generic labels (“Name”, “Address”) on forms with several such fields land in
  *ambiguous* rather than being guessed.
- A page field whose only signal is a helpful `placeholder` is **not** matched by
  design.
- Sites that put the real label in visually-adjacent text (not a `<label>`, not
  `aria`) will under-match.
- `autocomplete="name"` intentionally does **not** hint-match `first_name` /
  `last_name` (equality check, not substring), so split-name forms rely on label
  tokens.
