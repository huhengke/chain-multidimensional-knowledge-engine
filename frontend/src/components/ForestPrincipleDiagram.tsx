import { AimOutlined, MinusOutlined, PlusOutlined } from "@ant-design/icons";
import { Button, Card, Descriptions, Drawer, Empty, Pagination, Segmented, Space, Tag, Tooltip } from "antd";
import { useEffect, useId, useState } from "react";

type GrowthBranch = { node_id: string; generation?: number; cluster_id?: string; occurrence_count?: number; children?: string[] };
export type ForestTreeSummary = {
  tree_id: string; label?: string; documents?: number; unit_count?: number; published_units?: number;
  concept_nodes?: number; middle_nodes?: number; outer_nodes?: number; vector_count?: number;
  feature_count?: number; cluster_count?: number; growth_branches?: GrowthBranch[]; generation?: number | null;
};
type BrainKind = "knowledge" | "memory";
type Selection = { tree: ForestTreeSummary; branch?: GrowthBranch };
const INNER = "#2678d8", STORE = "#1b9a6c", OUTER = "#d88400", INK = "#243b53", MUTED = "#50667c";
const PAGE_SIZE = 3, WIDTH = 1080, BASE_Y = 548, TOP_Y = 190;
const count = (value?: number) => value == null ? "未提供" : value.toLocaleString("zh-CN");
const short = (value: string, limit = 22) => value.length > limit ? `${value.slice(0, limit - 1)}…` : value;

function labelFor(tree: ForestTreeSummary, brain: BrainKind) {
  if (tree.label) return tree.label;
  if (brain === "memory") return `${tree.tree_id} 记忆树`;
  const domain = tree.tree_id.replace("knowledge.", "");
  const names: Record<string, string> = { equipment: "设备", finance: "财务", general: "待归类", humanities: "人文典籍", legal: "法律", medical: "医疗", quality: "质量", safety: "安全" };
  return `${names[domain] || domain}知识树`;
}

// Independent of the structured-brain canvas. These are simultaneous layers of one trunk.
function LayeredTube({ d, branch = false }: { d: string; branch?: boolean }) {
  const ratio = branch ? .38 : 1;
  return <g className={branch ? "forest-growth-tube" : "forest-trunk-tube"}>
    {[{ color: OUTER, width: 44 }, { color: "#fff8e8", width: 34 }, { color: STORE, width: 30 }, { color: "#effcf6", width: 20 }, { color: INNER, width: 16 }].map(layer =>
      <path key={layer.width} d={d} fill="none" stroke={layer.color} strokeWidth={layer.width * ratio} strokeLinecap="round" strokeLinejoin="round" />)}
  </g>;
}

function CrossSection() {
  return <g transform="translate(91 154)" aria-label="管状主干截面：内链、周存、外链">
    <circle r="34" fill="#fff8e8" stroke={OUTER} strokeWidth="7" />
    <circle r="23" fill="#effcf6" stroke={STORE} strokeWidth="7" />
    <circle r="11" fill="#dcebff" stroke={INNER} strokeWidth="5" />
    <text y="57" textAnchor="middle" fill={INK} fontSize="12">主干截面</text>
  </g>;
}

function Branches({ tree, cx, detailed, onSelect }: { tree: ForestTreeSummary; cx: number; detailed: boolean; onSelect: (selection: Selection) => void }) {
  const shown = (tree.growth_branches || []).slice(0, 4);
  return <g aria-label="真实生长分枝">
    {shown.map((branch, index) => {
      const side = detailed ? 1 : index % 2 === 0 ? 1 : -1;
      const row = detailed ? index : Math.floor(index / 2);
      const y = 245 + row * (detailed ? 135 / Math.max(shown.length - 1, 1) : 85);
      const x = cx + side * (detailed ? 205 : 96);
      const d = `M ${cx} ${y + 83} C ${cx + side * 38} ${y + 61}, ${x - side * 35} ${y + 4}, ${x} ${y}`;
      return <g key={branch.node_id} className="forest-selectable" role="button" tabIndex={0}
        aria-label={`查看真实分枝 ${branch.node_id}`} onClick={() => onSelect({ tree, branch })}
        onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect({ tree, branch }); } }}>
        <LayeredTube d={d} branch />
        <circle cx={x} cy={y} r="8" fill="#fff0ea" stroke="#a34d2d" strokeWidth="2" />
        <text x={detailed ? x + 17 : x} y={detailed ? y - 3 : y - 22} textAnchor={detailed ? "start" : "middle"} fill="#803f27" fontSize="12" fontWeight="600">自动分枝 · {branch.generation == null ? "版本未提供" : `G${branch.generation}`}</text>
        {detailed && <text x={x + 17} y={y + 15} fill={MUTED} fontSize="11">{short(branch.cluster_id || branch.node_id, 26)}</text>}
        <title>{`节点：${branch.node_id}\n关联星团：${branch.cluster_id || "未提供"}\n发生次数：${count(branch.occurrence_count)}`}</title>
      </g>;
    })}
  </g>;
}

