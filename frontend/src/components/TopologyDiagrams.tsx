import { AimOutlined, MinusOutlined, PlusOutlined } from "@ant-design/icons";
import { Button, Card, Descriptions, Drawer, Empty, Space, Tag, Tooltip } from "antd";
import { useMemo, useState } from "react";

export type DiagramNode = {
  id: string;
  type: string;
  label: string;
  rank: number;
  status?: string;
  detail?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
};

export type DiagramEdge = {
  id: string;
  source: string;
  target: string;
  label?: string;
};

export type PrincipleStage = {
  id: string;
  label: string;
  detail: string;
  count?: string | number;
  status?: string;
};

const PALETTE: Record<string, { fill: string; stroke: string }> = {
  source: { fill: "#f6f8fb", stroke: "#6f8297" },
  runtime: { fill: "#e6f5f1", stroke: "#237b6c" },
  dataset: { fill: "#e9f2ff", stroke: "#2867a3" },
  field: { fill: "#fff5db", stroke: "#9a6b00" },
  query: { fill: "#f1ecff", stroke: "#7254ad" },
  result: { fill: "#eee9ff", stroke: "#6547a6" },
  memory: { fill: "#eaf7e7", stroke: "#397b36" },
  cluster: { fill: "#fff0ea", stroke: "#a34d2d" },
  feature: { fill: "#edf3f8", stroke: "#526f89" },
  asset: { fill: "#f5efff", stroke: "#7654ad" },
  anchor: { fill: "#edf3f8", stroke: "#526f89" },
  gate: { fill: "#fff5db", stroke: "#9a6b00" },
  trunk: { fill: "#e9f2ff", stroke: "#2867a3" },
  store: { fill: "#ffffff", stroke: "#49647e" },
  output: { fill: "#eee9ff", stroke: "#6547a6" },
  default: { fill: "#ffffff", stroke: "#49647e" },
};

const TYPE_NAMES: Record<string, string> = {
  source: "来源",
  runtime: "运行层",
  dataset: "数据集",
  field: "字段与特征",
  query: "查询路径",
  result: "事实结果",
  memory: "记忆单元",
  cluster: "时空星团",
  feature: "时间/地点特征",
  asset: "媒体资产",
  anchor: "公共锚点",
  gate: "数据契约 / 证据门禁",
  trunk: "中通内链",
  store: "中通周存",
  output: "事实结果 / 统一输出",
};

