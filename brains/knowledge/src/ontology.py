"""知识本体存储：领域 / 概念 / 条件 / 关系 / 版本。

- 概念路径形成**知识分枝**：``OntologyStore.attach_to_topology`` 把概念锚点写入
  M1 拓扑 ``inner`` 层（概念锚点 -> inner），树名前缀 ``knowledge.``。
- 领域风险分级供门禁使用：``legal / medical / safety / quality`` 为高风险。
- 领域/概念词表是**显式规则表**（可由调用方 ``register_concept`` 扩展），
  不做学术或业务共识的伪造。
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from brains.knowledge.src.runtime_adapter import slugify
from brains.knowledge.src.classification import ANCHORS, CLASSIFICATION_VERSION, normalized, term_present, topic_path

HIGH_RISK_DOMAINS = frozenset({"legal", "medical", "safety", "quality"})

# 领域 -> (标签, 关键词)
DOMAIN_LEXICON: dict[str, tuple[str, list[str]]] = {
    "legal": ("法律", ["法律", "法规", "法条", "条款", "合同", "责任", "违约", "诉讼", "判决", "裁决", "合规", "义务", "权利", "赔偿"]),
    "medical": ("医疗", ["医疗", "诊断", "用药", "剂量", "病历", "临床", "患者", "处方", "禁忌"]),
    "safety": ("安全", ["安全", "事故", "隐患", "防护", "操作规程", "应急预案", "危险", "风险控制", "警示"]),
    "quality": ("质量", ["质量", "放行", "检验", "合格", "不合格", "批次", "抽样", "质检", "验收", "出厂"]),
    "equipment": ("设备", ["设备", "维护", "检修", "保养", "部件", "温度", "压力", "转速", "润滑", "装配"]),
    "finance": ("财务", ["财务", "成本", "预算", "利润", "费用", "结算", "折旧", "税率"]),
    "humanities": ("人文典籍", list(ANCHORS["humanities"])),
    "general": ("待归类", []),
}
for _domain, _anchors in ANCHORS.items():
    _label, _terms = DOMAIN_LEXICON[_domain]
    DOMAIN_LEXICON[_domain] = (_label, list(dict.fromkeys([*_terms, *_anchors])))

_CONDITION_MARKERS = ("若", "如果", "当", "除非", "一旦", "在下列情况", "when", "if", "unless")

_RISK_RANK = {"low": 0, "medium": 1, "high": 2}


def risk_rank(level: str) -> int:
    return _RISK_RANK.get(level, 0)


def max_risk(*levels: str) -> str:
    best = "low"
    for level in levels:
        if risk_rank(level) > risk_rank(best):
            best = level
    return best


def domain_risk(domain: str) -> str:
    if domain in HIGH_RISK_DOMAINS:
        return "high"
    if domain in ("equipment", "finance"):
        return "medium"
    return "low"


def domain_label(domain: str) -> str:
    return DOMAIN_LEXICON.get(domain, DOMAIN_LEXICON["general"])[0]


class Concept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    parent: str | None = None
    aliases: list[str] = Field(default_factory=list)


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str = Field(min_length=1)
    version: str | None = None
    risk: str = "low"


class Annotation(BaseModel):
    """自动标注结果：候选领域/概念/条件/关联实体/版本/置信度。"""

    model_config = ConfigDict(extra="forbid")

    domain: str = "general"
    concepts: list[str] = Field(default_factory=list)
    concept_ids: list[str] = Field(default_factory=list)
    concept_path: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    version: str | None = None
    confidence: str = "0"
    related_domains: list[str] = Field(default_factory=list)


class OntologyStore:
    """领域 / 概念 / 关系 / 版本 本体存储。"""

    def __init__(self, version: str = CLASSIFICATION_VERSION) -> None:
        self.version = version
        self._concepts: dict[str, Concept] = {}
        self._relations: list[Relation] = []
        for domain, (label, _keywords) in DOMAIN_LEXICON.items():
            self._concepts[domain] = Concept(concept_id=domain, label=label, domain=domain)

    # ---- 注册 -------------------------------------------------------------
    def register_concept(self, concept: Concept) -> Concept:
        self._concepts[concept.concept_id] = concept
        return concept

    def register_relation(self, relation: Relation) -> Relation:
        self._relations.append(relation)
        return relation

    @property
    def concepts(self) -> dict[str, Concept]:
        return dict(self._concepts)

    @property
    def relations(self) -> list[Relation]:
        return list(self._relations)

    # ---- 标注 -------------------------------------------------------------
    def annotate(
        self, text: str, heading_path: list[str] | None = None, version: str | None = None,
        *, document_context: dict | None = None, title: str = ""
    ) -> Annotation:
        heading_path = heading_path or []
        matching_text = normalized(text)
        scores: dict[str, int] = {}
        matched_terms: dict[str, list[str]] = {}
        for domain, (_label, keywords) in DOMAIN_LEXICON.items():
            hits = [kw for kw in keywords if term_present(matching_text, kw, normalized_text=True)]
            if hits:
                scores[domain] = sum(len(kw) for kw in hits)
                matched_terms[domain] = hits
        # 已注册概念（含别名）也参与标注
        registered_hits: list[Concept] = []
        for concept in self._concepts.values():
            terms = [concept.label, *concept.aliases]
            if any(term and term_present(matching_text, term, normalized_text=True) for term in terms):
                registered_hits.append(concept)
                scores[concept.domain] = scores.get(concept.domain, 0) + len(concept.label)

        if scores:
            domain = max(sorted(scores), key=lambda d: scores[d])
        else:
            domain = "general"
        if document_context:
            domain = document_context["domain"]

        concepts: list[str] = []
        concept_ids: list[str] = []
        seen: set[str] = set()
        for term in sorted(matched_terms.get(domain, []), key=lambda t: (-len(t), t)):
            cid = f"{domain}:{slugify(term)}"
            if cid in seen:
                continue
            seen.add(cid)
            concepts.append(term)
            concept_ids.append(cid)
        for concept in sorted(registered_hits, key=lambda c: c.concept_id):
            if document_context and concept.domain != domain:
                continue
            if concept.concept_id in seen:
                continue
            seen.add(concept.concept_id)
            concepts.append(concept.label)
            concept_ids.append(concept.concept_id)

        conditions = self._extract_conditions(text)
        entities = sorted({*concepts, *(h for h in heading_path if h)})
        concept_path = [domain_label(domain), *concepts[:3]]
        if document_context:
            concept_path = [domain_label(domain), *topic_path(domain, text, heading_path, title)]

        # Manual subject correction cannot change the confidence of unchanged
        # source evidence. Count original signals, independently of the main tree.
        matches = len({term for terms in matched_terms.values() for term in terms}) + len(conditions)
        if matches == 0:
            confidence = "0.3"
        else:
            ratio = Decimal(min(matches, 10)) / Decimal(10)
            confidence = format(ratio.normalize(), "f")

        return Annotation(
            domain=domain,
            concepts=concepts,
            concept_ids=concept_ids,
            concept_path=concept_path,
            conditions=conditions,
            entities=entities,
            version=version,
            confidence=confidence,
            related_domains=[d for d, terms in ANCHORS.items() if d != domain and any(term_present(matching_text, t, normalized_text=True) for t in terms)],
        )

    @staticmethod
    def _extract_conditions(text: str) -> list[str]:
        sentences = [s.strip() for s in _split_sentences(text) if s.strip()]
        out: list[str] = []
        for sentence in sentences:
            low = sentence.lower()
            if any(marker.lower() in low for marker in _CONDITION_MARKERS):
                out.append(sentence)
        return out

    # ---- 概念路径 -> 知识分枝（写 M1 拓扑 inner 层）----------------------
    def attach_to_topology(
        self,
        payload_store,
        units: list,
        annotations: dict,
        evidence_by_unit: dict[str, list[str]],
        ruler,
        day_key: str,
    ) -> list:
        """把知识单元的概念锚点写入 M1 拓扑（概念锚点 -> inner 层）。

        ``annotations`` 为 ``{unit_id: Annotation}``；返回写入的 ``TopologyNode``
        列表；树名前缀 ``knowledge.``。
        """
        from topology.src.node import Layer, TopologyNode

        nodes = []
        for unit in units:
            annotation = annotations.get(unit.unit_id)
            domain = getattr(annotation, "domain", "general") if annotation else "general"
            concept_path = list(unit.concept_path) or [domain_label(domain)]
            tree_id = f"knowledge.{domain}"
            ring_index = ruler.ring_index(tree_id, day_key)
            node_id = TopologyNode.build_id(
                tree_id, day_key, "concept", *[slugify(p) for p in concept_path], unit.unit_id
            )
            payload_ref = payload_store.put(
                "inner",
                {
                    "kind": "knowledge-concept-anchor",
                    "unit_id": unit.unit_id,
                    "document_id": unit.document_id,
                    "domain": domain,
                    "concept_path": concept_path,
                    "conditions": list(getattr(annotation, "conditions", []) or []),
                    "entities": list(getattr(annotation, "entities", []) or []),
                    "ontology_version": self.version,
                },
            )
            node = TopologyNode(
                node_id=node_id,
                tree_id=tree_id,
                day_key=day_key,
                ring_index=ring_index,
                layer=Layer.inner,
                generation=1,
                payload_ref=payload_ref,
                evidence_refs=list(evidence_by_unit.get(unit.unit_id, [])),
            )
            nodes.append(node)
        return nodes


def _split_sentences(text: str) -> list[str]:
    import re

    return re.split(r"[。；;\n]+", text or "")


def concept_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


__all__ = [
    "HIGH_RISK_DOMAINS",
    "DOMAIN_LEXICON",
    "Concept",
    "Relation",
    "Annotation",
    "OntologyStore",
    "domain_risk",
    "domain_label",
    "max_risk",
    "risk_rank",
]
