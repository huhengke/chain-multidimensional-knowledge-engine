"""M6 跨脑编排器（Orchestrator）。

编排顺序：

1. **fast_recall**：先走记忆脑快速通道，产出低延迟、可达阈值且有权限的线索；
2. **并行三脑**：``ThreadPoolExecutor`` 真实并发，三脑**先独立计算**；
3. **merge / 冲突消解**：复用 M2 ``Merger``（异类不相加、冲突不静默）；
4. **final_answer**：citations 可解析、uncertain 显式标注限制；
5. 返回三段式结果。

控制面是唯一编排者、不是事实源：未接入的脑返回显式 ``unavailable``，不伪造数值。
"""

from __future__ import annotations

from typing import Any

from brains.protocol import (
    BrainName,
    BrainQuery,
    BrainResult,
    Claim,
    EvidenceRef,
    MergeMode,
    TimeScope,
)
from evidence import EvidenceAggregator
from pydantic import BaseModel, ConfigDict, Field
from router import ALL_TARGETS

from .conflict import resolve_conflicts, resolved_values
from .stage1_fast import StageFastRecall, build_fast_recall
from .stage2_verify import StageVerification, build_verification, run_brains_parallel
from .stage3_answer import StageFinalAnswer, build_final_answer


class Stages(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fast_recall: StageFastRecall
    verification: StageVerification
    final_answer: StageFinalAnswer


class OrchestrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    trace_id: str
    day_key: str
    fast_recall: StageFastRecall
    verification: StageVerification
    final_answer: StageFinalAnswer
    evidence: list[EvidenceRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)

    @property
    def stages(self) -> dict[str, Any]:
        """三段式视图（fast_recall / verification / final_answer）。"""
        return {
            "fast_recall": self.fast_recall,
            "verification": self.verification,
            "final_answer": self.final_answer,
        }


class Orchestrator:
    def __init__(
        self,
        brains: dict[BrainName, Any] | None = None,
        *,
        max_workers: int = 3,
        evidence_aggregator: EvidenceAggregator | None = None,
        unavailable_factory=None,
    ) -> None:
        self.brains: dict[BrainName, Any] = dict(brains) if brains else {}
        self.max_workers = max_workers
        self.evidence = evidence_aggregator or EvidenceAggregator()
        self.unavailable_factory = unavailable_factory

    def _build_query(
        self,
        *,
        request_id: str,
        trace_id: str,
        day_key: str,
        raw_query: str,
        profile_id: str | None,
        time_scope: TimeScope | None,
        constraints: dict[str, Any],
    ) -> BrainQuery:
        return BrainQuery(
            request_id=request_id,
            trace_id=trace_id,
            day_key=day_key,
            raw_query=raw_query,
            profile_id=profile_id,
            time_scope=time_scope,
            constraints=constraints or {},
        )

    def run(
        self,
        *,
        request_id: str,
        trace_id: str,
        day_key: str,
        raw_query: str,
        profile_id: str | None = None,
        time_scope: TimeScope | None = None,
        targets=None,
        merge_mode: MergeMode = MergeMode.separate,
        constraints: dict[str, Any] | None = None,
    ) -> OrchestrationResult:
        target_list = list(targets) if targets else list(ALL_TARGETS)

        query = self._build_query(
            request_id=request_id,
            trace_id=trace_id,
            day_key=day_key,
            raw_query=raw_query,
            profile_id=profile_id,
            time_scope=time_scope,
            constraints=constraints or {},
        )

        # ---- Stage 1：快速记得（低延迟通道，记忆脑） ------------------------
        fast_per_brain: dict[BrainName, BrainResult] = {}
        if BrainName.memory in self.brains and BrainName.memory in target_list:
            fast_per_brain[BrainName.memory] = self.brains[BrainName.memory].query(query)
        fast_recall = build_fast_recall(fast_per_brain)

        # ---- Stage 2：并行核对（三脑先独立计算后汇总） -----------------------
        per_brain = run_brains_parallel(
            target_list,
            self.brains,
            lambda _target: query,
            unavailable_factory=self.unavailable_factory,
            max_workers=self.max_workers,
        )

        # ---- merge / 冲突消解（异类不相加、冲突不静默） ---------------------
        evidence = self.evidence.aggregate(
            {brain: list(result.evidence) for brain, result in per_brain.items()}
        )
        outcome = resolve_conflicts(list(per_brain.values()), merge_mode)

        warnings: list[str] = []
        for result in per_brain.values():
            warnings.extend(result.warnings)
        warnings.extend(outcome.notes)

        verification = build_verification(
            per_brain, outcome.conflicts, resolved_values(outcome)
        )

        # ---- Stage 3：正式回答 ----------------------------------------------
        final_answer = build_final_answer(
            evidence=evidence,
            outcome=outcome,
            warnings=warnings,
            time_scope=time_scope,
        )

        return OrchestrationResult(
            request_id=request_id,
            trace_id=trace_id,
            day_key=day_key,
            fast_recall=fast_recall,
            verification=verification,
            final_answer=final_answer,
            evidence=evidence,
            warnings=warnings,
            claims=outcome.claims,
        )


__all__ = ["Orchestrator", "OrchestrationResult", "Stages"]
