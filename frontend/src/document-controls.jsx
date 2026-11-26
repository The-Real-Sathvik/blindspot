import React from 'react';
import { documentActions, documentLabel } from './api';

export function DocumentControls({ document, busy, onProcess, onEvidence, onUpload }) {
  if (!document) return <p>Document status was not provided by the backend. Restart the backend if the response remains incomplete.</p>;
  const actions = documentActions(document, busy);
  return <div className="document-controls"><h4>FULL TEXT &amp; EVIDENCE</h4><span className="tag">{documentLabel(document.status)}</span><p>{document.message}</p>
    {document.attempt?.outcome === 'failed' && <div className="panel-note" role="status"><strong>Previous attempt failed</strong><p>{document.message} Your saved work is unchanged.</p><p>{document.attempt.retryable === true ? 'You can retry this paper manually.' : document.attempt.retryable === false ? 'Retrying this source is not recommended. You can inspect another source or upload a PDF.' : 'Retry suitability is unknown; inspect the details before trying manually.'}</p><details><summary>Advanced failure details</summary><p>Stage: {document.attempt.stage} · Category: {document.attempt.code}{document.attempt.http_status ? ` · HTTP ${document.attempt.http_status}` : ''}</p></details></div>}
    {document.attempt?.outcome === 'started' && <p>Previous processing began but has no recorded completion. Check whether it is still running before retrying.</p>}
    {document.source && <p>Source: {document.source}</p>}
    {actions.acquire && <button className="secondary" disabled={actions.disabled} onClick={() => onProcess('acquire')}>{busy === 'acquire' ? 'Acquiring PDF…' : document.status === 'failed' ? 'Retry acquisition' : 'Acquire full text'}</button>}
    {actions.extract && <button className="primary" disabled={actions.disabled} onClick={() => onProcess('extract')}>{busy === 'extract' ? 'Extracting evidence…' : document.status === 'failed' ? 'Retry evidence extraction' : 'Extract evidence'}</button>}
    {actions.viewEvidence && onEvidence && <button className="secondary" onClick={onEvidence}>View {document.chunk_count} evidence chunks</button>}
    {!actions.extract && !actions.viewEvidence && onUpload && <div><label htmlFor="local-pdf">Upload a lawful local PDF (maximum 20 MB, 300 pages)</label><p>The file's association with this paper requires human review and is not independently verified.</p><input id="local-pdf" type="file" accept="application/pdf,.pdf" disabled={!!busy} onChange={event => { const file = event.target.files?.[0]; if (file) onUpload(file); event.target.value = ''; }}/></div>}
  </div>;
}
