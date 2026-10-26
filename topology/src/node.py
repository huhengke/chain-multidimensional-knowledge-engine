"""M1 拓扑节点（TopologyNode）。

三层载荷：``layer ∈ {inner, middle, outer}``，分别落在
``inner_chain(中通内链) / middle_store(中通周存) / outer_chain(DNA 外链)``。
节点身份（``node_id``）与物理载荷（``payload_ref``）分离；generation 不可变。
"""

from __future__ import annotations

from enum import Enum
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from topology.src.payload import LAYER_STORE, STORE_SCHEME
from topology.src.ring import DAY_KEY_RE


class Layer(str, Enum):
    """三层载荷。DNA 外链只描述物理拓扑，不新增业务查询层。"""

    inner = "inner"
    middle = "middle"
    outer = "outer"


class TopologyNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1)
    tree_id: str = Field(min_length=1)
    day_key: str
    ring_index: int = Field(ge=0)
    layer: Layer
    generation: int = Field(default=1, ge=1)
    payload_ref: str
    evidence_refs: list[str] = Field(default_factory=list)
    occurrence_refs: list[str] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)

    @field_validator("day_key")
    @classmethod
    def _check_day_key(cls, value: str) -> str:
        if not DAY_KEY_RE.match(value or ""):
            raise ValueError("day_key 必须为 YYYY-MM-DD")
        return value

    @field_validator("payload_ref")
    @classmethod
    def _check_payload_ref(cls, value: str) -> str:
        if not (value or "").startswith(STORE_SCHEME):
            raise ValueError(f"payload_ref 必须形如 {STORE_SCHEME}<层存储>/<hash>")
        return value

    @property
    def store(self) -> str:
        """该节点所属的物理存储名。"""
        return LAYER_STORE[self.layer.value]

    @classmethod
    def build_id(cls, tree_id: str, day_key: str, *path: str) -> str:
        """构造节点身份：``<tree_id>/<day_key>/<path...>``。"""
        parts: Sequence[str] = (tree_id, day_key, *path)
        return "/".join(part.strip("/") for part in parts if part != "")

    def branched(self, **overrides) -> "TopologyNode":
        """派生一个新节点（保留身份/载荷分离语义），用于生长分枝。"""
        data = self.model_dump()
        data.update(overrides)
        return TopologyNode(**data)