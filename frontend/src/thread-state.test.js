import test from 'node:test';
import assert from 'node:assert/strict';
import {threadState, sourceForClaim} from './thread-state.js';

test('next action follows saved state without running work', () => {
  assert.equal(threadState(null).action.kind, 'start');
  const details={searches:[],runs:[]};
  assert.equal(threadState({details}).action.kind, 'discover');
  const papers=[{id:'p',included:true,document:{status:'available_for_acquisition',can_acquire:true}}];
  assert.equal(threadState({details,papers}).action.kind, 'prepare');
  assert.equal(threadState({details,papers},{needsRefresh:true}).action.kind, 'refresh');
  const docs=[{paper_id:'p',claims_available:true,chunks:[{id:'c',page:1}]}];
  const prepared=[{id:'p',included:true,document:{status:'extracted'}}];
  assert.equal(threadState({details,papers:prepared,docs}).action.kind, 'inspect_evidence');
  assert.equal(threadState({details,papers:prepared,docs,claims:[{id:'x',semantic_status:'unverified'}]}).action.kind, 'review_claims');
});

test('unavailable PDFs and failed search offer concrete navigation', () => {
  const details={searches:[{failed:false}],runs:[]};
  const state=threadState({details,papers:[{id:'p',included:true,document:{status:'unsupported'}}]});
  assert.equal(state.action.kind, 'review_options'); assert.equal(state.states.fulltext, 'blocked');
  assert.equal(threadState({details:{searches:[{failed:true}]}}).states.papers,'failed');
  assert.equal(threadState({details:{searches:[{failed:false}]}}).action.kind,'new');
  assert.equal(threadState({details,papers:[{included:true}]}).action.kind,'review_options');
  const alternatives=threadState({details,papers:[{included:true,document:{status:'unsupported'}},{id:'other',included:false,document:{can_acquire:true}}]});
  assert.equal(alternatives.action.kind,'review_alternative'); assert.equal(alternatives.alternatives[0].id,'other');
});

test('saved claim source is resolved only within current documents', () => {
  const claim={evidence:[{chunk_id:'c',quotation:'exact'}]};
  assert.equal(sourceForClaim(claim,[{paper_id:'a',chunks:[{id:'c'}]}]).quotation,'exact');
  assert.equal(sourceForClaim(claim,[{paper_id:'b',chunks:[{id:'other'}]}]),null);
});

test('failed processing and identity-gated evidence are not treated as completed analysis', () => {
  const details={searches:[{failed:false}],runs:[]};
  const failed=threadState({details,papers:[{included:true,document:{status:'failed',can_acquire:true}}]});
  assert.equal(failed.states.fulltext,'failed');
  const docs=[{claims_available:false,origin:'user_upload_unverified',chunks:[{id:'c'}]}];
  const ready=threadState({details,papers:[{included:true,document:{status:'extracted'}}],docs});
  assert.equal(ready.states.evidence,'completed');assert.equal(ready.states.findings,'blocked');
  assert.equal(ready.action.kind,'inspect_evidence');assert.match(ready.action.reason,/identity review/);
});

test('two human-supported papers unlock only the explicit provisional-gap action', () => {
  const details={searches:[{failed:false}],runs:[]};
  const papers=[{id:'a',included:true,document:{status:'extracted'}},{id:'b',included:true,document:{status:'extracted'}}];
  const docs=[{paper_id:'a',claims_available:true,chunks:[{id:'ca'}]},{paper_id:'b',claims_available:true,chunks:[{id:'cb'}]}];
  const state=threadState({details,papers,docs,claims:[{paper_id:'a',semantic_status:'supported'},{paper_id:'b',semantic_status:'supported'}]});
  assert.equal(state.states.gaps,'not_started');
  assert.equal(state.action.kind,'detect_gaps');
  assert.equal(state.supportedPapers.size,2);
});

test('a persisted failure directs the user to the paper, not an automatic retry',()=>{
  const details={searches:[{failed:false}],runs:[]};
  const paper={id:'failed-paper',included:true,document:{status:'failed',can_acquire:true,message:'Source connection failed.',attempt:{outcome:'failed',code:'network_error',retryable:true}}};
  const state=threadState({details,papers:[paper]});
  assert.equal(state.action.kind,'review_failure');assert.equal(state.action.paperId,'failed-paper');
  assert.equal(state.eligible.length,0);assert.match(state.action.reason,/manual/);
});
