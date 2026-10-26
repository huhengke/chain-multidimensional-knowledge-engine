import { Alert, Button, Collapse, Empty, Table, Tag } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useCallback, useEffect, useState } from "react";
import { http, errorMessage } from "../../api/client";

export const API = "/api/v1/platform";
export const brainName: Record<string, string> = { orchestrator: "统一编排控制面", structured: "结构化数据推理脑", knowledge: "知识数据推理脑", memory: "时空记忆推理脑" };
export function useData(path: string, interval = 0) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    try { const r = await http.get(API + path); setData(r.data); setError(""); }
    catch (e) { setError(errorMessage(e)); }
    finally { setLoading(false); }
  }, [path]);
  useEffect(() => { void refresh(); if (interval) { const t = setInterval(() => void refresh(), interval); return () => clearInterval(t); } }, [refresh, interval]);
  return { data, error, loading, refresh };
}
export function Heading({ title, description, extra }: { title: string; description: string; extra?: React.ReactNode }) {
  return <div className="page-heading"><div><h2>{title}</h2><p>{description}</p></div>{extra}</div>;
}
export function LoadError({ error }: { error: string }) { return error ? <Alert style={{ marginBottom: 16 }} type="error" showIcon message={error} /> : null; }
export function Refresh({ onClick }: { onClick: () => void }) { return <Button icon={<ReloadOutlined />} onClick={onClick}>刷新</Button>; }
export function Status({ value }: { value: string }) {
  const names: Record<string, string> = { ready: "已就绪", succeeded: "已完成", failed: "失败", queued: "排队中", running: "处理中", pending_review: "待复核", rejected: "已拒绝", connecting: "连接中", connected: "已连接", unavailable: "不可用", stopped: "已停止", candidate: "候选", inferred: "推断", confirmed: "已确认", active: "生效", degraded: "连接不稳定" };
  names.partial = "部分完成";
  names.selected = "已选中";
  names.skipped = "未路由";
  names.no_hit = "未命中";
  names.template = "并列输出";
  return <Tag color={["ready", "succeeded", "connected", "confirmed", "active"].includes(value) ? "green" : ["failed", "unavailable", "rejected"].includes(value) ? "red" : value === "partial" ? "orange" : "blue"}>{names[value] || value}</Tag>;
}
export function Json({ value }: { value: any }) { return <pre className="evidence-json">{JSON.stringify(value, null, 2)}</pre>; }
export function Evidence({ value }: { value: any }) {
  const result = value?.result || value;
  if (!result) return <Empty description="提交问题后显示检索结果" />;
  return <div className="platform-stack">
    {result.status && <div><Status value={result.status} />{value.trace_id && <span className="status-note">追溯编号：{value.trace_id}</span>}</div>}
    {(result.warnings || []).map((w: string, i: number) => <Alert key={i} type="warning" message={w} />)}
    <Table<Record<string, any>> size="small" rowKey={(_, i) => String(i)} pagination={false} dataSource={result.claims || []} columns={[{ title: "结果", dataIndex: "field" }, { title: "内容", dataIndex: "value" }, { title: "证据编号", dataIndex: "evidence_id" }]} />
    <Collapse items={[{ key: "evidence", label: `展开证据与检索轨迹（${(result.evidence || []).length} 条引用）`, children: <Json value={value} /> }]} />
  </div>;
}
