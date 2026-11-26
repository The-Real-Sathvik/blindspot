import test from 'node:test';
import assert from 'node:assert/strict';
import {maySubmitHumanReview} from './semantic-review-state.js';

test('scientific assessment requires explicit human confirmation and provenance',()=>{
  const body={checked:false,reviewer:'Reviewer',rationale:'Reviewed the source.',busy:'',status:'supported'};
  assert.equal(maySubmitHumanReview(body),false);
  assert.equal(maySubmitHumanReview({...body,checked:true,reviewer:' '}),false);
  assert.equal(maySubmitHumanReview({...body,checked:true,rationale:' '}),false);
  assert.equal(maySubmitHumanReview({...body,checked:true,status:'unverified'}),false);
  assert.equal(maySubmitHumanReview({...body,checked:true,busy:'review'}),false);
  assert.equal(maySubmitHumanReview({...body,checked:true}),true);
});
