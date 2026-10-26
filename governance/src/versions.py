"""M7 版本与可回滚（versions）。

维护「知识版本」「拓扑 generation」等命名资源的**当前激活指针**与历史，
支持 ``activate``（前推）与 ``rollback``（回退到上一激活版本），拒绝回退到不存在的版本。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VersionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    previous: str | None = None
    activated_at: str = Field(default_factory=_now_iso)


class VersionRegistry:
    def __init__(self) -> None:
        self._active: dict[str, str] = {}
        self._history: dict[str, list[str]] = {}
        self._lock = threading.RLock()

    def activate(self, name: str, version: str) -> VersionRecord:
        with self._lock:
            previous = self._active.get(name)
            self._active[name] = version
            history = self._history.setdefault(name, [])
            if not history or history[-1] != version:
                history.append(version)
            return VersionRecord(name=name, version=version, previous=previous)

    def active(self, name: str) -> str | None:
        return self._active.get(name)

    def rollback(self, name: str) -> VersionRecord:
        """回退到上一个激活版本；无上一版本时抛 ``KeyError``（不回退到未激活版本）。"""
        with self._lock:
            history = self._history.get(name, [])
            if len(history) < 2:
                raise KeyError(f"资源 {name!r} 无可回滚的历史版本")
            history.pop()  # 丢弃当前
            target = history[-1]
            previous = self._active.get(name)
            self._active[name] = target
            return VersionRecord(name=name, version=target, previous=previous)

    def history(self, name: str) -> list[str]:
        return list(self._history.get(name, []))


__all__ = ["VersionRegistry", "VersionRecord"]
