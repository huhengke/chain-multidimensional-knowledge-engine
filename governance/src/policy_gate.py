"""M7 策略门禁（policy_gate）。

对**高风险结论 / 不可逆语义**实施受控复核：

- 命中法律、医疗、安全、质量放行、删除/注销、发布级别变更等类别 → ``pending_review``；
- 人工 ``approve`` 后方可发布（``can_publish`` 为真）；
- ``reject`` → 终态拒绝；
- ``rollback`` → 回到 ``pending_review``（已发布内容可回退至待复核）；
- 全程保留 ``history``，不静默放行。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RiskCategory(str, Enum):
    legal = "legal"
    medical = "medical"
    safety = "safety"
    quality_release = "quality_release"
    irreversible = "irreversible"
    publish = "publish"


class GateStatus(str, Enum):
    auto_approved = "auto_approved"
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"
    rolled_back = "rolled_back"


#: 高风险关键词 → 风险类别（真实规则；命中即进入受控复核）。
HIGH_RISK_KEYWORDS: dict[str, RiskCategory] = {
    # 法律 / 合规
    "法律": RiskCategory.legal,
    "合规": RiskCategory.legal,
    "责任判定": RiskCategory.legal,
    "条款": RiskCategory.legal,
    "处罚": RiskCategory.legal,
    # 医疗
    "医疗": RiskCategory.medical,
    "诊断": RiskCategory.medical,
    "病情": RiskCategory.medical,
    "用药": RiskCategory.medical,
    # 安全
    "安全": RiskCategory.safety,
    "事故": RiskCategory.safety,
    "风险预警": RiskCategory.safety,
    # 质量放行
    "质量放行": RiskCategory.quality_release,
    "质量判定": RiskCategory.quality_release,
    "放行": RiskCategory.quality_release,
    # 不可逆
    "删除": RiskCategory.irreversible,
    "注销": RiskCategory.irreversible,
    "撤销": RiskCategory.irreversible,
    # 发布级别
    "发布": RiskCategory.publish,
    "发布级别": RiskCategory.publish,
}

#: 仅当处于该状态才允许发布。
_PUBLISHABLE = {GateStatus.approved}


class GateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str = Field(min_length=1)
    category: str | None = None
    status: GateStatus = GateStatus.auto_approved
    high_risk: bool = False
    reasons: list[str] = Field(default_factory=list)
    history: list[dict] = Field(default_factory=list)


def detect_risk(text: str | None, tags=None) -> RiskCategory | None:
    """按关键词 / 标签检测风险类别（命中多个取首个命中的类别）。"""
    haystack = text or ""
    for keyword, category in HIGH_RISK_KEYWORDS.items():
        if keyword in haystack:
            return category
    for tag in tags or ():
        for keyword, category in HIGH_RISK_KEYWORDS.items():
            if keyword in str(tag):
                return category
    return None


class PolicyGate:
    """策略门禁状态机（线程安全、全程留痕）。"""

    def __init__(self) -> None:
        self._decisions: dict[str, GateDecision] = {}
        self._lock = threading.RLock()

    # ---- 提交 ------------------------------------------------------------
    def submit(
        self, object_id: str, *, text: str | None = None, tags=None
    ) -> GateDecision:
        category = detect_risk(text, tags)
        if category is None:
            decision = GateDecision(
                object_id=object_id,
                category=None,
                status=GateStatus.auto_approved,
                high_risk=False,
                reasons=["未命中高风险类别：自动放行"],
            )
        else:
            decision = GateDecision(
                object_id=object_id,
                category=category.value,
                status=GateStatus.pending_review,
                high_risk=True,
                reasons=[f"命中高风险类别 {category.value}：进入受控复核，禁止直接发布"],
            )
        self._append_history(decision, actor="system", action="submit")
        with self._lock:
            self._decisions[object_id] = decision
        return decision

    # ---- 复核 ------------------------------------------------------------
    def approve(self, object_id: str, *, approver: str) -> GateDecision:
        decision = self._require(object_id)
        with self._lock:
            if decision.status not in (GateStatus.pending_review, GateStatus.rolled_back):
                raise ValueError(
                    f"对象 {object_id!r} 当前状态 {decision.status.value} 不可 approve"
                )
            decision.status = GateStatus.approved
            self._append_history(decision, actor=approver, action="approve")
        return decision

    def reject(self, object_id: str, *, approver: str, reason: str = "") -> GateDecision:
        decision = self._require(object_id)
        with self._lock:
            decision.status = GateStatus.rejected
            if reason:
                decision.reasons.append(reason)
            self._append_history(decision, actor=approver, action="reject")
        return decision

    def rollback(self, object_id: str, *, actor: str) -> GateDecision:
        """回滚：无论已 approve 还是已发布，退回 ``pending_review``（待复核）。"""
        decision = self._require(object_id)
        with self._lock:
            decision.status = GateStatus.pending_review
            self._append_history(decision, actor=actor, action="rollback")
        return decision

    # ---- 查询 ------------------------------------------------------------
    def can_publish(self, object_id: str) -> bool:
        decision = self._decisions.get(object_id)
        return decision is not None and decision.status in _PUBLISHABLE

    def get(self, object_id: str) -> GateDecision | None:
        return self._decisions.get(object_id)

    def all(self) -> list[GateDecision]:
        """返回快照，供受控运维界面查看门禁状态。"""
        with self._lock:
            return [item.model_copy(deep=True) for item in self._decisions.values()]

    # ---- 内部 ------------------------------------------------------------
    def _require(self, object_id: str) -> GateDecision:
        decision = self._decisions.get(object_id)
        if decision is None:
            raise KeyError(f"门禁对象不存在：{object_id!r}")
        return decision

    @staticmethod
    def _append_history(decision: GateDecision, *, actor: str, action: str) -> None:
        decision.history.append(
            {"action": action, "actor": actor, "status": decision.status.value, "at": _now_iso()}
        )


__all__ = [
    "PolicyGate",
    "GateDecision",
    "GateStatus",
    "RiskCategory",
    "HIGH_RISK_KEYWORDS",
    "detect_risk",
]
