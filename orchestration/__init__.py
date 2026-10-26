"""M6 跨脑编排与三阶段输出（orchestration）。

真实实现跨脑编排：

- Stage 1 ``fast_recall``：低延迟快速记得（只取可达阈值且有权限的线索）；
- Stage 2 ``verification``：三脑**先独立并行计算**后汇总，冲突交由 ``conflict`` 消解；
- Stage 3 ``final_answer``：正式回答（``citations`` 可全部解析、``uncertain`` 显式标注限制、
  口径不可比的结果并列不相加）。

本包自举 ``sys.path``，使 ``orchestration.src`` 与 M2 顶层模块（``router`` / ``evidence`` /
``merge``）脱离 ``conftest`` 也可独立导入。
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[1]
_CP_SRC = _ROOT / "control-plane" / "src"
for _p in (_ROOT, _CP_SRC):
    if str(_p) not in _sys.path:
        _sys.path.insert(0, str(_p))

from .src import (  # noqa: E402  (自举路径之后)
    ConflictResolver,
    OrchestrationResult,
    Orchestrator,
    Stages,
    StageFastRecall,
    StageFinalAnswer,
    StageVerification,
    build_fast_recall,
    build_final_answer,
    build_verification,
    resolve_conflicts,
    resolved_values,
    run_brains_parallel,
)

__all__ = [
    "Orchestrator",
    "OrchestrationResult",
    "Stages",
    "StageFastRecall",
    "StageVerification",
    "StageFinalAnswer",
    "build_fast_recall",
    "build_verification",
    "build_final_answer",
    "ConflictResolver",
    "resolve_conflicts",
    "resolved_values",
    "run_brains_parallel",
]
