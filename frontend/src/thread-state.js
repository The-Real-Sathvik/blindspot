export const milestones = [
  {id:'topic', label:'Research topic'}, {id:'research', label:'Paper discovery & full-text preparation'},
  {id:'evidence', label:'Evidence'},
  {id:'findings', label:'Findings'}, {id:'gaps', label:'Research gaps'},
];

export function threadState(bundle, {busy = '', failures = {}, needsRefresh = false} = {}) {
  if (!bundle) return {active:'topic', action:{kind:'start', label:'Start research', reason:'Create a workspace for your question.'}, states:{}};
  const {details, papers = [], docs = [], claims = [], gaps = []} = bundle;
  const workflow = details?.workflow;
  const stageState = (stage) => ({finished:'completed',running:'in_progress',pending:'not_started',failed:'failed',blocked:'blocked'})[workflow?.stages?.[stage]?.processing] || null;
  const shortlist = papers.filter(p => p.included === true);
  const analysis = papers.filter(p => p.analysis_selected === true);
  const eligible = shortlist.filter(p => p.document?.status !== 'failed' && !['failed','started'].includes(p.document?.attempt?.outcome) && (p.document?.can_acquire || p.document?.can_extract));
  const failed = shortlist.find(p => p.document?.attempt?.outcome === 'failed' || p.document?.attempt?.outcome === 'started');
  const alternatives = papers.filter(p => p.included !== true && (p.document?.can_acquire || p.document?.can_extract || p.document?.status === 'extracted'));
  const recoveredEvidence = alternatives.filter(p => p.document?.status === 'extracted');
  const chunks = docs.flatMap(d => (d.chunks || []).map(c => ({document:d, chunk:c})));
  const usable = chunks.filter(x => x.document.claims_available);
  const complete = shortlist.filter(p => p.document?.status === 'extracted');
  const supportedPapers = workflow ? new Set(Array(workflow.supported_paper_count || 0).fill('supported')) : new Set(claims.filter(claim => claim.semantic_status === 'supported').map(claim => claim.paper_id));
  const latest = (details?.runs || []).find(r => r.role === 'claim_extraction');
  const discovered = papers.length > 0 || (details?.searches || []).some(s => !s.failed);
  const states = {
    topic:'completed',
    research:busy === 'discover' || ['acquire','extract','prepare','upload'].includes(busy) ? 'in_progress' : stageState('preparation') || (analysis.length ? 'completed' : discovered ? 'insufficient_evidence' : 'not_started'),
    evidence:stageState('evidence') || (analysis.length ? 'completed' : 'blocked'),
    findings:busy === 'claims' ? 'in_progress' : stageState('findings') || (claims.length ? 'completed' : 'blocked'),
    gaps:busy === 'gaps' ? 'in_progress' : stageState('gaps') || (gaps.length ? 'completed' : 'insufficient_evidence'),
  };
  states.papers = busy === 'discover' ? 'in_progress' : failures.papers ? 'failed' : details?.searches?.[0]?.failed ? (papers.length ? 'partial' : 'failed') : discovered ? 'completed' : 'not_started';
  states.fulltext = ['acquire','extract','prepare','upload'].includes(busy) ? 'in_progress' : failures.evidence ? 'failed' : complete.length && complete.length === shortlist.length ? 'completed' : recoveredEvidence.length && details?.status === 'completed' ? 'completed_with_alternatives' : chunks.length ? 'partial' : !papers.length ? 'blocked' : shortlist.some(p=>p.document?.status==='failed') ? 'failed' : eligible.length ? 'not_started' : 'blocked';
  if (!workflow) {
    states.evidence = chunks.length ? 'completed' : 'blocked';
    states.findings = busy === 'claims' ? 'in_progress' : claims.length ? 'completed' : latest?.outcome === 'no_claims' ? 'abstained' : latest?.status === 'failed' || latest?.outcome === 'partial_error' ? 'failed' : usable.length ? 'not_started' : 'blocked';
    states.gaps = busy === 'gaps' ? 'in_progress' : gaps.length ? 'completed' : supportedPapers.size >= 2 ? 'not_started' : 'insufficient_evidence';
  }
  let active, action;
  if (!workflow && needsRefresh) { active='fulltext'; action={kind:'refresh',label:'Refresh preparation status',reason:'Processing may still be running on the server.'}; }
  else if (!workflow && !discovered) { active='papers'; action={kind:'discover',label:states.papers === 'failed' ? 'Retry paper search' : 'Discover papers',reason:'Search OpenAlex for papers related to this topic.'}; }
  else if (!workflow && !papers.length) { active='papers'; action={kind:'new',label:'Start a new research topic',reason:'The search returned no saved papers. Try broader wording.'}; }
  else if (!workflow && shortlist.some(p=>!p.document)) { active='fulltext'; action={kind:'review_options',label:'Inspect paper details',reason:'The backend did not provide document status. Restart the backend if the mismatch persists.'}; }
  else if (!workflow && failed) { active='fulltext'; action={kind:'review_failure',label:'Review document issue',reason:'A previous attempt needs attention. Saved work is unchanged; retries are manual.',paperId:failed.id}; }
  else if (!workflow && eligible.length) { active='fulltext'; action={kind:'prepare',label:'Prepare eligible shortlist',reason:'Processes up to five shortlisted papers sequentially; no AI is invoked.'}; }
  else if (!workflow && !chunks.length && alternatives.length) { active='fulltext'; action={kind:'review_alternative',label:'Inspect an eligible alternative',reason:'No shortlisted PDF can be processed. The original shortlist remains unchanged.'}; }
  else if (!workflow && !chunks.length) { active='fulltext'; action={kind:'review_options',label:'Inspect a paper or upload PDF',reason:'No shortlisted PDF can be processed. Inspect a paper or upload a lawful local copy.'}; }
  else if (!workflow && !claims.length && usable.length) { active='evidence'; action={kind:'inspect_evidence',label:'Inspect source evidence',reason:'Review a passage and explicitly consent before sending it to a cloud provider.'}; }
  else if (!workflow && !claims.length) { active='evidence'; action={kind:'inspect_evidence',label:'Inspect source evidence',reason:'Some documents require identity review or are not eligible for cloud extraction.'}; }
  else if (!workflow && !gaps.length && supportedPapers.size >= 2) { active='gaps'; action={kind:'detect_gaps',label:'Detect provisional gaps',reason:'Two or more papers have current human-supported claims. Detection remains provisional and requires explicit cloud consent.'}; }
  else if (!workflow) { active='findings'; action={kind:'review_claims',label:'Review saved findings',reason:'Source quotations can be checked, but scientific interpretation requires human review.'}; }
  else if (needsRefresh) { active='research'; action={kind:'refresh',label:'Refresh research status',reason:'Processing may still be running on the server.'}; }
  else if (!discovered || !papers.length) { active='research'; action={kind:'start',label:'Research preparation is automatic',reason:'Discovery and source preparation run after topic submission.'}; }
  else if (!analysis.length) { active='research'; action={kind:'review_options',label:'View preparation limitations',reason:'No legally accessible, source-verified paper was prepared.'}; }
  else if (!claims.length && usable.length) { active='evidence'; action={kind:'inspect_evidence',label:'Inspect source evidence',reason:'Review a passage and explicitly consent before sending it to a cloud provider.'}; }
  else if (!claims.length) { active='evidence'; action={kind:'inspect_evidence',label:'Inspect source evidence',reason:'Some documents require identity review or are not eligible for cloud extraction.'}; }
  else if (!gaps.length) { active='gaps'; action={kind:'review_claims',label:'Review research-gap status',reason:workflow?.stages?.gaps?.detail || 'No provisional gap is saved.'}; }
  else { active='findings'; action={kind:'review_claims',label:'Review saved findings',reason:'Source quotations and automated semantic assessments remain separate.'}; }
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
