import React, { useState } from 'react';
import { request } from './api';
import { claimResultText, maySendClaimRequest } from './claim-state.js';

export function ClaimControls({ jobId, document, chunk, busy, setBusy, onRefresh, onReview }) {
  const [provider, setProvider] = useState('gemini');
  const [consent, setConsent] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const models = {gemini:'gemini-3.5-flash-lite', groq:'openai/gpt-oss-120b', openrouter:'inclusionai/ling-3.2-flash:free'};
  const model = models[provider];
  const outputLimit = provider === 'openrouter' ? 512 : 256;
  const processor = provider === 'gemini' ? 'Gemini' : provider === 'groq' ? 'Groq' : 'OpenRouter and its selected third-party model host';
  async function extract() {
    if (!maySendClaimRequest(consent, busy, document.claims_available)) return;
    setBusy('claims'); setError(''); setResult(null);
    try {
      const response = await request(`/jobs/${encodeURIComponent(jobId)}/claims/extract`, {method:'POST', signal:AbortSignal.timeout(60000), body:JSON.stringify({chunk_id:chunk.id, provider, cloud_consent:true})});
      setResult(response); await onRefresh();
    } catch (err) { setError(err.message); }
    finally { setConsent(false); setBusy(''); }
  }
  if (!document.claims_available) return <p className="claim-warning">Claim extraction is unavailable. A processed local PDF outside the held-out set is required; uploaded PDFs also require a confirmed association without metadata conflicts.</p>;
  return <div className="claim-controls"><h4>ANALYZE THIS PASSAGE</h4><p>{document.paper_title} · PDF page {chunk.page}</p><details><summary>Advanced source details</summary><p>Chunk <code>{chunk.id}</code></p></details>
    <label className="claim-field">Provider <select value={provider} onChange={event=>setProvider(event.target.value)} disabled={!!busy}><option value="gemini">Gemini</option><option value="groq">Groq (prior request returned 403)</option><option value="openrouter">OpenRouter — Ling 3.2 Flash (free)</option></select></label>
    <p>Model: <strong>{model}</strong> · one request maximum · {outputLimit} output tokens</p>
    {provider === 'openrouter' && <p className="claim-warning">OpenRouter sends this request to a third-party cloud model host. Blindspot verifies the pinned model is currently listed at zero prompt and completion price before sending; it never uses paid routing or fallback.</p>}
    <label className="consent"><input type="checkbox" checked={consent} onChange={event=>setConsent(event.target.checked)} disabled={!!busy}/> I consent to sending this evidence chunk to {processor} for cloud processing.</label>
    <p>Free-tier confirmation and local budgets are required; these do not guarantee billing settings.</p>
    <button className="primary" disabled={!maySendClaimRequest(consent, busy, document.claims_available)} onClick={extract}>{busy === 'claims' ? 'Extracting claims…' : 'Extract claims'}</button>
    {result && <div className="claim-result" role="status"><p>{claimResultText(result)}</p><small>Evidence matching is not semantic verification.</small><details><summary>Advanced run details</summary><code>{result.model_run_id}</code></details>{result.accepted_count > 0 && <div><button className="secondary" onClick={onReview}>Review claims and evidence</button></div>}</div>}
    {error && <p className="claim-error" role="alert">{error}</p>}
  </div>;
}
