"""M1 向量留痕层（TraceRecord / OccurrenceRef）。

**硬不变式**：留痕层聚合身份 ``aggregate_key`` **严禁包含日期**。
重复命中只增加次数并更新中心/离散度；日期由独立的成员引用边
``OccurrenceRef → {tree_id, day_key, node_id, evidence_ref}`` 保存。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, field_validator

from topology.src.ring import DAY_KEY_RE

# 可识别的日期形态：YYYY-MM-DD、YYYY/MM/DD、YYYYMMDD
_DATE_PATTERN = re.compile(
    r"(?:\d{4}-\d{2}-\d{2})|(?:\d{4}/\d{2}/\d{2})|(?:(?<!\d)\d{8}(?!\d))"
)


def contains_date(text: str) -> bool:
    """判断文本是否包含日期形态。"""
    return bool(_DATE_PATTERN.search(text or ""))


def assert_no_date_in_aggregate_key(aggregate_key: str) -> str:
    """留痕层硬不变式：``aggregate_key`` 不得包含日期。"""
    if contains_date(aggregate_key):
        raise ValueError(
            f"留痕层 aggregate_key 严禁包含日期：{aggregate_key!r}；"
            "日期只能经 OccurrenceRef 表达"
        )
    return aggregate_key


class TraceRecord(BaseModel):
    """向量留痕记录。时间无关的聚合身份 + 物理来源引用。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str = Field(min_length=1)
    aggregate_key: str
    vector: list[str]
    source_ref: str = Field(min_length=1)
    created_at: str = Field(min_length=1)

    @field_validator("aggregate_key")
    @classmethod
    def _check_aggregate_key(cls, value: str) -> str:
        if "://" not in (value or ""):
            raise ValueError("aggregate_key 必须形如 <向量空间>://<身份>")
        return assert_no_date_in_aggregate_key(value)

    @field_validator("vector")
    @classmethod
    def _check_vector(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("vector 不得为空")
        for item in value:
            try:
                Decimal(str(item))
            except InvalidOperation as exc:
                raise ValueError(f"vector 分量必须为定点数（Decimal 字符串）：{item!r}") from exc
        return value


class OccurrenceRef(BaseModel):
    """成员引用边：日期**只允许出现在这里**。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    occurrence_id: str = Field(min_length=1)
    tree_id: str = Field(min_length=1)
    day_key: str
    node_id: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)

    @field_validator("day_key")
    @classmethod
    def _check_day_key(cls, value: str) -> str:
        if not DAY_KEY_RE.match(value or ""):
            raise ValueError("day_key 必须为 YYYY-MM-DD")
        return value