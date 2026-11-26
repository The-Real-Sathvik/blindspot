import React, {useState} from 'react';
import {request} from './api';
import {maySubmitHumanReview} from './semantic-review-state.js';

export function SemanticReview({jobId, claim, busy, setBusy, onRefresh}) {
  const [reviewer,setReviewer]=useState(''), [rationale,setRationale]=useState(''), [status,setStatus]=useState('insufficient_evidence');
  const [checked,setChecked]=useState(false), [error,setError]=useState('');
  if (claim.review_locked) return <div className="panel-note">{claim.semantic_review ? <><strong>Human assessment by {claim.semantic_review.reviewer}</strong><p>{claim.semantic_review.rationale}</p><small>Self-reported reviewer · {claim.semantic_review.reviewed_at}</small></> : 'An earlier or changed review cannot be validated. Review revision is not yet supported.'}</div>;
  if (!claim.evidence?.length || claim.evidence.some(e=>e.quotation_status !== 'valid' || e.location_status !== 'valid')) return <p className="panel-note">Human review requires structurally valid source citations first.</p>;
  const canSubmit=maySubmitHumanReview({checked,reviewer,rationale,busy,status});
  async function submit(event){
    event.preventDefault();if(!canSubmit)return;
    setBusy('review');setError('');
    try {await request(`/jobs/${encodeURIComponent(jobId)}/claims/${encodeURIComponent(claim.id)}/semantic-review`,{method:'POST',body:JSON.stringify({semantic_status:status,reviewer:reviewer.trim(),rationale:rationale.trim(),reviewed_evidence:true})});await onRefresh();}
    catch(err){setError(err.message);}finally{setBusy('');}
  }
  return <details className="advanced semantic-review"><summary>Record a human scientific assessment</summary>
    <p>This is a self-reported human review, not an automated verifier. Read the claim and source passages before deciding. Reviews cannot yet be edited or revoked.</p>
    <form onSubmit={submit}>
      <label>Reviewer name <input value={reviewer} maxLength={80} onChange={e=>setReviewer(e.target.value)} required/></label>
      <label>Assessment <select value={status} onChange={e=>setStatus(e.target.value)}><option value="insufficient_evidence">Insufficient evidence</option><option value="supported">Supported</option><option value="partially_supported">Partially supported</option><option value="contradicted">Contradicted</option></select></label>
      <label>Reason and uncertainty <textarea value={rationale} maxLength={2000} onChange={e=>setRationale(e.target.value)} required rows="3"/></label>
      <label><input type="checkbox" checked={checked} onChange={e=>setChecked(e.target.checked)}/> I personally reviewed the claim and its source evidence.</label>
      <button className="secondary" disabled={!canSubmit}>{busy==='review'?'Saving…':'Save human assessment'}</button>
    </form>{error && <p role="alert">{error}</p>}
  </details>;
}
