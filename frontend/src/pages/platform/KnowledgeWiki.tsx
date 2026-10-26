import {
  AimOutlined, FileSearchOutlined, MinusOutlined, NodeIndexOutlined, PlusOutlined, ReloadOutlined,
} from "@ant-design/icons";
import {
  Alert, Button, Card, Col, Descriptions, Drawer, Empty, Input, List, Pagination, Row, Select,
  Space, Spin, Statistic, Tabs, Tag, Tooltip,
} from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { errorMessage, http } from "../../api/client";
import { API } from "./shared";

type WikiPage = {
  slug: string;
  title: string;
  page_type: string;
  status: string;
  content?: string;
  summary?: string;
  source_refs?: string[];
  chunk_refs?: string[];
  in_links?: string[];
  out_links?: string[];
  version?: number;
  category_path?: string[];
  updated_at?: string;
};
type WikiStats = {
  total_pages?: number;
  pages_by_type?: Record<string, number>;
  total_links?: number;
  orphan_count?: number;
  pending_tasks?: number;
  pending_issues?: number;
  is_active?: boolean;
};
type WikiSummaryItem = {
  tree: string;
  wiki_enabled: boolean;
  stats?: WikiStats | null;
  error?: string | null;
  synthesis_model_id?: string;
  knowledge_base_id?: string;
};
type PageList = { pages?: WikiPage[]; total?: number; page?: number; page_size?: number; total_pages?: number };
type GraphNode = { slug: string; title: string; page_type: string; link_count: number };
type GraphEdge = { source: string; target: string };
type GraphPayload = {
  nodes?: GraphNode[];
  edges?: GraphEdge[];
  meta?: { total?: number; returned?: number; truncated?: boolean; mode?: string };
};

const pageTypeNames: Record<string, string> = {
  summary: "来源摘要", entity: "实体", concept: "概念", index: "索引",
  synthesis: "综合", comparison: "对比",
};
const pageTypePalette: Record<string, { fill: string; stroke: string }> = {
  summary: { fill: "#e9f2ff", stroke: "#2867a3" },
  entity: { fill: "#e6f5f1", stroke: "#237b6c" },
  concept: { fill: "#fff5db", stroke: "#9a6b00" },
  index: { fill: "#f6f8fb", stroke: "#6f8297" },
  synthesis: { fill: "#f1ecff", stroke: "#7254ad" },
  comparison: { fill: "#fcecf2", stroke: "#a83d67" },
};

function pageUrl(slug: string) {
  return API + "/knowledge/wiki/pages/" + slug.split("/").map(encodeURIComponent).join("/");
}

function sourceTitle(ref: string) {
  return ref.split("|").slice(1).join("|") || ref;
}

function WikiGraphCanvas({ graph, scale, activeSlug, onNode }: {
  graph: GraphPayload | null; scale: number; activeSlug?: string; onNode: (slug: string) => void;
}) {
  const layout = useMemo(() => {
    const order = ["index", "summary", "entity", "concept", "synthesis", "comparison", "other"];
    const groups = new Map<string, GraphNode[]>();
    for (const node of graph?.nodes || []) {
      const key = order.includes(node.page_type) ? node.page_type : "other";
      groups.set(key, [...(groups.get(key) || []), node]);
    }
    const positions = new Map<string, { x: number; y: number }>();
    let y = 30;
    for (const key of order) {
      const nodes = groups.get(key);
      if (!nodes?.length) continue;
      nodes.sort((a, b) => b.link_count - a.link_count || a.title.localeCompare(b.title));
      const columns = Math.max(1, Math.ceil(Math.sqrt(nodes.length * 1.7)));
      nodes.forEach((node, index) => {
        positions.set(node.slug, { x: 28 + (index % columns) * 212, y: y + Math.floor(index / columns) * 64 });
      });
      y += Math.ceil(nodes.length / columns) * 64 + 40;
    }
    const width = Math.max(980, ...Array.from(positions.values()).map(value => value.x + 188));
    return { positions, width, height: Math.max(620, y + 20) };
  }, [graph]);
  if (!graph?.nodes?.length) return <Empty description="Wiki 关系图谱尚未生成" />;
  return <div className="wiki-graph-scroll" role="region" aria-label="WeKnora Wiki 关系图谱">
    <svg width={layout.width * scale} height={layout.height * scale}
      viewBox={`0 0 ${layout.width} ${layout.height}`} aria-label="WeKnora Wiki 页面关系图谱">
      <g>
        {(graph.edges || []).map((edge, index) => {
          const source = layout.positions.get(edge.source);
          const target = layout.positions.get(edge.target);
          if (!source || !target) return null;
          return <line key={`${edge.source}-${edge.target}-${index}`}
            x1={source.x + 92} y1={source.y + 23} x2={target.x + 92} y2={target.y + 23}
            stroke="#5f7891" strokeWidth="1" opacity=".22" />;
        })}
      </g>
      <g>
        {(graph.nodes || []).map(node => {
          const position = layout.positions.get(node.slug)!;
          const palette = pageTypePalette[node.page_type] || pageTypePalette.index;
          const active = node.slug === activeSlug;
          return <g key={node.slug} transform={`translate(${position.x} ${position.y})`}
            className="wiki-graph-node" role="button" tabIndex={0}
            onClick={() => onNode(node.slug)}
            onKeyDown={event => { if (event.key === "Enter" || event.key === " ") onNode(node.slug); }}>
            <rect width="184" height="46" rx="4" fill={active ? "#fff3bf" : palette.fill}
              stroke={active ? "#c77800" : palette.stroke} strokeWidth={active ? 2.5 : 1.3} />
            <text x="9" y="16" fontSize="10" fill="#40566c">{pageTypeNames[node.page_type] || node.page_type}</text>
            <text x="9" y="33" fontSize="11" fontWeight="600" fill="#172b40">
              {`${node.title.slice(0, 17)}${node.title.length > 17 ? "…" : ""}`}
            </text>
            <text x="174" y="16" fontSize="9" textAnchor="end" fill="#61758c">{node.link_count}</text>
          </g>;
        })}
      </g>
    </svg>
  </div>;
}

