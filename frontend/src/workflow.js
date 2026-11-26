export const stages = [
  { id: 'idea', label: 'Research idea' },
  { id: 'papers', label: 'Paper discovery' },
  { id: 'evidence', label: 'Evidence explorer' },
  { id: 'claims', label: 'Extracted claims' },
  { id: 'gaps', label: 'Research gaps' },
];

export function nextStageId(id) {
  const index = stages.findIndex(stage => stage.id === id);
  return index >= 0 && index + 1 < stages.length ? stages[index + 1].id : null;
}

export function resolveWorkspace(jobs, savedId) {
  return jobs.some(job => job.id === savedId) ? savedId : (jobs[0]?.id || null);
}

export async function createWorkspace(request, topic) {
  const cleaned = topic.trim();
  if (!cleaned) throw new Error('Enter a research idea first.');
  return request('/jobs', {method:'POST', body:JSON.stringify({topic:cleaned})});
}

export function jobPaths(id) {
  const root = `/jobs/${encodeURIComponent(id)}`;
  return ['', '/papers', '/evidence', '/claims', '/gaps'].map(suffix => root + suffix);
}

export function preferredPaperId(papers) {
  const usable = papers.filter(paper => paper.document?.status === 'extracted' || paper.document?.can_acquire || paper.document?.can_extract);
  const shortlisted = usable.filter(paper => paper.included === true);
  const candidates = shortlisted.length ? shortlisted : usable;
  const priority = {extracted:0, downloaded:1, available_for_acquisition:2, failed:3, unsupported:4, unavailable:5};
  return [...candidates].sort((a,b) => (priority[a.document?.status] ?? 6) - (priority[b.document?.status] ?? 6))[0]?.id || null;
}

export function shortlistSummary(papers) {
  const shortlist = papers.filter(paper => paper.included === true);
  const eligible = shortlist.filter(paper => paper.document?.can_acquire || paper.document?.can_extract);
  const withEvidence = shortlist.filter(paper => paper.document?.status === 'extracted');
  return {shortlist, eligible, withEvidence, hasSupported: shortlist.some(paper => paper.document?.status === 'extracted' || paper.document?.status === 'downloaded' || paper.document?.status === 'available_for_acquisition' || paper.document?.can_acquire || paper.document?.can_extract)};
}

export function stageStatus({ details, papers = [], docs = [], claims = [], gaps = [], busy = '', failures = {} }) {
  const chunks = docs.flatMap(doc => doc.chunks || []);
  const claimRuns = (details?.runs || []).filter(run => run.role === 'claim_extraction');
  const failedDiscovery = details?.searches?.[0]?.failed === true;
  const supportedPapers = new Set(claims.filter(claim => claim.semantic_status === 'supported').map(claim => claim.paper_id));
  const summary = shortlistSummary(papers);
  const evidenceState = summary.shortlist.length && summary.withEvidence.length === summary.shortlist.length ? 'completed' : summary.withEvidence.length || (!summary.shortlist.length && chunks.length) ? 'partial' : !papers.length ? 'blocked' : summary.shortlist.some(paper => paper.document?.status === 'failed') ? 'failed' : summary.eligible.length || (!summary.shortlist.length && papers.some(paper => paper.document?.can_acquire || paper.document?.can_extract)) ? 'not_started' : 'blocked';
  const latestFailed = claimRuns[0]?.status === 'failed' || claimRuns[0]?.outcome === 'partial_error';
  return {
    idea: details ? 'completed' : 'not_started',
    papers: busy === 'discover' ? 'in_progress' : failures.papers ? 'failed' : papers.length ? 'completed' : failedDiscovery ? 'failed' : details?.searches?.length ? 'completed' : 'not_started',
    evidence: ['acquire', 'extract', 'prepare'].includes(busy) ? 'in_progress' : failures.evidence ? 'failed' : evidenceState,
    claims: busy === 'claims' ? 'in_progress' : latestFailed ? (claims.length ? 'partial' : 'failed') : claims.length ? 'completed' : claimRuns[0]?.outcome === 'no_claims' ? 'abstained' : !chunks.length || !docs.some(doc => doc.claims_available && doc.chunks?.length) ? 'blocked' : 'not_started',
    gaps: gaps.length ? 'completed' : supportedPapers.size < 2 ? 'insufficient_evidence' : 'blocked',
  };
}

export const statusText = state => ({not_started:'Not started', in_progress:'In progress', completed:'Completed', partial:'Partially complete', abstained:'No claims found', blocked:'Blocked', failed:'Failed', insufficient_evidence:'Insufficient evidence'}[state] || 'Not started');

export function visibleBundle(bundle, selectedId) {
  return bundle?.jobId === selectedId ? bundle : null;
}
