// Whether a PACK field may be proposed (before any page match / confidence).
//
// `sensitive:true` does NOT make a field non-proposable. Agent A uses it for
// ordinary contact details (email, phone). It only means "individual
// confirmation is always required" -- which this extension enforces for every
// field regardless.
//
// Legal attestations, e-signatures, government IDs, payment/banking, medical and
// demographic/EEO fields are handled by the backend OMITTING them from the pack
// plus the page-field classifier (lib/classify.js). This module never sees them.
//
// Rules:
//   - status === "needs_input"            -> never proposable
//   - answer_kind === "declared_answer"   -> proposable ONLY when
//        source === "user_supplied" AND status === "sourced"
//   - answer_kind === "narrative"         -> proposable ONLY when the pack is
//        reviewed (still requires individual acceptance)
//   - standard fields                     -> proposable when
//        source in {profile, user_supplied}
//   - a non-empty value is required (enforced upstream in normalizeField)

const PROFILE_SOURCES = new Set(['profile', 'user_supplied']);

/**
 * @param {{source?:string, answerKind?:string, status?:string,
 *          sensitive?:boolean, reviewed?:boolean}} field
 * @returns {{proposable:boolean, reason:string}}
 */
export function decideProposable(field) {
  const no = (reason) => ({ proposable: false, reason });
  const yes = (reason) => ({ proposable: true, reason });

  const source = String(field.source || '').toLowerCase();
  const kind = String(field.answerKind || 'standard').toLowerCase();
  const status = String(field.status || '').toLowerCase();
  const reviewed = field.reviewed === true;

  if (status === 'needs_input') return no('needs_input');

  if (kind === 'declared_answer') {
    if (source !== 'user_supplied') return no('declared_not_user_supplied');
    if (status !== 'sourced') return no('declared_not_sourced');
    return yes('declared_ok');
  }

  if (kind === 'narrative') {
    return reviewed ? yes('narrative_reviewed_pack') : no('narrative_unreviewed_pack');
  }

  // standard
  return PROFILE_SOURCES.has(source) ? yes('standard_sourced') : no('standard_unsourced');
}
