"""brain 边界包：统一调用契约。M3/M4/M5 内部实现不在本增量范围。"""

from brains.protocol import (
    Brain,
    BrainError,
    BrainName,
    BrainQuery,
    BrainResult,
    Claim,
    Confidence,
    Constraints,
    EvidenceRef,
    Granularity,
    MergeMode,
    ResultStatus,
    TimeScope,
)

__all__ = [
    "Brain",
    "BrainName",
    "BrainQuery",
    "BrainResult",
    "BrainError",
    "Claim",
    "EvidenceRef",
    "TimeScope",
    "Constraints",
    "Granularity",
    "Confidence",
    "MergeMode",
    "ResultStatus",
]