function TreeDrawing({ tree, cx, brain, detailed, arrow, onSelect, onFocus }: {
  tree: ForestTreeSummary; cx: number; brain: BrainKind; detailed: boolean; arrow: string;
  onSelect: (selection: Selection) => void; onFocus: (treeId: string) => void;
}) {
  const branches = tree.growth_branches;
  const branchLabel = branches == null ? "分枝拓扑尚未提供" : branches.length ? `${branches.length} 个真实分枝${branches.length > 4 ? "（图示前 4 个）" : ""}` : "尚未长出自动分枝";
  const open = () => detailed ? onSelect({ tree }) : onFocus(tree.tree_id);
  return <g className="forest-live-tree" data-tree-id={tree.tree_id}>
    <line x1={cx} x2={cx} y1="130" y2="104" stroke="#7d55b3" strokeWidth="2" markerEnd={arrow} />
    <g className="forest-selectable" role="button" tabIndex={0} aria-label={`查看${labelFor(tree, brain)}${detailed ? "详情" : "剖面"}`}
      onClick={open} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } }}>
      <rect x={cx - 114} y="130" width="228" height="43" rx="5" fill="#faf8fc" stroke="#a38bbf" />
      <text x={cx} y="149" textAnchor="middle" fill="#49345f" fontSize="14" fontWeight="700">{short(labelFor(tree, brain), 19)}</text>
      <text x={cx} y="165" textAnchor="middle" fill={MUTED} fontSize="11">证据输出 · {tree.generation == null ? "版本未提供" : `G${tree.generation}`}</text>
    </g>
    <line x1={cx} x2={cx} y1={TOP_Y} y2="174" stroke="#7d55b3" strokeWidth="2" markerEnd={arrow} />
    <Branches tree={tree} cx={cx} detailed={detailed} onSelect={onSelect} />
    <LayeredTube d={`M ${cx} ${BASE_Y - 30} C ${cx - 5} 420, ${cx + 5} 310, ${cx} ${TOP_Y}`} />
    {/* Root connectors express the foundation, not additional data nodes. */}
    {[-1, 0, 1].map(direction => <path key={direction} d={`M ${cx} ${BASE_Y - 30} Q ${cx + direction * 20} ${BASE_Y - 8}, ${cx + direction * 60} ${BASE_Y + 8}`} fill="none" stroke={direction === 0 ? INNER : direction < 0 ? STORE : OUTER} strokeWidth="5" strokeLinecap="round" />)}
    <text x={cx} y="492" textAnchor="middle" fill={INK} fontSize="12" paintOrder="stroke" stroke="#fbfdff" strokeWidth="5">{branchLabel}</text>
    {!detailed && <>
      <text x={cx - 42} y="365" textAnchor="end" fill="#23634d" fontSize="12">周存 {count(tree.unit_count)}</text>
      <text x={cx + 42} y="413" fill={MUTED} fontSize="12">{count(tree.cluster_count)} 个星团</text>
      <text x={cx} y="533" textAnchor="middle" fill={INK} fontSize="12" paintOrder="stroke" stroke="#fbfdff" strokeWidth="5">{count(tree.documents)} 份来源 · {brain === "knowledge" ? `${count(tree.vector_count)} 个向量留痕` : `${count(tree.feature_count)} 个时空特征`}</text>
    </>}
  </g>;
}

function LayerAnnotations({ tree, brain }: { tree: ForestTreeSummary; brain: BrainKind }) {
  const annotations = [
    { y: 259, color: INNER, title: "中通 DNA 内链", detail: brain === "knowledge" ? `概念路径 · ${count(tree.concept_nodes)} 个内链节点` : `时间 / 地点 / 主体 · ${count(tree.feature_count)} 个特征`, note: "沿主干组织检索路径", end: 460 },
    { y: 357, color: STORE, title: "中通周存", detail: `${count(tree.unit_count)} 个${brain === "knowledge" ? "知识" : "记忆"}单元`, note: brain === "knowledge" ? "WeKnora：解析、分块、混合检索与重排" : "媒体片段、模态特征与时空索引", end: 450 },
    { y: 455, color: OUTER, title: "DNA 外链", detail: brain === "knowledge" ? `${count(tree.outer_nodes)} 个外链节点 · 原文与引用` : "原始媒体定位、证据引用与公共锚点", note: "与内链共同构成双链结构", end: 438 },
  ];
  return <g aria-label="智慧树主干三层结构标注">
    {annotations.map(item => <g key={item.title}>
      <circle cx="48" cy={item.y - 4} r="4" fill={item.color} />
      <text x="60" y={item.y} fill={INK} fontSize="15" fontWeight="700">{item.title}</text>
      <text x="60" y={item.y + 22} fill={MUTED} fontSize="12">{item.detail}</text>
      <text x="60" y={item.y + 42} fill={MUTED} fontSize="12">{item.note}</text>
      <path d={`M 350 ${item.y - 5} H ${item.end}`} fill="none" stroke={item.color} strokeWidth="1.5" />
      <circle cx={item.end} cy={item.y - 5} r="3" fill={item.color} />
    </g>)}
    <text x="705" y="454" fill={INK} fontSize="14" fontWeight="700">{brain === "knowledge" ? "知识星团" : "时空星团"} · {count(tree.cluster_count)}</text>
    <text x="705" y="476" fill={MUTED} fontSize="12">星团提供聚合线索；生长记录决定分枝</text>
    {brain === "knowledge" && <text x="705" y="498" fill={MUTED} fontSize="12">向量留痕 {count(tree.vector_count)} · 已发布 {count(tree.published_units)}</text>}
  </g>;
}

