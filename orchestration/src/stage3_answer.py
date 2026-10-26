"""M6 Stage 3 —— 正式回答（final_answer）。

硬约束（逐条落地）：

- ``citations`` 必须**全部能在 evidence 列表解析**（验收 23）：构建时以
  ``evidence`` 的 ``evidence_id`` 为准做白名单过滤，任何无法解析的引用都不写出；
- ``time_scope.confidence == uncertain`` 时**必须**显式标注 ``limitations``（验收 24）；
- 口径不可比的结果**并列呈现、不强行相加**（验收 25）——由 ``conflict`` / M2 ``merge`` 保证，
  本模块据 ``MergeOutcome`` 决定置信度与说明。
"""

from __future__ import annotations

from brains.protocol import Confidence, EvidenceRef, TimeScope
from merge import MergeOutcome
from pydantic import BaseModel, ConfigDict, Field

#: uncertain 时间的显式限制说明（验收 24 必须出现）。
UNCERTAIN_LIMITATION = (
    "时间范围不确定（confidence=uncertain）：不得将结果视为精确结论，存在数据缺口与口径漂移。"
)

_ANSWER_NO_MATCH = "未命中可用证据，不提供数值结论。"
_ANSWER_OK = "基于可用证据的回答；口径不可比的结果并列呈现，未强行相加。"


class StageFinalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = "final_answer"
    answer: str
    citations: list[str] = Field(default_factory=list)
    confidence: str
    limitations: list[str] = Field(default_factory=list)


def build_final_answer(
    *,
    evidence: list[EvidenceRef],
    outcome: MergeOutcome,
    warnings: list[str] | None = None,
    time_scope: TimeScope | None = None,
    extra_limitations: list[str] | None = None,
) -> StageFinalAnswer:
    """组织正式回答：citations 可解析、uncertain 显式标注、异类不相加。"""
    known = {ref.evidence_id for ref in evidence}

    # citations 以 evidence 为准（保证验收 23：全部可解析）。
    citations: list[str] = []
    for ref in evidence:
        if ref.evidence_id in known and ref.evidence_id not in citations:
            citations.append(ref.evidence_id)

    limitations: list[str] = list(warnings or [])
    limitations.extend(extra_limitations or [])

    uncertain = time_scope is not None and time_scope.confidence is Confidence.uncertain

    if uncertain:
        _append_unique(limitations, UNCERTAIN_LIMITATION)
        confidence = "low"
    elif outcome.no_match:
        confidence = "low"
    else:
        confidence = "medium"

    answer = _ANSWER_NO_MATCH if outcome.no_match else _ANSWER_OK

    if confidence == "low" and not limitations:
        limitations.append("证据不足或时间范围不确定：结果仅供参考，不得作为结论发布。")

    return StageFinalAnswer(
        stage="final_answer",
        answer=answer,
        citations=citations,
        confidence=confidence,
        limitations=limitations,
    )


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


__all__ = ["StageFinalAnswer", "build_final_answer", "UNCERTAIN_LIMITATION"]
