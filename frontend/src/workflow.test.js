import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ProfileMenuContent } from './profile-menu.js';
import { createWorkspace, jobPaths, nextStageId, preferredPaperId, resolveWorkspace, shortlistSummary, stageStatus, visibleBundle } from './workflow.js';

test('new research creates one real job and rejects empty ideas', async () => {
  const calls=[];
  const job=await createWorkspace(async (url, options) => {calls.push([url,options]); return {id:'saved-job'};}, '  Research idea  ');
  assert.equal(job.id, 'saved-job'); assert.equal(calls.length, 1); assert.equal(calls[0][0], '/jobs');
  assert.deepEqual(JSON.parse(calls[0][1].body), {topic:'Research idea'});
  await assert.rejects(createWorkspace(async()=>assert.fail('unexpected request'), '  '), /Enter a research idea/);
});

test('saved workspace selection and job-specific bundles stay isolated', () => {
  const jobs=[{id:'a'},{id:'b'}];
  assert.equal(resolveWorkspace(jobs, 'b'), 'b');
  assert.equal(resolveWorkspace(jobs, 'missing'), 'a');
  assert.equal(resolveWorkspace([], 'a'), null);
  assert.equal(visibleBundle({jobId:'a', claims:['private a']}, 'b'), null);
  assert.deepEqual(jobPaths('job/b'), ['/jobs/job%2Fb','/jobs/job%2Fb/papers','/jobs/job%2Fb/evidence','/jobs/job%2Fb/claims','/jobs/job%2Fb/gaps']);
});

test('continue follows the research stages without triggering actions', () => {
  assert.equal(nextStageId('idea'), 'papers'); assert.equal(nextStageId('papers'), 'evidence');
  assert.equal(nextStageId('evidence'), 'claims'); assert.equal(nextStageId('claims'), 'gaps');
  assert.equal(nextStageId('gaps'), null);
});

test('stage states depend on persisted artifacts and explicit processing', () => {
  const details={searches:[], runs:[]};
  const empty=stageStatus({details});
  assert.equal(empty.papers,'not_started'); assert.equal(empty.evidence,'blocked');
  assert.equal(empty.claims,'blocked'); assert.equal(empty.gaps,'insufficient_evidence');
  assert.equal(stageStatus({details,busy:'discover'}).papers,'in_progress');
  assert.equal(stageStatus({details,failures:{papers:true}}).papers,'failed');
  const state=stageStatus({details,papers:[{included:true,document:{status:'extracted'}}],docs:[{claims_available:true,chunks:[{id:'c'}]}],claims:[{paper_id:'a',semantic_status:'unverified'}]});
  assert.equal(state.papers,'completed'); assert.equal(state.evidence,'completed'); assert.equal(state.claims,'completed');
  assert.equal(state.gaps,'insufficient_evidence');
  const enough=stageStatus({details,claims:[{paper_id:'a',semantic_status:'supported'},{paper_id:'b',semantic_status:'supported'}]});
  assert.equal(enough.gaps,'blocked');
});

test('shortlist handoff prioritizes saved evidence and supported PDFs', () => {
  const papers=[{id:'a',included:true,document:{status:'unavailable'}},{id:'b',included:false,document:{status:'extracted'}},{id:'c',included:true,document:{status:'available_for_acquisition',can_acquire:true}},{id:'d',included:true,document:{status:'downloaded',can_extract:true}}];
  assert.equal(preferredPaperId(papers), 'd');
  assert.equal(shortlistSummary(papers).shortlist.length, 3);
  assert.equal(shortlistSummary(papers).eligible.length, 2);
  assert.equal(shortlistSummary(papers).hasSupported, true);
  assert.equal(shortlistSummary([papers[0]]).hasSupported, false);
  assert.equal(preferredPaperId([papers[0], papers[1]]), 'b');
  assert.equal(preferredPaperId([papers[0]]), null);
});

test('partial shortlist preparation is not marked completed', () => {
  const details={searches:[],runs:[]};
  const papers=[{included:true,document:{status:'extracted'}},{included:true,document:{status:'unsupported'}}];
  const state=stageStatus({details,papers,docs:[{chunks:[{}]}]});
  assert.equal(state.evidence, 'partial');
  assert.equal(stageStatus({details,papers,busy:'prepare'}).evidence, 'in_progress');
  assert.equal(stageStatus({details,papers:[papers[1]]}).evidence, 'blocked');
  assert.equal(stageStatus({details:{searches:[],runs:[{role:'claim_extraction',status:'success',outcome:'no_claims'}]}}).claims, 'abstained');
  assert.equal(stageStatus({details:{searches:[],runs:[{role:'claim_extraction',status:'success',outcome:'partial_error'}]}}).claims, 'failed');
});

test('profile menu exposes local options and honestly disables logout', () => {
  const html=renderToStaticMarkup(React.createElement(ProfileMenuContent,{onChoose:()=>{}}));
  assert.match(html, /Profile/); assert.match(html, /Settings/); assert.match(html, /Log out/);
  assert.match(html, /disabled/); assert.match(html, /no signed-in session/);
});
