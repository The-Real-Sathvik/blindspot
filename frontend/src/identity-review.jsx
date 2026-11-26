import React, { useEffect, useState } from 'react';
import { request } from './api';
import { identityLabel } from './identity-review-state.js';

export function IdentityReview({jobId, paperId, document, busy, setBusy, onRefresh}) {
  const [review, setReview] = useState(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState('');
  const isUpload = document?.source?.startsWith('User-uploaded PDF');
  const path = isUpload && document?.document_id ? `/jobs/${encodeURIComponent(jobId)}/papers/${encodeURIComponent(paperId)}/documents/${encodeURIComponent(document.document_id)}/identity` : null;
  useEffect(() => {
    let active = true; setReview(null); setError(''); setAcknowledged(false);
    if (path) request(path).then(value => {if (active) setReview(value);}).catch(err => {if (active) setError(err.message);});
    return () => { active = false; };
  }, [path]);
  if (!path) return null;
  async function confirm() {
    if (!acknowledged || busy || !review?.can_confirm) return;
    setBusy('identity'); setError('');
    try { const result = await request(path, {method:'POST', body:JSON.stringify({confirm_association:true, review_token:review.review_token})}); setReview(result); setAcknowledged(false); await onRefresh(); }
    catch (err) { setError(err.message); } finally { setBusy(''); }
  }
  return <section className="stage-hint" aria-label="PDF identity review"><h4>PDF association review</h4>
    {error && <p role="alert">{error}</p>}
    {!review && !error && <p>Checking document metadata…</p>}
    {review && <><strong>{identityLabel(review.status)}</strong><p>{review.message}</p>
      <p>Paper title: {review.comparison.paper_title}<br/>Paper DOI: {review.comparison.paper_doi || 'Not provided'}<br/>PDF embedded title: {review.comparison.pdf_title || 'Not provided'}<br/>PDF embedded author: {review.comparison.pdf_author || 'Not provided'}<br/>PDF metadata DOI: {review.comparison.pdf_dois.join(', ') || 'Not provided'}</p>
      <details><summary>Inspect first-page text</summary><p>{review.first_page_header || 'No readable first-page text.'}</p></details>
      <p>Metadata matching is not independent identity verification. Review the document before confirming.</p>
      {review.can_confirm && <><label><input type="checkbox" checked={acknowledged} disabled={!!busy} onChange={e => setAcknowledged(e.target.checked)}/> I reviewed the document and confirm its association with this paper.</label><div><button className="secondary" disabled={!acknowledged || !!busy} onClick={confirm}>{busy === 'identity' ? 'Saving…' : 'Confirm association'}</button></div></>}
    </>}
  </section>;
}
