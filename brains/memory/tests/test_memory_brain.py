"""M3 多模态时空记忆脑验收测试（对应验收项 01、05、10、11、12、13 等）。"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from brains.memory import ActionDescriptor, IngestItem, MemoryBrain, MemoryPolicy, MemoryUnit
from brains.memory.src.actions import ACTION_DESCRIPTOR_FIELDS, FORBIDDEN_CONTROL_FIELDS
from brains.memory.src.recall import Recaller  # noqa: F401 - 确保模块可导入
from brains.protocol import BrainQuery, Confidence, Granularity, ResultStatus, TimeScope
from topology.src.ring import DAY_KEY_RE
from topology.src.trace import contains_date


def _item(day: str, *, content="清晨在公园打太极", place="公园", person="张三", **kwargs) -> IngestItem:
    data = dict(
        media_ref=kwargs.pop("media_ref", f"memory://media/{day}"),
        modality=kwargs.pop("modality", "text"),
        content=content,
        timestamp=f"{day}T08:00:00+08:00",
        place=place,
        person=person,
        source_ref=kwargs.pop("source_ref", "camera-1"),
    )
    data.update(kwargs)
    return IngestItem(**data)


def _query(
    raw="公园 太极", *, day="2026-10-09", profile_id="alice", time_scope=None, **constraints
) -> BrainQuery:
    return BrainQuery(
        request_id="req-1",
        trace_id="tr-1",
        day_key=day,
        raw_query=raw,
        profile_id=profile_id,
        time_scope=time_scope,
        constraints=constraints,
    )


# ---- 01 五层管道端到端 -------------------------------------------------
def test_01_pipeline_end_to_end():
    brain = MemoryBrain(growth_threshold=3)
    unit = brain.ingest(_item("2026-10-09"))

    assert isinstance(unit, MemoryUnit)
    assert unit.day_key == "2026-10-09"
    assert unit.anchors.place == "公园"
    # 留痕层有 TraceRecord
    assert brain.store.traces
    # 有 OccurrenceRef 成员引用
    assert brain.store.occurrences
    # 日圈节点写入
    assert any(node.tree_id == "memory.text" for node in brain.store.nodes.values())
    # generation 已提交
    assert brain.store.generations.history("memory.text")


# ---- 02 留痕不变式 -----------------------------------------------------
def test_02_trace_aggregate_key_has_no_date():
    brain = MemoryBrain()
    for day in ("2026-10-07", "2026-10-08", "2026-10-09"):
        brain.ingest(_item(day, media_ref=f"memory://media/{day}"))
    assert brain.store.traces
    for trace in brain.store.traces.values():
        assert "://" in trace.aggregate_key
        assert not contains_date(trace.aggregate_key)
    # 日期只在 OccurrenceRef.day_key
    for occurrence in brain.store.occurrences.values():
        assert DAY_KEY_RE.match(occurrence.day_key)


# ---- 03 不伪造未知 -----------------------------------------------------
def test_03_unknown_anchors_are_none():
    brain = MemoryBrain()
    unit = brain.ingest(
        _item("2026-10-09", place=None, person=None, media_ref="memory://media/unknown-anchor")
    )
    assert unit.anchors.place is None
    assert unit.anchors.person is None
    assert unit.anchors.time == "2026-10-09"
    # 绝不填默认值
    assert unit.anchors.place != "unknown"
    assert unit.anchors.person != "unknown"

    # 时间也未知 -> day_key 与 anchors.time 均为 None，且不写留痕/日圈（无日期可依附）
    unit2 = brain.ingest(
        IngestItem(
            media_ref="memory://media/unknown-time",
            modality="text",
            content="一段无时间戳的记录",
            timestamp=None,
            source_ref="note",
        )
    )
    assert unit2.day_key is None
    assert unit2.anchors.time is None
    assert all(o.day_key != None for o in brain.store.occurrences.values())  # noqa: E711


# ---- 04 星团跨日聚合 ---------------------------------------------------
def test_04_cluster_aggregates_across_days():
    brain = MemoryBrain(growth_threshold=10)
    for day in ("2026-10-07", "2026-10-08", "2026-10-09"):
        brain.ingest(_item(day, media_ref=f"memory://media/{day}"))

    index = brain.store.clusters["memory.text"]
    assert len(index) == 1
    cluster = next(iter(index.clusters.values()))
    assert cluster.occurrence_count == 3

    days: set[str] = set()
    for trace_id in cluster.member_trace_ids:
        occurrence_id = brain.store.occurrence_by_trace[trace_id]
        occurrence = brain.store.occurrences[occurrence_id]
        assert occurrence.node_id in brain.store.nodes  # 可反查日圈节点
        assert occurrence.evidence_ref.startswith("memory://unit/")  # 可反查证据
        days.add(occurrence.day_key)
    assert days == {"2026-10-07", "2026-10-08", "2026-10-09"}


# ---- 05 生长阈值与旧代可读 ---------------------------------------------
def test_05_growth_threshold_creates_new_generation():
    brain = MemoryBrain(growth_threshold=3)
    for day in ("2026-10-07", "2026-10-08", "2026-10-09"):
        brain.ingest(_item(day, media_ref=f"memory://media/{day}"))

    history = brain.store.generations.history("memory.text")
    assert len(history) >= 2
    assert history[0].generation == 1
    newest = history[-1]
    assert newest.generation >= 2 and newest.parent is not None
    # 旧 generation 仍可读
    assert brain.store.generations.get("memory.text", 1) is history[0]
    # 分枝节点存在
    assert any("/cluster/" in node_id for node_id in newest.nodes)


# ---- 06 敏感项隔离 -----------------------------------------------------
def test_06_sensitive_features_are_gated_and_excluded():
    brain = MemoryBrain()
    unit = brain.ingest(
        IngestItem(
            media_ref="memory://media/gps",
            modality="precise_location",
            timestamp="2026-10-09T08:00:00+08:00",
            source_ref="gps-device",
            metadata={
                "gps": {"lat": "31.230416", "lon": "121.473701"},
                "voiceprint": ["0.10", "0.20", "0.30"],
            },
        )
    )
    location = next(f for f in unit.features if f.kind == "precise_location")
    voiceprint = next(f for f in unit.features if f.kind == "voiceprint")
    assert location.gated is True
    assert voiceprint.gated is True
    # 被门禁拦下 -> 无留痕、无星团
    assert len(brain.store.traces) == 0
    assert brain.store.clusters == {}


# ---- 07 “我记得”边界 ---------------------------------------------------
def test_07_fast_recall_boundary():
    policy = MemoryPolicy(fast_recall_min_occurrence=3, max_dispersion="0.05")
    # 策略层：未达阈值 / 离散度过大 / 无权限 -> 均不可 fast_recall
    assert policy.is_fast_recall(2, "0.0", True) is False
    assert policy.is_fast_recall(5, "0.9", True) is False
    assert policy.is_fast_recall(5, "0.0", False) is False
    assert policy.is_fast_recall(5, "0.0", True) is True

    brain = MemoryBrain(growth_threshold=3, policy=policy)
    for day in ("2026-10-07", "2026-10-08"):
        brain.ingest(_item(day, media_ref=f"memory://media/{day}"))

    pending = brain.query(_query())
    assert pending.status is ResultStatus.ok
    assert any(claim.field == "memory.cluster.pending" for claim in pending.claims)
    assert not any(claim.field == "memory.cluster" for claim in pending.claims)

    brain.ingest(_item("2026-10-09", media_ref="memory://media/2026-10-09"))
    fast = brain.query(_query())
    assert any(claim.field == "memory.cluster" for claim in fast.claims)

    # 无权限 -> 只能待核对
    no_permission = brain.query(_query(profile_id=None))
    assert not any(claim.field == "memory.cluster" for claim in no_permission.claims)
    assert any(claim.field == "memory.cluster.pending" for claim in no_permission.claims)


# ---- 08 未配置模态不编造 -----------------------------------------------
def test_08_unconfigured_modality_is_unavailable():
    brain = MemoryBrain()
    unit = brain.ingest(
        IngestItem(
            media_ref="memory://media/asr",
            modality="asr",
            timestamp="2026-10-09T08:00:00+08:00",
            source_ref="microphone",
        )
    )
    asr = next(f for f in unit.features if f.kind == "asr")
    assert asr.status == "unavailable"
    assert asr.reason == "未配置 ASR 引擎"
    assert asr.vector == []
    # 未配置模态未产生留痕
    assert len(brain.store.traces) == 0


# ---- 09 无命中 ---------------------------------------------------------
def test_09_no_match_returns_empty_claims():
    brain = MemoryBrain()
    brain.ingest(_item("2026-10-09"))

    missing_place = brain.query(_query("量子实验室", place="南极"))
    assert missing_place.status is ResultStatus.no_match
    assert missing_place.claims == []

    far_future = brain.query(
        _query(
            "公园 太极",
            time_scope=TimeScope(
                start="2030-01-01",
                end="2030-12-31",
                granularity=Granularity.day,
                confidence=Confidence.exact,
            ),
        )
    )
    assert far_future.status is ResultStatus.no_match
    assert far_future.claims == []


# ---- 10 动作特征结构化（不生成控制代码）--------------------------------
def test_10_action_feature_is_structured_only():
    brain = MemoryBrain()
    descriptor = ActionDescriptor(
        action_segment="太极云手",
        keypoint_series_ref="kp://series/1",
        pose_stage="起势",
        direction="顺时针",
        rhythm="缓慢",
        duration_seconds="12.5",
        confidence="0.9",
        camera_condition="正面固定机位",
        source_timecode="00:01:23",
    )
    unit = brain.ingest(
        IngestItem(
            media_ref="memory://media/action",
            modality="action",
            timestamp="2026-10-09T08:00:00+08:00",
            source_ref="camera-2",
            metadata={"action": descriptor.model_dump()},
        )
    )
    action = next(f for f in unit.features if f.kind == "action")
    assert action.status == "available"
    assert action.detail["action_segment"] == "太极云手"
    assert set(action.detail).issubset(set(ACTION_DESCRIPTOR_FIELDS))
    assert not (set(action.detail) & FORBIDDEN_CONTROL_FIELDS)
    assert Decimal(action.confidence) == Decimal("0.9")

    # 控制指令字段被拒绝（不生成控制代码）
    with pytest.raises(ValidationError):
        ActionDescriptor(action_segment="太极云手", command="move_forward")