import test from 'node:test';
import assert from 'node:assert/strict';
import { submitUploadBatch } from '../src/pages/platform/upload-batch.ts';

test('17 files: continue after oversize and network failure; retry only unsubmitted files', async () => {
  const entries = Array.from({length:17}, (_, i) => ({id:String(i), file:{name:`file-${i}`, size:i === 3 ? 101 : 10}, status:'pending'}));
  const calls = [];
  const updates = [];
  const first = await submitUploadBatch(entries, 100, async file => {
    calls.push(file.name);
    if (file.name === 'file-7') throw new Error('network failed');
    return `job-${file.name}`;
  }, entry => updates.push(entry), error => error.message);
  assert.equal(first.filter(e => e.status === 'submitted').length, 15);
  assert.equal(first[3].status, 'rejected');
  assert.equal(first[7].status, 'failed');
  assert.equal(first[7].error, 'network failed');
  assert.ok(calls.includes('file-16'));
  assert.ok(!calls.includes('file-3'));
  assert.equal(updates.filter(e => e.status === 'uploading').length, 16);
  const retries = [];
  const second = await submitUploadBatch(first, 200, async file => {retries.push(file.name);return `job-${file.name}`;}, () => {}, String);
  assert.deepEqual(retries, ['file-3','file-7']);
  assert.equal(second.filter(e => e.status === 'submitted').length, 17);
  assert.equal(second[0].jobId, first[0].jobId);
});

test('exact limit is accepted, empty file is rejected', async () => {
  const result = await submitUploadBatch([
    {id:'empty',file:{size:0},status:'pending'}, {id:'exact',file:{size:100},status:'pending'},
  ], 100, async () => 'job-exact', () => {}, String);
  assert.equal(result[0].status, 'rejected');
  assert.equal(result[1].status, 'submitted');
});
