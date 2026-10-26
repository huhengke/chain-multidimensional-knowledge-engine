import { useEffect, useMemo, useState } from "react";
import { Alert, Spin } from "antd";
import { errorMessage } from "../api/client";
import { listDatasets } from "../api/endpoints";
import type { DatasetSummary } from "../api/types";
import { DiagramEdge, DiagramNode, RelationGraph } from "./TopologyDiagrams";

function sourceLabel(dataset: DatasetSummary) {
  return dataset.source_file || dataset.source?.table || dataset.source?.datasource_id || `${dataset.origin} 注册来源`;
}

export default function StructuredRelationTopology() {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    void listDatasets().then(result => {
      if (!cancelled) { setDatasets((result.items || []).filter(item => !item.disabled)); setError(""); }
    }).catch(cause => { if (!cancelled) setError(errorMessage(cause, "无法读取结构化数据集来源")); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);
  const graph = useMemo(() => {
    const nodes: DiagramNode[] = [];
    const edges: DiagramEdge[] = [];
    datasets.forEach((dataset, index) => {
      const key = `structured:${dataset.id}`;
      const declaredFields = dataset.structure?.feature_layers?.length
        ? dataset.structure.feature_layers.map(field => ({
          id: field.source || field.name || field.label || "field",
          label: field.label || field.name || field.source || "未命名字段",
          detail: field,
          queryUses: field.query_uses || [],
        }))
        : (dataset.columns || []).map(field => ({
          id: field.source || field.name || field.label || "field",
          label: field.label || field.name || field.source || "未命名字段",
          detail: field,
          queryUses: [],
        }));
      const fields = declaredFields.length ? declaredFields.slice(0, 16) : [{
        id: "summary",
        label: "字段声明待补充",
        detail: { levels: dataset.levels || [], columns: dataset.columns || [] },
        queryUses: [],
      }];
      const levels = (dataset.levels || []).slice(0, 12);
      const trunks = levels.length ? levels : ["统一查询主干"];
      const metricCount = dataset.metrics?.length || 0;
      const sourceId = `${key}:source`;
      const registerId = `${key}:runtime:register`;
      const contractId = `${key}:gate:contract`;
      const queryEngineId = `${key}:runtime:query`;
      const datasetId = `${key}:dataset`;
      nodes.push(
        { id: sourceId, type: "source", rank: 0, label: sourceLabel(dataset), status: "registered", detail: { origin: dataset.origin, source: dataset.source || null, source_file: dataset.source_file || null }, metrics: { dataset_index: index } },
        { id: registerId, type: "runtime", rank: 1, label: "来源注册 / 导入", status: "registered", detail: { dataset: dataset.id, origin: dataset.origin }, metrics: { source_file: dataset.source_file || null } },
        { id: `${key}:runtime:schema`, type: "runtime", rank: 1, label: "字段画像 / 结构解析", status: "declared", detail: { columns: dataset.columns || [], levels: dataset.levels || [] }, metrics: { fields: fields.length, layers: dataset.structure?.layer_counts || {} } },
        { id: contractId, type: "gate", rank: 2, label: "数据契约 / 基座规范", status: "declared", detail: { kind: dataset.kind, base: dataset.base || null, metrics: dataset.metrics || [] }, metrics: { base_features: dataset.base?.features?.length || 0, metrics: metricCount } },
        { id: datasetId, type: "dataset", rank: 2, label: dataset.label, status: dataset.disabled ? "disabled" : "active", detail: { dataset: dataset.id, kind: dataset.kind, source_file: dataset.source_file || null }, metrics: { levels: levels.length, metrics: metricCount } },
        { id: queryEngineId, type: "runtime", rank: 6, label: "查询执行引擎", status: "available", detail: { time: dataset.time || null, physical_topology: dataset.structure?.physical_topology || null }, metrics: { physical_nodes: dataset.structure?.physical_topology?.node_count || 0 } },
      );
      edges.push(
        { id: `${key}:source-register`, source: sourceId, target: registerId, label: "导入" },
        { id: `${key}:source-schema`, source: sourceId, target: `${key}:runtime:schema`, label: "解析" },
        { id: `${key}:register-dataset`, source: registerId, target: datasetId, label: "注册数据集" },
        { id: `${key}:schema-contract`, source: `${key}:runtime:schema`, target: contractId, label: "形成契约" },
        { id: `${key}:contract-query`, source: contractId, target: queryEngineId, label: "授权执行" },
        { id: `${key}:dataset-query`, source: datasetId, target: queryEngineId, label: "进入查询" },
      );
      fields.forEach((field, fieldIndex) => {
        const fieldId = `${key}:field:${fieldIndex}`;
        nodes.push({ id: fieldId, type: "field", rank: 3, label: field.label, status: "declared", detail: field.detail as unknown as Record<string, unknown>, metrics: { query_uses: field.queryUses.length } });
        edges.push({ id: `${key}:dataset-field-${fieldIndex}`, source: datasetId, target: fieldId, label: fieldIndex === 0 ? "字段与特征" : undefined });
      });
      trunks.forEach((level, levelIndex) => {
        const trunkId = `${key}:trunk:${levelIndex}`;
        const storeId = `${key}:store:${levelIndex}`;
        nodes.push(
          { id: trunkId, type: "trunk", rank: 4, label: `中通内链 · ${level}`, status: "available", detail: { level, dataset: dataset.id }, metrics: { level_index: levelIndex } },
          { id: storeId, type: "store", rank: 5, label: `中通周存 · ${level}`, status: "queryable", detail: { level, dataset: dataset.id }, metrics: { records: dataset.record_count || null } },
        );
        edges.push(
          { id: `${key}:contract-trunk-${levelIndex}`, source: contractId, target: trunkId, label: levelIndex === 0 ? "生成主干" : undefined },
          { id: `${key}:trunk-store-${levelIndex}`, source: trunkId, target: storeId, label: "周存" },
          { id: `${key}:store-query-${levelIndex}`, source: storeId, target: queryEngineId, label: levelIndex === 0 ? "可查询" : undefined },
        );
      });
      const resultId = `${key}:result`;
      nodes.push({ id: resultId, type: "output", rank: 7, label: `${dataset.label} · 事实结果`, status: "queryable", detail: { dataset: dataset.id, route: dataset.structure?.physical_topology?.query_path || [] }, metrics: { records: dataset.record_count || null, branches: dataset.structure?.physical_topology?.branch_nodes || 0 } });
      edges.push({ id: `${key}:query-result`, source: queryEngineId, target: resultId, label: "返回事实" });
    });
    return { nodes, edges };
  }, [datasets]);
  if (loading) return <div className="topology-loading-inline"><Spin /> 正在读取结构化来源与数据集…</div>;
  return <div className="platform-stack">
    {error && <Alert type="warning" showIcon message={error} />}
    <RelationGraph title="结构化数据来源关系" description="按统一智慧树森林标准展示：来源文档 → 注册与结构解析 → 数据契约 → 字段特征 → 中通内链 → 中通周存 → 查询执行 → 事实结果。这里仅展示结构化脑自身的注册与执行链，不引入 WeKnora、向量或知识星团节点。" nodes={graph.nodes} edges={graph.edges} emptyDescription="尚未注册可展示的结构化数据集来源" />
  </div>;
}
