"""M7 治理审计（audit）。

与 M2 ``audit`` **协同**：复用其 ``AuditLog`` / ``AuditEntry`` 作为**取信根**，
治理事件（授权拒绝、媒体回放拒绝、门禁决策、回滚、版本激活/回滚）在自有事件流中
结构化留痕（可回放），并镜像写入 M2 ``AuditLog``（若已绑定）。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from audit import AuditEntry, AuditLog  # M2 控制面审计（取信根）
from pydantic import BaseModel, ConfigDict, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GovernanceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    subject: str = ""
    detail: str = ""
    timestamp: str = Field(default_factory=_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GovernanceAudit:
    """治理事件审计：自有事件流 + 可选镜像到 M2 ``AuditLog``。"""

    def __init__(self, control_log: AuditLog | None = None) -> None:
        self._events: list[GovernanceEvent] = []
        self._lock = threading.RLock()
        self.control_log = control_log

    def record(self, event: GovernanceEvent) -> GovernanceEvent:
        with self._lock:
            self._events.append(event)
            if self.control_log is not None:
                self.control_log.record(
                    AuditEntry(
                        request_id=event.request_id,
                        trace_id=event.trace_id,
                        day_key="",
                        profile_id=event.subject or "governance",
                        profile_frozen_at="",
                        engine="governance",
                        targets=[event.kind],
                        warnings=[event.detail] if event.detail else [],
                        status=event.kind,
                        started_at=event.timestamp,
                        ended_at=event.timestamp,
                    )
                )
            return event

    def replay(self, request_id: str) -> list[GovernanceEvent]:
        """按 ``request_id`` 回放治理事件（可复现决策与拒绝原因）。"""
        with self._lock:
            return [e for e in self._events if e.request_id == request_id]

    def by_kind(self, kind: str) -> list[GovernanceEvent]:
        with self._lock:
            return [e for e in self._events if e.kind == kind]

    def all(self) -> list[GovernanceEvent]:
        with self._lock:
            return list(self._events)


__all__ = ["GovernanceAudit", "GovernanceEvent"]
