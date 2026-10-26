"""M3 多模态时空记忆脑（MemoryBrain）。

五层记忆管道：①安全接入 ingest → ②提取向量 extract → ③天地人归一 normalize →
④向量留痕与生长 trace+growth → ⑤检索与治理 recall。

遵循 ``brains.protocol`` 契约，名字为 ``BrainName.memory``。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal

from brains.protocol import BrainName, BrainQuery, BrainResult
from brains.memory.src.extract import Feature, default_registry, quantize
from brains.memory.src.growth import MemoryGrowth
from brains.memory.src.ingest import (
    IngestItem,
    MemoryPolicy,
    MemoryUnit,
)
from brains.memory.src.media_store import MediaStore, PublicationRecord
from brains.memory.src.normalize import Anchors, Normalizer
from brains.memory.src.recall import Recaller
from brains.memory.src.trace_pipeline import TracePipeline
from topology.src.ruler import Ruler

EXTRACT_MODEL_VERSION = "memory-extract-v1"
RULE_VERSION = "memory-rules-v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryBrain:
    name = BrainName.memory

    def __init__(
        self,
        ruler: Ruler | None = None,
        *,
        tolerance: str = "eps=0.05",
        growth_threshold: int = 3,
        policy: MemoryPolicy | None = None,
    ) -> None:
        self.ruler = ruler or Ruler()
        self.tolerance = tolerance
        self.growth_threshold = growth_threshold
        self.policy = policy or MemoryPolicy()
        self.store = MediaStore()
        self.registry = default_registry()
        self.normalizer = Normalizer(self.ruler)
        self.pipeline = TracePipeline(self.store, self.ruler)
        self.growth = MemoryGrowth(
            self.store, self.ruler, growth_threshold, tolerance, self.policy
        )
        self.recaller = Recaller(self.store, self.policy)

    # ---- ①-⑤ 接入管道 ----------------------------------------------------
    def ingest(self, item: IngestItem) -> MemoryUnit:
        # ① 安全接入：权限与来源记账（原文以引用保存，不暴露明文）。
        raw_content = item.raw_content if item.raw_content is not None else item.content
        self.store.raw.put(item.media_ref, raw_content, item.permission_scope, item.source_ref)

        # ② 提取向量（含未配置模态的显式不可用）。
        features = self.registry.extract_all(item)

        # ③ 天地人归一（不确定项保留 None）。
        anchors, day_key = self.normalizer.normalize(item)

        # 敏感项门禁隔离（打标 gated=True，不参与星团/生长）。
        gated_features = [self._gate(feature) for feature in features]

        unit_id = self._unit_id(item)
        primary_tree = self.registry.tree_for(item.modality)
        unit = MemoryUnit(
            unit_id=unit_id,
            media_ref=item.media_ref,
            tree_id=primary_tree,
            day_key=day_key,
            anchors=anchors,
            features=gated_features,
            confidence=self._confidence(gated_features),
            extract_model_version=EXTRACT_MODEL_VERSION,
            status=self._resolve_status(item, anchors, gated_features),
            permission_scope=item.permission_scope,
            source_ref=item.source_ref,
        )
        self.store.units[unit_id] = unit
        for index, feature in enumerate(gated_features):
            self.store.features.put(unit_id, index, feature)

        # ④ 向量留痕与生长：先写留痕层，再写日圈/周存/外链。
        touched_trees: set[str] = set()
        if day_key is not None:
            for feature in gated_features:
                if feature.gated or feature.status != "available" or not feature.vector:
                    continue
                trace = self.pipeline.make_trace(unit, feature, day_key)
                self.growth.assign(trace, feature)
                self.pipeline.append_occurrence(unit, feature, trace, day_key)
                touched_trees.add(feature.tree_id)
            for tree_id in sorted(touched_trees):
                self.growth.commit(tree_id, day_key)

        # ⑤ 记忆发布记录（不静默把 AI 推断升级为已确认记忆）。
        self.store.publications.record(
            PublicationRecord(
                unit_id=unit_id,
                status=unit.status,
                extract_model_version=EXTRACT_MODEL_VERSION,
                rule_version=RULE_VERSION,
                operator=item.trust,
                reason="ingest",
                created_at=_now_iso(),
                day_key=day_key,
            )
        )
        return unit

    # ---- 检索 -------------------------------------------------------------
    def query(self, query: BrainQuery) -> BrainResult:
        return self.recaller.recall(query)

    # ---- 内部 -------------------------------------------------------------
    @staticmethod
    def _unit_id(item: IngestItem) -> str:
        digest = hashlib.sha256(
            "|".join([item.media_ref, item.modality, str(item.timestamp), item.source_ref]).encode(
                "utf-8"
            )
        ).hexdigest()
        return "mu-" + digest[:16]

    def _gate(self, feature: Feature) -> Feature:
        if self.policy.is_sensitive(feature):
            return feature.model_copy(update={"gated": True})
        return feature

    @staticmethod
    def _confidence(features: list[Feature]) -> str:
        available = [Decimal(f.confidence) for f in features if f.status == "available"]
        if not available:
            return "0"
        return quantize(sum(available, Decimal(0)) / Decimal(len(available)))

    @staticmethod
    def _resolve_status(item: IngestItem, anchors: Anchors, features: list[Feature]) -> str:
        anchors_complete = None not in (anchors.time, anchors.place, anchors.person)
        has_available = any(f.status == "available" for f in features)
        if anchors_complete and has_available and item.trust == "user":
            return "confirmed"
        if anchors_complete and has_available and item.trust != "model":
            return "inferred"
        return "candidate"


__all__ = ["MemoryBrain"]
