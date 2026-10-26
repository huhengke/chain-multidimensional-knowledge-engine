"""M4 知识运行时统一证据契约（六件套）。

字段、必填与 ``docs/contracts/knowledge-evidence.schema.json`` **逐字段一致**：
``KnowledgeSource / CanonicalDocument / KnowledgeUnit / KnowledgeEvidence /
KnowledgeRoute / KnowledgeReasoningResult``。

铁律：
* ``KnowledgeEvidence.time_status`` 只有 ``known`` / ``undetermined`` 两态；
  无可信时间的材料一律 ``undetermined``，**绝不伪造时序**。
* ``confidence`` 用字符串化定点数（``0`` / ``0.x`` / ``1``），永不浮点。
* 所有模型 ``additionalProperties=false``（pydantic ``extra="forbid"``），
  知识树只读取通过本契约校验的证据。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# JSON Schema 中 confidence 的正则：^(0|1)(\.\d+)?$
CONFIDENCE_PATTERN = r"^(0|1)(\.\d+)?$"

TimeStatus = Literal["known", "undetermined"]
DomainRisk = Literal["low", "medium", "high"]


class KnowledgeSource(BaseModel):
    """来源。必填 ``source_id`` / ``kind``。"""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    uri: str | None = None
    authority: str | None = None
    license: str | None = None
    retrieved_at: str | None = None


class UnitLocator(BaseModel):
    """知识单元定位：页码 / 锚点 / 媒体时间码（均可为 null）。"""

    model_config = ConfigDict(extra="forbid")

    page: int | None = None
    anchor: str | None = None
    timecode: str | None = None


class CanonicalDocument(BaseModel):
    """规范文档。必填 ``document_id`` / ``source_id``。"""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    title: str | None = None
    parse_version: str | None = None
    lang: str | None = None
    content_hash: str | None = None
    source_object_ref: str | None = None
    ingest_job_id: str | None = None
    document_status: str | None = None


class KnowledgeUnit(BaseModel):
    """可引用知识单元。必填 ``unit_id`` / ``document_id``。"""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    unit_type: str | None = None
    text_ref: str | None = None
    locator: UnitLocator = Field(default_factory=UnitLocator)
    concept_path: list[str] = Field(default_factory=list)
    version: str | None = None
    parent_unit_id: str | None = None
    chunk_level: int | None = Field(default=None, ge=0)
    chunk_order: int | None = Field(default=None, ge=0)
    index_version: str | None = None
    embedding_space_id: str | None = None


class KnowledgeEvidence(BaseModel):
    """证据。必填 ``evidence_id`` / ``source_id`` / ``document_id`` / ``unit_id`` / ``time_status``。

    ``locator`` 为自由对象（对应 JSON Schema 的 ``{"type": "object"}``），
    统一保存 ``{page, anchor, timecode}``，保证可展开到原文定位。
    """

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    locator: dict[str, Any] = Field(default_factory=dict)
    content_version: str | None = None
    parse_version: str | None = None
    permission_scope: list[str] = Field(default_factory=list)
    time_status: TimeStatus
    citation_chain: list[str] = Field(default_factory=list)
    confidence: str = Field(default="0", pattern=CONFIDENCE_PATTERN)
    source_object_ref: str | None = None
    runtime_version: str | None = None
    index_version: str | None = None


class KnowledgeRoute(BaseModel):
    """检索路由：候选召回与最终选中。必填 ``route_id``。"""

    model_config = ConfigDict(extra="forbid")

    route_id: str = Field(min_length=1)
    query: str | None = None
    candidates: list[str] = Field(default_factory=list)
    selected: list[str] = Field(default_factory=list)
    retrieval_version: str | None = None
    lexical_score: dict[str, str] = Field(default_factory=dict)
    semantic_score: dict[str, str] = Field(default_factory=dict)
    fusion_score: dict[str, str] = Field(default_factory=dict)
    rerank_score: dict[str, str] = Field(default_factory=dict)
    cluster_id: str | None = None
    cluster_generation: int | None = Field(default=None, ge=1)
    cross_brain_link_ids: list[str] = Field(default_factory=list)


class KnowledgeReasoningResult(BaseModel):
    """受控专业推理结果。必填 ``result_id`` / ``conclusion`` / ``domain_risk``。"""

    model_config = ConfigDict(extra="forbid")

    result_id: str = Field(min_length=1)
    conclusion: str
    applicable_conditions: list[str] = Field(default_factory=list)
    counter_examples: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    domain_risk: DomainRisk
    confidence: str | None = None
    runtime_version: str | None = None
    review_status: str | None = None


# 六件套名称 -> 模型（供契约自检与模块导出）
SIX_PIECE_MODELS = {
    "KnowledgeSource": KnowledgeSource,
    "CanonicalDocument": CanonicalDocument,
    "KnowledgeUnit": KnowledgeUnit,
    "KnowledgeEvidence": KnowledgeEvidence,
    "KnowledgeRoute": KnowledgeRoute,
    "KnowledgeReasoningResult": KnowledgeReasoningResult,
}

__all__ = [
    "CONFIDENCE_PATTERN",
    "TimeStatus",
    "DomainRisk",
    "KnowledgeSource",
    "UnitLocator",
    "CanonicalDocument",
    "KnowledgeUnit",
    "KnowledgeEvidence",
    "KnowledgeRoute",
    "KnowledgeReasoningResult",
    "SIX_PIECE_MODELS",
]
