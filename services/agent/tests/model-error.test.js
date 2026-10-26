import test from 'node:test';
import assert from 'node:assert/strict';
import {modelRequestError} from '../src/model-error.js';

test('model_not_found gives the user a repair action without leaking raw provider content',async()=>{
  const response=new Response(JSON.stringify({error:{type:'model_not_found',message:'provider echoed credential private-key'}}),{status:404});
  const error=await modelRequestError(response,'OpenAI');
  assert.equal(error.code,'MODEL_NOT_FOUND');
  assert.match(error.message,/OpenAI/);assert.match(error.message,/模型 ID/);
  assert.doesNotMatch(error.message,/private-key/);
});

test('unknown 404 and authentication failures remain distinct',async()=>{
  const missing=await modelRequestError(new Response('<html>not found</html>',{status:404}),'chosen-model');
  assert.equal(missing.code,'MODEL_ENDPOINT_NOT_FOUND');
  const auth=await modelRequestError(new Response('{}',{status:401}),'chosen-model');
  assert.equal(auth.code,'MODEL_AUTH_FAILED');
});
