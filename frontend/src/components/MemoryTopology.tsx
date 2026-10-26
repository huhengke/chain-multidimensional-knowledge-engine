import { Alert, Empty, Tabs, Tag } from "antd";
import ForestPrincipleDiagram from "./ForestPrincipleDiagram";
import { RelationGraph, DiagramEdge, DiagramNode } from "./TopologyDiagrams";

function countFeatures(unit: any) {
  return Array.isArray(unit?.features) ? unit.features.length : 0;
}

export default function MemoryTopology({ value }: { value: any }) {
  const units: any[] = value?.memory_units || [];
  const clusters: any[] = value?.memory_clusters || [];
  const links: any[] = value?.cross_brain_links || [];
  const sourceCount = new Set(units.map(item => item.source_ref).filter(Boolean)).size;
  const mediaCount = new Set(units.map(item => item.media_ref).filter(Boolean)).size;
  const memoryTrees = Array.from(new Set(units.map(item => item.tree_id).filter(Boolean))).map(treeId => {
    const treeUnits = units.filter(item => item.tree_id === treeId);
    const treeClusters = clusters.filter(item => item.tree_id === treeId);
    // Cluster revisions do not establish tree branches or tree generations.
    return { tree_id: treeId, label: `${treeId} 记忆树`, documents: new Set(treeUnits.map(item => item.source_ref).filter(Boolean)).size, unit_count: treeUnits.length, feature_count: treeUnits.reduce((sum, item) => sum + countFeatures(item), 0), cluster_count: treeClusters.length };
  });
  const nodes: DiagramNode[] = [];
  const edges: DiagramEdge[] = [];
  const sourceIds = new Map<string, string>();
  const assetIds = new Map<string, string>();
  units.forEach((unit: any) => {
    const source = String(unit.source_ref || "未知来源");
    const media = String(unit.media_ref || unit.unit_id);
    const sourceId = sourceIds.get(source) || `memory:source:${source}`;
    const assetId = assetIds.get(media) || `memory:asset:${media}`;
    if (!sourceIds.has(source)) {
      sourceIds.set(source, sourceId);
      nodes.push({ id: sourceId, type: "source", rank: 0, label: source, status: "registered", detail: { source_ref: source }, metrics: { units: 0 } });
    }
    if (!assetIds.has(media)) {
      assetIds.set(media, assetId);
      nodes.push({ id: assetId, type: "asset", rank: 1, label: media, status: "stored", detail: { media_ref: media, modality: unit.modality || "由记忆单元记录" }, metrics: { units: 0 } });
      edges.push({ id: `${sourceId}->${assetId}`, source: sourceId, target: assetId, label: "接入" });
    }
    const sourceNode = nodes.find(item => item.id === sourceId);
    const assetNode = nodes.find(item => item.id === assetId);
    if (sourceNode) sourceNode.metrics = { ...(sourceNode.metrics || {}), units: Number(sourceNode.metrics?.units || 0) + 1 };
    if (assetNode) assetNode.metrics = { ...(assetNode.metrics || {}), units: Number(assetNode.metrics?.units || 0) + 1 };
    const featureId = `memory:feature:${unit.unit_id}`;
    const unitId = `memory:unit:${unit.unit_id}`;
    nodes.push({ id: featureId, type: "feature", rank: 2, label: `${countFeatures(unit)} 个时空特征`, status: unit.status || "candidate", detail: { anchors: unit.anchors || {}, day_key: unit.day_key || null }, metrics: { feature_count: countFeatures(unit) } });
    nodes.push({ id: unitId, type: "memory", rank: 3, label: unit.unit_id, status: unit.status || "candidate", detail: { media_ref: unit.media_ref, tree_id: unit.tree_id, source_ref: unit.source_ref }, metrics: { confidence: unit.confidence, feature_count: countFeatures(unit) } });
    edges.push({ id: `${assetId}->${featureId}`, source: assetId, target: featureId, label: "解析" }, { id: `${featureId}->${unitId}`, source: featureId, target: unitId, label: "归一" });
  });
  clusters.forEach((cluster: any) => {
    const clusterId = `memory:cluster:${cluster.cluster_id}`;
    nodes.push({ id: clusterId, type: "cluster", rank: 4, label: cluster.cluster_id || "未命名星团", status: cluster.status || "active", detail: { tree_id: cluster.tree_id, generation: cluster.generation }, metrics: { member_count: cluster.member_count || 0, occurrence_count: cluster.occurrence_count || 0 } });
    (cluster.member_unit_ids || []).forEach((unitId: string) => edges.push({ id: `memory:unit:${unitId}->${clusterId}`, source: `memory:unit:${unitId}`, target: clusterId, label: "聚合" }));
    links.filter(link => String(link.source_ref || "").endsWith(String(cluster.cluster_id || ""))).forEach(link => {
      const anchorId = `memory:anchor:${link.link_id}`;
      nodes.push({ id: anchorId, type: "anchor", rank: 5, label: `${link.target_brain || "目标脑"} · ${link.anchor_value || "公共锚点"}`, status: link.status || "active", detail: { target_ref: link.target_ref, basis: link.basis }, metrics: { evidence_count: link.evidence_count || 0 } });
      edges.push({ id: `${clusterId}->${anchorId}`, source: clusterId, target: anchorId, label: "公共锚点" });
    });
  });
  if (!units.length && !clusters.length) return <Empty description="尚未形成可展示的时空记忆单元或星团" />;
  return <div className="platform-stack">
    <Alert type="info" showIcon message="时空记忆脑独立拓扑" description="媒体/实时流接入、时间地点归一、记忆单元和时空星团由时空脑独立管理；跨脑关系只通过公共锚点表达。" />
    <Tabs defaultActiveKey="principle" items={[
      { key: "principle", label: "时空记忆智慧树原理", children: <ForestPrincipleDiagram brain="memory" trees={memoryTrees} title="时空记忆智慧树 · 统一森林结构总览" description="从底部时空基座向上组织记忆：内链、周存、外链构成管状主干。分枝须有树拓扑记录，星团版本不作为分枝依据。" /> },
      { key: "relation", label: "时空来源关系", children: <RelationGraph title="时空来源关系" description="来源设备或文件 → 采样媒体资产 → 时间/地点/主体特征 → 记忆单元 → 时空星团 → 公共锚点；实时流尚未产生资产时不会伪造记忆节点。" nodes={nodes} edges={edges} emptyDescription="当前没有可追溯的媒体来源关系" /> },
    ]} />
    <div className="topology-node-summary"><Tag color="blue">来源 {sourceCount}</Tag><Tag color="purple">媒体资产 {mediaCount}</Tag><Tag color="green">记忆单元 {units.length}</Tag><Tag color="orange">时空星团 {clusters.length}</Tag></div>
  </div>;
}
