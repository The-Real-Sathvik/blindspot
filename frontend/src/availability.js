export function availabilitySummary(paper) {
  const document = paper.document || {};
  return {
    relevance: paper.score == null ? 'not scored' : String(paper.score),
    openAccess: paper.open_access === true ? 'open access reported' : paper.open_access === false ? 'not reported as open access' : 'not provided',
    eligibility: document.can_acquire || document.can_extract ? 'eligible' : document.status === 'extracted' ? 'evidence extracted' : 'not currently eligible',
  };
}

export function eligibleAlternatives(papers) {
  return papers.filter(p => p.included !== true && (p.document?.can_acquire || p.document?.can_extract || p.document?.status === 'extracted'));
}

// Alternatives remain outside the scholarly shortlist. This exposes their
// separate source-preparation state without promoting them or implying that a
// claim-analysis run used them.
export function alternativePreparationSummary(papers) {
  const alternatives = (papers || []).filter(p => p.included !== true);
  return {
    evidenceReady: alternatives.filter(p => p.document?.status === 'extracted').length,
    failed: alternatives.filter(p => p.document?.status === 'failed').length,
  };
}
