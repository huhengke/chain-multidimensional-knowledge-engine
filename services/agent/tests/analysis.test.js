import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

test('migrated analysis calls the configured model and keeps only known fields in editable drafts', async()=>{
  const root=await mkdtemp(join(tmpdir(),'three-brain-analysis-'));
  await writeFile(join(root,'installation.json'),JSON.stringify({service_token:'analysis-isolated-token'}));
  let url,round=0;
  const server=http.createServer(async(req,res)=>{
    let body='';for await(const data of req)body+=data;
    res.setHeader('Content-Type','application/json');
    if(req.url==='/api/internal/structured-models'){
      assert.equal(req.headers['x-service-token'],'analysis-isolated-token');
      res.end(JSON.stringify({items:[{id:'model',model:'test-model',enabled:true,base_url:url,api_key:'provider-fixture-key'}]}));return;
    }
    assert.equal(req.url,'/chat/completions');
    assert.equal(req.headers.authorization,'Bearer provider-fixture-key');
    assert.match(JSON.parse(body).messages[0].content,/草稿/);
    const drafts=[
      {columns:[{source:'amount',role:'metric'},{source:'invented',role:'metric'}]},
      {suggestions:[{dataset:'d',fields:[{source:'amount'},{source:'invented'}]},{dataset:'invented',fields:[{source:'amount'}]}]},
      {suggestions:[{name:'season',source:'month',precision:'month',mapping:{'3,4,5':'春'}}]},
    ];
    res.end(JSON.stringify({choices:[{message:{content:JSON.stringify(drafts[round++])}}]}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  url='http://127.0.0.1:'+server.address().port;
  process.env.PLATFORM_INSTALLATION_DIR=root;process.env.PLATFORM_BASE_URL=url;
  try{
    const {analyze}=await import('../src/analysis.js');
    const fields=await analyze('fields',{columns:[{source:'amount'}]});
    assert.equal(fields.columns.length,1);assert.equal(fields.columns[0].source,'amount');
    assert.equal(fields.columns[0].status,'pending_review');
    const foundation=await analyze('foundation',{datasets:[{id:'d',columns:[{source:'amount'}]}]});
    assert.equal(foundation.suggestions.length,1);assert.equal(foundation.suggestions[0].fields.length,1);
    const rules=await analyze('rules',{idea:'按月份划分季节'});
    assert.equal(rules.status,'pending_review');assert.equal(rules.suggestions[0].status,'pending_review');
    assert.equal(round,3);
  }finally{
    await new Promise(resolve=>server.close(resolve));await rm(root,{recursive:true,force:true});
  }
});
