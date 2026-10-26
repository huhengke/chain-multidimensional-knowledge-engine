"""脑统一调用契约边界（Brain / BrainQuery / BrainResult）。

三脑（M3 记忆脑 / M4 知识脑 / M5 结构化脑）统一遵循本契约；
控制面在目标脑未接入时返回**结构良好的显式错误**，不伪造任何数值结果。

数值一律以**字符串化定点数（Decimal 字符串）**表达，禁止浮点。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class BrainName(str, Enum):
    memory = "memory"
    knowledge = "knowledge"
    structured = "structured"


class ResultStatus(str, Enum):
    ok = "ok"
    no_match = "no_match"
    unavailable = "unavailable"
    error = "error"


class Granularity(str, Enum):
    none = "none"
    day = "day"
    month = "month"
    year = "year"  # 按需上卷的虚拟时间分组，不新增物理存储


class Confidence(str, Enum):
    exact = "exact"
    uncertain = "uncertain"


class MergeMode(str, Enum):
    separate = "separate"
    sum = "sum"


class TimeScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str
    end: str
    granularity: Granularity
    confidence: Confidence


class Constraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no_fabricated_precision: bool
    merge_mode: MergeMode


class Claim(BaseModel):
    """脑给出的单条主张。``value`` 为定点数字符串或文本。"""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    value: str
    source: BrainName
    unit: str | None = None
    evidence_id: str | None = None


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    source: BrainName
    ref: str = Field(min_length=1)
    detail: str | None = None


class BrainError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class BrainQuery(BaseModel):
    """统一脑调用入参。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    day_key: str
    raw_query: str
    profile_id: str | None = None
    time_scope: TimeScope | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)


class BrainResult(BaseModel):
    """统一脑调用出参。"""

    model_config = ConfigDict(extra="forbid")

    brain: BrainName
    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    day_key: str
    status: ResultStatus
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: BrainError | None = None


@runtime_checkable
class Brain(Protocol):
    """脑边界协议。M3/M4/M5 内部实现本轮不在范围内。"""

    name: BrainName

    def query(self, query: BrainQuery) -> BrainResult:  # pragma: no cover - 协议声明
        ...