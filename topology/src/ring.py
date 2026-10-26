"""M1 日圈引用（RingRef）。

``(tree_id, day_key)`` 对应一个 ring，``ring_index`` 同树内按 day_key 升序单调递增。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

DAY_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RingRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tree_id: str = Field(min_length=1)
    day_key: str
    ring_index: int = Field(ge=0)

    @field_validator("day_key")
    @classmethod
    def _check_day_key(cls, value: str) -> str:
        if not DAY_KEY_RE.match(value or ""):
            raise ValueError("day_key 必须为 YYYY-MM-DD")
        return value