import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
test('project Agent executes the real migrated MCP process through a model tool loop',async()=>{
  const root=await mkdtemp(join(tmpdir(),'three-brain-agent-'));
  await writeFile(join(root,'installation.json'),JSON.stringify({service_token:'isolated-test-token'}));
  let rounds=0,queries=0;
  const server=http.createServer(async(req,res)=>{
    let body='';for await(const x of req)body+=x;
    res.setHeader('Content-Type','application/json');
    if(req.url==='/api/v1/engine/datasets'){
      assert.equal(req.headers['x-service-token'],'isolated-test-token');queries++;
      res.end(JSON.stringify({items:[{id:'verified_fixture',record_count:7}]}));return;
    }
    assert.equal(req.url,'/chat/completions');const input=JSON.parse(body);rounds++;
    assert.match(input.messages[0].content,/dataset_scope=all_actual/);
    const queryTool = input.tools.find(tool => tool.function.name === 'engine_query').function;
    assert.match(queryTool.description,/当前全部适用实际数据集/);
    assert.doesNotMatch(JSON.stringify(queryTool),/汽油|柴油|oil_monthly_sales/);
    if(rounds===1)res.end(JSON.stringify({choices:[{message:{role:'assistant',content:null,tool_calls:[{id:'q1',type:'function',function:{name:'engine_list_datasets',arguments:'{}'}}]}}]}));
    else {
      assert.match(input.messages.at(-1).content,/verified_fixture/);
      res.end(JSON.stringify({choices:[{message:{role:'assistant',content:'工具核对：7 条记录。'}}]}));
    }
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const url='http://127.0.0.1:'+server.address().port;
  process.env.PLATFORM_INSTALLATION_DIR=root;process.env.ENGINE_BASE_URL=url;
  try{
    const {runHarness}=await import('../src/managed-runtime.js');
    const events=[];
    const result=await runHarness({prompt:'检查数据',model:{model:'protocol-fixture',base_url:url,environment:{}},onEvent:e=>events.push(e)});
    assert.equal(result.text,'工具核对：7 条记录。');assert.equal(queries,1);assert.equal(rounds,2);
    assert.ok(events.some(e=>e.type==='tool_call'));assert.ok(events.some(e=>e.type==='tool_result'));
  }finally{await new Promise(resolve=>server.close(resolve));await rm(root,{recursive:true,force:true});}
});
