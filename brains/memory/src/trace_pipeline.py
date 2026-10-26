"""M3 向量留痕与日圈管道（trace_pipeline）。

顺序：**先写留痕层（TraceRecord，聚合身份不含日期）**，再建成员引用
``OccurrenceRef``（日期只在这里出现）与日圈节点 ``TopologyNode``。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from topology.src.node import Layer, TopologyNode
from topology.src.ruler import Ruler
from topology.src.trace import OccurrenceRef, TraceRecord

from brains.memory.src.extract import Feature
from brains.memory.src.ingest import MemoryUnit
from brains.memory.src.media_store import MediaStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class TracePipeline:
    def __init__(self, store: MediaStore, ruler: Ruler) -> None:
        self.store = store
        self.ruler = ruler

    def make_trace(self, unit: MemoryUnit, feature: Feature, day_key: str) -> TraceRecord:
        """写留痕层：聚合身份**不含日期**，日期只经 OccurrenceRef 表达。"""
        tree_id = feature.tree_id
        aggregate_key = f"feature://{tree_id}/{feature.name}/{feature.model_version}"
        trace_id = "tr-" + _hash(unit.unit_id, feature.name, day_key)[:16]
        trace = TraceRecord(
            trace_id=trace_id,
            aggregate_key=aggregate_key,
            vector=list(feature.vector),
            source_ref=unit.media_ref,
            created_at=_now_iso(),
        )
        self.store.traces[trace_id] = trace
        self.store.trace_by_aggregate[aggregate_key].append(trace_id)
        return trace

    def append_occurrence(
        self, unit: MemoryUnit, feature: Feature, trace: TraceRecord, day_key: str
    ) -> tuple[OccurrenceRef, TopologyNode]:
        """写成员引用（日期在此）与日圈节点。"""
        tree_id = feature.tree_id
        ring_index = self.ruler.ring_index(tree_id, day_key)
        node_id = TopologyNode.build_id(tree_id, day_key, "unit", unit.unit_id, feature.name)
        occurrence_id = "occ-" + _hash(trace.trace_id)[:16]
        evidence_ref = f"memory://unit/{unit.unit_id}"
        payload_ref = self.store.payload.put(
            "inner",
            {
                "kind": "memory-unit",
                "unit_id": unit.unit_id,
                "tree_id": tree_id,
                "day_key": day_key,
                "feature": feature.name,
                "anchors": unit.anchors.model_dump(),
                "status": unit.status,
            },
        )
        occurrence = OccurrenceRef(
            occurrence_id=occurrence_id,
            tree_id=tree_id,
            day_key=day_key,
            node_id=node_id,
            evidence_ref=evidence_ref,
        )
        node = TopologyNode(
            node_id=node_id,
            tree_id=tree_id,
            day_key=day_key,
            ring_index=ring_index,
            layer=Layer.inner,
            generation=1,
            payload_ref=payload_ref,
            evidence_refs=[evidence_ref],
            occurrence_refs=[occurrence_id],
        )
        self.store.occurrences[occurrence_id] = occurrence
        self.store.occurrence_by_trace[trace.trace_id] = occurrence_id
        self.store.occurrence_unit_id[occurrence_id] = unit.unit_id
        self.store.nodes[node_id] = node
        self.store.ring_nodes[(tree_id, day_key)].append(node_id)
        return occurrence, node