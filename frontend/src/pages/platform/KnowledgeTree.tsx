import { AimOutlined, MinusOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Descriptions, Drawer, Empty, Select, Space, Spin, Tabs, Tag, Tooltip } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { errorMessage, http } from "../../api/client";
import { API, Json, Status } from "./shared";
import ForestPrincipleDiagram from "../../components/ForestPrincipleDiagram";
import KnowledgeWiki from "./KnowledgeWiki";

type GraphNode = {
  id: string;
  type: string;
  label: string;
  rank: number;
  layer?: string;
  status?: string;
  detail?: Record<string, any>;
  metrics?: Record<string, any>;
  query_hit?: boolean;
};
type GraphEdge = { id: string; source: string; target: string; relation: string; label?: string; query_hit?: boolean };

const typeNames: Record<string, string> = {
  source: "来源文档", runtime: "WeKnora 运行层", gate: "证据门禁", concept: "中通内链",
  parent_unit: "父块", knowledge_unit: "周存知识单元", vector_trace: "向量留痕",
  cluster: "知识星团", growth: "自动分枝", cross_anchor: "跨脑锚点", generation: "生长版本",
};
const nodePalette: Record<string, { fill: string; stroke: string }> = {
  source: { fill: "#f6f8fb", stroke: "#6f8297" },
  runtime: { fill: "#e6f5f1", stroke: "#237b6c" },
  gate: { fill: "#fff5db", stroke: "#9a6b00" },
  concept: { fill: "#e9f2ff", stroke: "#2867a3" },
  parent_unit: { fill: "#f1ecff", stroke: "#7254ad" },
  knowledge_unit: { fill: "#ffffff", stroke: "#49647e" },
  vector_trace: { fill: "#eaf7e7", stroke: "#397b36" },
  cluster: { fill: "#fff0ea", stroke: "#a34d2d" },
  growth: { fill: "#fcecf2", stroke: "#a83d67" },
  cross_anchor: { fill: "#edf3f8", stroke: "#526f89" },
  generation: { fill: "#eee9ff", stroke: "#6547a6" },
};

function treeLabel(tree: any) {
  if (tree?.label) return tree.label;
  const domain = String(tree?.tree_id || "").replace("knowledge.", "");
  const names: Record<string, string> = { general: "待归类", humanities: "人文典籍", medical: "医疗", legal: "法律", finance: "财务", equipment: "设备", quality: "质量", safety: "安全" };
  return `${names[domain] || domain}知识树`;
}

function KnowledgeCanvas({ graph, scale, onNode }: { graph: any; scale: number; onNode: (node: GraphNode) => void }) {
  const layout = useMemo(() => {
    const grouped = new Map<number, GraphNode[]>();
    for (const node of graph?.nodes || []) grouped.set(node.rank, [...(grouped.get(node.rank) || []), node]);
    const positions = new Map<string, { x: number; y: number }>();
    let maxRows = 1;
    for (const [rank, nodes] of grouped) {
      maxRows = Math.max(maxRows, nodes.length);
      nodes.forEach((node, index) => positions.set(node.id, { x: 34 + rank * 225, y: 44 + index * 76 }));
    }
    return { positions, width: 34 + 9 * 225 + 200, height: Math.max(650, 80 + maxRows * 76) };
  }, [graph]);
  if (!graph?.nodes?.length) return <Empty description="当前筛选下没有可显示的真实节点" />;
  return <div className="knowledge-canvas-scroll" role="region" aria-label="知识脑独立拓扑画布">
    <svg className="knowledge-topology-canvas" width={layout.width * scale} height={layout.height * scale} viewBox={`0 0 ${layout.width} ${layout.height}`} aria-label="知识智慧树中通周存双链结构">
      <defs><marker id="knowledge-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#6d7f91" /></marker></defs>
      <g className="knowledge-canvas-edges">{(graph.edges || []).map((edge: GraphEdge) => {
        const source = layout.positions.get(edge.source); const target = layout.positions.get(edge.target);
        if (!source || !target) return null;
        const x1 = source.x + 174; const y1 = source.y + 25; const x2 = target.x; const y2 = target.y + 25;
        return <g key={edge.id}><path d={`M ${x1} ${y1} C ${x1 + 35} ${y1}, ${x2 - 35} ${y2}, ${x2} ${y2}`} fill="none" stroke={edge.query_hit ? "#c77800" : "#6d7f91"} strokeWidth={edge.query_hit ? "3" : "1.4"} markerEnd="url(#knowledge-arrow)" />{edge.label && Math.abs(y1 - y2) < 70 && <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 5} textAnchor="middle" fill="#44586d" fontSize="10">{edge.label}</text>}</g>;
      })}</g>
      <g className="knowledge-canvas-nodes">{(graph.nodes || []).map((node: GraphNode) => {
        const position = layout.positions.get(node.id)!; const palette = nodePalette[node.type] || nodePalette.knowledge_unit;
        return <g key={node.id} transform={`translate(${position.x} ${position.y})`} className="knowledge-canvas-node" role="button" tabIndex={0} onClick={() => onNode(node)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") onNode(node); }}>
          <rect width="174" height="52" rx="5" fill={node.query_hit ? "#fff3bf" : palette.fill} stroke={node.query_hit ? "#c77800" : palette.stroke} strokeWidth={node.query_hit ? "3" : "1.5"} />
          <text x="10" y="18" fill="#16283b" fontSize="11" fontWeight="700">{typeNames[node.type] || node.type}</text>
          <text x="10" y="37" fill="#263d53" fontSize="11">{node.label.length > 23 ? node.label.slice(0, 22) + "…" : node.label}</text>
          {node.query_hit && <circle cx="162" cy="12" r="5" fill="#c77800"><title>本次 Agent 命中</title></circle>}
        </g>;
      })}</g>
    </svg>
  </div>;
}

