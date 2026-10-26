"""M3 检索与治理（recall）。

先命中星团（时间无关聚合）→ 按时间范围/地点/人物/词项过滤成员引用 →
回到日圈/树/节点/证据。区分 ``fast_recall``（达阈值 + 有权限）与
``pending``（待核对线索）。**记忆脑不产出数值指标型 claim**。
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from brains.protocol import (
    BrainName,
    BrainQuery,
    BrainResult,
    Claim,
    EvidenceRef,
    ResultStatus,
)
from brains.memory.src.extract import tokenize
from brains.memory.src.ingest import MemoryPolicy, MemoryUnit
from brains.memory.src.media_store import MediaStore
from brains.memory.src.normalize import clean_label


class Recaller:
    def __init__(self, store: MediaStore, policy: MemoryPolicy) -> None:
        self.store = store
        self.policy = policy

    # ---- 内部 -------------------------------------------------------------
    def _search_text(self, unit: MemoryUnit) -> str:
        parts: list[str] = []
        if unit.anchors.place:
            parts.append(unit.anchors.place)
        if unit.anchors.person:
            parts.append(unit.anchors.person)
        for feature in unit.features:
            if feature.gated:
                continue
            for key in ("tokens", "top_keywords"):
                value = feature.detail.get(key)
                if isinstance(value, list):
                    parts.extend(str(item) for item in value)
            for key in ("action_segment", "pose_stage", "direction", "rhythm"):
                value = feature.detail.get(key)
                if isinstance(value, str):
                    parts.append(value)
        return " ".join(parts).lower()

    def _feature_label(self, cluster) -> str:
        for trace_id in cluster.member_trace_ids:
            trace = self.store.traces.get(trace_id)
            if trace is None:
                continue
            tail = trace.aggregate_key.split("://", 1)[-1].split("/")
            if len(tail) > 1:
                return tail[1]
            return tail[0]
        return "memory"

    def _match_members(self, cluster, query: BrainQuery, constraints: dict):
        """返回 (day_key, unit, occurrence) 列表；无匹配则空。"""
        place_filter = clean_label(constraints.get("place"))
        person_filter = clean_label(constraints.get("person"))
        tree_filter = constraints.get("tree_id")
        tokens = [t for t in tokenize(query.raw_query) if t]
        time_scope = query.time_scope

        members: list[tuple[str, MemoryUnit, object]] = []
        for trace_id in dict.fromkeys(cluster.member_trace_ids):
            occurrence_id = self.store.occurrence_by_trace.get(trace_id)
            if occurrence_id is None:
                continue
            occurrence = self.store.occurrences[occurrence_id]
            unit_id = self.store.occurrence_unit_id.get(occurrence_id)
            unit = self.store.units.get(unit_id)
            if unit is None:
                continue
            if tree_filter and unit.tree_id != tree_filter:
                continue
            if time_scope is not None and not (time_scope.start <= occurrence.day_key <= time_scope.end):
                continue
            if place_filter and (not unit.anchors.place or place_filter not in unit.anchors.place):
                continue
            if person_filter and (not unit.anchors.person or person_filter not in unit.anchors.person):
                continue
            if tokens:
                text = self._search_text(unit)
                if not any(token in text for token in tokens):
                    continue
            members.append((occurrence.day_key, unit, occurrence))
        return members

    # ---- 对外 -------------------------------------------------------------
    def recall(self, query: BrainQuery) -> BrainResult:
        constraints = query.constraints or {}
        claims: list[Claim] = []
        evidence: list[EvidenceRef] = []
        warnings: list[str] = []

        for tree_id in sorted(self.store.clusters):
            index = self.store.clusters[tree_id]
            for cluster_id in sorted(index.clusters):
                cluster = index.clusters[cluster_id]
                members = self._match_members(cluster, query, constraints)
                if not members:
                    continue

                places = [unit.anchors.place for _, unit, _ in members if unit.anchors.place]
                persons = [unit.anchors.person for _, unit, _ in members if unit.anchors.person]
                label_parts: list[str] = []
                if places:
                    label_parts.append(Counter(places).most_common(1)[0][0])
                if persons:
                    label_parts.append(Counter(persons).most_common(1)[0][0])
                if not label_parts:
                    label_parts.append(self._feature_label(cluster))
                label = "·".join(label_parts)

                permission_ok = any(
                    self.policy.has_permission(query.profile_id, unit.permission_scope)
                    for _, unit, _ in members
                )
                fast = self.policy.is_fast_recall(
                    cluster.occurrence_count, cluster.dispersion, permission_ok
                )

                cluster_evidence_id = f"ev-memory-cluster-{cluster.cluster_id}"
                evidence.append(
                    EvidenceRef(
                        evidence_id=cluster_evidence_id,
                        source=BrainName.memory,
                        ref=f"memory://cluster/{cluster.cluster_id}",
                        detail=f"{label} · 命中 {cluster.occurrence_count} 次 · 成员 {len(members)}",
                    )
                )
                for i, (day_key, unit, _) in enumerate(members):
                    evidence.append(
                        EvidenceRef(
                            evidence_id=f"ev-memory-unit-{unit.unit_id}-{i}",
                            source=BrainName.memory,
                            ref=f"memory://unit/{unit.unit_id}",
                            detail=f"day={day_key}; source={unit.source_ref}",
                        )
                    )

                if fast:
                    claims.append(
                        Claim(
                            field="memory.cluster",
                            value=f"{label} · 命中 {cluster.occurrence_count} 次",
                            source=BrainName.memory,
                            evidence_id=cluster_evidence_id,
                        )
                    )
                else:
                    claims.append(
                        Claim(
                            field="memory.cluster.pending",
                            value=f"待核对线索：{label} · 命中 {cluster.occurrence_count} 次",
                            source=BrainName.memory,
                            evidence_id=cluster_evidence_id,
                        )
                    )
                    warnings.append(
                        f"记忆线索未达可信阈值或权限不足，仅作待核对：{cluster.cluster_id}"
                    )

        status = ResultStatus.ok if claims else ResultStatus.no_match
        if status is ResultStatus.no_match:
            return BrainResult(
                brain=BrainName.memory,
                request_id=query.request_id,
                trace_id=query.trace_id,
                day_key=query.day_key,
                status=status,
                claims=[],
                evidence=[],
                warnings=warnings,
            )
        return BrainResult(
            brain=BrainName.memory,
            request_id=query.request_id,
            trace_id=query.trace_id,
            day_key=query.day_key,
            status=status,
            claims=claims,
            evidence=evidence,
            warnings=warnings,
        )