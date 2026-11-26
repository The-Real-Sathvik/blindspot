export function claimResultText(result) {
  if (!result) return '';
  if (result.status === 'claims_extracted') return `Extraction complete: ${result.accepted_count} accepted, ${result.rejected_count} rejected${result.reused ? ' (previous result reused)' : ''}.`;
  if (result.status === 'no_claims') return 'The model abstained: no claims were extracted.';
  if (result.status === 'partial_error') return `Partial result: ${result.accepted_count} accepted, ${result.rejected_count} rejected. ${(result.rejection_codes || []).join(', ')}`;
  if (result.http_status === 503 || result.error_category === 'CloudInferenceError') return 'The AI provider is unavailable. No research findings were saved for this run.';
  const errors = {CloudQuotaError:'The provider quota or local budget was reached.', CloudTimeoutError:'The provider timed out.', CloudInputLimitError:'This chunk exceeds the configured request budget.', authentication_invalid:'The provider rejected authentication.', permission_denied:'The provider denied permission.', output_truncated:'The provider output reached its limit.', CloudInferenceError:'The provider is unavailable.'};
  return `Extraction failed: ${errors[result.error_category] || 'The provider response could not be used.'} No research findings were saved for this run.`;
}

export const maySendClaimRequest = (consent, busy, available) => consent === true && !busy && available === true;
