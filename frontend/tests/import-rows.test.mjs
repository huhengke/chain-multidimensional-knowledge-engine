import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeImportRows } from '../src/pages/platform/import-rows.ts';

test('same-name files keep independent tasks by asset ID', () => {
  const rows = mergeImportRows([{id:'a',title:'同名.pdf'}, {id:'b',title:'同名.pdf'}], [{id:'j',asset_id:'a',attempts:2}]);
  assert.equal(rows.length, 2);
  assert.equal(rows.find(r => r.id === 'a').job.id, 'j');
  assert.equal(rows.find(r => r.id === 'b').job, undefined);
});

test('all processing records survive consolidation, latest task selected', () => {
  const jobs = [{id:'old',asset_id:'a',created_at:'2026-10-14',status:'failed'}, {id:'new',asset_id:'a',created_at:'2026-10-15',status:'succeeded'}];
  const [row] = mergeImportRows([{id:'a',processing_status:'partial',publication_status:'published'}], jobs);
  assert.equal(row.job.id, 'new');
  assert.equal(row.jobs.length, 2);
  assert.equal(row.processing_status, 'partial');
  assert.equal(row.publication_status, 'published');
  assert.equal(jobs[0].id, 'old');
});

test('a live retry takes precedence over a stale completed asset snapshot', () => {
  const [row] = mergeImportRows([{id:'a',processing_status:'partial',publication_status:'rejected'}], [{id:'j',asset_id:'a',status:'queued'}]);
  assert.equal(row.processing_status, 'queued');
  assert.equal(row.publication_status, 'rejected');
});

test('stream assets without jobs and orphan jobs both remain visible', () => {
  const rows = mergeImportRows([{id:'frame',title:'摄像头帧',brain:'memory'}], [{id:'j',asset_id:'missing',title:'处理中.pdf',brain:'knowledge',processing_status:'running'}]);
  assert.equal(rows.length, 2);
  assert.equal(rows.find(r => r.id === 'frame').jobs.length, 0);
  assert.equal(rows.find(r => r.id === 'job:j').asset, undefined);
  assert.equal(rows.find(r => r.id === 'job:j').processing_status, 'running');
});

test('a newer asset state is retained until the task refresh catches up', () => {
  const [row] = mergeImportRows([{id:'a',processing_status:'running'}], [{id:'j',asset_id:'a',status:'failed',processing_status:'failed'}]);
  assert.equal(row.processing_status, 'running');
});
