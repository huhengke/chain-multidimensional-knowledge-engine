"""M3 提取向量层（ExtractorRegistry）。

真实实现：用标准库做**确定性**特征提取（文本 TF 定点向量、媒体元数据标量）。

- 文本：分词/关键词 TF 统计，哈希到固定维，归一化为 ``Decimal`` 定点字符串；
- 元数据（duration/captured_at/gps）：归一为标量特征与时空锚点；
- **未配置/不可用**模态（无 ASR/OCR/视觉/姿态/声学引擎）返回
  ``Feature(status="unavailable", reason=...)``，向量留空，**绝不编造向量值**，
  也不静默丢弃该模态。
"""

from __future__ import annotations

import hashlib
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VECTOR_DIM = 16
FEATURE_STATUSES = ("available", "unavailable")

_QUANT = Decimal("0.000001")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")
_CJK_SEQ_RE = re.compile(r"[\u4e00-\u9fff]+")

# 模态 -> 记忆脑树（前缀 memory.）
MODALITY_TREES: dict[str, str] = {
    "metadata": "memory.meta",
    "text": "memory.text",
    "asr": "memory.voice",
    "ocr": "memory.text",
    "keyframe": "memory.visual",
    "scene": "memory.visual",
    "object": "memory.visual",
    "waveform": "memory.voice",
    "pose": "memory.action",
    "action": "memory.action",
    "voiceprint": "memory.voice",
    "precise_location": "memory.spatial",
}


def quantize(value: Decimal) -> str:
    """规范化定点字符串（定点 6 位，去尾零，永不科学计数法）。"""
    return format(value.quantize(_QUANT, rounding=ROUND_HALF_UP).normalize(), "f")


def to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - 统一转换为显式错误
        raise ValueError(f"非定点数：{value!r}") from exc


def fmt_decimal(value: Any) -> str:
    return format(to_decimal(value).normalize(), "f")


def tokenize(text: str) -> list[str]:
    """确定性分词：拉丁词 + CJK 单字 + CJK 二元组（捕捉“太极”等词）。"""
    text = text or ""
    tokens = [t.lower() for t in _TOKEN_RE.findall(text)]
    for seq in _CJK_SEQ_RE.findall(text):
        tokens.extend(seq[i : i + 2] for i in range(len(seq) - 1))
    return tokens


def hash_vector(tokens: list[str], dim: int = VECTOR_DIM) -> list[str]:
    """把 token 序列哈希到固定维频次向量，归一化为定点字符串。空输入返回 []。"""
    counts = [Decimal(0)] * dim
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        counts[int(digest[:8], 16) % dim] += 1
    total = sum(counts, Decimal(0))
    if total == 0:
        return []
    return [quantize(c / total) for c in counts]


def _decimals(text: str) -> int:
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1].rstrip("0"))


