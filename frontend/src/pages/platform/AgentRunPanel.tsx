import { useEffect, useState } from 'react';
import { Alert, Empty, Select, Space, Tag, Timeline } from 'antd';
import { Link } from 'react-router-dom';
import { ClockCircleOutlined, LoadingOutlined } from '@ant-design/icons';
import { Status } from './shared';

const duration = (ms: number) => `${(Math.max(0, ms) / 1000).toFixed(1)} 秒`;
export default function AgentRunPanel({ run, brain, runOptions, onSelect }: {
  run: any; brain: string; runOptions: { value: string; label: string }[]; onSelect: (id: string) => void;
}) {
  const [clock, setClock] = useState(Date.now());
  useEffect(() => { if (run?.status !== 'running') return; const timer = setInterval(() => setClock(Date.now()), 500); return () => clearInterval(timer); }, [run?.id, run?.status]);
  const elapsed = run?.status === 'running' ? clock - run.started_epoch * 1000 : run?.elapsed_ms;
  return <aside className="cognitive-run-panel" aria-label="运行步骤与耗时">
    <div className="cognitive-run-heading"><strong><ClockCircleOutlined /> 运行步骤与耗时</strong></div>
    {runOptions.length > 0 && <Select aria-label="选择运行记录" style={{ width: '100%', marginBottom: 16 }} value={run?.id} options={runOptions} onChange={onSelect} />}
    {run ? <>
      <Space wrap><Status value={run.status} /><strong>{duration(elapsed || 0)}</strong></Space>
      <p className="status-note">模型：{run.model}<br />开始：{new Date(run.started_at).toLocaleTimeString()}</p>
      <Timeline items={(run.steps || []).map((step: any) => ({
        color: step.status === 'failed' ? 'red' : step.status === 'succeeded' ? 'green' : 'blue',
        dot: step.status === 'running' ? <LoadingOutlined /> : undefined,
        children: <div className="cognitive-run-step"><strong>{step.title}</strong>
          <small>{new Date(step.started_at).toLocaleTimeString()} · {step.status === 'running' ? `进行中 ${duration(clock - step.started_epoch * 1000)}` : duration(step.duration_ms || 0)}</small>
          <p>{step.detail || '正在执行，请稍候…'}</p></div>,
      }))} />
      {run.error && <Alert showIcon type="error" message={run.error.message} description={run.error.code} />}
      {run.context && <p className="status-note">回答使用 {run.context.selected_count} / {run.context.available_count} 条检索结果 · {run.context.characters} 字符</p>}
      {run.usage?.total_tokens != null && <Tag>模型计费词元：{run.usage.total_tokens}</Tag>}
      <p className="cognitive-run-id">运行编号：{run.id}</p>
    </> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="发送问题后显示实际执行步骤" />}
    {brain === 'knowledge' && <div className="cognitive-tree-note"><strong>知识智慧树</strong><p>概念路径与中通内链 → 知识单元周存 → 星团与自动分枝。WeKnora 为周存提供解析、向量检索和重排。</p><Link to="/cognition?brain=knowledge">查看知识树、节点与生长版本 →</Link></div>}
  </aside>;
}
