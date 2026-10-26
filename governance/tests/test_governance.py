"""M7 治理·隐私·安全验收测试（验收项 26–27）。"""

from __future__ import annotations

import pytest

from audit import AuditLog
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
from service import CognitiveRequest, CognitionService

DAY = "2026-10-11"


# --------------------------------------------------------------------------- #
# 验收 26：越权拒绝 + 原始媒体不外泄
# --------------------------------------------------------------------------- #
def test_26_media_access_denied_without_scope():
    policy = default_policy()

    denied = Principal(subject="agent-x", roles=[], scopes=[])
    result = guard_media_access(denied, policy, ["memory://media/1"])
    assert result.allowed is False
    assert result.refs == []  # 不返回任何媒体内容/引用
    assert policy.media_scope == SCOPE_MEMORY_MEDIA_READ
    assert policy.media_scope in result.missing

    # 条件：直接授予 scope -> 放行
    owner = Principal(subject="owner-1", scopes=[SCOPE_MEMORY_MEDIA_READ])
    ok = guard_media_access(owner, policy, ["memory://media/1"])
    assert ok.allowed is True
    assert ok.refs == ["memory://media/1"]

    # 角色映射同样生效
    by_role = Principal(subject="admin-1", roles=["admin"])
    assert guard_media_access(by_role, policy, ["memory://media/2"]).allowed is True


def test_26_authorize_rejects_missing_scope():
    policy = default_policy()
    decision = policy.has_scope(Principal(subject="a"), SCOPE_COGNITION_QUERY)
    assert decision is False
    assert policy.has_scope(Principal(subject="a", roles=["analyst"]), SCOPE_COGNITION_QUERY) is True


# --------------------------------------------------------------------------- #
# 验收 27：门禁（pending_review → approve → 可发布 → rollback → 回到待复核）
# --------------------------------------------------------------------------- #
def test_27_gate_pending_approve_rollback():
    gov = Governance()

    decision = gov.submit_gate(
        "r-1", "t-1", text="依据条款判定事故责任并对外发布", subject="alice"
    )
    assert decision.high_risk is True
    assert decision.status is GateStatus.pending_review
    assert gov.gate.can_publish(decision.object_id) is False

    approved = gov.approve(decision.object_id, approver="admin", request_id="r-1", trace_id="t-1")
    assert approved.status is GateStatus.approved
    assert gov.gate.can_publish(decision.object_id) is True

    rolled = gov.rollback(decision.object_id, actor="admin", request_id="r-1", trace_id="t-1")
    assert rolled.status is GateStatus.pending_review  # 回滚后回到待复核
    assert gov.gate.can_publish(decision.object_id) is False

    # 低风险自动放行（不误伤）
    low = gov.submit_gate("r-2", "t-2", text="本月油品销量环比")
    assert low.high_risk is False
    assert low.status is GateStatus.auto_approved


# --------------------------------------------------------------------------- #
# 治理审计：授权拒绝 / 门禁决策 / 回滚 均可回放
# --------------------------------------------------------------------------- #
def test_governance_audit_replayable():
    control = AuditLog()
    gov = Governance(control_log=control)

    decision = gov.authorize(
        Principal(subject="agent-x"), SCOPE_MEMORY_MEDIA_READ, request_id="r-9", trace_id="t-9"
    )
    assert decision.allowed is False

    gate = gov.submit_gate("r-9", "t-9", text="对外发布质量放行结论", subject="agent-x")
    assert gate.high_risk is True
    gov.approve(gate.object_id, approver="admin", request_id="r-9", trace_id="t-9")
    gov.rollback(gate.object_id, actor="admin", request_id="r-9", trace_id="t-9")

    gov.activate_version("knowledge", "v2", request_id="r-9", trace_id="t-9")
    gov.activate_version("knowledge", "v3", request_id="r-9", trace_id="t-9")
    gov.rollback_version("knowledge", request_id="r-9", trace_id="t-9")
    assert gov.versions.active("knowledge") == "v2"

    assert gov.audit.by_kind("authz_denied")
    assert gov.audit.by_kind("gate_decision")
    assert gov.audit.by_kind("gate_rolled_back")
    assert gov.audit.by_kind("version_rolled_back")

    replay = gov.audit.replay("r-9")
    assert len(replay) >= 5
    # 协同 M2 控制面审计（治理事件镜像进 AuditLog）
    assert any(entry.engine == "governance" for entry in control.all())


# --------------------------------------------------------------------------- #
# 与 M2 衔接：注入治理后越权请求在 CognitionService 层被显式拒绝
# --------------------------------------------------------------------------- #
def test_09_service_denies_unauthorized_principal():
    service = CognitionService(governance=Governance())

    denied = CognitiveRequest(
        request_id="r-x",
        trace_id="t-x",
        raw_query="本月油品销售",
        day_key=DAY,
    )
    with pytest.raises(GovernanceDeniedError) as exc:
        service.query(denied, principal=Principal(subject="agent-x", roles=[], scopes=[]))
    assert exc.value.code == "AUTHZ_DENIED"
    assert SCOPE_COGNITION_QUERY in exc.value.missing
    assert exc.value.as_error()["error"]["code"] == "AUTHZ_DENIED"
    # 显式拒绝留痕
    assert service.governance.audit.by_kind("authz_denied")

    allowed = CognitiveRequest(
        request_id="r-y",
        trace_id="t-y",
        raw_query="本月油品销售",
        day_key=DAY,
    )
    response = service.query(allowed, principal=Principal(subject="alice", roles=["analyst"]))
    assert response.request_id == "r-y"
    assert response.day_key == DAY
    assert set(response.stages.model_dump()) == {
        "fast_recall",
        "verification",
        "final_answer",
    }
