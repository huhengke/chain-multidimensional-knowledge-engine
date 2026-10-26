"""M1 尺与日界线（Ruler）。

- **尺**：全局唯一自然日序列，``day_key = YYYY-MM-DD``。
- **日界线**：``Asia/Shanghai``。
- **每天一圈**：``(tree_id, day_key)`` 对应一个日圈，``ring_index`` 在同树内按
  ``day_key`` 升序**单调递增**且唯一。

本模块不使用桩或假数据：``day_key`` 由真实时间戳换算，``ring_index`` 由真实注册序列确定。
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from typing import Union
from zoneinfo import ZoneInfo

DateLike = Union[str, int, float, date, datetime]

_TIMEZONE_NAME = "Asia/Shanghai"


def _load_timezone():
    try:
        return ZoneInfo(_TIMEZONE_NAME)
    except Exception:  # pragma: no cover - 环境缺少 tzdata 时的确定性回退
        # Asia/Shanghai 当前无夏令时，固定 UTC+8 与 ZoneInfo 结果一致。
        return timezone(timedelta(hours=8), name=_TIMEZONE_NAME)


SHANGHAI_TZ = _load_timezone()


class Ruler:
    """尺与日界线。负责时间戳 -> ``day_key``，以及 ``(tree_id, day_key)`` -> ``ring_index``。"""

    def __init__(self, tzinfo=None) -> None:
        self._tz = tzinfo or SHANGHAI_TZ
        self._lock = threading.RLock()
        # tree_id -> {day_key: ring_index}
        self._tree_days: dict[str, dict[str, int]] = {}
        # tree_id -> 已注册的最大日期，用于强制升序
        self._tree_max: dict[str, date] = {}

    @property
    def timezone_name(self) -> str:
        return _TIMEZONE_NAME

    # ---- day_key ---------------------------------------------------------
    def to_date(self, value: DateLike) -> date:
        """把多种时间表示归一到 ``Asia/Shanghai`` 日界线下的自然日。"""
        if isinstance(value, datetime):
            dt = value if value.tzinfo is not None else value.replace(tzinfo=self._tz)
            return dt.astimezone(self._tz).date()
        if isinstance(value, date):
            return value
        if isinstance(value, bool):
            raise TypeError("bool 不是有效时间戳")
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=self._tz).date()
        if isinstance(value, str):
            text = value.strip()
            try:
                return date.fromisoformat(text)
            except ValueError:
                pass
            try:
                dt = datetime.fromisoformat(text)
            except ValueError as exc:
                raise ValueError(f"无法解析时间：{value!r}") from exc
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self._tz)
            return dt.astimezone(self._tz).date()
        raise TypeError(f"不支持的时间类型：{type(value)!r}")

    def day_key(self, value: DateLike = None) -> str:
        """返回 ``YYYY-MM-DD``；``value`` 缺省取当前时刻。"""
        if value is None:
            value = datetime.now(timezone.utc)
        return self.to_date(value).isoformat()

    # ---- ring_index ------------------------------------------------------
    def ring_index(self, tree_id: str, day_key: DateLike) -> int:
        """按升序为 ``(tree_id, day_key)`` 分配唯一日圈序号。

        首次出现的树从 0 起编号；同一 ``(tree_id, day_key)`` 幂等返回原值；
        若注册更早的日期则显式报错（保证已发出的 ``ring_index`` 不可变且单调）。
        """
        if not tree_id:
            raise ValueError("tree_id 不得为空")
        day = self.to_date(day_key)
        key = day.isoformat()
        with self._lock:
            days = self._tree_days.setdefault(tree_id, {})
            if key in days:
                return days[key]
            if days:
                max_day = self._tree_max[tree_id]
                if day <= max_day:
                    raise ValueError(
                        f"ring_index 必须按 day_key 升序注册：tree_id={tree_id!r} "
                        f"已注册至 {max_day.isoformat()}，收到 {key}"
                    )
                index = max(days.values()) + 1
            else:
                index = 0
            days[key] = index
            self._tree_max[tree_id] = day
            return index

    def known_days(self, tree_id: str) -> dict[str, int]:
        with self._lock:
            return dict(self._tree_days.get(tree_id, {}))

    def has_ring(self, tree_id: str, day_key: DateLike) -> bool:
        day = self.to_date(day_key).isoformat()
        with self._lock:
            return day in self._tree_days.get(tree_id, {})