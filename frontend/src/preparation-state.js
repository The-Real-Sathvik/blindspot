// Availability describes recorded access metadata; processing describes local work.
export function preparationStatus(paper) {
  const document = paper?.document;
  if (!document) return {availability:'Status unavailable', processing:'Not reported', group:'unknown'};
  const {status, can_acquire:canAcquire, can_extract:canExtract} = document;
  if (status === 'extracted') return {availability:'Saved PDF', processing:'Evidence ready', group:'ready'};
  if (status === 'downloaded') return {availability:'Saved PDF', processing:'Needs extraction', group:'processing'};
  if (status === 'available_for_acquisition') return {availability:document.source ? `Eligible ${document.source} PDF link` : 'Eligible PDF link', processing:'Not yet acquired', group:'processing'};
  if (status === 'failed') return {availability:canExtract ? 'Saved PDF' : canAcquire ? 'Eligible PDF link' : 'No eligible PDF', processing:'Processing failed', group:canExtract||canAcquire?'processing':'needs_pdf'};
  if (status === 'unsupported') return {availability:paper.open_access === true ? 'OA reported; PDF unsupported' : 'Source unsupported', processing:'Needs a PDF', group:'needs_pdf'};
  if (status === 'unavailable') return {availability:'No supported PDF recorded', processing:'Needs a PDF', group:'needs_pdf'};
  return {availability:'Status unavailable', processing:'Not reported', group:'unknown'};
}

export function preparationSummary(papers) {
  const result={ready:0,processing:0,needs_pdf:0,unknown:0};
  for (const paper of papers) result[preparationStatus(paper).group]++;
  return result;
}
