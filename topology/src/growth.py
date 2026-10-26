"""M1 自动生长与不可变 generation（GrowthEngine）。

生长引擎按 ``growth_threshold`` 触发：星团出现次数达标后，按 ``growth_dimensions``
顺序分枝，并**重建不可变 generation**。旧 generation 仍可读、可回滚。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

from topology.src.node import Layer, TopologyNode
from topology.src.payload import PayloadStore
from topology.src.star_cluster import StarCluster

DEFAULT_GROWTH_DIMENSIONS: tuple[str, ...] = ("time", "place", "person")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TopologyGeneration:
    """**不可变**拓扑代。

    节点集合一经构建即冻结（``MappingProxyType``），且不允许再赋值任何属性。
    生长会产生**新的** generation 对象，而非就地修改。
    """

    __slots__ = ("_generation", "_tree_id", "_nodes", "_parent", "_created_at", "_cause")

    def __init__(
        self,
        generation: int,
        tree_id: str,
        nodes: Mapping[str, TopologyNode],
        parent: int | None = None,
        created_at: str | None = None,
        cause: dict[str, Any] | None = None,
    ) -> None:
        if generation < 1:
            raise ValueError("generation 必须 >= 1")
        object.__setattr__(self, "_generation", generation)
        object.__setattr__(self, "_tree_id", tree_id)
        object.__setattr__(self, "_nodes", MappingProxyType(dict(nodes)))
        object.__setattr__(self, "_parent", parent)
        object.__setattr__(self, "_created_at", created_at or _now_iso())
        object.__setattr__(self, "_cause", MappingProxyType(dict(cause or {})))

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def tree_id(self) -> str:
        return self._tree_id

    @property
    def nodes(self) -> Mapping[str, TopologyNode]:
        return self._nodes

    @property
    def parent(self) -> int | None:
        return self._parent

    @property
    def created_at(self) -> str:
        return self._created_at

    @property
    def cause(self) -> Mapping[str, Any]:
        return self._cause

    def node(self, node_id: str) -> TopologyNode:
        return self._nodes[node_id]

    def __len__(self) -> int:
        return len(self._nodes)

    def snapshot(self) -> dict[str, Any]:
        return {
            "generation": self._generation,
            "tree_id": self._tree_id,
            "parent": self._parent,
            "created_at": self._created_at,
            "cause": dict(self._cause),
            "node_ids": sorted(self._nodes),
        }

    def __setattr__(self, key, value):  # noqa: D105
        raise AttributeError("TopologyGeneration 为不可变对象，禁止修改")

    def __delattr__(self, key):  # noqa: D105
        raise AttributeError("TopologyGeneration 为不可变对象，禁止修改")


class GenerationStore:
    """按 ``tree_id`` 保存多代不可变拓扑，支持读取任意历史代与回滚指针。"""

    def __init__(self) -> None:
        self._history: dict[str, list[TopologyGeneration]] = {}
        self._current: dict[str, TopologyGeneration] = {}

    def commit(self, generation: TopologyGeneration) -> TopologyGeneration:
        history = self._history.setdefault(generation.tree_id, [])
        if any(g.generation == generation.generation for g in history):
            raise ValueError(
                f"generation 已存在：tree_id={generation.tree_id!r} gen={generation.generation}"
            )
        if history and generation.generation != history[-1].generation + 1:
            raise ValueError("generation 必须连续递增提交")
        history.append(generation)
        self._current[generation.tree_id] = generation
        return generation

    def current(self, tree_id: str) -> TopologyGeneration:
        return self._current[tree_id]

    def get(self, tree_id: str, generation: int) -> TopologyGeneration:
        for gen in self._history.get(tree_id, []):
            if gen.generation == generation:
                return gen
        raise KeyError(f"未知 generation：tree_id={tree_id!r} gen={generation}")

    def history(self, tree_id: str) -> list[TopologyGeneration]:
        return list(self._history.get(tree_id, []))

    def tree_ids(self) -> list[str]:
        """返回已有 generation 的树标识快照，避免调用方读取内部存储。"""
        return sorted(self._history)

    def rollback(self, tree_id: str, generation: int) -> TopologyGeneration:
        """把当前指针回滚到旧代；历史代全部保留、仍可读。"""
        target = self.get(tree_id, generation)
        self._current[tree_id] = target
        return target


class GrowthEngine:
    """按阈值触发分枝并重建不可变 generation。"""

    def __init__(
        self,
        growth_threshold: int,
        growth_dimensions: list[str] | None = None,
        algo_version: str = "v1",
        rule_version: str = "v1",
    ) -> None:
        if growth_threshold < 1:
            raise ValueError("growth_threshold 必须 >= 1")
        self.growth_threshold = growth_threshold
        self.growth_dimensions = list(growth_dimensions or DEFAULT_GROWTH_DIMENSIONS)
        self.algo_version = algo_version
        self.rule_version = rule_version
        self._event_counter = 0

    def triggers(self, cluster: StarCluster) -> bool:
        return cluster.occurrence_count >= self.growth_threshold

    def _next_event_id(self) -> str:
        self._event_counter += 1
        return f"ge-{self._event_counter}"

    def grow(
        self,
        current: TopologyGeneration,
        clusters: list[StarCluster],
        store: PayloadStore,
        day_key: str,
        ring_index: int,
    ) -> TopologyGeneration:
        """对达标星团分枝并返回**新的** generation；无触发则原样返回当前代。"""
        triggering = [c for c in clusters if self.triggers(c)]
        if not triggering:
            return current

        tree_id = current.tree_id
        new_generation = current.generation + 1
        nodes: dict[str, TopologyNode] = dict(current.nodes)
        events: list[dict[str, Any]] = []

        for cluster in triggering:
            root_id = TopologyNode.build_id(tree_id, day_key, "cluster", cluster.cluster_id)
            payload_ref = store.put(
                "middle",
                {
                    "kind": "growth-cluster",
                    "cluster_id": cluster.cluster_id,
                    "occurrence_count": cluster.occurrence_count,
                    "prototype_vector": cluster.prototype_vector,
                    "centroid": cluster.centroid,
                    "dispersion": cluster.dispersion,
                    "algo_version": cluster.algo_version,
                    "rule_version": cluster.rule_version,
                },
            )
            child_ids: list[str] = []
            for dimension in self.growth_dimensions:
                child_id = f"{root_id}/{dimension}"
                child_ids.append(child_id)
                nodes[child_id] = TopologyNode(
                    node_id=child_id,
                    tree_id=tree_id,
                    day_key=day_key,
                    ring_index=ring_index,
                    layer=Layer.middle,
                    generation=new_generation,
                    payload_ref=store.put(
                        "middle",
                        {
                            "kind": "growth-dimension",
                            "dimension": dimension,
                            "cluster_id": cluster.cluster_id,
                            "prototype_vector": cluster.prototype_vector,
                        },
                    ),
                )
            nodes[root_id] = TopologyNode(
                node_id=root_id,
                tree_id=tree_id,
                day_key=day_key,
                ring_index=ring_index,
                layer=Layer.middle,
                generation=new_generation,
                payload_ref=payload_ref,
                children=child_ids,
            )
            event_id = self._next_event_id()
            cluster.growth_events = [*cluster.growth_events, event_id]
            events.append(
                {
                    "event_id": event_id,
                    "cluster_id": cluster.cluster_id,
                    "dimensions": list(self.growth_dimensions),
                    "branched_node_ids": [root_id, *child_ids],
                }
            )

        return TopologyGeneration(
            generation=new_generation,
            tree_id=tree_id,
            nodes=nodes,
            parent=current.generation,
            cause={
                "reason": "growth_threshold_reached",
                "threshold": self.growth_threshold,
                "dimensions": list(self.growth_dimensions),
                "events": events,
            },
        )