export default function KnowledgeTree({ value }: { value: any }) {
  const [params, setParams] = useSearchParams();
  const [graph, setGraph] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [generation, setGeneration] = useState<number | undefined>();
  const [types, setTypes] = useState<string[]>([]);
  const [scale, setScale] = useState(.85);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const trees = value?.trees || [];
  const treeId = params.get("tree") || trees[0]?.tree_id;
  const runId = params.get("run_id") || "";

  const load = useCallback(async () => {
    if (!treeId) { setGraph(null); return; }
    setLoading(true);
    try {
      const response = await http.get(API + `/topology/knowledge/trees/${encodeURIComponent(treeId)}`, { params: {
        cursor, limit: 160, generation, node_types: types.join(","), run_id: runId || undefined,
      } });
      setGraph(response.data); setError("");
    } catch (cause) { setError(errorMessage(cause)); setGraph(null); }
    finally { setLoading(false); }
  }, [treeId, cursor, generation, types, runId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setCursor(0); setGeneration(undefined); setSelected(null); }, [treeId]);

  if (!value) return <Empty description="加载知识智慧树后显示实际结构" />;
  if (value.status === "rebuilding") return <div className="platform-stack"><Alert type="warning" showIcon message="正在重建知识智慧树" description="原件和索引已持久化；概念路径、星团与生长版本重建完成后自动显示，页面不会伪造节点。" /><Card title="已确认的持久化资料">{(value.durable_documents || []).map((item: any) => <p key={item.document_id}>{item.title} · {item.unit_count} 个知识单元</p>)}</Card></div>;
  if (!trees.length) return <Empty description="尚未导入可生成知识智慧树的资料" />;

  const generationOptions = (graph?.generation_history || []).map((item: any) => ({ value: item.generation, label: `G${item.generation} · ${item.cause?.reason || "生长版本"}` }));
  const activeTree = trees.find((item: any) => item.tree_id === treeId) || trees[0];
  return <div className="knowledge-topology-workspace">
    <Alert type="info" showIcon message="知识脑独立拓扑 · 中通周存双链结构" description="WeKnora 已嵌入知识脑的解析、父子分块、混合索引与重排运行层；概念内链、周存、DNA 外链、星团、生长版本及跨脑公共锚点仍由知识脑独立管理。" />
    {value.workspace_status === "warming" && <Alert type="warning" showIcon message="查询工作区恢复中" description="当前显示持久化快照；查询工作区恢复完成后可查看节点详情和最新生长版本。" />}
    {value.classification_status === "rebuilding" && <Alert type="warning" showIcon message="文档主题分类更新中" description="当前为更新前的快照；完成后自动显示按文档主领域整理的知识树。" />}
    {value.classification_status === "failed" && <Alert type="error" showIcon message="知识分类更新未完成" description={value.classification_error} />}
    {value.classification_version && <Alert type="info" message="按文档主领域组织知识树，章节与概念构成树内分枝" description={<span>跨领域词保留为关联信息；依据不足的资料显示为待归类。可在 <Link to="/jobs?brain=knowledge" style={{ display: "inline-block" }}>导入任务</Link> 查看每份文档的分类依据或进行纠偏。</span>} />}
    <div className="knowledge-forest-strip" aria-label="知识森林">{trees.map((tree: any) => <button key={tree.tree_id} className={tree.tree_id === treeId ? "active" : ""} onClick={() => { const next = new URLSearchParams(params); next.set("brain", "knowledge"); next.set("tree", tree.tree_id); next.delete("run_id"); setParams(next); }}><strong>{treeLabel(tree)}</strong><span>{tree.documents} 份资料 · {tree.unit_count} 个周存单元</span><small>G{tree.generation ?? 0} · {tree.cluster_count} 个星团 · {tree.growth_branches.length} 个分枝</small></button>)}</div>
    <Tabs defaultActiveKey="principle" items={[{ key: "principle", label: "知识智慧树原理", children: <ForestPrincipleDiagram brain="knowledge" trees={trees} activeTreeId={treeId} onTreeChange={id => { const next = new URLSearchParams(params); next.set("brain", "knowledge"); next.set("tree", id); next.delete("run_id"); setParams(next); }} title="知识智慧树 · 统一森林结构总览" description="从底部基座向上生长：蓝色内链、绿色周存、金色外链组成同一管状主干，真实分枝向两侧伸展，顶部汇聚证据输出。" /> }, { key: "relation", label: `${treeLabel(activeTree)}关系`, children: <Card className="knowledge-topology-card" title={<Space><span>{treeLabel(activeTree)}</span>{runId && <Tag color="gold"><AimOutlined /> Agent 命中路径</Tag>}</Space>} extra={<Space wrap>
      <Select aria-label="选择生长版本" allowClear placeholder="当前版本" style={{ width: 170 }} value={generation} options={generationOptions} onChange={value => { setGeneration(value); setCursor(0); }} />
      <Select aria-label="筛选节点类型" mode="multiple" maxTagCount={1} placeholder="全部节点" style={{ minWidth: 180 }} value={types} options={Object.entries(typeNames).map(([value, label]) => ({ value, label }))} onChange={value => { setTypes(value); setCursor(0); }} />
      <Tooltip title="缩小"><Button icon={<MinusOutlined />} aria-label="缩小" onClick={() => setScale(value => Math.max(.45, value - .1))} /></Tooltip>
      <Tooltip title="放大"><Button icon={<PlusOutlined />} aria-label="放大" onClick={() => setScale(value => Math.min(1.5, value + .1))} /></Tooltip>
      <Tooltip title="适合画布"><Button icon={<AimOutlined />} aria-label="适合画布" onClick={() => setScale(.85)} /></Tooltip>
      <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
    </Space>}>
      <div className="knowledge-architecture-path" aria-label="知识脑结构层次"><span>资料来源</span><i>→</i><span>WeKnora 运行层</span><i>→</i><span>证据门禁</span><i>→</i><span>中通内链</span><i>→</i><span>中通周存</span><i>→</i><span>星团与分枝</span><i>→</i><span>DNA 外链 / 生长版本</span></div>
      <div className="knowledge-node-legend">{Object.entries(graph?.counts || {}).map(([type, count]) => <Tag key={type} style={{ borderColor: nodePalette[type]?.stroke, color: "#263d53", background: nodePalette[type]?.fill }}>{typeNames[type] || type} {String(count)}</Tag>)}</div>
      {error && <Alert type="error" showIcon message={error} />}
      {loading ? <Spin className="knowledge-canvas-loading" size="large" /> : <KnowledgeCanvas graph={graph} scale={scale} onNode={setSelected} />}
      {graph?.page && <div className="knowledge-canvas-pagination"><span>真实节点 {graph.page.cursor + 1}-{Math.min(graph.page.cursor + graph.nodes.length, graph.page.total)} / {graph.page.total}</span><Space><Button disabled={graph.page.cursor === 0} onClick={() => setCursor(Math.max(0, cursor - graph.page.limit))}>上一页</Button><Button disabled={graph.page.next_cursor == null} onClick={() => setCursor(graph.page.next_cursor)}>下一页</Button></Space></div>}
    </Card> }, { key: "wiki", label: "知识 Wiki 与图谱", children: <KnowledgeWiki treeId={treeId} treeLabel={treeLabel(activeTree)} /> }]} />
    <Drawer title={selected ? `${typeNames[selected.type] || selected.type} · ${selected.label}` : "节点详情"} width={520} open={!!selected} onClose={() => setSelected(null)}>{selected && <div className="platform-stack"><Descriptions column={1} bordered size="small" items={[
      { key: "id", label: "节点标识", children: selected.id },
      { key: "layer", label: "结构层", children: selected.layer || "-" },
      { key: "status", label: "状态", children: <Status value={selected.status || "active"} /> },
      { key: "hit", label: "本次查询", children: selected.query_hit ? <Tag color="gold">命中</Tag> : "未命中" },
    ]} /><Card size="small" title="真实计数"><Json value={selected.metrics || {}} /></Card><Card size="small" title="来源与定位"><Json value={selected.detail || {}} /></Card></div>}</Drawer>
  </div>;
}
