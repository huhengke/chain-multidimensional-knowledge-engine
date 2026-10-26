"""受控专业知识推理（确定性规则引擎）。

在检索之上做**受控推理**：识别问题事实/时间/适用域 → 匹配定义/规则/例外/版本/相似案例
→ 输出结论 + 适用条件 + 反例 + 待补事实 + 逐项证据（``KnowledgeReasoningResult``）。

- 规则表是**显式**的，由调用方提供（``ReasoningRule``）；不伪造学术/业务共识。
- 高风险领域（法律/医疗/安全/质量）标注 ``domain_risk`` 与适用范围；
  **不得把一般规则宣称为个案结论**：当规则前提未被事实满足时进 ``missing_facts``。
- 全程 ``Decimal`` 定点数，无浮点。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from brains.knowledge.src.ontology import max_risk, risk_rank
from brains.knowledge.src.runtime_adapter import tokenize

_QUANT = Decimal("0.000001")


def _quantize(value: Decimal) -> str:
    return format(value.quantize(_QUANT, rounding=ROUND_HALF_UP).normalize(), "f")


class ReasoningRule(BaseModel):
    """显式专业规则。``exceptions`` 为反例/例外，``missing_facts`` 为需补事实。"""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    definition: str = ""
    conditions: list[str] = Field(default_factory=list)
    conclusion: str = Field(min_length=1)
    exceptions: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    version: str | None = None
    risk: str = "low"
    evidence_ids: list[str] = Field(default_factory=list)


class ProfessionalReasoner:
    """确定性规则引擎。"""

    def __init__(self, rules: list[ReasoningRule] | None = None) -> None:
        self._rules: list[ReasoningRule] = list(rules or [])

    def add_rule(self, rule: ReasoningRule) -> ReasoningRule:
        self._rules.append(rule)
        return rule

    @property
    def rules(self) -> list[ReasoningRule]:
        return list(self._rules)

    # ---- 核心 -------------------------------------------------------------
    def reason(
        self,
        result_id: str,
        query_text: str,
        *,
        domain: str,
        evidence_ids: list[str] | None = None,
        rules: list[ReasoningRule] | None = None,
        domain_risk_level: str = "low",
    ) -> "KnowledgeReasoningResult":
        from brains.knowledge.src.contract import KnowledgeReasoningResult

        evidence_ids = list(evidence_ids or [])
        candidates = list(rules if rules is not None else self._rules)
        q_tokens = set(tokenize(query_text))

        matched: list[tuple[Decimal, ReasoningRule]] = []
        for rule in candidates:
            score = self._match_score(rule, domain, q_tokens)
            if score > 0:
                matched.append((score, rule))
        matched.sort(key=lambda item: (-item[0], item[1].rule_id))

        if not matched:
            return KnowledgeReasoningResult(
                result_id=result_id,
                conclusion=(
                    "未检索到可推导个案结论的规则；仅并列证据与适用条件，不作裁决。"
                ),
                applicable_conditions=[],
                counter_examples=[],
                missing_facts=["缺少匹配规则或个案事实"],
                evidence_ids=evidence_ids,
                domain_risk=_as_risk(max(domain_risk_level, "low")),
                confidence="0.3" if evidence_ids else "0",
            )

        best_score, best_rule = matched[0]
        applicable_conditions: list[str] = []
        counter_examples: list[str] = []
        missing_facts: list[str] = []
        merged_evidence: list[str] = list(evidence_ids)
        risk_levels: list[str] = [domain_risk_level, best_rule.risk]

        for _score, rule in matched:
            for condition in rule.conditions:
                if condition not in applicable_conditions:
                    applicable_conditions.append(condition)
                # 前提未被查询事实覆盖 -> 待补事实（不得默认成立）
                if not (set(tokenize(condition)) & q_tokens):
                    missing = f"未确认前提：{condition}"
                    if missing not in missing_facts:
                        missing_facts.append(missing)
            for exception in rule.exceptions:
                if exception not in counter_examples:
                    counter_examples.append(exception)
            for fact in rule.missing_facts:
                if fact not in missing_facts:
                    missing_facts.append(fact)
            for eid in rule.evidence_ids:
                if eid not in merged_evidence:
                    merged_evidence.append(eid)
            risk_levels.append(rule.risk)

        version_note = f"（规则版本 {best_rule.version}）" if best_rule.version else ""
        conclusion = f"{best_rule.conclusion}{version_note}"

        # 置信度：命中规则得分 × 前提满足率，最多两条规则共同支撑。
        satisfied = len(applicable_conditions) - len(
            [f for f in missing_facts if f.startswith("未确认前提：")]
        )
        total_conditions = len(applicable_conditions)
        if total_conditions == 0:
            ratio = Decimal(1)
        else:
            ratio = Decimal(max(satisfied, 0)) / Decimal(total_conditions)
        confidence = _quantize(min(best_score, Decimal(1)) * ratio)

        return KnowledgeReasoningResult(
            result_id=result_id,
            conclusion=conclusion,
            applicable_conditions=applicable_conditions,
            counter_examples=counter_examples,
            missing_facts=missing_facts,
            evidence_ids=merged_evidence,
            domain_risk=_as_risk(max_risk(*risk_levels)),
            confidence=confidence,
        )

    def _match_score(self, rule: ReasoningRule, domain: str, q_tokens: set[str]) -> Decimal:
        """规则匹配得分：领域命中 + 规则条件/定义与查询的词项重合。"""
        score = Decimal(0)
        if rule.domain == domain:
            score += Decimal("0.6")
        rule_tokens = set(tokenize(rule.definition)) | set(tokenize(rule.conclusion))
        for condition in rule.conditions:
            rule_tokens |= set(tokenize(condition))
        if q_tokens and rule_tokens:
            inter = len(q_tokens & rule_tokens)
            if inter:
                score += Decimal(inter) / Decimal(len(q_tokens | rule_tokens))
        return score


def _as_risk(level: str) -> str:
    return level if level in ("low", "medium", "high") else "low"


__all__ = ["ReasoningRule", "ProfessionalReasoner"]
