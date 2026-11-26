import React from 'react';

export function ClaimCard({ claim }) {
  const h = React.createElement;
  return h('div', {className:'artifact'},
    h('span', {className:'source-tag'}, claim.category.replaceAll('_', ' ').toUpperCase()),
    h('h3', null, claim.statement), h('p', null, claim.paper_title),
    h('div', {className:'badges'}, h('span', {className:'tag'}, `Semantic support: ${claim.semantic_status.replaceAll('_', ' ')}`)),
    ...claim.evidence.map(e => h('blockquote', {key:e.id}, `“${e.quotation}”`,
      h('footer', null, `Page ${e.page} · Quote: ${e.quotation_status} · Location: ${e.location_status} · ${e.chunk_id}`))),
    claim.uncertainty ? h('p', null, `Uncertainty: ${claim.uncertainty}`) : null);
}
