"""M1 三层载荷存储（PayloadStore）。

节点**身份**与**物理载荷**分离：``TopologyNode.node_id`` 只表达拓扑身份，
真实载荷按内容寻址写入本存储并返回 ``payload_ref``（``store://<层存储>/<hash>``）。
"""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType
from typing import Any, Iterator, Mapping

# 拓扑层 -> 物理存储名（对应 inner_chain / middle_store / outer_chain）
LAYER_STORE: Mapping[str, str] = MappingProxyType(
    {"inner": "inner_chain", "middle": "middle_store", "outer": "outer_chain"}
)

STORE_SCHEME = "store://"


class PayloadStore:
    """内容寻址、进程内不可变载荷存储。

    同一载荷的多次写入产生同一 ``payload_ref``（幂等）；载荷内容一经写入不再修改。
    """

    def __init__(self) -> None:
        self._blobs: dict[str, str] = {}

    def put(self, layer: str, payload: Any) -> str:
        if layer not in LAYER_STORE:
            raise ValueError(f"未知拓扑层：{layer!r}，应为 {sorted(LAYER_STORE)}")
        canonical = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        ref = f"{STORE_SCHEME}{LAYER_STORE[layer]}/{digest}"
        self._blobs.setdefault(ref, canonical)
        return ref

    def get(self, ref: str) -> Any:
        if ref not in self._blobs:
            raise KeyError(f"载荷不存在：{ref}")
        return json.loads(self._blobs[ref])

    def has(self, ref: str) -> bool:
        return ref in self._blobs

    def refs(self) -> Iterator[str]:
        return iter(self._blobs)

    def __len__(self) -> int:
        return len(self._blobs)