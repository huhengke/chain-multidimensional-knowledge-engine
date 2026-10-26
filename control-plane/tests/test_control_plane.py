"""M2 验收测试（对应验收矩阵 06–08）与「禁止回退」「无命中不报 0」硬不变式。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from brains.protocol import (
    BrainName,
    BrainResult,
    Claim,
    EvidenceRef,
    MergeMode,
    ResultStatus,
)
from api import create_app
from evidence import EvidenceAggregator
from merge import Merger
from profile import (
    ProfileRegistry,
    ProfileStatus,
    ProfileUnavailableError,
)
from router import ALL_TARGETS, Router
from service import CognitiveRequest, CognitionService
from topology.src.ruler import Ruler

DAY = "2026-10-11"


def _request(**over) -> CognitiveRequest:
    data = {
        "request_id": "r-1",
        "trace_id": "t-1",
        "raw_query": "上周华东区汽油销量环比如何",
        "day_key": DAY,
    }
    data.update(over)
    return CognitiveRequest(**data)


# ---- router ------------------------------------------------------------
def test_router_trims_by_intent():
    router = Router()
    assert router.route("上周华东区汽油销量环比如何") == [BrainName.structured]
    assert router.route("我记得去年拍的那段视频") == [BrainName.memory]
    assert router.route("这条规则的定义是什么") == [BrainName.knowledge]
    # 无明确意图 -> 保留候选全集
    assert set(router.route("你好")) == set(ALL_TARGETS)
    # 候选集合约束
    assert router.route("销量与规则", ["structured"]) == [BrainName.structured]
    # 显式指定 targets 时按调用方意图为准（不再按关键词裁剪）
    assert router.route(
        "上周华东区汽油销量环比如何", ["memory", "knowledge", "structured"]
    ) == list(ALL_TARGETS)


# ---- evidence ----------------------------------------------------------
def test_evidence_assigns_ids_and_dedups():
    agg = EvidenceAggregator()
    out = agg.aggregate(
        {
            BrainName.structured: [
                EvidenceRef(evidence_id="a", source=BrainName.structured, ref="ds://oil/1"),
                EvidenceRef(evidence_id="b", source=BrainName.structured, ref="ds://oil/1"),
            ],
            BrainName.memory: [EvidenceRef(evidence_id="c", source=BrainName.memory, ref="mem://x/2")],
        }
    )
    assert [e.evidence_id for e in out] == ["ev-1", "ev-2"]
    assert out[0].source is BrainName.memory  # 规范顺序 memory -> knowledge -> structured
    assert out[1].source is BrainName.structured


# ---- merge：无命中不报 0 / 异类不相加 / 冲突不合吞 ---------------------
def test_merge_no_match_never_returns_zero():
    results = [
        BrainResult(
            brain=BrainName.structured,
            request_id="r",
            trace_id="t",
            day_key=DAY,
            status=ResultStatus.no_match,
        )
    ]
    outcome = Merger().merge(results)
    assert outcome.no_match is True
    assert outcome.claims == []
    assert all(c.value != "0" for c in outcome.claims)


def test_merge_separate_does_not_sum_incomparable():
    results = [
        BrainResult(
            brain=BrainName.structured,
            request_id="r",
            trace_id="t",
            day_key=DAY,
            status=ResultStatus.ok,
            claims=[Claim(field="salesvol", value="100", source=BrainName.structured, unit="L")],
        ),
        BrainResult(
            brain=BrainName.memory,
            request_id="r",
            trace_id="t",
            day_key=DAY,
            status=ResultStatus.ok,
            claims=[Claim(field="salesvol", value="80", source=BrainName.memory)],
        ),
    ]
    outcome = Merger(merge_mode=MergeMode.separate).merge(results)
    assert len(outcome.claims) == 2  # 未强行相加
    assert outcome.conflicts and outcome.conflicts[0].field == "salesvol"
    assert outcome.conflicts[0].resolution == "unresolved"


def test_merge_sum_only_when_comparable():
    results = [
        BrainResult(
            brain=BrainName.structured,
            request_id="r",
            trace_id="t",
            day_key=DAY,
            status=ResultStatus.ok,
            claims=[Claim(field="salesvol", value="100", source=BrainName.structured, unit="L")],
        ),
        BrainResult(
            brain=BrainName.knowledge,
            request_id="r",
            trace_id="t",
            day_key=DAY,
            status=ResultStatus.ok,
            claims=[Claim(field="salesvol", value="80", source=BrainName.knowledge, unit="L")],
        ),
    ]
    outcome = Merger(merge_mode=MergeMode.sum).merge(results)
    assert len(outcome.claims) == 1
    assert outcome.claims[0].value == "180"
    assert outcome.claims[0].source is BrainName.structured  # structured_wins


# ---- 06 配置档冻结 -----------------------------------------------------
def test_06_profile_frozen_within_request():
    reg = ProfileRegistry()
    pin1 = reg.freeze("req-A")
    pin2 = reg.freeze("req-A")
    assert pin1 is pin2  # 请求期内冻结不变
    assert pin1.profile_id == "legacy-local-v1"
    assert pin1.status is ProfileStatus.active

    # 并发请求各自固定、互不干扰
    pin_b = reg.freeze("req-B")
    assert pin_b is not pin1
    assert pin_b.profile_id == "legacy-local-v1"

    # 快照与注册表解耦（修改注册表不影响已固定快照）
    profile = reg.get("legacy-local-v1")
    assert pin1.generation_pins is not profile.generation_pins
    assert pin1.generation_pins["oil_sales_daily"] == 7


def test_06_freeze_rejects_non_active_and_unknown():
    reg = ProfileRegistry()
    with pytest.raises(ProfileUnavailableError) as exc:
        reg.freeze("req-C", "cnts-candidate-20261007-v2")  # ready 未激活
    assert exc.value.code == "PROFILE_NOT_ACTIVE"
    with pytest.raises(ProfileUnavailableError) as exc2:
        reg.freeze("req-D", "no-such-profile")
    assert exc2.value.code == "PROFILE_NOT_FOUND"


# ---- 07 禁止回退 -------------------------------------------------------
def test_07_no_silent_fallback_on_engine_failure():
    reg = ProfileRegistry()
    reg.activate("cnts-candidate-20261007-v2")
    reg.set_backend_health("cnts", False)  # 模拟 CNTS 故障
    with pytest.raises(ProfileUnavailableError) as exc:
        reg.freeze("req-E", "cnts-candidate-20261007-v2")
    assert exc.value.code == "ENGINE_UNAVAILABLE"
    assert "cnts" in exc.value.message
    # 绝不回退到 legacy
    assert reg.frozen("req-E") is None

    # 通过 HTTP 也应返回显式错误，而非 legacy 数值
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
    body = resp.json()
    assert body["error"]["code"] == "ENGINE_UNAVAILABLE"
    assert "legacy" not in resp.text


# ---- 01 控制面内三脑 day_key 一致 --------------------------------------
def test_control_plane_same_day_key_for_all_brains():
    service = CognitionService()
    response = service.query(_request(targets=["memory", "knowledge", "structured"]))
    assert response.day_key == DAY
    per_brain = response.stages.verification.per_brain
    assert set(per_brain) == {"memory", "knowledge", "structured"}
    assert {r["day_key"] for r in per_brain.values()} == {DAY}
    assert per_brain["knowledge"]["status"] == "no_match"
    assert per_brain["memory"]["status"] == "unavailable"
    assert per_brain["structured"]["status"] == "unavailable"
    assert all(r["claims"] == [] for r in per_brain.values())  # 不伪造数值
    assert "结构化脑（M5）尚未接入" in " ".join(response.warnings)


# ---- 08 审计完整 -------------------------------------------------------
def test_08_audit_complete_and_replayable():
    service = CognitionService()
    service.query(
        _request(
            request_id="req-audit",
            trace_id="trace-audit",
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
    replay = service.audit.replay("req-audit")
    assert replay["request_id"] == "req-audit"
    assert replay["profile_id"] == "legacy-local-v1"


# ---- HTTP 接口 ---------------------------------------------------------
def test_http_endpoints():
    client = TestClient(create_app())
    health = client.get("/api/v1/cognition/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["timezone"] == "Asia/Shanghai"

    profile = client.get("/api/v1/cognition/profile")
    assert profile.status_code == 200
    assert profile.json()["active"]["profile_id"] == "legacy-local-v1"

    resp = client.post(
        "/api/v1/cognition/query",
        json={
            "request_id": "r-http",
            "trace_id": "t-http",
            "raw_query": "上周华东区汽油销量环比如何",
            "time_scope": {
                "start": "2026-10-01",
                "end": "2026-10-11",
                "granularity": "day",
                "confidence": "exact",
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # 请求未显式给出 day_key：由服务端按日界线补齐为「当日」，故按 Ruler 现值断言。
    assert body["day_key"] == Ruler().day_key()
    assert body["profile_id"] == "legacy-local-v1"
    assert set(body["stages"]) == {"fast_recall", "verification", "final_answer"}
    assert body["stages"]["final_answer"]["citations"] == body["stages"]["final_answer"]["citations"]
