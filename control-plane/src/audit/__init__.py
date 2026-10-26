"""M2 审计（audit）。

每次请求记录 ``trace_id``、固定配置档、涉及目标与 generation、证据引用，
并支持按 ``request_id`` 回放。
"""

from __future__ import annotations

import threading

from pydantic import BaseModel, ConfigDict, Field


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    day_key: str
    profile_id: str
    profile_frozen_at: str
    engine: str
    targets: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    generations: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    status: str = "ok"
    started_at: str = Field(min_length=1)
    ended_at: str = Field(min_length=1)


class AuditLog:
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._by_request: dict[str, AuditEntry] = {}
        self._lock = threading.RLock()

    def record(self, entry: AuditEntry) -> AuditEntry:
        with self._lock:
            self._entries.append(entry)
            self._by_request[entry.request_id] = entry
            return entry

    def get(self, request_id: str) -> AuditEntry | None:
        return self._by_request.get(request_id)

    def all(self) -> list[AuditEntry]:
        return list(self._entries)

    def replay(self, request_id: str) -> dict:
        """回放：返回该请求的完整审计记录（可据此复现固定配置档与证据引用）。"""
        entry = self._by_request.get(request_id)
        if entry is None:
            raise KeyError(f"无审计记录：{request_id!r}")
        return entry.model_dump()