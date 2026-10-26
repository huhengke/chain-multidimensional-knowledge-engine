"""M6 Stage 2 —— 并行核对（verification）。

硬约束：三脑**先独立计算**（可真实并发），控制面/编排器只做汇总与冲突消解，
**不跨脑互相填充**，也不把某脑的推算回灌给另一脑。

并发调度使用标准库 ``concurrent.futures.ThreadPoolExecutor``（真实并发）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from brains.protocol import (
    BrainError,
    BrainName,
    BrainQuery,
    BrainResult,
    ResultStatus,
)
from pydantic import BaseModel, ConfigDict, Field

#: 未接入脑的构造器签名：``(target, query) -> BrainResult``。
UnavailableFactory = Callable[[BrainName, BrainQuery], BrainResult]


class StageVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = "verification"
    per_brain: dict[str, Any] = Field(default_factory=dict)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    resolved: list[Any] = Field(default_factory=list)


def _default_unavailable(target: BrainName, query: BrainQuery) -> BrainResult:
    return BrainResult(
        brain=target,
        request_id=query.request_id,
        trace_id=query.trace_id,
        day_key=query.day_key,
        status=ResultStatus.unavailable,
        error=BrainError(code="BRAIN_NOT_CONNECTED", message=f"{target.value} 脑尚未接入"),
    )


def run_brains_parallel(
    targets,
    brains: dict[BrainName, Any],
    build_query: Callable[[BrainName], BrainQuery],
    *,
    unavailable_factory: UnavailableFactory | None = None,
    max_workers: int = 3,
) -> dict[BrainName, BrainResult]:
    """并发调用各脑，**先独立计算后汇总**（返回顺序与 ``targets`` 一致）。

    未注入的脑交由 ``unavailable_factory``（缺省为显式 ``unavailable`` 错误），
    **绝不伪造数值**。
    """
    target_list = list(targets)
    if not target_list:
        return {}

    factory = unavailable_factory or _default_unavailable

    def _call(target: BrainName) -> tuple[BrainName, BrainResult]:
        query = build_query(target)
        brain = brains.get(target)
        if brain is not None:
            return target, brain.query(query)
        return target, factory(target, query)

    workers = max(1, min(max_workers, len(target_list)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return dict(executor.map(_call, target_list))


def build_verification(
    per_brain: dict[BrainName, BrainResult],
    conflicts,
    resolved,
) -> StageVerification:
    """汇总各脑结果与冲突消解结论（冲突不静默吞掉）。"""
    return StageVerification(
        per_brain={
            brain.value: result.model_dump(mode="json") for brain, result in per_brain.items()
        },
        conflicts=[
            item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
            for item in conflicts
        ],
        resolved=list(resolved),
    )


__all__ = ["StageVerification", "build_verification", "run_brains_parallel"]
