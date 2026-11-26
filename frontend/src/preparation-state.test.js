import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {preparationStatus, preparationSummary} from './preparation-state.js';
import {PreparationRow} from './preparation-view.js';

test('preparation counts distinguish saved evidence, processable links and unsupported PDFs',()=>{
  const papers=[{document:{status:'extracted'}},{document:{status:'downloaded',can_extract:true}},
    {document:{status:'available_for_acquisition',source:'arXiv repository',can_acquire:true}},
    {open_access:true,document:{status:'unsupported'}},{document:{status:'unavailable'}},{}];
  assert.deepEqual(preparationSummary(papers),{ready:1,processing:2,needs_pdf:2,unknown:1});
  assert.match(preparationStatus(papers[2]).availability,/Eligible arXiv/);
  assert.equal(preparationStatus(papers[3]).availability,'OA reported; PDF unsupported');
});

test('failed PDF processing and missing backend state stay explicit',()=>{
  assert.equal(preparationStatus({document:{status:'failed',can_extract:true}}).processing,'Processing failed');
  assert.equal(preparationStatus({}).group,'unknown');
});

test('compact preparation row renders title, year, access, processing and inspect control',()=>{
  const html=renderToStaticMarkup(React.createElement(PreparationRow,{paper:{title:'Paper fixture',year:2022,document:{status:'downloaded'}},onInspect:()=>{}}));
  for(const text of ['Paper fixture','2022','Saved PDF','Needs extraction','Inspect details']) assert.match(html,new RegExp(text));
  assert.doesNotMatch(html,/Authors|abstract/);
});
