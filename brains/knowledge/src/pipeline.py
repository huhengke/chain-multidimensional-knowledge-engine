"""M4 五层导入管道（parse / segment / annotate / gate / publish）与知识存储。

五层：
① ``parse``    文本/表格/页码/标题层级；无 OCR/转写引擎时**显式 unavailable**。
② ``segment``  按标题层级 + 段落/表格切为可引用知识单元，保留 locator（page/anchor/timecode）。
③ ``annotate`` 候选领域/概念/条件/版本/置信度/关联实体；低风险稳定模式写 **M1 留痕层**
               （``TraceRecord``，``aggregate_key`` 严禁含日期）并自动生长。
④ ``gate``     高风险本体关系、法律/安全结论、冲突裁决、发布级别、不可逆语义
               → 受控复核 ``pending_review``；支持 approve/reject/rollback。
⑤ ``publish``  写知识版本 + 证据索引 + 外链；旧版可追溯；查询只读**已发布版本**。

存储边界：原文/版本/证据 **不因索引重建被静默覆盖**。
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from brains.knowledge.src.contract import (
    CanonicalDocument,
    KnowledgeEvidence,
    KnowledgeUnit,
    UnitLocator,
)
from brains.knowledge.src.ontology import (
    Annotation,
    OntologyStore,
    domain_risk,
    domain_label,
)
from brains.knowledge.src.reasoning import ProfessionalReasoner
from brains.knowledge.src.classification import CLASSIFICATION_VERSION, classify_document, aggregate_subject_identity
from brains.knowledge.src.runtime_adapter import (
    KnowledgeInput,
    KnowledgeRuntime,
    ParsedContent,
    Segment,
    slugify,
)
from topology.src.growth import GenerationStore, GrowthEngine, TopologyGeneration
from topology.src.node import TopologyNode
from topology.src.payload import PayloadStore
from topology.src.ruler import Ruler
from topology.src.star_cluster import StarClusterIndex
from brains.knowledge.src.clustering import KnowledgeClusterIndex
from topology.src.trace import TraceRecord
from brains.knowledge.src.temporal import ContentTimeAnchor, KnowledgeTemporalIndex, extract_content_time

ANNOTATE_MODEL_VERSION = CLASSIFICATION_VERSION
RULE_VERSION = "knowledge-rules-v1"

# 触发受控复核的高风险信号
_HIGH_RISK_KEYWORDS = ("结论", "裁决", "放行", "判定", "合规", "不可逆", "最终")
_IRREVERSIBLE_FLAGS = ("irreversible", "authoritative")

# 属性型断言：<主体>(为|是|不超过|至少为|不得超过) <数值+单位>
_ATTR_RE = re.compile(
    r"([\u4e00-\u9fff]{2,12}|[A-Za-z][A-Za-z0-9_]{1,15})"
    r"(?:为|是|不超过|至少为|不得超过|不得低于|上限为|下限为)\s*"
    r"([0-9][0-9.]*\s*[A-Za-z℃°%‰]*|[A-Za-z]+)"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 存储模型
# --------------------------------------------------------------------------- #
class DocumentPublication(BaseModel):
    """文档发布状态（可追溯旧版本）。"""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    status: str = "pending_review"  # pending_review | published | rejected
    version: str | None = None
    published_at: str | None = None
    evidence_index_ref: str | None = None
    external_links: list[str] = Field(default_factory=list)
    gate_ids: list[str] = Field(default_factory=list)
    history: list[str] = Field(default_factory=list)


class GateRecord(BaseModel):
    """策略门禁记录。支持 approve / reject / rollback。"""

    model_config = ConfigDict(extra="forbid")

    gate_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    unit_id: str | None = None
    risk: str = "high"
    reason: str = Field(min_length=1)
    status: str = "pending_review"  # pending_review | approved | rejected
    created_at: str = Field(min_length=1)
    decided_at: str | None = None
    reviewer: str | None = None
    decision_reason: str | None = None


class ConflictRecord(BaseModel):
    """冲突并列记录：保留双方证据、适用条件与发布日期，**禁止静默裁决**。"""

    model_config = ConfigDict(extra="forbid")

    conflict_id: str = Field(min_length=1)
    concept_path: list[str] = Field(default_factory=list)
    subject: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    published_dates: list[str | None] = Field(default_factory=list)
    note: str = ""


class KnowledgeStore:
    """知识存储：文档 / 单元 / 证据 / 路由 / 发布 / 门禁 / 冲突 / 拓扑 / 留痕。"""

    def __init__(self) -> None:
        self.payload = PayloadStore()
        self.generations = GenerationStore()
        self.documents: dict[str, CanonicalDocument] = {}
        self.sources: dict[str, Any] = {}
        self.document_published: dict[str, str | None] = {}
        self.document_classification: dict[str, dict] = {}
        self.units: dict[str, KnowledgeUnit] = {}
        self.unit_text: dict[str, str] = {}
        self.unit_vector: dict[str, list[str]] = {}
        self.unit_tree: dict[str, str] = {}
        self.unit_annotation: dict[str, Annotation] = {}
        self.unit_attr_claims: dict[str, list[tuple[str, str]]] = {}
        self.evidence: dict[str, KnowledgeEvidence] = {}
        self.evidence_by_unit: dict[str, list[str]] = defaultdict(list)
        self.evidence_by_document: dict[str, list[str]] = defaultdict(list)
        # 留痕层（聚合身份不含日期）
        self.traces: dict[str, TraceRecord] = {}
        # Stable read snapshots keep the vector matrix separately.  This map
        # preserves topology diagnostics after compacting duplicate trace data.
        self.trace_vector_dimensions: dict[str, int] = {}
        self.trace_by_aggregate: dict[str, list[str]] = defaultdict(list)
        self.trace_by_unit: dict[str, str] = {}
        self.clusters: dict[str, StarClusterIndex] = {}
        # 拓扑
        self.nodes: dict[str, TopologyNode] = {}
        self.ring_nodes: dict[tuple[str, str], list[str]] = defaultdict(list)
        # 治理
        self.publications: dict[str, DocumentPublication] = {}
        self.gates: dict[str, GateRecord] = {}
        self.gate_by_document: dict[str, list[str]] = defaultdict(list)
        self.conflicts: list[ConflictRecord] = []
        self.versions: dict[str, list[str]] = defaultdict(list)
        # 时间投影只保存可重建的索引与引用，不复制正文、向量或权限。
        self.temporal_index = KnowledgeTemporalIndex()
        self.unit_content_time: dict[str, list[ContentTimeAnchor]] = {}
        self.unit_ingested_at: dict[str, str] = {}
        self.unit_observed_at: dict[str, str] = {}
        self.document_ingested_at: dict[str, str] = {}
        self.document_observed_at: dict[str, str] = {}

    def cluster_index(self, tree_id: str, tolerance: str) -> StarClusterIndex:
        index = self.clusters.get(tree_id)
        if index is None:
            index = KnowledgeClusterIndex(tree_id, tolerance=tolerance)
            self.clusters[tree_id] = index
        return index

    def document_units(self, document_id: str) -> list[KnowledgeUnit]:
        return [u for u in self.units.values() if u.document_id == document_id]

    def is_published(self, document_id: str) -> bool:
        pub = self.publications.get(document_id)
        return pub is not None and pub.status == "published"


# --------------------------------------------------------------------------- #
# 自动生长（复用 M1 GrowthEngine / GenerationStore）
# --------------------------------------------------------------------------- #
class KnowledgeGrowth:
    """按 ``growth_threshold`` 触发分枝并重建不可变 generation。"""

    def __init__(
        self,
        store: KnowledgeStore,
        ruler: Ruler,
        growth_threshold: int,
        tolerance: str,
    ) -> None:
        self.store = store
        self.ruler = ruler
        self.tolerance = tolerance
        self.engine = GrowthEngine(growth_threshold)
        self._grown: set[str] = set()

    def assign(self, trace: TraceRecord, tree_id: str):
        return self.store.cluster_index(tree_id, self.tolerance).assign(trace)

    def _tree_nodes(self, tree_id: str) -> dict[str, TopologyNode]:
        return {nid: node for nid, node in self.store.nodes.items() if node.tree_id == tree_id}

    def commit(self, tree_id: str, day_key: str) -> TopologyGeneration:
        history = self.store.generations.history(tree_id)
        if not history:
            base = TopologyGeneration(
                generation=1,
                tree_id=tree_id,
                nodes=self._tree_nodes(tree_id),
                parent=None,
                cause={"reason": "bootstrap"},
            )
            return self.store.generations.commit(base)

        current = self.store.generations.current(tree_id)
        index = self.store.clusters.get(tree_id)
        if index is None:
            return current

        triggering = [
            cluster
            for cluster in index.clusters.values()
            if self.engine.triggers(cluster) and cluster.cluster_id not in self._grown
        ]
        if not triggering:
            return current

        ring_index = self.ruler.ring_index(tree_id, day_key)
        new_generation = self.engine.grow(
            current, triggering, self.store.payload, day_key, ring_index
        )
        merged: dict[str, TopologyNode] = dict(current.nodes)
        merged.update(self._tree_nodes(tree_id))
        merged.update(dict(new_generation.nodes))
        generation = TopologyGeneration(
            generation=new_generation.generation,
            tree_id=tree_id,
            nodes=merged,
            parent=new_generation.parent,
            cause=new_generation.cause,
        )
        self.store.generations.commit(generation)
        self._grown.update(cluster.cluster_id for cluster in triggering)
        return generation


# --------------------------------------------------------------------------- #
# 五层导入管道
# --------------------------------------------------------------------------- #
class KnowledgePipeline:
    """五层导入管道编排：parse → segment → annotate → gate → publish。"""

    def __init__(
        self,
        runtime: KnowledgeRuntime,
        store: KnowledgeStore,
        ontology: OntologyStore,
        ruler: Ruler,
        *,
        tolerance: str = "eps=0.05",
        growth_threshold: int = 3,
    ) -> None:
        self.runtime = runtime
        self.store = store
        self.ontology = ontology
        self.ruler = ruler
        self.tolerance = tolerance
        self.growth = KnowledgeGrowth(store, ruler, growth_threshold, tolerance)
        self.reasoner = ProfessionalReasoner()

    # ---- ① parse ---------------------------------------------------------
    def parse(self, raw: KnowledgeInput) -> ParsedContent:
        # Classification is host metadata; it must not invalidate parsing caches.
        metadata = {k: v for k, v in raw.metadata.items() if k != 'classification_override'}
        return self.runtime.parse(raw.model_copy(update={'metadata': metadata}))

    # ---- ② segment -------------------------------------------------------
    def segment(self, parsed: ParsedContent) -> list[Segment]:
        return self.runtime.segment(parsed)

    # ---- ③ annotate ------------------------------------------------------
    def annotate(
        self, raw: KnowledgeInput, document: CanonicalDocument, segments: list[Segment]
    ) -> list[KnowledgeUnit]:
        day_key = self._topology_day()
        time_status = self._time_status(raw)
        ingested_at = str(raw.metadata.get("ingested_at") or _now_iso())
        observed_at = raw.metadata.get("observed_at")
        self.store.document_ingested_at[document.document_id] = ingested_at
        if observed_at:
            self.store.document_observed_at[document.document_id] = str(observed_at)
        units: list[KnowledgeUnit] = []
        annotations: dict[str, Annotation] = {}
        classification = classify_document(raw.title or "", raw.content or "",
                                           override=raw.metadata.get("classification_override"))
        self.store.document_classification[document.document_id] = classification

        for chunk_order, segment in enumerate(segments):
            annotation = self.ontology.annotate(
                segment.text, segment.heading_path, raw.version,
                document_context=classification, title=raw.title or ""
            )
            unit_id = "ku-" + _digest(document.document_id, segment.anchor or segment.text)[:16]
            parent_key = "/".join(segment.heading_path) or segment.anchor or f"root-{chunk_order}"
            if segment.parent_index >= 0:
                parent_key = f"weknora-parent-{segment.page}-{segment.parent_index}"
            parent_unit_id = "kp-" + _digest(document.document_id, parent_key)[:16]
            evidence_id = "ke-" + _digest(unit_id)[:16]
            locator = UnitLocator(
                page=segment.page, anchor=segment.anchor, timecode=segment.timecode
            )
            if parent_unit_id not in self.store.units:
                parent_ref = self.store.payload.put(
                    "middle",
                    {
                        "kind": "knowledge-parent-chunk",
                        "unit_id": parent_unit_id,
                        "document_id": document.document_id,
                        "heading_path": list(segment.heading_path),
                        "text": segment.parent_text or segment.text,
                        "locator": locator.model_dump(),
                    },
                )
                self.store.units[parent_unit_id] = KnowledgeUnit(
                    unit_id=parent_unit_id,
                    document_id=document.document_id,
                    unit_type="parent",
                    text_ref=parent_ref,
                    locator=locator,
                    concept_path=list(annotation.concept_path),
                    version=raw.version,
                    chunk_level=0,
                    chunk_order=chunk_order,
                )
            # 三层载荷：原文块 -> outer；摘要/版本 -> middle（概念锚点 inner 由本体写）
            text_ref = self.store.payload.put(
                "outer",
                {
                    "kind": "knowledge-unit-text",
                    "unit_id": unit_id,
                    "document_id": document.document_id,
                    "text": segment.text,
                    "locator": locator.model_dump(),
                },
            )
            self.store.payload.put(
                "middle",
                {
                    "kind": "knowledge-unit-summary",
                    "unit_id": unit_id,
                    "document_id": document.document_id,
                    "domain": annotation.domain,
                    "concept_path": annotation.concept_path,
                    "version": raw.version,
                    "parse_version": raw.parse_version,
                },
            )

            unit = KnowledgeUnit(
                unit_id=unit_id,
                document_id=document.document_id,
                unit_type=segment.unit_type,
                text_ref=text_ref,
                locator=locator,
                concept_path=list(annotation.concept_path),
                version=raw.version,
                parent_unit_id=parent_unit_id,
                chunk_level=1,
                chunk_order=chunk_order,
            )
            evidence = KnowledgeEvidence(
                evidence_id=evidence_id,
                source_id=document.source_id,
                document_id=document.document_id,
                unit_id=unit_id,
                locator={"page": segment.page, "anchor": segment.anchor, "timecode": segment.timecode},
                content_version=raw.version,
                parse_version=raw.parse_version,
                permission_scope=list(raw.permission_scope),
                time_status=time_status,
                citation_chain=[document.source_id, document.document_id, unit_id],
                confidence=annotation.confidence,
            )
            self.store.units[unit_id] = unit
            self.store.unit_text[unit_id] = segment.text
            self.store.unit_annotation[unit_id] = annotation
            self.store.unit_attr_claims[unit_id] = _extract_attr_claims(segment.text)
            self.store.unit_ingested_at[unit_id] = ingested_at
            if observed_at:
                self.store.unit_observed_at[unit_id] = str(observed_at)
            self.store.unit_content_time[unit_id] = extract_content_time(
                raw=raw,
                unit_id=unit_id,
                text=segment.text,
                title=raw.title,
                heading_path=segment.heading_path,
                locator={"page": segment.page, "anchor": segment.anchor, "timecode": segment.timecode},
                ingested_at=ingested_at,
            )
            self.store.evidence[evidence_id] = evidence
            self.store.evidence_by_unit[unit_id].append(evidence_id)
            self.store.evidence_by_document[document.document_id].append(evidence_id)
            self.store.unit_vector[unit_id] = self.runtime.embed(segment.text)
            self.store.unit_tree[unit_id] = f"knowledge.{annotation.domain}"
            units.append(unit)
            annotations[unit_id] = annotation

        # 概念锚点 -> M1 拓扑 inner 层
        nodes = self.ontology.attach_to_topology(
            self.store.payload, units, annotations, self.store.evidence_by_unit, self.ruler, day_key
        )
        for node in nodes:
            self.store.nodes[node.node_id] = node
            self.store.ring_nodes[(node.tree_id, day_key)].append(node.node_id)

        # 低风险稳定模式 -> 留痕层（aggregate_key 不含日期）并自动生长
        touched_trees: set[str] = set()
        for unit in units:
            tree_id = self.store.unit_tree[unit.unit_id]
            vector = self.store.unit_vector[unit.unit_id]
            if not vector:
                continue
            aggregate_key = (
                f"knowledge://{tree_id}/{aggregate_subject_identity(unit.concept_path)}"
                f"/{ANNOTATE_MODEL_VERSION}"
            )
            trace_id = "ktr-" + _digest(unit.unit_id, aggregate_key)[:16]
            trace = TraceRecord(
                trace_id=trace_id,
                aggregate_key=aggregate_key,
                vector=list(vector),
                source_ref=f"knowledge://unit/{unit.unit_id}",
                created_at=_now_iso(),
            )
            self.store.traces[trace_id] = trace
            self.store.trace_by_aggregate[aggregate_key].append(trace_id)
            self.store.trace_by_unit[unit.unit_id] = trace_id
            self.growth.assign(trace, tree_id)
            touched_trees.add(tree_id)
        for tree_id in sorted(touched_trees):
            self.growth.commit(tree_id, day_key)

        # 冲突检测（并列保留，不裁决）
        self._detect_conflicts(document, units)
        return units

    # ---- ④ gate ----------------------------------------------------------
    def gate(
        self, raw: KnowledgeInput, document: CanonicalDocument, units: list[KnowledgeUnit]
    ) -> list[GateRecord]:
        records: list[GateRecord] = []
        triggers: list[str] = []
        # Subject correction must never bypass the original evidence risk gate.
        classification = self.store.document_classification.get(document.document_id, {})
        for candidate in classification.get("candidates", []):
            if candidate["eligible"] and domain_risk(candidate["domain"]) == "high":
                triggers.append(f"原文高风险领域「{domain_label(candidate['domain'])}」")
        for unit in units:
            annotation = self.store.unit_annotation.get(unit.unit_id)
            risk = domain_risk(annotation.domain) if annotation else "low"
            if risk == "high":
                triggers.append(f"高风险领域「{domain_label(annotation.domain)}」")
            for related in annotation.related_domains if annotation else []:
                if domain_risk(related) == "high":
                    triggers.append(f"关联高风险领域「{domain_label(related)}」")
        if raw.risk_domain and domain_risk(raw.risk_domain) == "high":
            triggers.append(f"显式高风险域「{domain_label(raw.risk_domain)}」")
        text = raw.content or ""
        for keyword in _HIGH_RISK_KEYWORDS:
            if keyword in text:
                triggers.append(f"高风险语义「{keyword}」")
        for flag in _IRREVERSIBLE_FLAGS:
            if raw.metadata.get(flag) or raw.metadata.get("publish_level"):
                triggers.append(f"发布级别/不可逆标记「{flag}」")
        # 显式请求冲突裁决时进受控复核（本系统默认不裁决、只并列）
        if raw.metadata.get("adjudicate"):
            triggers.append("请求冲突裁决需人工复核（不得静默选择来源）")

        triggers = list(dict.fromkeys(triggers))
        if not triggers:
            return records

        reason = "；".join(triggers)
        gate_id = "kg-" + _digest(document.document_id, reason)[:16]
        record = GateRecord(
            gate_id=gate_id,
            document_id=document.document_id,
            unit_id=None,
            risk="high",
            reason=reason,
            status="pending_review",
            created_at=_now_iso(),
        )
        self.store.gates[gate_id] = record
        self.store.gate_by_document[document.document_id].append(gate_id)
        records.append(record)
        return records

    # ---- ⑤ publish -------------------------------------------------------
    def publish(self, document_id: str) -> DocumentPublication:
        """写知识版本 + 证据索引 + 外链；有未决门禁则保持 ``pending_review``。"""
        pub = self.store.publications.get(document_id)
        if pub is None:
            pub = DocumentPublication(document_id=document_id)

        gate_ids = self.store.gate_by_document.get(document_id, [])
        pub.gate_ids = list(gate_ids)
        pending = [
            self.store.gates[gid]
            for gid in gate_ids
            if self.store.gates[gid].status == "pending_review"
        ]
        if pending:
            pub.status = "pending_review"
            self.store.publications[document_id] = pub
            return pub

        if any(self.store.gates[gid].status == "rejected" for gid in gate_ids):
            pub.status = "rejected"
            self.store.publications[document_id] = pub
            return pub

        evidence_ids = self.store.evidence_by_document.get(document_id, [])
        index_ref = self.store.payload.put(
            "outer",
            {
                "kind": "knowledge-evidence-index",
                "document_id": document_id,
                "version": self.store.documents[document_id].parse_version,
                "evidence_ids": list(evidence_ids),
            },
        )
        if pub.published_at is not None and pub.evidence_index_ref is not None:
            pub.history = [*pub.history, pub.evidence_index_ref]
        pub.status = "published"
        pub.published_at = _now_iso()
        pub.evidence_index_ref = index_ref
        pub.external_links = [
            f"knowledge://document/{document_id}",
            *[f"knowledge://evidence/{eid}" for eid in evidence_ids],
        ]
        self.store.publications[document_id] = pub
        return pub

    def review(
        self,
        document_id: str,
        *,
        action: str,
        reviewer: str | None = None,
        reason: str | None = None,
    ) -> DocumentPublication:
        """受控复核：approve / reject / rollback。"""
        if action not in ("approve", "reject", "rollback"):
            raise ValueError(f"未知复核动作：{action!r}")

        gate_ids = self.store.gate_by_document.get(document_id, [])
        if action in ("approve", "reject") and not gate_ids:
            gate_id = "kg-" + _digest(document_id, "manual")[:16]
            self.store.gates[gate_id] = GateRecord(
                gate_id=gate_id,
                document_id=document_id,
                risk="high",
                reason="人工复核",
                status="pending_review",
                created_at=_now_iso(),
            )
            self.store.gate_by_document[document_id].append(gate_id)
            gate_ids = [gate_id]

        if action == "approve":
            for gid in gate_ids:
                gate = self.store.gates[gid]
                gate.status = "approved"
                gate.decided_at = _now_iso()
                gate.reviewer = reviewer
                gate.decision_reason = reason
            return self.publish(document_id)

        if action == "reject":
            for gid in gate_ids:
                gate = self.store.gates[gid]
                gate.status = "rejected"
                gate.decided_at = _now_iso()
                gate.reviewer = reviewer
                gate.decision_reason = reason
            pub = self.store.publications.get(document_id) or DocumentPublication(
                document_id=document_id
            )
            pub.status = "rejected"
            self.store.publications[document_id] = pub
            return pub

        # rollback：恢复待复核
        for gid in gate_ids:
            gate = self.store.gates[gid]
            gate.status = "pending_review"
            gate.decided_at = None
            gate.reviewer = None
            gate.decision_reason = None
        pub = self.store.publications.get(document_id) or DocumentPublication(
            document_id=document_id
        )
        pub.status = "pending_review"
        self.store.publications[document_id] = pub
        return pub

    # ---- 编排 -------------------------------------------------------------
    def run(
        self,
        raw: KnowledgeInput,
        *,
        stage_callback: Callable[[str], None] | None = None,
    ) -> CanonicalDocument:
        def notify(stage: str) -> None:
            if stage_callback is not None:
                stage_callback(stage)

        document = CanonicalDocument(
            document_id=self._document_id(raw),
            source_id=raw.source.source_id,
            title=raw.title,
            parse_version=raw.parse_version,
            lang=raw.lang,
            content_hash=hashlib.sha256((raw.content or "").encode("utf-8")).hexdigest(),
            source_object_ref=raw.metadata.get("source_object_ref"),
            ingest_job_id=raw.metadata.get("ingest_job_id"),
            document_status="validating",
        )
        self.store.documents[document.document_id] = document
        self.store.sources[raw.source.source_id] = raw.source
        self.store.document_published[document.document_id] = raw.published_at

        notify("parsing")
        parsed = self.parse(raw)
        notify("segmenting")
        segments = self.segment(parsed)
        notify("indexing")
        units = self.annotate(raw, document, segments)
        notify("clustering")
        if not units:
            # 未解析出任何可引用单元（如未配置 OCR/转写）——如实留空，不伪造。
            self.store.publications[document.document_id] = DocumentPublication(
                document_id=document.document_id, status="pending_review"
            )
            document.document_status = "pending_review"
            return document

        notify("ready_to_publish")
        self.gate(raw, document, units)
        publication = self.publish(document.document_id)
        document.document_status = publication.status
        return document

    # ---- 内部 -------------------------------------------------------------
    @staticmethod
    def _document_id(raw: KnowledgeInput) -> str:
        return "kd-" + _digest(
            raw.source.source_id, raw.title or "", raw.content or "", raw.version or ""
        )[:16]

    @staticmethod
    def _time_status(raw: KnowledgeInput) -> str:
        reliable = any(
            [raw.published_at, raw.effective_at, raw.revised_at, raw.retrieved_at]
        )
        return "known" if reliable else "undetermined"

    def _topology_day(self) -> str:
        # 拓扑日圈用**注册日**（真实时间戳），与材料自身的 time_status 无关。
        return self.ruler.day_key()

    def _detect_conflicts(
        self, document: CanonicalDocument, units: list[KnowledgeUnit]
    ) -> None:
        for unit in units:
            claims = self.store.unit_attr_claims.get(unit.unit_id, [])
            if not claims:
                continue
            concept_key = "|".join(unit.concept_path)
            for other in self.store.units.values():
                if other.unit_id == unit.unit_id or other.document_id == document.document_id:
                    continue
                if "|".join(other.concept_path) != concept_key:
                    continue
                if not self.store.is_published(other.document_id):
                    continue
                other_claims = self.store.unit_attr_claims.get(other.unit_id, [])
                for subject, value in claims:
                    for o_subject, o_value in other_claims:
                        if subject != o_subject or value == o_value:
                            continue
                        self._record_conflict(unit, other, subject, value, o_value)

    def _record_conflict(
        self,
        unit: KnowledgeUnit,
        other: KnowledgeUnit,
        subject: str,
        value: str,
        o_value: str,
    ) -> None:
        conflict_id = "kc-" + _digest(unit.unit_id, other.unit_id, subject)[:16]
        if any(c.conflict_id == conflict_id for c in self.store.conflicts):
            return
        pairs = sorted(
            [(unit, value), (other, o_value)],
            key=lambda p: (self.store.documents[p[0].document_id].source_id, p[0].unit_id),
        )
        record = ConflictRecord(
            conflict_id=conflict_id,
            concept_path=list(unit.concept_path),
            subject=subject,
            evidence_ids=[self.store.evidence_by_unit[u.unit_id][0] for u, _ in pairs],
            sources=[self.store.documents[u.document_id].source_id for u, _ in pairs],
            conditions=[f"{subject}={v}" for _, v in pairs],
            published_dates=[self.store.document_published.get(u.document_id) for u, _ in pairs],
            note=f"同概念「{subject}」取值不一致（{value} vs {o_value}），并列保留、禁止静默裁决。",
        )
        self.store.conflicts.append(record)


def _extract_attr_claims(text: str) -> list[tuple[str, str]]:
    claims: list[tuple[str, str]] = []
    for match in _ATTR_RE.finditer(text or ""):
        subject = match.group(1).strip()
        value = re.sub(r"\s+", "", match.group(2))
        claims.append((subject, value))
    return claims


__all__ = [
    "ANNOTATE_MODEL_VERSION",
    "RULE_VERSION",
    "DocumentPublication",
    "GateRecord",
    "ConflictRecord",
    "KnowledgeStore",
    "KnowledgeGrowth",
    "KnowledgePipeline",
]
