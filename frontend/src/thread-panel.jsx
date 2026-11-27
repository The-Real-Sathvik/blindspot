import React, {useEffect, useRef} from 'react';
import {X} from 'lucide-react';
import {displayAuthors, documentLabel} from './api';
import {DocumentControls} from './document-controls.jsx';
import {IdentityReview} from './identity-review.jsx';
import {ClaimControls} from './claim-controls.jsx';
import {sourceForClaim} from './thread-state.js';
import {SemanticReview} from './semantic-review.jsx';

export function EvidencePanel({panel, close, jobId, papers, docs, claims, readOnly=false, busy, setBusy, processPaper, uploadPaper, refresh, openPanel, openPaper, onReviewClaims}) {
  const container = useRef(null);
  useEffect(() => {
    if (!panel) return undefined;
    const previous = window.document.activeElement;
    container.current?.querySelector('button')?.focus();
    function key(event) {
      if (event.key === 'Escape') {event.preventDefault(); close(); return;}
      if (event.key !== 'Tab' || !window.matchMedia('(max-width: 1100px)').matches) return;
      const items = [...(container.current?.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]') || [])];
      if (!items.length) return;
      const first=items[0], last=items[items.length-1];
      if (event.shiftKey && window.document.activeElement === first) {event.preventDefault(); last.focus();}
      if (!event.shiftKey && window.document.activeElement === last) {event.preventDefault(); first.focus();}
    }
    window.document.addEventListener('keydown',key);
    return () => {window.document.removeEventListener('keydown',key); if (previous?.isConnected) previous.focus();};
  }, [!!panel, close]);
  if (!panel) return null;
  const paper=papers.find(p=>p.id===panel.paperId);
  const paperDocument=paper && docs.find(d=>d.paper_id===paper.id && d.chunks?.length);
  const document=docs.find(d=>d.id===panel.documentId);
  const chunk=document?.chunks?.find(c=>c.id===panel.chunkId);
  const claim=claims.find(c=>c.id===panel.claimId);
  const gap=panel.gap;
  const title=panel.kind==='paper'?'Paper details':panel.kind==='chunk'?'Source passage':panel.kind==='claim'?'Finding and citations':'Candidate gap';
  return <><button className="panel-backdrop" aria-label="Close evidence panel" onClick={close}/><aside className="evidence-panel" ref={container} role="dialog" aria-modal={typeof window !== 'undefined' && window.matchMedia('(max-width: 1100px)').matches ? true : undefined} aria-label={title}>
    <header className="panel-heading"><div><span className="mini-label">EVIDENCE PANEL</span><h2>{title}</h2></div><button className="icon-button" onClick={close} aria-label="Close evidence panel"><X size={18}/></button></header>
    <div className="panel-content">
      {panel.kind==='paper' && paper && <><h3>{paper.title}</h3><p>{displayAuthors(paper.authors)} · {paper.year || 'Year not provided'} · {paper.source}</p>
        <div className="panel-note"><strong>Full text</strong><p>{documentLabel(paper.document?.status)}. {paper.document?.message}</p></div>
        <p>Research relevance and full-text access are independent.</p>
        {readOnly ? <p className="muted">Document collection is handled automatically. This panel is read-only.</p> : <><DocumentControls document={paper.document} busy={busy} onProcess={processPaper} onUpload={uploadPaper} onEvidence={paperDocument?()=>openPanel({kind:'chunk',paperId:paper.id,documentId:paperDocument.id,chunkId:paperDocument.chunks[0].id}):undefined}/><IdentityReview jobId={jobId} paperId={paper.id} document={paper.document} busy={busy} setBusy={setBusy} onRefresh={refresh}/></>}
        <h4>About this paper</h4><p>{paper.abstract || 'No abstract was provided by the source.'}</p>
        {paper.landing_page_url && <a href={paper.landing_page_url} target="_blank" rel="noreferrer" className="text-link">Open source page ↗</a>}
        <details className="advanced"><summary>Advanced selection details</summary><p>{paper.selection_reason || 'No selection explanation was provided.'}</p><p>Relevance score: {paper.score ?? 'not available'} · DOI: {paper.doi || 'not provided'}</p></details>
      </>}
      {panel.kind==='chunk' && document && chunk && <><button className="text-button" onClick={()=>openPaper(document.paper_id)}>← {document.paper_title}</button><h3>PDF page {chunk.page}</h3><p>Extracted passage from the saved PDF. Other parts of this page may not be shown.</p>
        {document.origin==='user_upload_unverified' && <p className="panel-note">Uploaded PDF association is not independently verified.</p>}
        <div className="source-text">{highlight(chunk.text, panel.quotation)}</div>
        <div className="source-navigation">{document.chunks.map((c,i)=><button key={c.id} className={c.id===chunk.id?'active':''} onClick={()=>openPanel({kind:'chunk',paperId:document.paper_id,documentId:document.id,chunkId:c.id})} aria-current={c.id===chunk.id?'true':undefined}>p. {c.page} · {i+1}</button>)}</div>
        <details className="advanced"><summary>Advanced source details</summary><p>Document <code>{document.id}</code><br/>Chunk <code>{chunk.id}</code><br/>Page offsets {chunk.start_offset}–{chunk.end_offset}</p></details>
        {!readOnly && <ClaimControls key={`${jobId}-${chunk.id}`} jobId={jobId} document={document} chunk={chunk} busy={busy} setBusy={setBusy} onRefresh={refresh} onReview={onReviewClaims}/>} 
      </>}
      {panel.kind==='claim' && claim && <><h3>{claim.statement}</h3><p>{claim.paper_title} · {claim.category?.replaceAll('_',' ')}</p><div className="panel-note"><strong>Automated semantic assessment</strong><p>{claim.automated_assessment_status?.replaceAll('_',' ') || 'Not assessed'} · This is automated evidence interpretation, not human review.</p>{claim.automated_assessment_rationale && <p>{claim.automated_assessment_rationale}</p>}{claim.automated_assessment_uncertainty && <p>Uncertainty: {claim.automated_assessment_uncertainty}</p>}</div><div className="panel-note">Human scientific review: {claim.semantic_status === 'unverified' ? 'Not independently checked' : claim.semantic_status}. Human review remains separate.</div>
        {claim.evidence.map(e=><div className="citation" key={e.id}><blockquote>{e.quotation}</blockquote><p>PDF page {e.page} · Source link: {e.quotation_status} / {e.location_status}</p>{sourceForClaim({evidence:[e]},docs) && <button className="secondary" onClick={()=>{const s=sourceForClaim({evidence:[e]},docs);openPanel({kind:'chunk',paperId:s.document.paper_id,documentId:s.document.id,chunkId:s.chunk.id,quotation:s.quotation});}}>Read this passage</button>}<details className="advanced"><summary>Advanced citation details</summary><code>{e.id}</code></details></div>)}
        {claim.uncertainty && <><h4>Uncertainty</h4><p>{claim.uncertainty}</p></>}
        <SemanticReview key={claim.id} jobId={jobId} claim={claim} busy={busy} setBusy={setBusy} onRefresh={refresh}/>
      </>}
      {panel.kind==='gap' && gap && <><span className="tag">Provisional · {gap.validation_status?.replaceAll('_',' ')}</span><h3>{gap.statement}</h3><p>{gap.category?.replaceAll('_',' ')}</p><p>A possible opening based on these papers only; not established as globally novel.</p><h4>Explanation</h4><p>{gap.explanation}</p><h4>Counterevidence</h4><p>{gap.counterevidence || 'Not provided.'}</p><h4>Uncertainty</h4><p>{gap.uncertainty || 'Not provided.'}</p>
        {['supporting','counterevidence'].map(role=>{const refs=gap.references.filter(ref=>ref.role===role);return <section key={role} className="gap-reference-group"><h4>{role==='supporting'?'Supporting evidence':'Counterevidence references'}</h4>{refs.length?refs.map((ref,i)=>{const c=claims.find(x=>x.id===ref.claim_id);const p=papers.find(x=>x.id===ref.paper_id);const e=c?.evidence.find(item=>item.id===ref.evidence_id);return <div className="reference-row" key={i}><p>{p?.title || 'Paper not available in this workspace'}{e?` · PDF page ${e.page}`:''}</p>{e && <blockquote>{e.quotation}</blockquote>}{c && <button className="text-button" onClick={()=>openPanel({kind:'claim',claimId:c.id})}>Open finding →</button>}<details className="advanced"><summary>Advanced references</summary><code>{ref.paper_id} / {ref.claim_id} / {ref.evidence_id}</code></details></div>}):<p className="muted">{role==='supporting'?'No supporting references were saved.':'No counterevidence was selected.'}</p>}</section>;})}</>}
    </div>
  </aside></>;
}

function highlight(text, quote) {
  if (!quote) return text;
  const start=text.indexOf(quote);
  if (start<0) return text;
  return <>{text.slice(0,start)}<mark>{quote}</mark>{text.slice(start+quote.length)}</>;
}
