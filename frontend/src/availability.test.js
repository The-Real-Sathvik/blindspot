import test from 'node:test';
import assert from 'node:assert/strict';
import { alternativePreparationSummary, availabilitySummary, eligibleAlternatives } from './availability.js';

test('OA status, eligibility and evidence remain separate from relevance', () => {
  assert.deepEqual(availabilitySummary({score: 0.9, open_access: true, document: {status: 'unsupported'}}),
    {relevance: '0.9', openAccess: 'open access reported', eligibility: 'not currently eligible'});
  assert.equal(availabilitySummary({document: {status: 'extracted'}}).eligibility, 'evidence extracted');
  assert.equal(availabilitySummary({}).openAccess, 'not provided');
});

test('eligible alternatives exclude shortlisted and ineligible papers without changing their selection', () => {
  const papers = [{id:'a', included:true, document:{can_acquire:true}}, {id:'b', included:false, document:{can_extract:true}}, {id:'c', included:false, document:{status:'unsupported'}}];
  assert.deepEqual(eligibleAlternatives(papers).map(p => p.id), ['b']);
  assert.equal(papers[1].included, false);
});

test('alternative preparation is reported separately from shortlist membership', () => {
  const papers = [
    {included:true, document:{status:'unsupported'}},
    {included:false, document:{status:'extracted'}},
    {included:false, document:{status:'failed'}},
    {included:false, document:{status:'available_for_acquisition', can_acquire:true}},
  ];
  assert.deepEqual(alternativePreparationSummary(papers), {evidenceReady:1, failed:1});
  assert.equal(eligibleAlternatives(papers).length, 2);
});
