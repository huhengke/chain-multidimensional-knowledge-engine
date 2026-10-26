"""M6 编排源码包（orchestration.src）。"""

from __future__ import annotations

from .conflict import ConflictResolver, resolve_conflicts, resolved_values
from .orchestrator import OrchestrationResult, Orchestrator, Stages
from .stage1_fast import StageFastRecall, build_fast_recall
from .stage2_verify import StageVerification, build_verification, run_brains_parallel
from .stage3_answer import (
    UNCERTAIN_LIMITATION,
    StageFinalAnswer,
    build_final_answer,
)

__all__ = [
    "Orchestrator",
    "OrchestrationResult",
    "Stages",
    "StageFastRecall",
    "build_fast_recall",
    "StageVerification",
    "build_verification",
    "run_brains_parallel",
    "StageFinalAnswer",
    "build_final_answer",
    "UNCERTAIN_LIMITATION",
    "ConflictResolver",
    "resolve_conflicts",
    "resolved_values",
]