class Feature(BaseModel):
    """提取出的单条特征。``status="unavailable"`` 时**必须**无向量（禁止编造）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    tree_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    status: str
    reason: str | None = None
    vector: list[str] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)
    confidence: str = "0"
    model_version: str = Field(min_length=1)
    sensitive: bool = False
    gated: bool = False

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        if value not in FEATURE_STATUSES:
            raise ValueError(f"status 必须为 {FEATURE_STATUSES}：{value!r}")
        return value

    @field_validator("vector")
    @classmethod
    def _check_vector(cls, value: list[str]) -> list[str]:
        for item in value:
            to_decimal(item)
        return value

    @field_validator("confidence")
    @classmethod
    def _check_confidence(cls, value: str) -> str:
        number = to_decimal(value)
        if number < 0 or number > 1:
            raise ValueError(f"confidence 必须在 [0,1]：{value!r}")
        return value

    @model_validator(mode="after")
    def _check_availability_consistency(self) -> "Feature":
        if self.status == "unavailable" and self.vector:
            raise ValueError("不可用模态不得携带向量（禁止编造特征值）")
        return self


class Extractor:
    """模态提取器基类。``extract`` 必须真实返回或显式不可用。"""

    modality: str = ""
    feature_name: str = ""
    tree_id: str = ""
    kind: str = ""
    model_version: str = ""

    def applies(self, item) -> bool:  # pragma: no cover - 由子类覆写
        return True

    def extract(self, item) -> Feature:  # pragma: no cover - 由子类覆写
        raise NotImplementedError


class TextExtractor(Extractor):
    modality = "text"
    feature_name = "text.keywords"
    tree_id = "memory.text"
    kind = "text"
    model_version = "text-tf-v1"

    def applies(self, item) -> bool:
        return bool((item.content or "").strip())

    def extract(self, item) -> Feature:
        tokens = tokenize(item.content)
        freq: dict[str, int] = {}
        for token in tokens:
            freq[token] = freq.get(token, 0) + 1
        top = [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]
        return Feature(
            name=self.feature_name,
            tree_id=self.tree_id,
            kind=self.kind,
            status="available",
            vector=hash_vector(tokens),
            detail={"tokens": tokens[:64], "top_keywords": top, "token_count": len(tokens)},
            confidence="1",
            model_version=self.model_version,
        )


class MetadataExtractor(Extractor):
    modality = "metadata"
    feature_name = "media.metadata"
    tree_id = "memory.meta"
    kind = "metadata"
    model_version = "meta-v1"

    def extract(self, item) -> Feature:
        meta = item.metadata or {}
        duration = meta.get("duration")
        captured_at = meta.get("captured_at")
        gps = meta.get("gps")
        if duration is None and captured_at is None and gps is None:
            return Feature(
                name=self.feature_name,
                tree_id=self.tree_id,
                kind=self.kind,
                status="unavailable",
                reason="无可用媒体元数据",
                model_version=self.model_version,
            )
        vector: list[str] = []
        if duration is not None:
            vector = [quantize(to_decimal(duration) / Decimal(3600))]
        return Feature(
            name=self.feature_name,
            tree_id=self.tree_id,
            kind=self.kind,
            status="available",
            vector=vector,
            detail={"duration": duration, "captured_at": captured_at, "has_gps": gps is not None},
            confidence="1",
            model_version=self.model_version,
        )


class LocationExtractor(Extractor):
    """高精度经纬度：**敏感**，默认不进自动星团。"""

    modality = "precise_location"
    feature_name = "location.precise"
    tree_id = "memory.spatial"
    kind = "precise_location"
    model_version = "location-v1"

    def applies(self, item) -> bool:
        return "gps" in (item.metadata or {})

    def extract(self, item) -> Feature:
        gps = (item.metadata or {}).get("gps")
        if not isinstance(gps, dict) or "lat" not in gps or "lon" not in gps:
            return Feature(
                name=self.feature_name,
                tree_id=self.tree_id,
                kind=self.kind,
                status="unavailable",
                reason="无高精度定位数据（缺经纬度）",
                model_version=self.model_version,
                sensitive=True,
            )
        lat = str(gps["lat"])
        lon = str(gps["lon"])
        return Feature(
            name=self.feature_name,
            tree_id=self.tree_id,
            kind=self.kind,
            status="available",
            vector=hash_vector([f"lat:{lat}", f"lon:{lon}"]),
            detail={"lat": lat, "lon": lon, "precision_digits": max(_decimals(lat), _decimals(lon))},
            confidence="1",
            model_version=self.model_version,
            sensitive=True,
        )


class VoiceprintExtractor(Extractor):
    """声纹：**敏感**，需门禁，默认不进自动星团。"""

    modality = "voiceprint"
    feature_name = "audio.voiceprint"
    tree_id = "memory.voice"
    kind = "voiceprint"
    model_version = "voiceprint-v1"

    def applies(self, item) -> bool:
        return "voiceprint" in (item.metadata or {})

    def extract(self, item) -> Feature:
        raw = (item.metadata or {}).get("voiceprint")
        if not raw:
            return Feature(
                name=self.feature_name,
                tree_id=self.tree_id,
                kind=self.kind,
                status="unavailable",
                reason="未配置声纹模型",
                model_version=self.model_version,
                sensitive=True,
            )
        vector = [fmt_decimal(x) for x in raw]
        return Feature(
            name=self.feature_name,
            tree_id=self.tree_id,
            kind=self.kind,
            status="available",
            vector=vector,
            detail={"dim": len(vector)},
            confidence="1",
            model_version=self.model_version,
            sensitive=True,
        )


class ActionExtractor(Extractor):
    """动作特征：仅保存可查询、可追溯的**结构化描述**（不生成控制代码）。"""

    modality = "action"
    feature_name = "action.descriptor"
    tree_id = "memory.action"
    kind = "action"
    model_version = "action-struct-v1"

    def applies(self, item) -> bool:
        return "action" in (item.metadata or {})

    def extract(self, item) -> Feature:
        raw = (item.metadata or {}).get("action")
        if not raw:
            return Feature(
                name=self.feature_name,
                tree_id=self.tree_id,
                kind=self.kind,
                status="unavailable",
                reason="未配置动作识别模型",
                model_version=self.model_version,
            )
        # 延迟导入避免与 actions 模块循环依赖。
        from brains.memory.src.actions import ActionDescriptor, build_action_feature

        try:
            descriptor = ActionDescriptor(**raw)
        except Exception as exc:  # noqa: BLE001 - 描述非法即显式不可用，不编造
            return Feature(
                name=self.feature_name,
                tree_id=self.tree_id,
                kind=self.kind,
                status="unavailable",
                reason=f"动作描述不合法：{exc}",
                model_version=self.model_version,
            )
        return build_action_feature(descriptor, self.model_version)


class UnavailableExtractor(Extractor):
    """未配置模态的**诚实**占位：返回显式不可用，不编造向量。"""

    def __init__(
        self,
        modality: str,
        reason: str,
        *,
        tree_id: str | None = None,
        kind: str | None = None,
        feature_name: str | None = None,
        model_version: str = "n/a",
    ) -> None:
        self.modality = modality
        self.tree_id = tree_id or MODALITY_TREES.get(modality, f"memory.{modality}")
        self.kind = kind or modality
        self.feature_name = feature_name or f"{modality}.feature"
        self.model_version = model_version
        self.reason = reason

    def extract(self, item) -> Feature:
        return Feature(
            name=self.feature_name,
            tree_id=self.tree_id,
            kind=self.kind,
            status="unavailable",
            reason=self.reason,
            detail={"modality": self.modality},
            model_version=self.model_version,
        )


class ExtractorRegistry:
    """模态 -> 提取器注册表。未注册模态返回显式不可用特征。"""

    def __init__(self) -> None:
        self._by_modality: dict[str, Extractor] = {}

    def register(self, extractor: Extractor) -> Extractor:
        self._by_modality[extractor.modality] = extractor
        return extractor

    def get(self, modality: str) -> Extractor | None:
        return self._by_modality.get(modality)

    def is_available(self, modality: str) -> bool:
        extractor = self._by_modality.get(modality)
        return extractor is not None and not isinstance(extractor, UnavailableExtractor)

    def tree_for(self, modality: str) -> str:
        return MODALITY_TREES.get(modality, f"memory.{modality}")

    def extract(self, modality: str, item) -> Feature:
        extractor = self._by_modality.get(modality)
        if extractor is None:
            return Feature(
                name=f"{modality}.feature",
                tree_id=self.tree_for(modality),
                kind=modality,
                status="unavailable",
                reason=f"未配置“{modality}”模态提取器",
                model_version="n/a",
            )
        return extractor.extract(item)

    def extract_all(self, item) -> list[Feature]:
        modalities = ["metadata"]
        if (item.content or "").strip():
            modalities.append("text")
        if item.modality and item.modality not in modalities:
            modalities.append(item.modality)
        meta = item.metadata or {}
        for key, modality in (("action", "action"), ("voiceprint", "voiceprint"), ("gps", "precise_location")):
            if key in meta and modality not in modalities:
                modalities.append(modality)

        features: list[Feature] = []
        seen: set[tuple[str, str]] = set()
        for modality in modalities:
            feature = self.extract(modality, item)
            marker = (feature.name, feature.kind)
            if marker in seen:
                continue
            seen.add(marker)
            features.append(feature)
        return features


def default_registry() -> ExtractorRegistry:
    """注册全部模态：真实可运行的用真实 extractor，缺失引擎的用显式不可用。"""
    registry = ExtractorRegistry()
    registry.register(MetadataExtractor())
    registry.register(TextExtractor())
    registry.register(LocationExtractor())
    registry.register(VoiceprintExtractor())
    registry.register(ActionExtractor())
    # 本环境无 ASR/OCR/视觉/姿态/声学模型 -> 显式不可用（不编造）。
    registry.register(UnavailableExtractor("asr", "未配置 ASR 引擎"))
    registry.register(UnavailableExtractor("ocr", "未配置 OCR 引擎"))
    registry.register(UnavailableExtractor("keyframe", "未配置关键帧/视觉模型"))
    registry.register(UnavailableExtractor("scene", "未配置场景识别模型"))
    registry.register(UnavailableExtractor("object", "未配置物体识别模型"))
    registry.register(UnavailableExtractor("waveform", "未配置声学波形模型"))
    registry.register(UnavailableExtractor("pose", "未配置姿态估计模型"))
    return registry