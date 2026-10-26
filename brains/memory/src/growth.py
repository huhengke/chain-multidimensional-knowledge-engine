"""M3 自动聚类与生长（growth）。

复用 M1 ``StarClusterIndex.assign`` 与 ``GrowthEngine.grow``：星团由留痕层自动
形成（时间无关），达标后分枝并重建**不可变** generation；旧 generation 仍可读。

敏感项门禁（``gated=True``）由 ``ingest`` 打标，**不进入星团/生长**。
"""

from __future__ import annotations

from topology.src.growth import GrowthEngine, TopologyGeneration
from topology.src.node import TopologyNode
from topology.src.ruler import Ruler
from topology.src.star_cluster import StarCluster
from topology.src.trace import TraceRecord

from brains.memory.src.extract import Feature
from brains.memory.src.ingest import MemoryPolicy
from brains.memory.src.media_store import MediaStore


class MemoryGrowth:
    def __init__(
        self,
        store: MediaStore,
        ruler: Ruler,
        growth_threshold: int,
        tolerance: str,
        policy: MemoryPolicy,
    ) -> None:
        self.store = store
        self.ruler = ruler
        self.tolerance = tolerance
        self.policy = policy
        self.engine = GrowthEngine(growth_threshold)
        self._grown: set[str] = set()

    def assign(self, trace: TraceRecord, feature: Feature) -> StarCluster:
        """把留痕并入该树最近的星团邻域（时间无关），或新建星团。"""
        index = self.store.cluster_index(feature.tree_id, self.tolerance)
        return index.assign(trace)

    def _tree_nodes(self, tree_id: str) -> dict[str, TopologyNode]:
        return {nid: node for nid, node in self.store.nodes.items() if node.tree_id == tree_id}

    def commit(self, tree_id: str, day_key: str) -> TopologyGeneration:
        """提交不可变 generation：首次建基础代，达标星团触发分枝重建新代。"""
        history = self.store.generations.history(tree_id)
        if not history:
            base = TopologyGeneration(
                generation=1,
                tree_id=tree_id,
                nodes=self._tree_nodes(tree_id),
                parent=None,
                cause={"reason": "bootstrap"},
            )
            return self.store.generations.commit(base)

        current = self.store.generations.current(tree_id)
        index = self.store.clusters.get(tree_id)
        if index is None:
            return current

        triggering = [
            cluster
            for cluster in index.clusters.values()
            if self.engine.triggers(cluster) and cluster.cluster_id not in self._grown
        ]
        if not triggering:
            return current

        ring_index = self.ruler.ring_index(tree_id, day_key)
        new_generation = self.engine.grow(
            current, triggering, self.store.payload, day_key, ring_index
        )
        merged: dict[str, TopologyNode] = dict(current.nodes)
        merged.update(self._tree_nodes(tree_id))
        merged.update(dict(new_generation.nodes))
        generation = TopologyGeneration(
            generation=new_generation.generation,
            tree_id=tree_id,
            nodes=merged,
            parent=new_generation.parent,
            cause=new_generation.cause,
        )
        self.store.generations.commit(generation)
        self._grown.update(cluster.cluster_id for cluster in triggering)
        return generation