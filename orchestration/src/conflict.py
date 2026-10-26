"""M6 跨脑冲突消解（conflict）。

与 M2 ``merge`` 的 ``Merger`` 协同，复用其硬不变式：

- 仅在**口径可比**（同单位）且存在结构化精确值时才由结构化结果优先
  （``structured_wins``）；
- 否则并列保留、标记 ``unresolved``，**绝不静默吞掉**冲突；
- 异类（不同单位/不可比）结果**不强行相加**。

本模块只做「编排层」的薄封装与结果整形，合并判定语义仍在 M2 ``Merger``。
"""

from __future__ import annotations

from brains.protocol import BrainResult, MergeMode
from merge import MergeOutcome, Merger


class ConflictResolver:
    def __init__(self, merge_mode: MergeMode = MergeMode.separate) -> None:
        self.merge_mode = merge_mode
        self.merger = Merger(merge_mode=merge_mode)

    def resolve(self, results) -> MergeOutcome:
        return self.merger.merge(list(results))


def resolve_conflicts(
    results, merge_mode: MergeMode = MergeMode.separate
) -> MergeOutcome:
    """跨脑冲突消解入口（异类不相加、冲突不静默）。"""
    return ConflictResolver(merge_mode).resolve(results)


def resolved_values(outcome: MergeOutcome) -> list[dict]:
    """取「已消解（结构化精确值胜出）」的冲突摘要；``unresolved`` 只留在 conflicts 里。"""
    return [
        {
            "field": conflict.field,
            "values": list(conflict.values),
            "sources": list(conflict.sources),
            "resolution": conflict.resolution,
        }
        for conflict in outcome.conflicts
        if conflict.resolution == "structured_wins"
    ]


__all__ = ["ConflictResolver", "resolve_conflicts", "resolved_values"]
