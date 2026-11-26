export function identityLabel(status) {
  return ({human_confirmed:'Confirmed association', identity_conflict:'Identity conflict', identity_review_required:'Identity review required'})[status] || 'Identity review unavailable';
}
