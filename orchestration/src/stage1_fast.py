"""M6 Stage 1 —— 快速记得（fast recall）。

Stage 1 只允许呈现**低延迟、可达阈值且有权限**的线索：

- 记忆脑的 ``memory.cluster.pending``（未达次数阈值 / 高离散度 / 无权限命中）
  **一律不得**被包装成事实，仅留在 Stage 2 的核对结果中；
- 其余各脑已判定为 ``ok`` 的主张作为快速线索返回。

Stage 1 只是**部分结果**（``partial=True``），不构成最终结论。
"""

from __future__ import annotations

from typing import Any

from brains.protocol import BrainName, BrainResult, ResultStatus
from pydantic import BaseModel, ConfigDict, Field

#: 规范顺序：memory -> knowledge -> structured
_ORDER: tuple[BrainName, ...] = (
    BrainName.memory,
    BrainName.knowledge,
    BrainName.structured,
)

#: 明确表示「待核对线索」的字段后缀 / 文本前缀（对齐记忆脑 pending 语义）。
_PENDING_FIELD_SUFFIX = ".pending"
_PENDING_TEXT_PREFIX = "待核对"


def is_pending_claim(claim) -> bool:
    """判定一条主张是否为「待核对线索」（不得作为事实）。"""
    if claim.field.endswith(_PENDING_FIELD_SUFFIX):
        return True
    return (claim.value or "").startswith(_PENDING_TEXT_PREFIX)


class StageFastRecall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = "fast_recall"
    items: list[dict[str, Any]] = Field(default_factory=list)
    partial: bool = True


def build_fast_recall(per_brain: dict[BrainName, BrainResult]) -> StageFastRecall:
    """从各脑结果中提取可作快速线索的条目（排除 pending）。"""
    ordered = [b for b in _ORDER if b in per_brain]
    ordered += [b for b in per_brain if b not in _ORDER]

    items: list[dict[str, Any]] = []
    for brain in ordered:
        result = per_brain[brain]
        if result.status is not ResultStatus.ok:
            continue
        for claim in result.claims:
            if is_pending_claim(claim):
                continue
            items.append(
                {
                    "summary": f"{claim.field}={claim.value}",
                    "source": brain.value,
                    "evidence_id": claim.evidence_id,
                }
            )
    return StageFastRecall(items=items, partial=True)


__all__ = ["StageFastRecall", "build_fast_recall", "is_pending_claim"]
