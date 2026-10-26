import { useEffect, useState } from "react";
import { Button, Card, Form, Input, Space } from "antd";
import { http, errorMessage } from "../../api/client";
import { API, Evidence, Heading, LoadError } from "./shared";
import KnowledgeEvaluation from './KnowledgeEvaluation';

export function SearchWorkspace({ brain }: { brain: string }) {
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setResult(null); setError(""); }, [brain]);
  const search = async (v: any) => { setBusy(true); setError(""); try { setResult((await http.post(API + `/search/${brain}`, v)).data); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); } };
  return <><Heading title={brain === "knowledge" ? "知识证据检索" : "时空记录检索"} description="直接检索已导入资料，展开命中结果与证据。" /><div className="platform-stack"><Card><Form layout="vertical" onFinish={search}><Form.Item name="query" label="检索问题" rules={[{ required: true }]}><Input.TextArea rows={3} placeholder="输入需要查找的内容" /></Form.Item>{brain === "memory" && <Space wrap><Form.Item name="start" label="开始日期"><Input type="date" /></Form.Item><Form.Item name="end" label="结束日期"><Input type="date" /></Form.Item><Form.Item name="place" label="地点"><Input /></Form.Item><Form.Item name="person" label="人物/主体"><Input /></Form.Item></Space>}<Button type="primary" htmlType="submit" loading={busy}>开始检索</Button></Form></Card><LoadError error={error} /><Card title="检索结果"><Evidence value={result} /></Card>{brain==='knowledge'&&<KnowledgeEvaluation/>}</div></>;
}
