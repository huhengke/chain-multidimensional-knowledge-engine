"""M7 治理门面（facade）。

把 ``authz`` / ``policy_gate`` / ``audit`` / ``versions`` 组合为一个**真实可用**的
``Governance``，供 M2 ``CognitionService`` 注入。所有拒绝与门禁决策均留痕（可回放），
**绝不静默放行**。
"""

from __future__ import annotations

from audit import AuditLog

from .audit import GovernanceAudit, GovernanceEvent
from .authz import (
    AuthzDecision,
    MediaAccessResult,
    Policy,
    Principal,
    authorize,
    default_policy,
    guard_media_access,
)
from .policy_gate import GateDecision, PolicyGate
from .versions import VersionRecord, VersionRegistry

_PLACEHOLDER = "<governance>"


class Governance:
    def __init__(
        self,
        *,
        policy: Policy | None = None,
        gate: PolicyGate | None = None,
        audit: GovernanceAudit | None = None,
        versions: VersionRegistry | None = None,
        control_log: AuditLog | None = None,
    ) -> None:
        self.policy = policy or default_policy()
        self.gate = gate or PolicyGate()
        self.audit = audit or GovernanceAudit(control_log)
        self.versions = versions or VersionRegistry()

    # ---- 授权 ------------------------------------------------------------
    def authorize(
        self,
        principal: Principal,
        required_scope: str,
        *,
        request_id: str = "",
        trace_id: str = "",
    ) -> AuthzDecision:
        decision = authorize(principal, required_scope, self.policy)
        if not decision.allowed:
            self.audit.record(
                GovernanceEvent(
                    kind="authz_denied",
                    request_id=request_id or _PLACEHOLDER,
                    trace_id=trace_id or _PLACEHOLDER,
                    subject=principal.subject,
                    detail=decision.reason,
                    metadata={"required": decision.required, "missing": decision.missing},
                )
            )
        return decision

    def guard_media(
        self,
        principal: Principal,
        media_refs,
        *,
        request_id: str = "",
        trace_id: str = "",
    ) -> MediaAccessResult:
        result = guard_media_access(principal, self.policy, media_refs)
        if not result.allowed:
            self.audit.record(
                GovernanceEvent(
                    kind="media_access_denied",
                    request_id=request_id or _PLACEHOLDER,
                    trace_id=trace_id or _PLACEHOLDER,
                    subject=principal.subject,
                    detail=result.reason,
                    metadata={"missing": result.missing},
                )
            )
        return result

    # ---- 门禁 ------------------------------------------------------------
    def submit_gate(
        self,
        request_id: str,
        trace_id: str,
        *,
        text: str | None = None,
        object_id: str | None = None,
        subject: str = "",
    ) -> GateDecision:
        decision = self.gate.submit(
            object_id or f"answer:{request_id}", text=text, tags=None
        )
        if decision.high_risk:
            self.audit.record(
                GovernanceEvent(
                    kind="gate_decision",
                    request_id=request_id or _PLACEHOLDER,
                    trace_id=trace_id or _PLACEHOLDER,
                    subject=subject,
                    detail="；".join(decision.reasons),
                    metadata={
                        "object_id": decision.object_id,
                        "category": decision.category,
                        "status": decision.status.value,
                    },
                )
            )
        return decision

    def approve(
        self, object_id: str, *, approver: str, request_id: str = "", trace_id: str = ""
    ) -> GateDecision:
        decision = self.gate.approve(object_id, approver=approver)
        self._record_gate("gate_approved", decision, actor=approver, request_id=request_id, trace_id=trace_id)
        return decision

    def reject(
        self,
        object_id: str,
        *,
        approver: str,
        reason: str = "",
        request_id: str = "",
        trace_id: str = "",
    ) -> GateDecision:
        decision = self.gate.reject(object_id, approver=approver, reason=reason)
        self._record_gate("gate_rejected", decision, actor=approver, request_id=request_id, trace_id=trace_id)
        return decision

    def rollback(
        self, object_id: str, *, actor: str, request_id: str = "", trace_id: str = ""
    ) -> GateDecision:
        decision = self.gate.rollback(object_id, actor=actor)
        self._record_gate("gate_rolled_back", decision, actor=actor, request_id=request_id, trace_id=trace_id)
        return decision

    # ---- 版本 ------------------------------------------------------------
    def activate_version(
        self, name: str, version: str, *, request_id: str = "", trace_id: str = ""
    ) -> VersionRecord:
        record = self.versions.activate(name, version)
        self.audit.record(
            GovernanceEvent(
                kind="version_activated",
                request_id=request_id or _PLACEHOLDER,
                trace_id=trace_id or _PLACEHOLDER,
                subject=name,
                detail=f"激活 {name} -> {version}",
                metadata={"previous": record.previous},
            )
        )
        return record

    def rollback_version(
        self, name: str, *, request_id: str = "", trace_id: str = ""
    ) -> VersionRecord:
        record = self.versions.rollback(name)
        self.audit.record(
            GovernanceEvent(
                kind="version_rolled_back",
                request_id=request_id or _PLACEHOLDER,
                trace_id=trace_id or _PLACEHOLDER,
                subject=name,
                detail=f"回滚 {name} -> {record.version}",
                metadata={"previous": record.previous},
            )
        )
        return record

    # ---- 内部 ------------------------------------------------------------
    def _record_gate(
        self,
        kind: str,
        decision: GateDecision,
        *,
        actor: str,
        request_id: str,
        trace_id: str,
    ) -> None:
        self.audit.record(
            GovernanceEvent(
                kind=kind,
                request_id=request_id or _PLACEHOLDER,
                trace_id=trace_id or _PLACEHOLDER,
                subject=actor,
                detail=f"{decision.object_id} -> {decision.status.value}",
                metadata={"category": decision.category, "object_id": decision.object_id},
            )
        )


__all__ = ["Governance"]
