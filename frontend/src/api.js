export class LocalApiError extends Error {
  constructor(message, code, stage='local_api') { super(message); this.name='LocalApiError'; this.code=code; this.stage=stage; }
}
export async function request(path, options = {}) {
  let response;
  try { response = await fetch(`/api${path}`, { ...options, headers: { 'Content-Type': 'application/json', 'X-Blindspot-Request': '1', ...options.headers } }); }
  catch (error) { if (error?.name === 'TimeoutError' || error?.name === 'AbortError') throw new LocalApiError('The request timed out. Processing may still be running; refresh before retrying.','local_api_timeout'); throw new LocalApiError('Unable to reach the local API. Make sure the backend is running.','local_api_connection'); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const serverCode=response.headers?.get?.('X-Blindspot-Error-Code');
    const known=['job_not_found','paper_not_found'].includes(serverCode) ? serverCode : null;
    throw new LocalApiError(typeof data.detail === 'string' ? data.detail : 'The request could not be completed.', known || (response.status===404 || response.status===400 || response.status===422 ? 'invalid_request' : response.status>=500 ? 'unexpected_server_error' : 'local_api_error'), response.status>=500 ? 'backend' : 'request_validation');
  }
  return data;
}
export const displayAuthors = (authors) => Array.isArray(authors) && authors.length ? authors.map(a => typeof a === 'string' ? a : (a.name || a.display_name)).filter(Boolean).join(', ') || 'Authors not provided' : 'Authors not provided';
export const stateLabel = (value) => ({complete: 'Complete', available: 'Available', not_started: 'Not started', blocked: 'Unavailable', experimental: 'Experimental', pending: 'Pending', success: 'Complete', failed: 'Failed'}[value] || value || 'Unknown');
export const documentLabel = (state) => ({available_for_acquisition:'Supported PDF link available', downloaded:'Saved PDF ready to extract', extracted:'Evidence already extracted', failed:'Download or processing failed', unsupported:'Source unsupported', unavailable:'Full text unavailable'}[state] || 'Full-text status unavailable');
export const documentActions = (document, busy) => ({ acquire: !!document?.can_acquire, extract: !!document?.can_extract, viewEvidence: document?.status === 'extracted', disabled: !!busy });
