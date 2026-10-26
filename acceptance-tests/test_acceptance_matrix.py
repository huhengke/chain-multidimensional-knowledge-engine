"""M8 全量验收矩阵可执行验证（对应实施计划第 13 节 28 项）。

每项一个测试函数 ``test_acNN_<short>``，docstring 写明验收原文。
所有断言均基于各模块（M1–M7）**真实行为**，不使用同义反复或空断言。

依赖约定：工程根 ``conftest.py`` 已把工程根与 ``control-plane/src`` 加入
``sys.path``，故可像既有测试一样直接导入 ``topology`` / ``brains`` /
``orchestration`` / ``governance`` 以及 M2 顶层模块（``service`` / ``api`` / ...）。
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from api import create_app
from brains.knowledge import KnowledgeBrain, KnowledgeInput, KnowledgeSource
from brains.memory import IngestItem, MemoryBrain, MemoryPolicy
from brains.protocol import (
    BrainName,
    BrainQuery,
    BrainResult,
    Claim,
    Confidence,
    Granularity,
    MergeMode,
    ResultStatus,
    TimeScope,
)
from brains.structured import HttpEngineClient, StructuredBrain
from governance.src import (
    SCOPE_COGNITION_QUERY,
    SCOPE_MEMORY_MEDIA_READ,
    GateStatus,
    Governance,
    GovernanceDeniedError,
    Principal,
    default_policy,
    guard_media_access,
)
from merge import Merger
from orchestration.src.orchestrator import Orchestrator
from profile import ProfileRegistry, ProfileUnavailableError
from service import CognitiveRequest, CognitionService
from topology.src.growth import GenerationStore, GrowthEngine, TopologyGeneration
from topology.src.node import Layer, TopologyNode
from topology.src.payload import LAYER_STORE, PayloadStore
from topology.src.ring import DAY_KEY_RE, RingRef
from topology.src.ruler import Ruler
from topology.src.star_cluster import StarClusterIndex
from topology.src.trace import (
    OccurrenceRef,
    TraceRecord,
    assert_no_date_in_aggregate_key,
    contains_date,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = ROOT / "docs" / "contracts"
WEKNORA_DIR = ROOT / "third_party" / "weknora"
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

DAY = "2026-10-11"
TS = "2026-10-11T09:30:00+08:00"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2}|(?<!\d)\d{8}(?!\d)")

MANUAL = (
    "# 设备维护手册\n"
    "## 温度控制\n"
    "设备运行温度应保持在合理区间。\n"
    "本段说明温度监控要点。\n"
    "## 润滑规范\n"
    "每月对运动部件进行润滑保养。\n"
)


# --------------------------------------------------------------------------- #
# 构造帮助函数（真实模块）
# --------------------------------------------------------------------------- #
def _memory_item(day, *, content="清晨在公园打太极", place="公园", person="张三", **over) -> IngestItem:
    data = dict(
        media_ref=f"memory://media/{day}",
        modality="text",
        content=content,
        timestamp=f"{day}T08:00:00+08:00",
        place=place,
        person=person,
        source_ref="camera-1",
    )
    data.update(over)
    return IngestItem(**data)


def _memory_with_cluster(days=("2026-10-07", "2026-10-08", "2026-10-09")) -> MemoryBrain:
    brain = MemoryBrain(growth_threshold=3)
    for day in days:
        brain.ingest(_memory_item(day))
    return brain


def _brain_query(raw="公园 太极", *, day=DAY, profile_id="alice", **constraints) -> BrainQuery:
    return BrainQuery(
        request_id="r-1",
        trace_id="t-1",
        day_key=day,
        raw_query=raw,
        profile_id=profile_id,
        constraints=constraints,
    )


def _kinput(content, *, raw_id="r1", source_id="src-1", **over) -> KnowledgeInput:
    data = dict(
        raw_id=raw_id,
        source=KnowledgeSource(source_id=source_id, kind="note", uri=f"file://{raw_id}/{raw_id}.md"),
        content=content,
        media_kind="text",
        title="示例文档",
        permission_scope=["public"],
        metadata={},
    )
    data.update(over)
    return KnowledgeInput(**data)


def _knowledge_published(
    content="# 太极规则\n公园太极练习规则与要点。", *, raw_id="taiji"
) -> KnowledgeBrain:
    brain = KnowledgeBrain()
    document = brain.ingest(_kinput(content, raw_id=raw_id))
    brain.publish(document.document_id)
    return brain


def _structured_ok_response() -> dict:
    return {
        "trace_id": "tr-s1",
        "actual": {
            "dataset": "oil_monthly_sales",
            "available": True,
            "record_count": 3,
            "aggregations": {"monthlysales": "12345.67", "monthlyqty": "999.00"},
        },
        "warnings": [],
    }


class FakeTransport:
    """注入式传输：不发真实网络请求。"""

    def __init__(self, response: dict | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return 200, self.response


def _structured_brain(response: dict | None = None) -> StructuredBrain:
    client = HttpEngineClient(
        base_url="http://engine.test:8000",
        transport=FakeTransport(response if response is not None else _structured_ok_response()),
    )
    return StructuredBrain(client)


class _StubBrain:
    """契约兼容的桩 Brain（用于「异类不相加」等需要精确构造 claim 的验收项）。"""

    def __init__(self, name, *, claims=(), evidence=(), status: ResultStatus = ResultStatus.ok) -> None:
        self.name = name
        self._claims = list(claims)
        self._evidence = list(evidence)
        self._status = status

    def query(self, query: BrainQuery) -> BrainResult:
        return BrainResult(
            brain=self.name,
            request_id=query.request_id,
            trace_id=query.trace_id,
            day_key=query.day_key,
            status=self._status,
            claims=list(self._claims),
            evidence=list(self._evidence),
        )


def _real_orchestrator() -> Orchestrator:
    return Orchestrator(
        brains={
            BrainName.memory: _memory_with_cluster(),
            BrainName.knowledge: _knowledge_published(),
            BrainName.structured: _structured_brain(),
        }
    )


_ALL = [BrainName.memory, BrainName.knowledge, BrainName.structured]


# --------------------------------------------------------------------------- #
# 01 同尺一致
# --------------------------------------------------------------------------- #
def test_ac01_same_day_key_across_three_brains():
    """验收 01｜M1 同尺一致：同一请求内三脑 day_key 一致。"""
    result = _real_orchestrator().run(
        request_id="r-ac01",
        trace_id="t-ac01",
        day_key=DAY,
        raw_query="公园太极油品销售规则",
        profile_id="alice",
        targets=_ALL,
    )
    per_brain = result.verification.per_brain
    assert set(per_brain) == {"memory", "knowledge", "structured"}
    assert {r["day_key"] for r in per_brain.values()} == {DAY}
    assert result.day_key == DAY


# --------------------------------------------------------------------------- #
# 02 每天一圈
# --------------------------------------------------------------------------- #
def test_ac02_one_ring_per_day_unique_and_monotonic():
    """验收 02｜M1 每天一圈：(tree_id, day_key) 生成唯一 ring_index 且单调递增。"""
    ruler = Ruler()
    tree = "structured.oil_sales_daily"
    days = ("2026-10-09", "2026-10-10", "2026-10-11")
    idx = [ruler.ring_index(tree, d) for d in days]
    assert idx == [0, 1, 2]
    assert len(set(idx)) == len(idx)  # 唯一
    assert idx[0] < idx[1] < idx[2]  # 单调递增
    assert ruler.ring_index(tree, DAY) == idx[2]  # 幂等
    with pytest.raises(ValueError):
        ruler.ring_index(tree, "2026-10-08")  # 不允许插入更早日期
    ref = RingRef(tree_id=tree, day_key=DAY, ring_index=idx[2])
    assert ref.ring_index == 2
    # 不同树各自独立编号
    other = [ruler.ring_index("memory.text", d) for d in days]
    assert other == [0, 1, 2]


# --------------------------------------------------------------------------- #
# 03 三层载荷
# --------------------------------------------------------------------------- #
def test_ac03_three_layer_payload_no_extra_query_layer():
    """验收 03｜M1 三层载荷：节点可表达 inner/middle/outer；DNA 外链不新增业务查询层。"""
    assert set(Layer) == {Layer.inner, Layer.middle, Layer.outer}
    assert dict(LAYER_STORE) == {
        "inner": "inner_chain",
        "middle": "middle_store",
        "outer": "outer_chain",
    }
    store = PayloadStore()
    tree = "memory.voice"
    for layer, expected_store in (
        (Layer.inner, "inner_chain"),
        (Layer.middle, "middle_store"),
        (Layer.outer, "outer_chain"),
    ):
        ref = store.put(layer.value, {"layer": layer.value})
        node = TopologyNode(
            node_id=TopologyNode.build_id(tree, DAY, layer.value),
            tree_id=tree,
            day_key=DAY,
            ring_index=0,
            layer=layer,
            payload_ref=ref,
        )
        assert node.store == expected_store
        assert node.payload_ref.startswith(f"store://{expected_store}/")
        assert node.node_id != node.payload_ref  # 身份与载荷分离
    # DNA 外链（outer）只描述物理拓扑，不新增业务查询层
    outer_ref = store.put("outer", {"media": "s3://raw/clip-1"})
    assert store.get(outer_ref) == {"media": "s3://raw/clip-1"}
    assert "layer" in TopologyNode.model_fields
    assert not ({"query_layer", "dna_layer", "business_layer"} & set(TopologyNode.model_fields))


# --------------------------------------------------------------------------- #
# 04 留痕不变式
# --------------------------------------------------------------------------- #
def test_ac04_aggregate_key_must_not_contain_date():
    """验收 04｜M1 留痕不变式：aggregate_key 不含日期（正则断言）；日期仅在 occurrence_ref。"""
    brain = MemoryBrain()
    for day in ("2026-10-07", "2026-10-08", "2026-10-09"):
        brain.ingest(_memory_item(day))
    assert brain.store.traces
    for trace in brain.store.traces.values():
        assert DATE_RE.search(trace.aggregate_key) is None
        assert not contains_date(trace.aggregate_key)
        assert assert_no_date_in_aggregate_key(trace.aggregate_key) == trace.aggregate_key
    assert "day_key" not in TraceRecord.model_fields  # 留痕层不含日期字段
    assert "day_key" in OccurrenceRef.model_fields  # 日期只在成员引用
    for occurrence in brain.store.occurrences.values():
        assert DAY_KEY_RE.match(occurrence.day_key)
    for bad in ("feature://x/2026-10-11", "feature://x/2026/10/11", "feature://x/20261011"):
        with pytest.raises(ValueError):
            assert_no_date_in_aggregate_key(bad)
        with pytest.raises(ValueError):
            TraceRecord(
                trace_id="tr-bad",
                aggregate_key=bad,
                vector=["0.1"],
                source_ref="memory://m",
                created_at="now",
            )


# --------------------------------------------------------------------------- #
# 05 不可变 generation
# --------------------------------------------------------------------------- #
def test_ac05_immutable_generation_grow_and_rollback():
    """验收 05｜M1 不可变 generation：超阈值分枝重建 generation，旧代仍可读、可回滚。"""
    store = PayloadStore()
    ruler = Ruler()
    tree = "memory.voice"
    ring_index = ruler.ring_index(tree, DAY)

    index = StarClusterIndex(tree, tolerance="eps=0.05")
    for i in range(1, 6):
        index.assign(
            TraceRecord(
                trace_id=f"tr-{i}",
                aggregate_key="feature://acoustic-signature/9f2c1d",
                vector=["0.10", "0.20"] if i == 1 else ["0.11", "0.19"],
                source_ref=f"memory://media/{i}",
                created_at="2026-10-11T00:00:00Z",
            )
        )
    cluster = index.get("sc-voice-1")
    assert cluster.occurrence_count == 5

    engine = GrowthEngine(growth_threshold=3, growth_dimensions=["time", "place", "person"])
    assert engine.triggers(cluster)
    gen1 = TopologyGeneration(generation=1, tree_id=tree, nodes={})
    gen2 = engine.grow(gen1, [cluster], store, DAY, ring_index)
    assert gen2.generation == 2
    assert gen1.generation == 1  # 旧代未被就地修改
    assert len(gen2) > len(gen1)
    root_id = TopologyNode.build_id(tree, DAY, "cluster", cluster.cluster_id)
    assert len(gen2.node(root_id).children) == 3
    with pytest.raises(AttributeError):
        gen2.generation = 99  # type: ignore[misc]

    gs = GenerationStore()
    gs.commit(gen1)
    gs.commit(gen2)
    assert gs.current(tree).generation == 2
    assert gs.get(tree, 1) is gen1  # 旧代仍可读
    rolled = gs.rollback(tree, 1)
    assert rolled is gen1
    assert gs.current(tree) is gen1
    assert gs.get(tree, 2) is gen2  # 历史代保留


# --------------------------------------------------------------------------- #
# 06 配置档冻结
# --------------------------------------------------------------------------- #
def test_ac06_profile_frozen_within_request():
    """验收 06｜M2 配置档冻结：请求期内配置档不变；并发请求各自固定。"""
    reg = ProfileRegistry()
    pin_a1 = reg.freeze("req-A")
    pin_a2 = reg.freeze("req-A")
    assert pin_a1 is pin_a2  # 请求期内冻结不变
    assert pin_a1.profile_id == "legacy-local-v1"
    pin_b = reg.freeze("req-B")
    assert pin_b is not pin_a1  # 并发请求各自固定
    assert pin_b.profile_id == "legacy-local-v1"
    # 快照与注册表解耦（改注册表不影响已固定快照）
    profile = reg.get("legacy-local-v1")
    assert pin_a1.generation_pins is not profile.generation_pins
    assert pin_a1.generation_pins["oil_sales_daily"] == 7
    assert pin_a1.frozen_at and pin_b.frozen_at


# --------------------------------------------------------------------------- #
# 07 禁止回退
# --------------------------------------------------------------------------- #
def test_ac07_no_silent_fallback_on_engine_failure():
    """验收 07｜M2 禁止回退：模拟 CNTS 失败时不回退 Legacy，返回显式错误。"""
    reg = ProfileRegistry()
    reg.activate("cnts-candidate-20261007-v2")
    reg.set_backend_health("cnts", False)
    with pytest.raises(ProfileUnavailableError) as exc:
        reg.freeze("req-E", "cnts-candidate-20261007-v2")
    assert exc.value.code == "ENGINE_UNAVAILABLE"
    assert "cnts" in exc.value.message
    assert reg.frozen("req-E") is None  # 绝不回退

    service = CognitionService(profiles=reg.list_profiles())
    service.registry.activate("cnts-candidate-20261007-v2")
    service.registry.set_backend_health("cnts", False)
    client = TestClient(create_app(service))
    resp = client.post(
        "/api/v1/cognition/query",
        json={
            "request_id": "r-fail",
            "trace_id": "t-fail",
            "raw_query": "上周汽油销量",
            "profile_id": "cnts-candidate-20261007-v2",
        },
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "ENGINE_UNAVAILABLE"
    assert "legacy" not in resp.text


# --------------------------------------------------------------------------- #
# 08 审计完整
# --------------------------------------------------------------------------- #
def test_ac08_audit_records_full_context():
    """验收 08｜M2 审计完整：每次请求记录 trace/配置档/数据集/generation/证据。"""
    service = CognitionService(
        brains={
            BrainName.memory: _memory_with_cluster(),
            BrainName.knowledge: _knowledge_published(),
            BrainName.structured: _structured_brain(),
        }
    )
    service.query(
        CognitiveRequest(
            request_id="req-audit",
            trace_id="trace-audit",
            raw_query="公园太极油品销售规则",
            targets=["memory", "knowledge", "structured"],
        )
    )
    entry = service.audit.get("req-audit")
    assert entry is not None
    assert entry.trace_id == "trace-audit"
    assert entry.profile_id == "legacy-local-v1"
    assert entry.profile_frozen_at
    assert entry.datasets == ["oil_monthly_sales", "oil_sales_daily"]
    assert entry.generations == {"oil_sales_daily": 7, "oil_monthly_sales": 3}
    assert set(entry.targets) == {"memory", "knowledge", "structured"}
    assert entry.evidence_ids  # 证据被记录
    replay = service.audit.replay("req-audit")
    assert replay["request_id"] == "req-audit"
    assert replay["profile_id"] == "legacy-local-v1"


# --------------------------------------------------------------------------- #
# 09 五层管道
# --------------------------------------------------------------------------- #
def test_ac09_memory_five_layer_pipeline():
    """验收 09｜M3 五层管道：真实素材走完接入→提向量→归一→留痕生长→检索治理。"""
    brain = MemoryBrain(growth_threshold=3)
    unit = brain.ingest(_memory_item("2026-10-09"))
    assert brain.store.raw.has(unit.media_ref)  # ① 安全接入
    assert any(f.status == "available" and f.vector for f in unit.features)  # ② 提向量
    assert unit.anchors.place == "公园" and unit.anchors.time == "2026-10-09"  # ③ 天地人归一
    assert brain.store.traces and brain.store.occurrences  # ④ 留痕
    assert any(node.tree_id == "memory.text" for node in brain.store.nodes.values())
    assert brain.store.generations.history("memory.text")  # ④ 生长
    result = brain.query(_brain_query("公园 太极", day="2026-10-09"))  # ⑤ 检索治理
    assert result.status is ResultStatus.ok
    assert result.claims


# --------------------------------------------------------------------------- #
# 10 不伪造未知
# --------------------------------------------------------------------------- #
def test_ac10_unknown_anchors_not_fabricated():
    """验收 10｜M3 不伪造未知：时间/地点/人物不确定项保留未知状态，不填默认值。"""
    brain = MemoryBrain()
    unit = brain.ingest(_memory_item("2026-10-09", place=None, person=None))
    assert unit.anchors.place is None and unit.anchors.person is None
    assert unit.anchors.place != "unknown" and unit.anchors.person != "unknown"
    assert unit.anchors.time == "2026-10-09"
    # 显式未知哨兵同样归为 None（不填默认值）
    unit2 = brain.ingest(_memory_item("2026-10-09", place="未知", person="不详"))
    assert unit2.anchors.place is None and unit2.anchors.person is None
    # 时间未知 -> day_key 与 anchors.time 均为 None
    unit3 = brain.ingest(
        IngestItem(
            media_ref="memory://media/unknown-time",
            modality="text",
            content="一段无时间戳的记录",
            timestamp=None,
            source_ref="note",
        )
    )
    assert unit3.day_key is None and unit3.anchors.time is None


# --------------------------------------------------------------------------- #
# 11 星团可复算
# --------------------------------------------------------------------------- #
def test_ac11_star_cluster_recomputable():
    """验收 11｜M3 星团可复算：从成员引用可反查日圈、树、节点、证据。"""
    brain = MemoryBrain(growth_threshold=10)
    for day in ("2026-10-07", "2026-10-08", "2026-10-09"):
        brain.ingest(_memory_item(day))
    index = brain.store.clusters["memory.text"]
    cluster = next(iter(index.clusters.values()))
    assert cluster.occurrence_count == 3
    days: set[str] = set()
    for trace_id in cluster.member_trace_ids:
        occurrence = brain.store.occurrences[brain.store.occurrence_by_trace[trace_id]]
        assert occurrence.tree_id == "memory.text"  # 树
        assert occurrence.node_id in brain.store.nodes  # 节点
        assert occurrence.node_id in brain.store.ring_node_ids(occurrence.tree_id, occurrence.day_key)  # 日圈
        assert occurrence.evidence_ref.startswith("memory://unit/")  # 证据
        days.add(occurrence.day_key)
    assert days == {"2026-10-07", "2026-10-08", "2026-10-09"}


# --------------------------------------------------------------------------- #
# 12 我记得边界
# --------------------------------------------------------------------------- #
def test_ac12_fast_recall_boundary_pending_only():
    """验收 12｜M3 我记得边界：未达阈值/高离散度/无权限只显示“待核对线索”。"""
    policy = MemoryPolicy(fast_recall_min_occurrence=3, max_dispersion="0.05")
    assert policy.is_fast_recall(2, "0.0", True) is False  # 未达阈值
    assert policy.is_fast_recall(5, "0.9", True) is False  # 高离散度
    assert policy.is_fast_recall(5, "0.0", False) is False  # 无权限
    assert policy.is_fast_recall(5, "0.0", True) is True

    brain = MemoryBrain(growth_threshold=3, policy=policy)
    for day in ("2026-10-07", "2026-10-08"):
        brain.ingest(_memory_item(day))
    pending = brain.query(_brain_query())
    assert pending.status is ResultStatus.ok
    assert any(c.field == "memory.cluster.pending" for c in pending.claims)
    assert not any(c.field == "memory.cluster" for c in pending.claims)
    assert all(c.value.startswith("待核对线索") for c in pending.claims)

    brain.ingest(_memory_item("2026-10-09"))
    fast = brain.query(_brain_query())
    assert any(c.field == "memory.cluster" for c in fast.claims)

    no_permission = brain.query(_brain_query(profile_id=None))
    assert not any(c.field == "memory.cluster" for c in no_permission.claims)
    assert any(c.field == "memory.cluster.pending" for c in no_permission.claims)


# --------------------------------------------------------------------------- #
# 13 敏感项隔离
# --------------------------------------------------------------------------- #
def test_ac13_sensitive_features_isolated_from_auto_cluster():
    """验收 13｜M3 敏感项隔离：身份/声纹/精确位置不进默认自动星团。"""
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
    assert len(brain.store.traces) == 0  # 被门禁拦下 -> 无留痕
    assert brain.store.clusters == {}  # 不进自动星团


# --------------------------------------------------------------------------- #
# 14 五层导入
# --------------------------------------------------------------------------- #
def test_ac14_knowledge_five_layer_import():
    """验收 14｜M4 五层导入：真实资料走完解析→切分→标注生长→门禁→发布。"""
    brain = KnowledgeBrain(growth_threshold=3)
    document = brain.ingest(_kinput(MANUAL, raw_id="manual", title="设备维护手册"))
    assert document.document_id.startswith("kd-")  # ① 解析
    units = brain.store.document_units(document.document_id)
    assert len(units) >= 2  # ② 切分（多标题层级）
    assert brain.store.traces  # ③ 标注 + 留痕生长
    assert any(node.tree_id.startswith("knowledge.") for node in brain.store.nodes.values())
    assert brain.store.generations.history("knowledge.equipment")
    publication = brain.publish(document.document_id)  # ④ 门禁 → ⑤ 发布
    assert publication.status == "published"
    assert brain.store.evidence_by_document[document.document_id]


# --------------------------------------------------------------------------- #
# 15 证据可展开
# --------------------------------------------------------------------------- #
def test_ac15_evidence_expandable_to_locator():
    """验收 15｜M4 证据可展开：每条证据可定位到原文/页码/段落/图表/时间码。"""
    brain = KnowledgeBrain()
    document = brain.ingest(_kinput(MANUAL, raw_id="manual-15"))
    brain.publish(document.document_id)
    result = brain.query(_brain_query("温度监控要点"))
    assert result.status is ResultStatus.ok
    assert result.claims
    for claim in result.claims:
        evidence = brain.store.evidence[claim.evidence_id]
        assert evidence.document_id == document.document_id
        assert set(evidence.locator.keys()) == {"page", "anchor", "timecode"}
        ref = next(r for r in result.evidence if r.evidence_id == claim.evidence_id)
        assert document.document_id in ref.ref
        assert claim.unit in ref.ref
        assert "page=" in ref.ref and "anchor=" in ref.ref


# --------------------------------------------------------------------------- #
# 16 未定时间层
# --------------------------------------------------------------------------- #
def test_ac16_undetermined_time_layer_not_fabricated():
    """验收 16｜M4 未定时间层：无可靠时间材料显式标记，不伪造时序。"""
    brain = KnowledgeBrain()
    document = brain.ingest(_kinput("# 无日期资料\n某设备的维护要点与注意事项。", raw_id="undated"))
    brain.publish(document.document_id)
    evidence_ids = brain.store.evidence_by_document[document.document_id]
    assert evidence_ids
    for evidence_id in evidence_ids:
        evidence = brain.store.evidence[evidence_id]
        assert evidence.time_status == "undetermined"
        assert evidence.content_version is None  # 不伪造版本/时间
    unit = brain.store.document_units(document.document_id)[0]
    assert unit.version is None
    assert brain.store.document_published[document.document_id] is None


# --------------------------------------------------------------------------- #
# 17 冲突不静默
# --------------------------------------------------------------------------- #
def test_ac17_conflict_not_silently_resolved():
    """验收 17｜M4 冲突不静默：冲突并列呈现来源、条件、发布日期。"""
    brain = KnowledgeBrain()
    doc_a = brain.ingest(
        _kinput("# 设备参数\n温度上限为 80℃。", raw_id="a", source_id="src-A", published_at="2026-01-01")
    )
    brain.publish(doc_a.document_id)
    doc_b = brain.ingest(
        _kinput("# 设备参数\n温度上限为 120℃。", raw_id="b", source_id="src-B", published_at="2026-02-01")
    )
    brain.publish(doc_b.document_id)

    assert brain.store.conflicts, "应记录冲突"
    conflict = brain.store.conflicts[0]
    assert len(conflict.evidence_ids) == 2
    assert set(conflict.sources) == {"src-A", "src-B"}
    assert len(conflict.conditions) == 2
    assert conflict.published_dates == ["2026-01-01", "2026-02-01"]

    result = brain.query(_brain_query("温度上限"))
    assert result.status is ResultStatus.ok
    values = {claim.value for claim in result.claims}
    assert any("80" in v for v in values) and any("120" in v for v in values)
    assert any("冲突并列" in warning for warning in result.warnings)


# --------------------------------------------------------------------------- #
# 18 内嵌合规
# --------------------------------------------------------------------------- #
def test_ac18_embedded_compliance_files_and_no_forged_hash():
    """验收 18｜M4 内嵌合规：上游提交号、抽取目录、许可证、改动记录齐全。"""
    commit = WEKNORA_DIR / "UPSTREAM_COMMIT"
    notices = WEKNORA_DIR / "THIRD_PARTY_NOTICES.md"
    patches = WEKNORA_DIR / "patches" / "README.md"
    assert commit.exists()
    assert notices.exists()
    assert patches.exists()

    commit_text = commit.read_text(encoding="utf-8")
    assert "https://github.com/Tencent/WeKnora" in commit_text
    assert "status=LOCKED" in commit_text
    import hashlib
    import json
    manifest = json.loads((WEKNORA_DIR / 'source-manifest.json').read_text(encoding='utf-8-sig'))
    assert manifest
    for entry in manifest:
        assert entry['upstream_commit'] in commit_text
        assert hashlib.sha256((WEKNORA_DIR / 'derived' / entry['path']).read_bytes()).hexdigest() == entry['sha256']

    assert "许可证" in notices.read_text(encoding="utf-8")
    assert patches.read_text(encoding="utf-8").strip()  # 改动记录约定非空


# --------------------------------------------------------------------------- #
# 19 复用接通
# --------------------------------------------------------------------------- #
def test_ac19_structured_adapter_returns_evidence_id():
    """验收 19｜M5 复用接通：适配器调用现有引擎返回带 evidence_id 的结果。"""
    brain = _structured_brain()
    result = brain.query(_brain_query("本月油品销售"))
    assert result.status is ResultStatus.ok
    assert result.claims
    assert all(claim.evidence_id for claim in result.claims)  # 每条 claim 带 evidence_id
    evidence_ids = {ref.evidence_id for ref in result.evidence}
    assert "ev-structured-oil_monthly_sales" in evidence_ids
    ref = next(r for r in result.evidence if r.evidence_id == "ev-structured-oil_monthly_sales")
    assert ref.source is BrainName.structured
    assert ref.ref.startswith("engine://")


# --------------------------------------------------------------------------- #
# 20 无命中语义
# --------------------------------------------------------------------------- #
def test_ac20_no_match_semantics_never_returns_zero():
    """验收 20｜M5 无命中语义：no_match: true 且不返回数值 0。"""
    response = _structured_ok_response()
    response["actual"]["available"] = False
    response["actual"]["aggregations"] = {}
    brain = _structured_brain(response)
    result = brain.query(_brain_query("本月油品销售"))
    assert result.status is ResultStatus.no_match
    assert result.claims == []
    assert all(claim.value != "0" for claim in result.claims)  # 不把无命中答成 0

    outcome = Merger().merge([result])
    assert outcome.no_match is True
    assert outcome.claims == []
    assert all(claim.value != "0" for claim in outcome.claims)


# --------------------------------------------------------------------------- #
# 21 Decimal 保真
# --------------------------------------------------------------------------- #
def test_ac21_decimal_fidelity_no_float_error():
    """验收 21｜M5 Decimal 保真：指标值为 Decimal 字符串，无浮点误差。"""
    brain = _structured_brain()
    result = brain.query(_brain_query("本月油品销售"))
    values = sorted(claim.value for claim in result.claims)
    assert values == ["12345.67", "999.00"]
    for value in values:
        assert isinstance(value, str)  # 非浮点
        assert "e" not in value.lower()  # 无科学计数法
        assert format(Decimal(value), "f") == value  # 精确十进制表示
    assert Decimal(values[0]) == Decimal("12345.67")
    assert values[0] != format(Decimal(values[0]).quantize(Decimal("1")), "f")  # 非整数近似


# --------------------------------------------------------------------------- #
# 22 三阶段闭环
# --------------------------------------------------------------------------- #
def test_ac22_three_stage_closed_loop():
    """验收 22｜M6 三阶段闭环：端到端返回 fast_recall/verification/final_answer。"""
    result = _real_orchestrator().run(
        request_id="r-ac22",
        trace_id="t-ac22",
        day_key=DAY,
        raw_query="公园太极油品销售规则",
        profile_id="alice",
        targets=_ALL,
    )
    assert set(result.stages) == {"fast_recall", "verification", "final_answer"}
    assert result.fast_recall.stage == "fast_recall"
    assert result.fast_recall.partial is True
    assert result.verification.stage == "verification"
    assert result.final_answer.stage == "final_answer"
    assert set(result.verification.per_brain) == {"memory", "knowledge", "structured"}


# --------------------------------------------------------------------------- #
# 23 证据可追溯
# --------------------------------------------------------------------------- #
def test_ac23_citations_resolvable_in_evidence():
    """验收 23｜M6 证据可追溯：final_answer.citations 全可在 evidence 解析。"""
    result = _real_orchestrator().run(
        request_id="r-ac23",
        trace_id="t-ac23",
        day_key=DAY,
        raw_query="公园太极油品销售规则",
        profile_id="alice",
        targets=_ALL,
    )
    evidence_ids = {ref.evidence_id for ref in result.evidence}
    assert result.final_answer.citations  # 非空
    assert set(result.final_answer.citations) <= evidence_ids


# --------------------------------------------------------------------------- #
# 24 不伪造精度
# --------------------------------------------------------------------------- #
def test_ac24_uncertain_marks_limitations():
    """验收 24｜M6 不伪造精度：uncertain 时最终回答显式标注限制。"""
    scope = TimeScope(
        start="2026-10-01",
        end="2026-10-11",
        granularity=Granularity.day,
        confidence=Confidence.uncertain,
    )
    result = _real_orchestrator().run(
        request_id="r-ac24",
        trace_id="t-ac24",
        day_key=DAY,
        raw_query="公园太极油品销售规则",
        profile_id="alice",
        time_scope=scope,
        targets=_ALL,
    )
    assert result.final_answer.confidence == "low"
    assert result.final_answer.limitations
    assert any("不确定" in text for text in result.final_answer.limitations)


# --------------------------------------------------------------------------- #
# 25 异类不相加
# --------------------------------------------------------------------------- #
def test_ac25_incomparable_units_not_summed():
    """验收 25｜M6 异类不相加：口径不可比的结果并列呈现，不强行相加。"""
    knowledge = _StubBrain(
        BrainName.knowledge,
        claims=[Claim(field="salesvol", value="80", source=BrainName.knowledge, unit="KG")],
    )
    structured = _StubBrain(
        BrainName.structured,
        claims=[Claim(field="salesvol", value="100", source=BrainName.structured, unit="L")],
    )
    result = Orchestrator(
        brains={BrainName.knowledge: knowledge, BrainName.structured: structured}
    ).run(
        request_id="r-ac25",
        trace_id="t-ac25",
        day_key=DAY,
        raw_query="销量合计",
        targets=[BrainName.knowledge, BrainName.structured],
        merge_mode=MergeMode.sum,  # 即便要求合并，口径不可比也不相加
    )
    values = sorted(claim.value for claim in result.claims if claim.field == "salesvol")
    assert values == ["100", "80"]  # 两者都在
    assert all(claim.value != "180" for claim in result.claims)  # 未被求和成单值
    conflicts = [c for c in result.verification.conflicts if c["field"] == "salesvol"]
    assert conflicts and conflicts[0]["resolution"] == "unresolved"


# --------------------------------------------------------------------------- #
# 26 权限生效
# --------------------------------------------------------------------------- #
def test_ac26_permission_enforced_and_media_protected():
    """验收 26｜M7 权限生效：越权访问被拒；原始媒体不暴露给无权限 Agent。"""
    policy = default_policy()
    denied = Principal(subject="agent-x", roles=[], scopes=[])
    media = guard_media_access(denied, policy, ["memory://media/1"])
    assert media.allowed is False
    assert media.refs == []  # 不返回任何媒体内容/引用
    assert policy.media_scope == SCOPE_MEMORY_MEDIA_READ
    assert policy.media_scope in media.missing

    owner = Principal(subject="owner-1", scopes=[SCOPE_MEMORY_MEDIA_READ])
    granted = guard_media_access(owner, policy, ["memory://media/1"])
    assert granted.allowed is True
    assert granted.refs == ["memory://media/1"]

    assert policy.has_scope(Principal(subject="a"), SCOPE_COGNITION_QUERY) is False

    service = CognitionService(governance=Governance())
    request = CognitiveRequest(request_id="r-denied", trace_id="t-denied", raw_query="本月油品销售", day_key=DAY)
    with pytest.raises(GovernanceDeniedError) as exc:
        service.query(request, principal=Principal(subject="agent-x", roles=[], scopes=[]))
    assert exc.value.code == "AUTHZ_DENIED"
    assert SCOPE_COGNITION_QUERY in exc.value.missing
    assert service.governance.audit.by_kind("authz_denied")  # 显式拒绝留痕


# --------------------------------------------------------------------------- #
# 27 门禁生效
# --------------------------------------------------------------------------- #
def test_ac27_gate_requires_review_and_rollback():
    """验收 27｜M7 门禁生效：高风险结论/不可逆语义走人工复核，可回滚。"""
    gov = Governance()
    decision = gov.submit_gate(
        "r-ac27", "t-ac27", text="依据条款判定事故责任并对外发布", subject="alice"
    )
    assert decision.high_risk is True
    assert decision.status is GateStatus.pending_review
    assert gov.gate.can_publish(decision.object_id) is False

    approved = gov.approve(decision.object_id, approver="admin", request_id="r-ac27", trace_id="t-ac27")
    assert approved.status is GateStatus.approved
    assert gov.gate.can_publish(decision.object_id) is True

    rolled = gov.rollback(decision.object_id, actor="admin", request_id="r-ac27", trace_id="t-ac27")
    assert rolled.status is GateStatus.pending_review  # 回滚后回到待复核
    assert gov.gate.can_publish(decision.object_id) is False

    # 低风险自动放行（不误伤）
    low = gov.submit_gate("r-ac27b", "t-ac27b", text="本月油品销量环比")
    assert low.high_risk is False
    assert low.status is GateStatus.auto_approved


# --------------------------------------------------------------------------- #
# 28 契约测试
# --------------------------------------------------------------------------- #
def test_ac28_all_contracts_valid():
    """验收 28｜M8 契约测试：docs/contracts/*.schema.json 全部通过校验。"""
    files = sorted(CONTRACTS_DIR.glob("*.schema.json"))
    assert [p.name for p in files] == [
        "brain-contract.schema.json",
        "cognitive-request.schema.json",
        "knowledge-evidence.schema.json",
        "knowledge-workbench.schema.json",
        "runtime-profile.schema.json",
        "topology-node.schema.json",
        "trace-record.schema.json",
    ]
    for path in files:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)

    # 代表性实例须能通过对应契约校验
    schema = json.loads((CONTRACTS_DIR / "cognitive-request.schema.json").read_text(encoding="utf-8"))
    wrapper = {
        "$schema": SCHEMA_DIALECT,
        "$defs": schema["$defs"],
        "$ref": "#/$defs/CognitiveRequest",
    }
    Draft202012Validator.check_schema(wrapper)
    instance = CognitiveRequest(
        request_id="r-1", trace_id="t-1", raw_query="上周华东区汽油销量环比如何", day_key=DAY
    ).model_dump(mode="json")
    Draft202012Validator(wrapper).validate(instance)
