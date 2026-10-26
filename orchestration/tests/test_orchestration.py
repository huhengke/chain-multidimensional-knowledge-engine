"""M6 跨脑编排验收测试（验收项 22–25）。

用**真实三脑**组合：真实 ``MemoryBrain``、真实 ``StructuredBrain``（注入 fake transport）、
以及可控的 ``KnowledgeBrain`` 桩（M4 知识脑在工程内尚未落地，用契约兼容的桩 Brain
记录调用并返回真实 ``BrainResult``）。
"""

from __future__ import annotations

from typing import Any

from brains.memory import IngestItem, MemoryBrain
from brains.protocol import (
    BrainName,
    BrainQuery,
    BrainResult,
    Claim,
    Confidence,
    EvidenceRef,
    Granularity,
    MergeMode,
    ResultStatus,
    TimeScope,
)
from brains.structured import HttpEngineClient, StructuredBrain
from orchestration.src.orchestrator import Orchestrator

DAY = "2026-10-11"


# --------------------------------------------------------------------------- #
# 测试替身：可控 Brain（记录调用）
# --------------------------------------------------------------------------- #
class StubBrain:
    """契约兼容的桩 Brain：记录每次调用并返回预置 BrainResult。"""

    def __init__(
        self,
        name: BrainName,
        *,
        claims=(),
        evidence=(),
        status: ResultStatus = ResultStatus.ok,
    ) -> None:
        self.name = name
        self._claims = list(claims)
        self._evidence = list(evidence)
        self._status = status
        self.calls: list[BrainQuery] = []

    def query(self, query: BrainQuery) -> BrainResult:
        self.calls.append(query)
        return BrainResult(
            brain=self.name,
            request_id=query.request_id,
            trace_id=query.trace_id,
            day_key=query.day_key,
            status=self._status,
            claims=list(self._claims),
            evidence=list(self._evidence),
        )


class FakeTransport:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        return 200, self.response


# --------------------------------------------------------------------------- #
# 构造真实/可控三脑
# --------------------------------------------------------------------------- #
def make_memory_brain() -> MemoryBrain:
    """三条同质记录 -> 达阈值 + 有权限 -> 产出 memory.cluster 快速线索。"""
    brain = MemoryBrain(growth_threshold=3)
    for day in ("2026-10-07", "2026-10-08", "2026-10-09"):
        brain.ingest(
            IngestItem(
                media_ref=f"memory://media/{day}",
                modality="text",
                content="清晨在公园打太极",
                timestamp=f"{day}T08:00:00+08:00",
                place="公园",
                person="张三",
                source_ref="camera-1",
            )
        )
    return brain


def structured_ok_response() -> dict:
    return {
        "trace_id": "tr-s1",
        "actual": {
            "dataset": "oil_monthly_sales",
            "available": True,
            "record_count": 3,
            "aggregations": {"monthlysales": "12345.67"},
        },
        "warnings": [],
    }


def make_structured_brain() -> StructuredBrain:
    client = HttpEngineClient(
        base_url="http://engine.test:8000", transport=FakeTransport(structured_ok_response())
    )
    return StructuredBrain(client)


def make_knowledge_brain() -> StubBrain:
    return StubBrain(
        BrainName.knowledge,
        claims=[Claim(field="knowledge.rule", value="规则R-1", source=BrainName.knowledge, evidence_id="ev-know-1")],
        evidence=[EvidenceRef(evidence_id="ev-know-1", source=BrainName.knowledge, ref="knowledge://doc/1")],
    )


def make_orchestrator(*, memory=None, knowledge=None, structured=None) -> Orchestrator:
    return Orchestrator(
        brains={
            BrainName.memory: memory if memory is not None else make_memory_brain(),
            BrainName.knowledge: knowledge if knowledge is not None else make_knowledge_brain(),
            BrainName.structured: structured if structured is not None else make_structured_brain(),
        }
    )


_ALL = [BrainName.memory, BrainName.knowledge, BrainName.structured]


# --------------------------------------------------------------------------- #
# 验收 22：三阶段闭环
# --------------------------------------------------------------------------- #
def test_22_three_stage_closed_loop():
    result = make_orchestrator().run(
        request_id="r-1",
        trace_id="t-1",
        day_key=DAY,
        raw_query="公园太极与本月油品销售规则",
        profile_id="alice",
        targets=_ALL,
    )

    assert set(result.stages) == {"fast_recall", "verification", "final_answer"}
    assert result.fast_recall.stage == "fast_recall"
    assert result.fast_recall.partial is True
    assert result.verification.stage == "verification"
    assert result.final_answer.stage == "final_answer"
    # 三脑各自返回（先独立计算后汇总）
    assert set(result.verification.per_brain) == {"memory", "knowledge", "structured"}


