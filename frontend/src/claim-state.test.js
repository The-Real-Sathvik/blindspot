import test from 'node:test';
import assert from 'node:assert/strict';
import { claimResultText, maySendClaimRequest } from './claim-state.js';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ClaimCard } from './claim-view.js';
import { readFileSync } from 'node:fs';

test('claim extraction requires explicit consent and an eligible idle chunk', () => {
  assert.equal(maySendClaimRequest(false, '', true), false);
  assert.equal(maySendClaimRequest(true, 'claims', true), false);
  assert.equal(maySendClaimRequest(true, '', false), false);
  assert.equal(maySendClaimRequest(true, '', true), true);
});

test('claim results distinguish abstention, partial errors and failures', () => {
  assert.match(claimResultText({status:'claims_extracted',accepted_count:1,rejected_count:0}), /1 accepted/);
  assert.match(claimResultText({status:'no_claims'}), /abstained/);
  assert.match(claimResultText({status:'partial_error',accepted_count:0,rejected_count:1,rejection_codes:['unknown_evidence_id']}), /unknown_evidence_id/);
  assert.match(claimResultText({status:'failed_inference',error_category:'permission_denied'}), /denied permission/);
  assert.match(claimResultText({status:'failed_inference',error_category:'provider_http_error',http_status:503}), /unavailable.*No research findings were saved/);
});

test('claim card renders exact evidence and separates semantic support', () => {
  const claim = {category:'result', statement:'Finding <42>', paper_title:'Source paper', semantic_status:'unverified', uncertainty:null,
    evidence:[{id:'e1',quotation:'Exact <text>',page:3,quotation_status:'valid',location_status:'valid',chunk_id:'chunk-1'}]};
  const html = renderToStaticMarkup(React.createElement(ClaimCard, {claim}));
  assert.match(html, /Finding &lt;42&gt;/);
  assert.match(html, /Exact &lt;text&gt;/);
  assert.match(html, /Page 3/);
  assert.match(html, /Semantic support: unverified/);
  assert.match(html, /Quote: valid/);
});

test('claim controls offer the pinned OpenRouter model', () => {
  const controls = readFileSync(new URL('./claim-controls.jsx', import.meta.url), 'utf8');
  assert.match(controls, /inclusionai\/ling-3\.2-flash:free/);
  assert.match(controls, /Ling 3\.2 Flash/);
  assert.doesNotMatch(controls, /ERNIE 5\.5/);
  assert.match(controls, /OpenRouter/);
  assert.match(controls, /never uses paid routing or fallback/);
});
