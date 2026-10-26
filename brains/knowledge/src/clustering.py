"""Exact Decimal clustering with early rejection for large knowledge corpora."""
from decimal import Decimal

from topology.src.star_cluster import StarClusterIndex, _to_vec


class KnowledgeClusterIndex(StarClusterIndex):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._prototypes = {}

    def assign(self, trace):
        vector = _to_vec(trace.vector)
        best, best_distance = None, None
        for cluster in self._clusters.values():
            identity = tuple(cluster.prototype_vector)
            cached = self._prototypes.get(cluster.cluster_id)
            if cached is None or cached[0] != identity:
                cached = (identity, _to_vec(identity))
                self._prototypes[cluster.cluster_id] = cached
            prototype = cached[1]
            if len(prototype) != len(vector):
                continue
            epsilon = cluster.eps()
            squared_limit, squared = epsilon * epsilon, Decimal(0)
            # Each term is non-negative: a partial distance above epsilon can
            # never become an eligible full distance. Preserve Decimal rounding
            # and the baseline's first-nearest tie order, including the boundary.
            for a, b in zip(prototype, vector):
                delta = a-b
                squared += delta*delta
                if squared > squared_limit and squared.sqrt() > epsilon:
                    break
            else:
                distance = squared.sqrt()
                if distance <= epsilon and (best_distance is None or distance < best_distance):
                    best, best_distance = cluster, distance
        return self._create(trace, vector) if best is None else self._incorporate(best, trace, vector)