export default function ForestPrincipleDiagram({ brain, trees, title, description, activeTreeId, onTreeChange }: {
  brain: BrainKind; trees: ForestTreeSummary[]; title: string; description: string; activeTreeId?: string; onTreeChange?: (treeId: string) => void;
}) {
  const [mode, setMode] = useState<"forest" | "section">("forest");
  const [focusedId, setFocusedId] = useState(activeTreeId || trees[0]?.tree_id);
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [selected, setSelected] = useState<Selection | null>(null);
  const marker = `forest-up-${useId().replace(/:/g, "")}`;
  const activeIndex = trees.findIndex(tree => tree.tree_id === activeTreeId);
  useEffect(() => {
    if (activeTreeId) { setFocusedId(activeTreeId); setPage(Math.floor(Math.max(0, activeIndex) / PAGE_SIZE) + 1); }
  }, [activeTreeId, activeIndex]);
  const currentPage = Math.min(page, Math.max(1, Math.ceil(trees.length / PAGE_SIZE)));
  const focusedTree = trees.find(tree => tree.tree_id === focusedId) || trees[0];
  const detailed = mode === "section";
  const displayed = detailed ? (focusedTree ? [focusedTree] : []) : trees.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const centers = displayed.map((_, index) => detailed ? 460 : 210 + index * 330);
  const focus = (treeId: string) => { setFocusedId(treeId); setMode("section"); setZoom(1); onTreeChange?.(treeId); };
  if (!trees.length) return <Card title={title}><Empty description="当前脑暂无可展示的真实智慧树" /></Card>;
  return <Card className="forest-principle-card" title={<Space wrap><span>{title}</span><Tag color="blue">{trees.length} 棵树</Tag></Space>}>
    <div className="forest-controls">
      <Segmented aria-label="智慧树展示方式" value={mode} onChange={value => { setMode(value as "forest" | "section"); setZoom(1); }} options={[{ value: "forest", label: "森林总览" }, { value: "section", label: "当前树剖面" }]} />
      <Space>
        <Tooltip title="缩小"><Button icon={<MinusOutlined />} aria-label="缩小智慧树" disabled={zoom <= .8} onClick={() => setZoom(value => Math.max(.8, value - .1))} /></Tooltip>
        <Tooltip title="放大"><Button icon={<PlusOutlined />} aria-label="放大智慧树" disabled={zoom >= 1.5} onClick={() => setZoom(value => Math.min(1.5, value + .1))} /></Tooltip>
        <Tooltip title="恢复比例"><Button icon={<AimOutlined />} aria-label="恢复智慧树比例" onClick={() => setZoom(1)} /></Tooltip>
      </Space>
    </div>
    <p className="topology-diagram-description">{description}</p>
    <div className="forest-layer-key" aria-label="主干结构图例">
      <span><i style={{ background: INNER }} />中心：中通 DNA 内链</span><span><i style={{ background: STORE }} />包层：中通周存{brain === "knowledge" ? " · WeKnora 索引与检索" : " · 记忆单元"}</span><span><i style={{ background: OUTER }} />外层：DNA 外链</span><span>侧枝：已有生长记录</span>
    </div>
    <div className="forest-principle-scroll" role="region" aria-label={`${title}画布`} tabIndex={0}>
      <svg className="forest-principle-svg" style={{ width: `${zoom * 100}%`, minWidth: 880 * zoom }} viewBox={`0 0 ${WIDTH} 700`} role="img" aria-label={`${brain === "knowledge" ? "知识" : "时空"}智慧树${detailed ? "主干剖面" : "森林总览"}：从基座向上生长`}>
        <defs><marker id={marker} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10Z" fill="#7d55b3" /></marker></defs>
        <rect x="340" y="16" width="400" height="45" rx="5" fill="#efe5fb" stroke="#8d5bd1" strokeWidth="1.5" />
        <text x="540" y="36" textAnchor="middle" fill="#51326f" fontSize="15" fontWeight="700">{brain === "knowledge" ? "知识脑" : "时空记忆脑"}统一结果输出</text>
        <text x="540" y="53" textAnchor="middle" fill="#58456c" fontSize="11">保留证据、来源、权限和版本；公共锚点支持跨脑追溯</text>
        <line x1="540" y1="77" x2="540" y2="62" stroke="#7d55b3" strokeWidth="2" markerEnd={`url(#${marker})`} />
        <rect x="440" y="77" width="200" height="27" rx="4" fill="#fbf7ff" stroke="#b494d4" />
        <text x="540" y="95" textAnchor="middle" fill="#67458a" fontSize="12" fontWeight="600">证据核验 / 汇聚</text>
        <path d={`M ${Math.min(centers[0], 540)} 104 H ${Math.max(centers[centers.length - 1], 540)}`} fill="none" stroke="#b7a9c7" strokeWidth="2" />
        {displayed.map((tree, index) => <TreeDrawing key={tree.tree_id} tree={tree} cx={centers[index]} brain={brain} detailed={detailed} arrow={`url(#${marker})`} onSelect={setSelected} onFocus={focus} />)}
        {detailed && <><CrossSection /><LayerAnnotations tree={focusedTree} brain={brain} /></>}
        <rect x="28" y={BASE_Y} width="1024" height="74" rx="5" fill="#f6ead5" stroke="#8a6137" strokeWidth="2" />
        <rect x="39" y={BASE_Y + 10} width="1002" height="53" rx="3" fill="#fff9ed" stroke="#c9a776" />
        <text x="540" y={BASE_Y + 32} textAnchor="middle" fill="#66451f" fontSize="15" fontWeight="700">{brain === "knowledge" ? "知识脑统一共享基座" : "时空脑统一共享基座"} · 规范与约束</text>
        <text x="540" y={BASE_Y + 53} textAnchor="middle" fill="#6e5739" fontSize="12">{brain === "knowledge" ? "概念、时间、权限、证据版本与发布门禁" : "时间、地点、主体、权限与原始媒体定位"} · 每棵树从基座向上生长</text>
        <line x1="540" y1="649" x2="540" y2="623" stroke="#2878b5" strokeWidth="2" markerEnd={`url(#${marker})`} />
        <rect x="310" y="649" width="460" height="36" rx="5" fill="#eaf4ff" stroke="#2878b5" />
        <text x="540" y="672" textAnchor="middle" fill="#1e5e91" fontSize="13">输入 · {brain === "knowledge" ? "资料导入 / 用户与 Agent 查询" : "媒体 / 摄像头 / RTSP / Agent 查询"}</text>
      </svg>
    </div>
    <div className="forest-caption">
      <span>{detailed ? "管状主干展示结构分层；点击真实侧枝可查看生长记录。" : "点击树名查看主干剖面。侧枝数量取自生长记录；未生长的树保留主干。"}</span>
      {!detailed && trees.length > PAGE_SIZE && <Pagination size="small" current={currentPage} pageSize={PAGE_SIZE} total={trees.length} showSizeChanger={false} onChange={setPage} showTotal={total => `共 ${total} 棵树`} />}
    </div>
    <Drawer title={selected?.branch ? "真实生长分枝" : selected ? labelFor(selected.tree, brain) : "智慧树详情"} width={480} open={!!selected} onClose={() => setSelected(null)}>
      {selected && <Descriptions column={1} size="small" bordered items={[
        { key: "tree", label: "智慧树", children: selected.tree.tree_id },
        { key: "version", label: "生长版本", children: selected.tree.generation == null ? "未提供" : `G${selected.tree.generation}` },
        { key: "source", label: "来源数量", children: count(selected.tree.documents) },
        { key: "units", label: "周存单元", children: count(selected.tree.unit_count) },
        { key: "clusters", label: "星团数量", children: count(selected.tree.cluster_count) },
        { key: "branchCount", label: "真实分枝", children: count(selected.tree.growth_branches?.length) },
        ...(selected.branch ? [
          { key: "branch", label: "分枝节点", children: selected.branch.node_id },
          { key: "cluster", label: "关联星团", children: selected.branch.cluster_id || "未提供" },
          { key: "branchVersion", label: "形成版本", children: selected.branch.generation == null ? "未提供" : `G${selected.branch.generation}` },
          { key: "occurrences", label: "发生次数", children: count(selected.branch.occurrence_count) },
        ] : []),
      ]} />}
    </Drawer>
  </Card>;
}
