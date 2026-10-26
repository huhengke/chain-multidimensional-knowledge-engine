import { useState } from 'react';
import { Button, Card, Form, Input, InputNumber, Select, Table, message } from 'antd';
import { http, errorMessage } from '../../api/client';
import { API, useData, LoadError } from './shared';
export default function KnowledgeEvaluation(){
  const assets=useData('/assets'),runs=useData('/knowledge/evaluations');const [busy,setBusy]=useState(false);
  const evaluate=async(v:any)=>{setBusy(true);try{await http.post(API+'/knowledge/evaluations',v);await runs.refresh();message.success('评测完成');}catch(e){message.error(errorMessage(e));}finally{setBusy(false);}};
  return <Card title="知识检索评测"><p>选择你确认包含答案的资料作为标准答案，比较召回率、排名与耗时。该评测按文档计算。</p><LoadError error={runs.error||assets.error}/><Form layout="vertical" onFinish={evaluate} initialValues={{k:10}}>
    <Form.Item name="query" label="验收问题" rules={[{required:true}]}><Input.TextArea rows={2}/></Form.Item>
    <Form.Item name="expected_asset_ids" label="应当命中的资料" rules={[{required:true}]}><Select mode="multiple" options={(assets.data?.items||[]).filter((a:any)=>a.brain==='knowledge'&&a.document_id).map((a:any)=>({value:a.id,label:a.title}))}/></Form.Item>
    <Form.Item name="k" label="前 K 个文档"><InputNumber min={1} max={100}/></Form.Item><Button type="primary" htmlType="submit" loading={busy}>运行评测</Button>
  </Form><Table<Record<string,any>> style={{marginTop:20}} rowKey="id" dataSource={runs.data?.items||[]} columns={[{title:'问题',dataIndex:'query'},{title:'Recall@K',render:(_,r)=>r.metrics.recall},{title:'MRR',render:(_,r)=>r.metrics.mrr},{title:'NDCG@K',render:(_,r)=>r.metrics.ndcg},{title:'耗时 ms',dataIndex:'duration_ms'},{title:'运行版本',dataIndex:'runtime_version'}]}/></Card>;
}
