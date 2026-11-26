export const milestones = [
  {id:'topic', label:'Research topic'}, {id:'research', label:'Paper discovery & full-text preparation'},
  {id:'evidence', label:'Evidence'},
  {id:'findings', label:'Findings'}, {id:'gaps', label:'Research gaps'},
];

export function threadState(bundle, {busy = '', failures = {}, needsRefresh = false} = {}) {
  if (!bundle) return {active:'topic', action:{kind:'start', label:'Start research', reason:'Create a workspace for your question.'}, states:{}};
  const {details, papers = [], docs = [], claims = [], gaps = []} = bundle;
  const shortlist = papers.filter(p => p.included === true);
  const analysis = papers.filter(p => p.analysis_selected === true);
  const eligible = shortlist.filter(p => p.document?.status !== 'failed' && !['failed','started'].includes(p.document?.attempt?.outcome) && (p.document?.can_acquire || p.document?.can_extract));
  const failed = shortlist.find(p => p.document?.attempt?.outcome === 'failed' || p.document?.attempt?.outcome === 'started');
  const alternatives = papers.filter(p => p.included !== true && (p.document?.can_acquire || p.document?.can_extract || p.document?.status === 'extracted'));
  const recoveredEvidence = alternatives.filter(p => p.document?.status === 'extracted');
  const chunks = docs.flatMap(d => (d.chunks || []).map(c => ({document:d, chunk:c})));
  const usable = chunks.filter(x => x.document.claims_available);
  const complete = shortlist.filter(p => p.document?.status === 'extracted');
  const supportedPapers = new Set(claims.filter(claim => claim.semantic_status === 'supported').map(claim => claim.paper_id));
  const latest = (details?.runs || []).find(r => r.role === 'claim_extraction');
  const discovered = papers.length > 0 || (details?.searches || []).some(s => !s.failed);
  const states = {
    topic:'completed',
    research:busy === 'discover' || ['acquire','extract','prepare','upload'].includes(busy) ? 'in_progress' : analysis.length ? 'completed' : failures.evidence ? 'failed' : discovered ? 'insufficient_evidence' : 'not_started',
    evidence:analysis.length ? 'completed' : 'blocked',
    findings:busy === 'claims' ? 'in_progress' : claims.length ? 'completed' : latest?.outcome === 'no_claims' ? 'abstained' : latest?.status === 'failed' || latest?.outcome === 'partial_error' ? 'failed' : usable.length ? 'not_started' : 'blocked',
    gaps:busy === 'gaps' ? 'in_progress' : gaps.length ? 'completed' : supportedPapers.size >= 2 ? 'not_started' : 'insufficient_evidence',
  };
  let active, action;
  if (needsRefresh) { active='research'; action={kind:'refresh',label:'Refresh research status',reason:'Processing may still be running on the server.'}; }
  else if (!discovered || !papers.length) { active='research'; action={kind:'start',label:'Research preparation is automatic',reason:'Discovery and source preparation run after topic submission.'}; }
  else if (!analysis.length) { active='research'; action={kind:'review_options',label:'View preparation limitations',reason:'No legally accessible, source-verified paper was prepared.'}; }
  else if (!claims.length && usable.length) { active='evidence'; action={kind:'inspect_evidence',label:'Inspect source evidence',reason:'Review a passage and explicitly consent before sending it to a cloud provider.'}; }
  else if (!claims.length) { active='evidence'; action={kind:'inspect_evidence',label:'Inspect source evidence',reason:'Some documents require identity review or are not eligible for cloud extraction.'}; }
  else if (!gaps.length && supportedPapers.size >= 2) { active='gaps'; action={kind:'detect_gaps',label:'Detect provisional gaps',reason:'Two or more papers have current human-supported claims. Detection remains provisional and requires explicit cloud consent.'}; }
  else { active='findings'; action={kind:'review_claims',label:'Review saved findings',reason:gaps.length ? 'Claims and candidate gaps remain subject to independent review.' : 'Source quotations can be checked, but scientific interpretation requires human review.'}; }
  return {active, action, states, shortlist, analysis, eligible, alternatives, chunks, usable, supportedPapers};
}

export function sourceForClaim(claim, docs) {
  for (const evidence of claim?.evidence || []) {
    for (const document of docs || []) {
      const chunk = (document.chunks || []).find(c => c.id === evidence.chunk_id);
      if (chunk) return {document, chunk, quotation:evidence.quotation};
    }
  }
  return null;
}