export default function KnowledgeWiki({ treeId, treeLabel }: { treeId: string; treeLabel: string }) {
  const [summary, setSummary] = useState<WikiSummaryItem | null>(null);
  const [summaryError, setSummaryError] = useState("");
  const [pageData, setPageData] = useState<PageList>({});
  const [pagesLoading, setPagesLoading] = useState(false);
  const [pagesError, setPagesError] = useState("");
  const [page, setPage] = useState(1);
  const [pageType, setPageType] = useState("");
  const [query, setQuery] = useState("");
  const [graph, setGraph] = useState<GraphPayload | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState("");
  const [graphLimit, setGraphLimit] = useState(500);
  const [scale, setScale] = useState(.72);
  const [tab, setTab] = useState("pages");
  const [selected, setSelected] = useState<WikiPage | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const wikiEnabled = !!summary?.wiki_enabled;
  const stats: WikiStats = summary?.stats || {};

  const loadSummary = useCallback(async () => {
    try {
      const response = await http.get(API + "/knowledge/wiki");
      const items: WikiSummaryItem[] = response.data?.items || [];
      setSummary(items.find(item => item.tree === treeId) || null);
      setSummaryError("");
    } catch (cause) { setSummaryError(errorMessage(cause)); setSummary(null); }
  }, [treeId]);

  const loadPages = useCallback(async () => {
    if (!wikiEnabled) return;
    setPagesLoading(true);
    try {
      const response = await http.get(API + "/knowledge/wiki/pages", {
        params: { tree: treeId, page, page_size: 20, page_type: pageType, query },
      });
      setPageData(response.data || {});
      setPagesError("");
    } catch (cause) { setPagesError(errorMessage(cause)); setPageData({}); }
    finally { setPagesLoading(false); }
  }, [treeId, page, pageType, query, wikiEnabled]);

  const loadGraph = useCallback(async () => {
    if (!wikiEnabled) return;
    setGraphLoading(true);
    try {
      const response = await http.get(API + "/knowledge/wiki/graph", {
        params: { tree: treeId, mode: "overview", limit: graphLimit },
      });
      setGraph(response.data || null);
      setGraphError("");
    } catch (cause) { setGraphError(errorMessage(cause)); setGraph(null); }
    finally { setGraphLoading(false); }
  }, [treeId, graphLimit, wikiEnabled]);

  const openPage = useCallback(async (slug: string) => {
    setDrawerOpen(true);
    setDetailLoading(true);
    setSelected(null);
    try {
      const response = await http.get(pageUrl(slug), { params: { tree: treeId } });
      setSelected(response.data || null);
    } catch (cause) {
      setPagesError(errorMessage(cause));
      setDrawerOpen(false);
    } finally { setDetailLoading(false); }
  }, [treeId]);

  useEffect(() => {
    setSummary(null); setPage(1); setPageType(""); setQuery("");
    setGraph(null); setSelected(null); setDrawerOpen(false);
    void loadSummary();
  }, [loadSummary]);
  useEffect(() => { void loadPages(); }, [loadPages]);
  useEffect(() => { if (tab === "graph") void loadGraph(); }, [tab, loadGraph]);
  useEffect(() => {
    const active = !!stats.is_active || (stats.pending_tasks || 0) > 0;
    if (!active) return;
    const timer = setInterval(() => {
      void loadSummary();
      if (tab === "pages") void loadPages(); else void loadGraph();
    }, 15000);
    return () => clearInterval(timer);
  }, [stats.is_active, stats.pending_tasks, tab, loadSummary, loadPages, loadGraph]);

  const pageCount = pageData.total || 0;
  const pages = pageData.pages || [];
  return <div className="wiki-workspace">
    <Alert type="info" showIcon message={`${treeLabel} · WeKnora 完整 Wiki 页面与关系图谱`}
      description="Wiki 页面由 WeKnora 基于已发布的周存知识自动生成：来源摘要、实体、概念、综合页面和双向链接图谱；平台只展示真实生成结果，不伪造占位内容。" />
    {summaryError && <Alert type="error" showIcon message={summaryError} />}
    {summary?.error && <Alert type="warning" showIcon message="WeKnora Wiki 状态读取失败" description={summary.error} />}
    {summary && !summary.wiki_enabled && <Alert type="warning" showIcon message="该知识树尚未启用 Wiki 自动生成"
      description="后台启用并重新处理后会在这里显示完整页面与图谱；当前不展示任何伪造内容。" />}
    {wikiEnabled && <Row gutter={[12, 12]} className="wiki-stat-grid">
      <Col xs={12} md={6}><Card size="small"><Statistic title="Wiki 页面" value={stats.total_pages || 0} prefix={<FileSearchOutlined />} /></Card></Col>
      <Col xs={12} md={6}><Card size="small"><Statistic title="页面链接" value={stats.total_links || 0} /></Card></Col>
      <Col xs={12} md={6}><Card size="small"><Statistic title="待生成任务" value={stats.pending_tasks || 0} /></Card></Col>
      <Col xs={12} md={6}><Card size="small"><Statistic title="生成状态" value={stats.is_active ? "生成中" : "已就绪"} valueStyle={{ fontSize: 20 }} /></Card></Col>
    </Row>}
    {wikiEnabled && <Space wrap>
      {(Object.entries(stats.pages_by_type || {}) as [string, number][]).map(([type, count]) =>
        <Tag key={type}>{pageTypeNames[type] || type} {count}</Tag>)}
      <Tag color={stats.orphan_count ? "orange" : "green"}>孤立页面 {stats.orphan_count || 0}</Tag>
    </Space>}
    {wikiEnabled && <Tabs activeKey={tab} onChange={setTab} items={[
      {
        key: "pages",
        label: <Space><FileSearchOutlined />Wiki 页面</Space>,
        children: <Card className="wiki-browser" title={`${treeLabel} 页面目录`} extra={<Space wrap>
          <Input.Search allowClear aria-label="搜索 Wiki 页面" placeholder="搜索标题或正文" style={{ width: 220 }}
            onSearch={value => { setQuery(value.trim()); setPage(1); }} />
          <Select allowClear aria-label="筛选页面类型" placeholder="全部类型" style={{ width: 140 }}
            value={pageType || undefined}
            options={Object.entries(pageTypeNames).map(([value, label]) => ({ value, label }))}
            onChange={value => { setPageType(value || ""); setPage(1); }} />
          <Button icon={<ReloadOutlined />} onClick={() => { void loadSummary(); void loadPages(); }}>刷新</Button>
        </Space>}>
          {pagesError && <Alert type="error" showIcon message={pagesError} />}
          {pagesLoading ? <Spin className="wiki-page-loading" size="large" /> : pages.length ? <>
            <List dataSource={pages} renderItem={item => <List.Item key={item.slug} onClick={() => void openPage(item.slug)}
              className="wiki-page-row">
              <div className="wiki-page-row-main">
                <Space wrap><strong>{item.title}</strong>
                  <Tag style={{ borderColor: pageTypePalette[item.page_type]?.stroke, background: pageTypePalette[item.page_type]?.fill }}>
                    {pageTypeNames[item.page_type] || item.page_type}
                  </Tag>
                  <Tag color={item.status === "published" ? "green" : "default"}>{item.status}</Tag>
                  {item.version ? <Tag>v{item.version}</Tag> : null}
                </Space>
                <p>{item.summary || (item.content || "").replace(/[#*_>`-]/g, "").slice(0, 140) || "暂无摘要"}</p>
                <small>{item.category_path?.join(" / ") || "知识 Wiki"} · 来源 {item.source_refs?.length || 0}</small>
              </div>
            </List.Item>} />
            <Pagination className="wiki-page-pagination" current={page} pageSize={20} total={pageCount}
              showSizeChanger={false} onChange={setPage} />
          </> : !pagesError && <Empty description="当前筛选下没有 Wiki 页面" />}
        </Card>,
      },
      {
        key: "graph",
        label: <Space><NodeIndexOutlined />关系图谱</Space>,
        children: <Card title={<Space><span>{treeLabel} · Wiki 双链关系图谱</span>{graph?.meta?.truncated && <Tag color="orange">已截断</Tag>}</Space>}
          extra={<Space wrap>
            <Select aria-label="图谱节点上限" style={{ width: 150 }} value={graphLimit}
              options={[100, 300, 500, 1000, 2000].map(value => ({ value, label: `最多 ${value} 节点` }))}
              onChange={value => setGraphLimit(value)} />
            <Tooltip title="缩小"><Button icon={<MinusOutlined />} aria-label="缩小" onClick={() => setScale(value => Math.max(.4, value - .1))} /></Tooltip>
            <Tooltip title="放大"><Button icon={<PlusOutlined />} aria-label="放大" onClick={() => setScale(value => Math.min(1.4, value + .1))} /></Tooltip>
            <Tooltip title="适合画布"><Button icon={<AimOutlined />} aria-label="适合画布" onClick={() => setScale(.72)} /></Tooltip>
            <Button icon={<ReloadOutlined />} onClick={() => void loadGraph()}>刷新</Button>
          </Space>}>
          <div className="wiki-graph-meta">
            <Space wrap>
              <Tag>节点 {graph?.meta?.returned ?? graph?.nodes?.length ?? 0} / {graph?.meta?.total ?? 0}</Tag>
              <Tag>边 {graph?.edges?.length ?? 0}</Tag>
              {graph?.meta?.truncated && <Tag color="orange">当前上限下未展示全部页面，可提高节点上限或点击页面查看 ego 关系</Tag>}
            </Space>
            <Space wrap>{Object.entries(pageTypePalette).map(([type, palette]) =>
              <span key={type} className="wiki-graph-key"><i style={{ background: palette.fill, borderColor: palette.stroke }} />{pageTypeNames[type]}</span>)}</Space>
          </div>
          {graphError && <Alert type="error" showIcon message={graphError} />}
          {graphLoading ? <Spin className="wiki-page-loading" size="large" />
            : <WikiGraphCanvas graph={graph} scale={scale} activeSlug={selected?.slug} onNode={slug => void openPage(slug)} />}
        </Card>,
      },
    ]} />}
    <Drawer width={800} open={drawerOpen} onClose={() => setDrawerOpen(false)}
      title={selected?.title || "Wiki 页面"}
      extra={selected && <Space wrap>
        <Tag>{pageTypeNames[selected.page_type] || selected.page_type}</Tag>
        <Tag color={selected.status === "published" ? "green" : "default"}>{selected.status}</Tag>
      </Space>}>
      {detailLoading ? <Spin className="wiki-page-loading" size="large" /> : selected ? <div className="wiki-detail">
        {selected.summary && <Alert type="info" showIcon message={selected.summary} />}
        {(selected.in_links?.length || selected.out_links?.length) ? <Space wrap>
          {(selected.in_links || []).map(slug => <Button key={`in-${slug}`} size="small" onClick={() => void openPage(slug)}>← {slug}</Button>)}
          {(selected.out_links || []).map(slug => <Button key={`out-${slug}`} size="small" onClick={() => void openPage(slug)}>{slug} →</Button>)}
        </Space> : null}
        <div className="wiki-markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{selected.content || "（页面暂无正文）"}</ReactMarkdown>
        </div>
        <Descriptions column={1} size="small" bordered items={[
          { key: "slug", label: "页面标识", children: selected.slug },
          { key: "category", label: "目录", children: selected.category_path?.join(" / ") || "知识 Wiki" },
          { key: "version", label: "版本", children: selected.version ? `v${selected.version}` : "-" },
          { key: "updated", label: "更新时间", children: selected.updated_at ? new Date(selected.updated_at).toLocaleString() : "-" },
          { key: "chunks", label: "引用分块", children: `${selected.chunk_refs?.length || 0} 个` },
          { key: "sources", label: "来源知识", children: (selected.source_refs || []).length
            ? <Space wrap>{selected.source_refs!.map(ref => <Tag key={ref}>{sourceTitle(ref)}</Tag>)}</Space>
            : "无" },
        ]} />
      </div> : <Empty description="页面内容不可用" />}
    </Drawer>
  </div>;
}
