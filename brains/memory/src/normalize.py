"""M3 天地人归一（normalize）。

时间 → ``Ruler`` 日界线；地点 / 人物 / 来源 → 标准坐标。
**不确定项保留显式未知状态（None）**：绝不填默认值、绝不编造。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from topology.src.ruler import Ruler

# 视为“未知”的哨兵串（大小写无关）——归一后统一表达为 None。
_UNKNOWN_SENTINELS = {"", "unknown", "未知", "不详", "n/a", "na", "none", "null", "?", "-"}


def clean_label(value: Any) -> str | None:
    """把显式未知/空值归为 ``None``；否则返回去空白后的原文。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in _UNKNOWN_SENTINELS:
        return None
    return text


class Anchors(BaseModel):
    """天地人锚点。任一项可为 ``None``（未知）。"""

    model_config = ConfigDict(extra="forbid")

    time: str | None = None
    place: str | None = None
    person: str | None = None


class Normalizer:
    def __init__(self, ruler: Ruler) -> None:
        self.ruler = ruler

    def normalize(self, item) -> tuple[Anchors, str | None]:
        """返回 ``(anchors, day_key)``；时间不确定时 ``day_key`` 为 ``None``。"""
        timestamp = item.timestamp
        if isinstance(timestamp, str) and not timestamp.strip():
            timestamp = None
        day_key = self.ruler.day_key(timestamp) if timestamp is not None else None

        place = clean_label(item.place)
        if place is None:
            place = clean_label((item.metadata or {}).get("place"))
        person = clean_label(item.person)
        return Anchors(time=day_key, place=place, person=person), day_key