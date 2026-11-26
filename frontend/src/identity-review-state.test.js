import test from 'node:test';
import assert from 'node:assert/strict';
import { identityLabel } from './identity-review-state.js';

test('identity review shows honest confirmation, conflict and unresolved states', () => {
  assert.equal(identityLabel('human_confirmed'), 'Confirmed association');
  assert.equal(identityLabel('identity_conflict'), 'Identity conflict');
  assert.equal(identityLabel('identity_review_required'), 'Identity review required');
  assert.equal(identityLabel(null), 'Identity review unavailable');
});