function short(value: unknown, limit = 24) {
  const text = String(value ?? "");
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

export function PrincipleDiagram({
  title,
  description,
  stages,
}: {
  title: string;
  description: string;
  stages: PrincipleStage[];
}) {
  if (!stages.length) return <Empty description="当前脑暂无可展示的原理节点" />;
  return <Card className="topology-principle-card" title={title}>
    <p className="topology-diagram-description">{description}</p>
    <div className="topology-principle-scroll" role="region" aria-label={`${title}原理图`}>
      <div className="topology-principle-flow">
        {stages.map((stage, index) => <div className="topology-principle-segment" key={stage.id}>
          <div className={`topology-principle-node ${stage.status === "degraded" ? "degraded" : ""}`}>
            <strong>{stage.label}</strong>
            {stage.count !== undefined && <Tag color="blue">{stage.count}</Tag>}
            <span>{stage.detail}</span>
            {stage.status && <small>状态：{stage.status}</small>}
          </div>
          {index < stages.length - 1 && <div className="topology-principle-arrow" aria-hidden="true">→</div>}
        </div>)}
      </div>
    </div>
  </Card>;
}

export function RelationGraph({
  title,
  description,
  nodes,
  edges,
  emptyDescription = "当前没有可追溯的真实关系",
}: {
  title: string;
  description: string;
  nodes: DiagramNode[];
  edges: DiagramEdge[];
  emptyDescription?: string;
}) {
  const [selected, setSelected] = useState<DiagramNode | null>(null);
  const [scale, setScale] = useState(0.9);
  const layout = useMemo(() => {
    const groups = new Map<number, DiagramNode[]>();
    nodes.forEach(node => groups.set(node.rank, [...(groups.get(node.rank) || []), node]));
    const positions = new Map<string, { x: number; y: number }>();
    let maxRows = 1;
    for (const [rank, items] of groups) {
      maxRows = Math.max(maxRows, items.length);
      items.forEach((node, index) => positions.set(node.id, { x: 30 + rank * 236, y: 36 + index * 88 }));
    }
    return { positions, width: Math.max(960, 70 + (Math.max(...groups.keys(), 0) + 1) * 236), height: Math.max(360, 100 + maxRows * 88) };
  }, [nodes]);
  if (!nodes.length) return <Card title={title}><p className="topology-diagram-description">{description}</p><Empty description={emptyDescription} /></Card>;
  return <Card className="topology-relation-card" title={title} extra={<Space>
    <Tooltip title="缩小"><Button icon={<MinusOutlined />} aria-label="缩小关系图" onClick={() => setScale(value => Math.max(0.55, value - 0.1))} /></Tooltip>
    <Tooltip title="放大"><Button icon={<PlusOutlined />} aria-label="放大关系图" onClick={() => setScale(value => Math.min(1.4, value + 0.1))} /></Tooltip>
    <Tooltip title="适合画布"><Button icon={<AimOutlined />} aria-label="适合关系图" onClick={() => setScale(0.9)} /></Tooltip>
  </Space>}>
    <p className="topology-diagram-description">{description}</p>
    <div className="topology-relation-scroll" role="region" aria-label={`${title}关系图`}>
      <svg className="topology-relation-svg" width={layout.width * scale} height={layout.height * scale} viewBox={`0 0 ${layout.width} ${layout.height}`}>
        <defs><marker id="topology-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#6d7f91" /></marker></defs>
        <g>{edges.map(edge => {
          const source = layout.positions.get(edge.source); const target = layout.positions.get(edge.target);
          if (!source || !target) return null;
          const x1 = source.x + 196; const y1 = source.y + 30; const x2 = target.x; const y2 = target.y + 30;
          return <g key={edge.id}><path d={`M ${x1} ${y1} C ${x1 + 36} ${y1}, ${x2 - 36} ${y2}, ${x2} ${y2}`} fill="none" stroke="#6d7f91" strokeWidth="1.5" markerEnd="url(#topology-arrow)" />{edge.label && Math.abs(y1 - y2) < 65 && <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 6} textAnchor="middle" fill="#44586d" fontSize="10">{short(edge.label, 14)}</text>}</g>;
        })}</g>
        <g>{nodes.map(node => {
          const position = layout.positions.get(node.id)!; const palette = PALETTE[node.type] || PALETTE.default;
          return <g key={node.id} transform={`translate(${position.x} ${position.y})`} className="topology-relation-node" role="button" tabIndex={0} onClick={() => setSelected(node)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") setSelected(node); }}>
            <title>{`${TYPE_NAMES[node.type] || node.type} · ${node.label}`}</title>
            <rect width="196" height="60" rx="5" fill={palette.fill} stroke={palette.stroke} strokeWidth="1.6" />
            <text x="10" y="19" fill="#16283b" fontSize="11" fontWeight="700">{TYPE_NAMES[node.type] || node.type}</text>
            <text x="10" y="39" fill="#263d53" fontSize="11">{short(node.label)}</text>
            {node.status && <text x="181" y="19" textAnchor="end" fill={node.status === "ready" || node.status === "active" ? "#237b6c" : "#9a6b00"} fontSize="9">{short(node.status, 10)}</text>}
          </g>;
        })}</g>
      </svg>
    </div>
    <Drawer title={selected ? `${TYPE_NAMES[selected.type] || selected.type} · ${selected.label}` : "节点详情"} width={500} open={!!selected} onClose={() => setSelected(null)}>
      {selected && <Descriptions column={1} bordered size="small" items={[
        { key: "id", label: "节点标识", children: selected.id },
        { key: "status", label: "状态", children: selected.status || "未声明" },
        { key: "metrics", label: "真实计数", children: <pre className="topology-json">{JSON.stringify(selected.metrics || {}, null, 2)}</pre> },
        { key: "detail", label: "来源与定位", children: <pre className="topology-json">{JSON.stringify(selected.detail || {}, null, 2)}</pre> },
      ]} />}
    </Drawer>
  </Card>;
}
