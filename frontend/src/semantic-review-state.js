export function maySubmitHumanReview({checked, reviewer, rationale, busy, status}) {
  return checked === true && !busy && !!reviewer.trim() && !!rationale.trim() &&
    ['supported','partially_supported','contradicted','insufficient_evidence'].includes(status);
}
