import { ClockCircleOutlined, LoadingOutlined } from "@ant-design/icons";
import { Alert, Button, Empty, Select, Space, Tag, Timeline } from "antd";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Status, brainName } from "./shared";

const duration = (ms = 0) => `${(Math.max(0, ms) / 1000).toFixed(1)} 秒`;
const specialistPath: Record<string, string> = {
  structured: "/structured/agent",
  knowledge: "/knowledge/agent",
  memory: "/memory/agent",
};

export default function UnifiedRunPanel({ run, runOptions, onSelect }: {
  run: any;
  runOptions: { value: string; label: string }[];
  onSelect: (id: string) => void;
}) {
  const [clock, setClock] = useState(Date.now());
  useEffect(() => {
    if (run?.status !== "running") return;
    const timer = setInterval(() => setClock(Date.now()), 500);
    return () => clearInterval(timer);
  }, [run?.id, run?.status]);
  const elapsed = run?.status === "running" ? clock - run.started_epoch * 1000 : run?.elapsed_ms;
  const brains = ["structured", "knowledge", "memory"];

  return <aside className="cognitive-run-panel unified-run-panel" aria-label="3+1 运行步骤与耗时">
    <div className="cognitive-run-heading"><strong><ClockCircleOutlined /> 3+1 运行轨迹</strong></div>
    {runOptions.length > 0 && <Select aria-label="选择运行记录" style={{ width: "100%", marginBottom: 16 }} value={run?.id} options={runOptions} onChange={onSelect} />}
    {!run ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="发送问题后显示星团、三脑和合一过程" /> : <>
      <Space wrap><Status value={run.status} /><strong>{duration(elapsed)}</strong></Space>
      <p className="status-note">路由：{run.route_mode === "scoped" ? "指定脑" : "自动"} · 档位：{run.answer_mode === "fast" ? "快速回答" : "深度分析"}<br />运行编号：{run.id}</p>
      <Timeline items={(run.steps || []).map((step: any) => ({
        color: step.status === "failed" ? "red" : ["succeeded", "no_hit"].includes(step.status) ? "green" : "blue",
        dot: step.status === "running" ? <LoadingOutlined /> : undefined,
        children: <div className="cognitive-run-step"><strong>{step.title}</strong><small>{duration(step.duration_ms)}</small><p>{step.detail}</p></div>,
      }))} />

      <section className="unified-cluster-run">
        <div><strong>+1 记忆星团</strong><Status value={run.cluster_run?.status || "queued"} /></div>
        <p>{run.cluster_run?.hit_count ?? 0} 个可追溯线索；不替代目标脑核验。</p>
      </section>

      <div className="unified-brain-runs">
        {brains.map(brain => {
          const item = run.brain_runs?.[brain] || { status: "queued" };
          return <section key={brain} className={`unified-brain-run ${item.status}`}>
            <div><strong>{brainName[brain]}</strong><Status value={item.status} /></div>
            <p>{item.route_reason || "等待路由"}</p>
            <small>{item.evidence_count ?? 0} 条证据 · {duration(item.duration_ms)}</small>
            {item.retrieval_duration_ms != null && <small className="status-note">检索 {duration(item.retrieval_duration_ms)} · 生成 {duration(item.model_duration_ms)} · 首字 {duration(item.ttft_ms ?? item.model_duration_ms)}</small>}
            {item.answer_basis === "general" && <small className="status-note">本次没有可引用证据，答案来自通识补充（非本项目资料）。</small>}
            {brain === "knowledge" && item.evidence?.temporal && <small className="status-note">时间路由：{item.evidence.temporal.mode} · 候选交集 {item.evidence.temporal.intersection_candidate_count ?? "—"} · 规划 {duration((item.evidence.temporal.total_planning_ms || 0))}</small>}
            {item.error && <Alert type="error" showIcon message={item.error.message} />}
            <Link to={specialistPath[brain]}><Button size="small" type="link">专脑管理与诊断</Button></Link>
            {brain === "knowledge" && item.tree_ids?.[0] && <Link to={`/cognition?brain=knowledge&tree=${encodeURIComponent(item.tree_ids[0])}&run_id=${encodeURIComponent(run.id)}`}><Button size="small" type="link">查看命中拓扑</Button></Link>}
          </section>;
        })}
      </div>
      {run.merge_run && <div className="unified-merge-run"><strong>统一证据合一</strong><Tag>{run.merge_run.status}</Tag><p>{run.merge_run.model ? `编排模型：${run.merge_run.model}` : "未配置编排模型，保持各脑原结论并列。"}</p></div>}
      {run.error && <Alert showIcon type="error" message={run.error.message} description={run.error.code} />}
    </>}
  </aside>;
}
