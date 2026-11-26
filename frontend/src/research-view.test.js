import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { PaperSummary } from './research-view.js';
import { displayAuthors, documentActions, documentLabel, request } from './api.js';

test('renders real research record safely and shows missing authors', () => {
  const html = renderToStaticMarkup(React.createElement(PaperSummary, { paper: {title: 'An <evidence> paper', authors: []} }));
  assert.match(html, /An &lt;evidence&gt; paper/);
  assert.match(html, /Authors not provided/);
  assert.equal(displayAuthors([{name:'A. Researcher'}]), 'A. Researcher');
});

test('shows local API failures without exposing response internals', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => { throw new Error('internal network details'); };
    await assert.rejects(request('/jobs'), /Unable to reach the local API/);
    globalThis.fetch = async () => ({ok:false, json:async () => ({detail:'Research job not found.'})});
    await assert.rejects(request('/jobs/missing'), /Research job not found/);
  } finally { globalThis.fetch = original; }
});

test('local connection, timeout and HTTP errors have distinct safe codes',async()=>{
  const original=globalThis.fetch;
  try {
    globalThis.fetch=async()=>{throw new Error('private network detail')};
    await assert.rejects(request('/jobs'),error=>error.code==='local_api_connection'&&!error.message.includes('private'));
    globalThis.fetch=async()=>{throw Object.assign(new Error('private'),{name:'TimeoutError'})};
    await assert.rejects(request('/jobs'),error=>error.code==='local_api_timeout'&&!error.message.includes('private'));
    globalThis.fetch=async()=>({ok:false,status:404,json:async()=>({detail:'Paper not found.'})});
    await assert.rejects(request('/jobs'),error=>error.code==='invalid_request');
    globalThis.fetch=async()=>({ok:false,status:500,json:async()=>({})});
    await assert.rejects(request('/jobs'),error=>error.code==='unexpected_server_error');
  } finally {globalThis.fetch=original;}
});

test('document states expose only permitted actions and disable during processing', () => {
  assert.deepEqual(documentActions({status:'available_for_acquisition',can_acquire:true}, ''), {acquire:true,extract:false,viewEvidence:false,disabled:false});
  assert.equal(documentActions({status:'downloaded',can_extract:true}, 'extract').disabled, true);
  assert.equal(documentActions({status:'extracted'}, '').viewEvidence, true);
  assert.equal(documentActions({status:'unsupported'}, '').acquire, false);
  assert.equal(documentLabel('failed'), 'Download or processing failed');
});
