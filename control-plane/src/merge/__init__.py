"""M2 合并与冲突消解（merge）。

硬约束：
- 三脑结果**先独立计算**；``merge_mode = separate`` 为默认，异类结果**不强行相加**。
- **无命中绝不返回数值 0**：返回 ``no_match: true, claims: []``。
- 冲突**不静默吞掉**：记录并列取值、来源与消解结论（``structured_wins | unresolved``）。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field

from brains.protocol import BrainName, BrainResult, Claim, MergeMode, ResultStatus


class ConflictItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    values: list[str]
    sources: list[str]
    resolution: str  # structured_wins | unresolved


class MergeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[Claim] = Field(default_factory=list)
    conflicts: list[ConflictItem] = Field(default_factory=list)
    no_match: bool = False
    notes: list[str] = Field(default_factory=list)


def _is_numeric(text: str) -> bool:
    try:
        Decimal(str(text))
        return True
    except (InvalidOperation, ValueError):
        return False


class Merger:
    def __init__(self, merge_mode: MergeMode = MergeMode.separate) -> None:
        self.merge_mode = merge_mode

    def merge(self, results: list[BrainResult]) -> MergeOutcome:
        notes: list[str] = []
        claims: list[Claim] = []

        for result in results:
            if result.status is ResultStatus.unavailable:
                message = result.error.message if result.error else "脑尚未接入"
                notes.append(f"{result.brain.value}: {message}")
            elif result.status is ResultStatus.error:
                message = result.error.message if result.error else "脑返回错误"
                notes.append(f"{result.brain.value}: 错误 — {message}")
            elif result.status is ResultStatus.no_match:
                notes.append(f"{result.brain.value}: 无命中（不返回数值 0）")
            elif result.status is ResultStatus.ok:
                claims.extend(result.claims)

        by_field: dict[str, list[Claim]] = {}
        for claim in claims:
            by_field.setdefault(claim.field, []).append(claim)

        conflicts: list[ConflictItem] = []
        for field, field_claims in by_field.items():
            values = list(dict.fromkeys(c.value for c in field_claims))
            if len(values) > 1:
                sources = list(dict.fromkeys(c.source.value for c in field_claims))
                resolution = self._resolve(field_claims)
                conflicts.append(
                    ConflictItem(field=field, values=values, sources=sources, resolution=resolution)
                )
                notes.append(
                    f"字段 {field} 存在冲突（{' vs '.join(values)}），来源 {sources}，"
                    f"消解={resolution}；未静默吞掉"
                )

        if not claims and not conflicts:
            if not notes:
                notes.append("无命中：不返回数值 0")
            return MergeOutcome(claims=[], conflicts=[], no_match=True, notes=notes)

        if self.merge_mode is MergeMode.sum:
            merged_claims = self._sum_comparable(by_field, notes)
        else:
            merged_claims = claims

        return MergeOutcome(claims=merged_claims, conflicts=conflicts, no_match=False, notes=notes)

    @staticmethod
    def _resolve(field_claims: list[Claim]) -> str:
        """仅在口径可比（同单位）且有结构化精确值时由结构化结果优先；否则并列不可确定。"""
        structured = [c for c in field_claims if c.source is BrainName.structured]
        units = {c.unit for c in field_claims}
        comparable = len(units) == 1
        if structured and comparable and all(_is_numeric(c.value) for c in field_claims):
            return "structured_wins"
        return "unresolved"

    @staticmethod
    def _sum_comparable(by_field: dict[str, list[Claim]], notes: list[str]) -> list[Claim]:
        """仅当同字段、同单位、全部为数值时才相加；否则保持并列（异类不相加）。"""
        merged: list[Claim] = []
        for field, field_claims in by_field.items():
            units = {c.unit for c in field_claims}
            numeric = all(_is_numeric(c.value) for c in field_claims)
            if len(field_claims) > 1 and len(units) != 1:
                notes.append(f"字段 {field} 口径（单位）不可比，不强行相加，保持并列")
                merged.extend(field_claims)
                continue
            if len(field_claims) > 1 and numeric:
                total = sum((Decimal(str(c.value)) for c in field_claims), Decimal(0))
                source = (
                    BrainName.structured
                    if any(c.source is BrainName.structured for c in field_claims)
                    else field_claims[0].source
                )
                merged.append(
                    Claim(
                        field=field,
                        value=format(total.normalize(), "f"),
                        source=source,
                        unit=units.pop() if units else None,
                    )
                )
                notes.append(f"字段 {field} 同口径数值已相加（merge_mode=sum）")
            else:
                merged.extend(field_claims)
        return merged