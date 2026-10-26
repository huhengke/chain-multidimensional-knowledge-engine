"""M3 四层存储（media_store）。

1. 原始媒体库（``RawMediaStore``）：原文以哈希引用保存，无权限不暴露明文；
2. 特征与索引库（``FeatureIndexStore``）：提取特征与索引（候选，不作最终事实）；
3. 智慧树拓扑（``PayloadStore`` + ``GenerationStore`` + 日圈节点 + 留痕层成员引用）；
4. 记忆发布记录（``PublicationStore``）：确认状态、模型版本、操作者与原因。
"""

from __future__ import annotations

import hashlib
import json
from base64 import b64encode
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from topology.src.growth import GenerationStore
from topology.src.node import TopologyNode
from topology.src.payload import PayloadStore
from topology.src.star_cluster import StarClusterIndex
from topology.src.trace import OccurrenceRef, TraceRecord

from brains.memory.src.extract import Feature


class RawMediaStore:
    """原始媒体库。内容以哈希引用保存；无权限调用方拿不到明文。"""

    def __init__(self) -> None:
        self._blobs: dict[str, str] = {}
        self._scopes: dict[str, str] = {}
        self._digests: dict[str, str] = {}

    def put(self, ref: str, content: Any, permission_scope: str, source_ref: str = "") -> str:
        if isinstance(content, bytes):
            content = {
                "encoding": "base64",
                "media_type": "application/octet-stream",
                "data": b64encode(content).decode("ascii"),
            }
        canonical = json.dumps(
            {"content": content, "source_ref": source_ref},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self._blobs[ref] = canonical
        self._scopes[ref] = permission_scope
        self._digests[ref] = digest
        return ref

    def digest(self, ref: str) -> str:
        return self._digests[ref]

    def scope(self, ref: str) -> str | None:
        return self._scopes.get(ref)

    def has(self, ref: str) -> bool:
        return ref in self._blobs

    def delete(self, ref: str) -> bool:
        """按受控保留策略回收原始媒体，保留上层可审计的 unit/trace。"""
        if ref not in self._blobs:
            return False
        del self._blobs[ref]
        self._scopes.pop(ref, None)
        self._digests.pop(ref, None)
        return True

    def get(self, ref: str, allowed_scopes: set[str]) -> Any:
        if ref not in self._blobs:
            raise KeyError(f"原始媒体不存在：{ref}")
        if self._scopes[ref] not in allowed_scopes:
            raise PermissionError(f"无权访问原始媒体：{ref}")
        return json.loads(self._blobs[ref])

    def __len__(self) -> int:
        return len(self._blobs)


class FeatureIndexStore:
    """特征与索引库。只作候选召回，不作人物/活动/地点的最终事实。"""

    def __init__(self) -> None:
        self._by_ref: dict[str, Feature] = {}
        self._by_unit: dict[str, list[str]] = defaultdict(list)
        self._by_tree: dict[str, list[str]] = defaultdict(list)

    def put(self, unit_id: str, index: int, feature: Feature) -> str:
        ref = f"memory://feature/{unit_id}/{index}"
        self._by_ref[ref] = feature
        self._by_unit[unit_id].append(ref)
        self._by_tree[feature.tree_id].append(ref)
        return ref

    def for_unit(self, unit_id: str) -> list[Feature]:
        return [self._by_ref[ref] for ref in self._by_unit.get(unit_id, [])]

    def for_tree(self, tree_id: str) -> list[Feature]:
        return [self._by_ref[ref] for ref in self._by_tree.get(tree_id, [])]

    def get(self, ref: str) -> Feature:
        return self._by_ref[ref]

    def __len__(self) -> int:
        return len(self._by_ref)


class PublicationRecord(BaseModel):
    """记忆发布记录：不静默把 AI 推断升级为已确认记忆。"""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    extract_model_version: str = Field(min_length=1)
    rule_version: str = Field(min_length=1)
    operator: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    day_key: str | None = None


class PublicationStore:
    def __init__(self) -> None:
        self._records: list[PublicationRecord] = []
        self._by_unit: dict[str, list[PublicationRecord]] = defaultdict(list)

    def record(self, record: PublicationRecord) -> PublicationRecord:
        self._records.append(record)
        self._by_unit[record.unit_id].append(record)
        return record

    def for_unit(self, unit_id: str) -> list[PublicationRecord]:
        return list(self._by_unit.get(unit_id, []))

    def __len__(self) -> int:
        return len(self._records)


class MediaStore:
    """四层存储门面 + 智慧树拓扑/留痕层成员引用登记。"""

    def __init__(self) -> None:
        self.raw = RawMediaStore()
        self.features = FeatureIndexStore()
        self.publications = PublicationStore()
        # 智慧树拓扑
        self.payload = PayloadStore()
        self.generations = GenerationStore()
        # 留痕层
        self.traces: dict[str, TraceRecord] = {}
        self.trace_by_aggregate: dict[str, list[str]] = defaultdict(list)
        self.occurrences: dict[str, OccurrenceRef] = {}
        self.occurrence_by_trace: dict[str, str] = {}
        self.occurrence_unit_id: dict[str, str] = {}
        # 日圈节点
        self.nodes: dict[str, TopologyNode] = {}
        self.ring_nodes: dict[tuple[str, str], list[str]] = defaultdict(list)
        # 星团索引（按树）
        self._clusters: dict[str, StarClusterIndex] = {}
        # 记忆单元
        self.units: dict[str, Any] = {}

    def cluster_index(self, tree_id: str, tolerance: str) -> StarClusterIndex:
        index = self._clusters.get(tree_id)
        if index is None:
            index = StarClusterIndex(tree_id, tolerance=tolerance)
            self._clusters[tree_id] = index
        return index

    @property
    def clusters(self) -> dict[str, StarClusterIndex]:
        return dict(self._clusters)

    def ring_node_ids(self, tree_id: str, day_key: str) -> list[str]:
        return list(self.ring_nodes.get((tree_id, day_key), []))
