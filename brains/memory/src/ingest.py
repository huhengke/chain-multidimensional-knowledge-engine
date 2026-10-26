"""M3 记忆单元与接入契约（ingest 层的数据模型 + 策略）。

- ``IngestItem``：接入入参（含权限/来源记账）。
- ``MemoryUnit``：记忆单元（记忆单元与原始媒体分离；``status`` 区分
  ``confirmed / inferred / candidate``，**不把相似度当最终事实**）。
- ``MemoryPolicy``：策略门禁与“我记得”边界阈值。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from brains.memory.src.extract import Feature, to_decimal
from brains.memory.src.normalize import Anchors

UNIT_STATUSES = ("confirmed", "inferred", "candidate")


class IngestItem(BaseModel):
    """安全接入入参。``trust`` 表达来源可信度（user / device / model）。"""

    model_config = ConfigDict(extra="forbid")

    media_ref: str = Field(min_length=1)
    modality: str = Field(min_length=1)
    content: str | None = None
    raw_content: Any = None
    timestamp: Any = None
    place: str | None = None
    person: str | None = None
    permission_scope: str = Field(default="owner", min_length=1)
    source_ref: str = Field(min_length=1)
    source_kind: str = Field(default="manual", min_length=1)
    trust: str = "device"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryUnit(BaseModel):
    """可解释的时空记忆单元。``day_key`` 为 ``None`` 表示时间未知（不编造）。"""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1)
    media_ref: str = Field(min_length=1)
    tree_id: str = Field(min_length=1)
    day_key: str | None = None
    anchors: Anchors
    features: list[Feature] = Field(default_factory=list)
    confidence: str = "0"
    extract_model_version: str = Field(min_length=1)
    status: str
    permission_scope: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        if value not in UNIT_STATUSES:
            raise ValueError(f"status 必须为 {UNIT_STATUSES}：{value!r}")
        return value

    @field_validator("confidence")
    @classmethod
    def _check_confidence(cls, value: str) -> str:
        number = to_decimal(value)
        if number < 0 or number > 1:
            raise ValueError(f"confidence 必须在 [0,1]：{value!r}")
        return value


class MemoryPolicy(BaseModel):
    """记忆策略：敏感项门禁 + “我记得”边界阈值。"""

    model_config = ConfigDict(extra="forbid")

    fast_recall_min_occurrence: int = Field(default=3, ge=1)
    max_dispersion: str = "0.05"
    sensitive_kinds: set[str] = Field(
        default_factory=lambda: {"identity", "voiceprint", "precise_location"}
    )
    readable_scopes: set[str] = Field(default_factory=lambda: {"owner", "family", "public"})

    @field_validator("max_dispersion")
    @classmethod
    def _check_dispersion(cls, value: str) -> str:
        to_decimal(value)
        return value

    def is_sensitive(self, feature: Feature) -> bool:
        """敏感项（身份/声纹/精确位置）需门禁，默认不进自动星团。"""
        return bool(feature.sensitive or feature.kind in self.sensitive_kinds)

    def has_permission(self, profile_id: str | None, scope: str) -> bool:
        return profile_id is not None and scope in self.readable_scopes

    def is_fast_recall(
        self, occurrence_count: int, dispersion: str, permission_ok: bool
    ) -> bool:
        """仅当达阈值、离散度受控、且当前用户有权限时才可作快速线索。"""
        if not permission_ok:
            return False
        if occurrence_count < self.fast_recall_min_occurrence:
            return False
        try:
            return Decimal(dispersion) <= Decimal(self.max_dispersion)
        except Exception:  # noqa: BLE001 - 离散度非法则拒绝快速线索
            return False
