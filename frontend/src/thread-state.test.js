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

test('verified alternative evidence completes preparation without rewriting the shortlist', () => {
  const details={searches:[{failed:false}],runs:[],status:'completed'};
  const papers=[
    {id:'short',included:true,document:{status:'unsupported'}},
    {id:'alternative',included:false,document:{status:'extracted'}},
  ];
  const docs=[{paper_id:'alternative',claims_available:true,chunks:[{id:'chunk'}]}];
  const state=threadState({details,papers,docs});
  assert.equal(state.states.fulltext,'completed_with_alternatives');
  assert.equal(papers[0].included,true);
  assert.equal(papers[1].included,false);
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

test('authoritative workflow projection keeps a legacy job finished but limited', () => {
  const workflow={analysis_paper_ids:['q'],candidate_count:27,evidence_chunk_count:27,claim_count:1,assessment_count:0,supported_paper_count:0,provisional_gap_count:0,
    stages:{preparation:{processing:'finished',coverage:'sufficient',detail:'27 candidates discovered; 1 verified analysis-ready papers finalized.'},evidence:{processing:'finished',coverage:'sufficient',detail:'27 page-linked evidence chunks across 1 finalized papers.'},findings:{processing:'finished',coverage:'limited',detail:'1 source-grounded findings; 0 automatically assessed.'},gaps:{processing:'blocked',coverage:'limited',detail:'Automated semantic assessment has not run for the saved findings (legacy run); no gap analysis was attempted.'}}};
  const state=threadState({details:{workflow,searches:[{failed:false}],runs:[]},papers:[{id:'q',analysis_selected:true,document:{status:'extracted'}}],docs:[{paper_id:'q',chunks:[{id:'c'}]}],claims:[{paper_id:'q'}]});
  assert.equal(state.states.research,'finished_sufficient');
  assert.equal(state.states.evidence,'finished_sufficient');
  assert.equal(state.states.findings,'finished_limited');
  assert.equal(state.states.gaps,'blocked');
  assert.match(state.action.reason,/legacy run/);
});

test('authoritative Agent 2 failures remain visible and are not represented as a successful no-gap result', () => {
  const reason="Agent 2's response was truncated at its output limit; no provisional gap was saved. No automatic retry was made.";
  const workflow={analysis_paper_ids:['a','b'],supported_paper_count:2,provisional_gap_count:0,
    stages:{preparation:{processing:'finished',coverage:'sufficient',detail:'Two papers finalized.'},
      evidence:{processing:'finished',coverage:'sufficient',detail:'Saved evidence.'},
      findings:{processing:'finished',coverage:'sufficient',detail:'Findings assessed.'},
      gaps:{processing:'failed',coverage:'limited',detail:reason}}};
  const state=threadState({details:{workflow,searches:[{failed:false}],runs:[{role:'gap_detection',status:'failed',error_category:'output_truncated'}]},
    papers:[{id:'a',analysis_selected:true},{id:'b',analysis_selected:true}],
    claims:[{paper_id:'a'},{paper_id:'b'}]});
  assert.equal(state.states.gaps,'failed');
  assert.equal(state.active,'gaps');
  assert.equal(state.action.reason,reason);
});