# --------------------------------------------------------------------------- #
# 验收 23：citations 可解析
# --------------------------------------------------------------------------- #
def test_23_citations_resolvable_in_evidence():
    result = make_orchestrator().run(
        request_id="r-2",
        trace_id="t-2",
        day_key=DAY,
        raw_query="公园太极油品销售规则",
        profile_id="alice",
        targets=_ALL,
    )

    evidence_ids = {ref.evidence_id for ref in result.evidence}
    assert result.final_answer.citations  # 非空
    assert set(result.final_answer.citations) <= evidence_ids


# --------------------------------------------------------------------------- #
# 验收 24：uncertain 时必须显式标注限制
# --------------------------------------------------------------------------- #
def test_24_uncertain_time_marks_limitations():
    time_scope = TimeScope(
        start="2026-10-01",
        end="2026-10-11",
        granularity=Granularity.day,
        confidence=Confidence.uncertain,
    )
    result = make_orchestrator().run(
        request_id="r-3",
        trace_id="t-3",
        day_key=DAY,
        raw_query="公园太极油品销售规则",
        profile_id="alice",
        time_scope=time_scope,
        targets=_ALL,
    )

    assert result.final_answer.confidence == "low"
    assert result.final_answer.limitations
    assert any("不确定" in text for text in result.final_answer.limitations)


# --------------------------------------------------------------------------- #
# 验收 25：异类不相加（同 field 不同单位并列保留）
# --------------------------------------------------------------------------- #
def test_25_incomparable_values_not_summed():
    knowledge = StubBrain(
        BrainName.knowledge,
        claims=[Claim(field="salesvol", value="80", source=BrainName.knowledge, unit="KG")],
    )
    structured = StubBrain(
        BrainName.structured,
        claims=[Claim(field="salesvol", value="100", source=BrainName.structured, unit="L")],
    )
    result = make_orchestrator(knowledge=knowledge, structured=structured).run(
        request_id="r-4",
        trace_id="t-4",
        day_key=DAY,
        raw_query="销量合计",
        targets=[BrainName.knowledge, BrainName.structured],
        merge_mode=MergeMode.sum,  # 即使要求合并，口径不可比也不相加
    )

    values = sorted(claim.value for claim in result.claims if claim.field == "salesvol")
    assert values == ["100", "80"]  # 并列保留
    assert all(claim.value != "180" for claim in result.claims)  # 未相加
    conflicts = [c for c in result.verification.conflicts if c["field"] == "salesvol"]
    assert conflicts and conflicts[0]["resolution"] == "unresolved"


# --------------------------------------------------------------------------- #
# 验收 22/25：三脑独立计算（各自被调用且各自返回）
# --------------------------------------------------------------------------- #
def test_three_brains_computed_independently():
    memory = StubBrain(
        BrainName.memory,
        claims=[Claim(field="memory.cluster", value="公园·太极", source=BrainName.memory, evidence_id="ev-m1")],
        evidence=[EvidenceRef(evidence_id="ev-m1", source=BrainName.memory, ref="memory://cluster/1")],
    )
    knowledge = make_knowledge_brain()
    structured = StubBrain(
        BrainName.structured,
        claims=[Claim(field="销售吨数", value="12345.67", source=BrainName.structured, evidence_id="ev-s1")],
        evidence=[EvidenceRef(evidence_id="ev-s1", source=BrainName.structured, ref="engine://tr/1")],
    )
    result = make_orchestrator(memory=memory, knowledge=knowledge, structured=structured).run(
        request_id="r-5",
        trace_id="t-5",
        day_key=DAY,
        raw_query="公园太极油品销售规则",
        profile_id="alice",
        targets=_ALL,
    )

    assert memory.calls and knowledge.calls and structured.calls
    assert set(result.verification.per_brain) == {"memory", "knowledge", "structured"}
    for brain_result in result.verification.per_brain.values():
        assert brain_result["status"] == "ok"


# --------------------------------------------------------------------------- #
# Stage 1：pending 线索不得被包装成事实
# --------------------------------------------------------------------------- #
def test_stage1_excludes_pending_clues():
    memory = StubBrain(
        BrainName.memory,
        claims=[
            Claim(field="memory.cluster", value="公园·太极", source=BrainName.memory, evidence_id="ev-m1"),
            Claim(
                field="memory.cluster.pending",
                value="待核对线索：模糊片段",
                source=BrainName.memory,
                evidence_id="ev-m2",
            ),
        ],
    )
    result = make_orchestrator(memory=memory).run(
        request_id="r-6",
        trace_id="t-6",
        day_key=DAY,
        raw_query="公园太极",
        profile_id="alice",
        targets=[BrainName.memory],
    )

    summaries = [item["summary"] for item in result.fast_recall.items]
    assert any("memory.cluster=" in s for s in summaries)
    assert all("待核对" not in s for s in summaries)
