"""Hierarchical, rebuildable query indexes for the knowledge brain.

The index is a projection of the knowledge store.  It never owns source text,
permissions, publications, or evidence; those remain authoritative in
``KnowledgeStore``.  The projection is deliberately split by tree and by the
day-ring metadata carried by topology nodes so a large knowledge forest does
not require a full Python scan for every question.
"""

from __future__ import annotations

import math
import sqlite3
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path
from typing import Iterable

try:  # numpy is a platform dependency; keep a small fallback for core tests.
    import numpy as np
except ImportError:  # pragma: no cover - exercised only in minimal installations
    np = None

from brains.knowledge.src.runtime_adapter import tokenize


class KnowledgeQueryIndex:
    """A coarse-to-fine vector and posting-list index.

    Query order is forest/tree/cluster -> unit -> evidence.  The index keeps
    only identifiers and vectors.  The caller still performs publication,
    permission and evidence checks before returning a result.
    """

    def __init__(self) -> None:
        self.signature: tuple | None = None
        self.unit_ids: list[str] = []
        # ``unit_ids`` is the complete text/time projection.  Vector arrays
        # are kept separately because a knowledge unit may be published before
        # its embedding job finishes (or may intentionally be text-only).
        self.vector_unit_ids: list[str] = []
        self._unit_vector_positions: dict[str, int] = {}
        self.unit_tree: dict[str, str] = {}
        self.unit_days: dict[str, set[str]] = defaultdict(set)
        self.token_units: dict[str, set[str]] = defaultdict(set)
        # A published snapshot can move the high-cardinality lexical posting
        # lists into its own immutable SQLite artifact.  Retaining them as
        # Python ``set`` objects expands a compact JSON file into a large hot
        # memory graph; only term counts stay resident for route decisions.
        self.posting_counts: dict[str, int] = {}
        self.lexical_db_path: str | None = None
        # Python lists of per-dimension floats are prohibitively expensive at
        # corpus scale.  NumPy matrices are the primary storage when available;
        # these lists exist only for the no-NumPy compatibility path.
        self.unit_vectors: list[list[float]] = []
        self.unit_matrix = None
        self.cluster_ids: list[str] = []
        self.cluster_tree: dict[str, str] = {}
        self.cluster_members: dict[str, list[str]] = {}
        self.cluster_days: dict[str, set[str]] = {}
        self.cluster_vectors: list[list[float]] = []
        self.cluster_matrix = None
        self.cluster_positions_by_tree: dict[str, list[int]] = defaultdict(list)
        self.cluster_days_by_tree: dict[str, list[str]] = defaultdict(list)
        self.cluster_positions_by_tree_day: dict[tuple[str, str], list[int]] = defaultdict(list)
        self.cluster_positions_without_day_by_tree: dict[str, list[int]] = defaultdict(list)
        self.last_build_stats: dict[str, int] = {
            "unit_count": 0,
            "vector_unit_count": 0,
            "text_unit_count": 0,
            "cluster_count": 0,
            "token_count": 0,
            "ring_count": 0,
        }

    @staticmethod
    def _signature(store) -> tuple:
        cluster_counts = tuple(
            sorted((tree_id, len(index.clusters), sum(c.occurrence_count for c in index.clusters.values()))
                   for tree_id, index in store.clusters.items())
        )
        # Include the text/vector projection shape so a retry that fills a
        # previously empty embedding is visible without requiring a manual
        # rebuild.  The total text length is cheap and catches replacement
        # content while keeping the signature independent of source payloads.
        vector_count = sum(bool(store.unit_vector.get(unit_id)) for unit_id in store.units)
        text_length = sum(len(store.unit_text.get(unit_id, "")) for unit_id in store.units)
        return (
            len(store.units),
            len(store.nodes),
            len(store.traces),
            vector_count,
            text_length,
            cluster_counts,
        )

    @staticmethod
    def _vector(values: Iterable) -> list[float]:
        result: list[float] = []
        for value in values or []:
            try:
                result.append(float(value))
            except (TypeError, ValueError):
                return []
        return result

    def rebuild(self, store) -> dict[str, int]:
        """Build a new immutable-in-use projection from the live store."""
        unit_ids: list[str] = []
        vector_unit_ids: list[str] = []
        unit_tree: dict[str, str] = {}
        unit_rows: list = []
        previous_positions = dict(self._unit_vector_positions)
        previous_matrix = self.unit_matrix
        previous_vectors = self.unit_vectors
        token_units: dict[str, set[str]] = defaultdict(set)
        for unit_id, unit in store.units.items():
            # During a bounded migration batch older source vectors may
            # already have been compacted.  Reuse the preceding immutable
            # query projection instead of materialising another string copy.
            source_vector = store.unit_vector.get(unit_id, [])
            previous_position = previous_positions.get(unit_id)
            has_previous_vector = (
                previous_position is not None
                and ((previous_matrix is not None and previous_position < len(previous_matrix))
                     or previous_position < len(previous_vectors))
            )
            if not store.unit_text.get(unit_id, "") and not source_vector and not has_previous_vector:
                # Parent/container units are retained by the source store but
                # have no independently searchable payload.
                continue
            # Text and temporal routing must not depend on embedding
            # completion.  Keep every unit in the posting projection first.
            unit_ids.append(unit_id)
            unit_tree[unit_id] = store.unit_tree.get(unit_id, "knowledge.general")
            for token in set(tokenize(store.unit_text.get(unit_id, ""))):
                token_units[token].add(unit_id)

            vector = self._vector(source_vector)
            if vector:
                row = vector
            elif previous_position is not None and previous_matrix is not None and previous_position < len(previous_matrix):
                row = previous_matrix[previous_position]
            elif previous_position is not None and previous_position < len(previous_vectors):
                row = previous_vectors[previous_position]
            else:
                row = []
            if not len(row):
                continue
            vector_unit_ids.append(unit_id)
            unit_rows.append(row)

        # The same unit can appear in multiple day rings.  Derive this mapping
        # from topology evidence edges; date never enters the star-cluster key.
        unit_days: dict[str, set[str]] = defaultdict(set)
        for node in store.nodes.values():
            for evidence_id in node.evidence_refs:
                evidence = store.evidence.get(evidence_id)
                if evidence is not None:
                    unit_days[evidence.unit_id].add(node.day_key)

        cluster_ids: list[str] = []
        cluster_tree: dict[str, str] = {}
        cluster_members: dict[str, list[str]] = {}
        cluster_days: dict[str, set[str]] = {}
        cluster_rows: list = []
        for tree_id, index in store.clusters.items():
            for raw_id, cluster in index.clusters.items():
                key = f"{tree_id}::{raw_id}"
                members: list[str] = []
                for trace_id in cluster.member_trace_ids:
                    trace = store.traces.get(trace_id)
                    if trace is None:
                        continue
                    unit_id = trace.source_ref.rsplit("/", 1)[-1]
                    if unit_id in store.units and unit_id in unit_tree:
                        members.append(unit_id)
                centroid = self._vector(cluster.centroid)
                if not members or not centroid:
                    continue
                cluster_ids.append(key)
                cluster_tree[key] = tree_id
                cluster_members[key] = sorted(set(members))
                cluster_days[key] = {
                    day for member_id in members for day in unit_days.get(member_id, set())
                }
                cluster_rows.append(centroid)

        self.unit_ids = unit_ids
        self.vector_unit_ids = vector_unit_ids
        self._unit_vector_positions = {unit_id: position for position, unit_id in enumerate(vector_unit_ids)}
        self.unit_tree = unit_tree
        self.unit_days = unit_days
        self.token_units = token_units
        self.posting_counts = {token: len(units) for token, units in token_units.items()}
        self.lexical_db_path = None
        self.unit_matrix = self._matrix(unit_rows)
        self.unit_vectors = unit_rows if self.unit_matrix is None else []
        self.cluster_ids = cluster_ids
        self.cluster_tree = cluster_tree
        self.cluster_members = cluster_members
        self.cluster_days = cluster_days
        self.cluster_matrix = self._matrix(cluster_rows)
        self.cluster_vectors = cluster_rows if self.cluster_matrix is None else []
        self.cluster_positions_by_tree = defaultdict(list)
        self.cluster_days_by_tree = defaultdict(list)
        self.cluster_positions_by_tree_day = defaultdict(list)
        self.cluster_positions_without_day_by_tree = defaultdict(list)
        for position, cluster_id in enumerate(cluster_ids):
            tree_id = cluster_tree[cluster_id]
            self.cluster_positions_by_tree[tree_id].append(position)
            days = sorted(self.cluster_days.get(cluster_id, set()))
            if not days:
                self.cluster_positions_without_day_by_tree[tree_id].append(position)
                continue
            self.cluster_days_by_tree[tree_id].extend(days)
            for day in days:
                self.cluster_positions_by_tree_day[(tree_id, day)].append(position)
        self.cluster_days_by_tree = {
            tree_id: sorted(set(days)) for tree_id, days in self.cluster_days_by_tree.items()
        }
        self.signature = self._signature(store)
        self.last_build_stats = {
            "unit_count": len(unit_ids),
            "vector_unit_count": len(vector_unit_ids),
            "text_unit_count": len(unit_ids),
            "cluster_count": len(cluster_ids),
            "token_count": len(token_units),
            "ring_count": len({day for days in unit_days.values() for day in days}),
        }
        return dict(self.last_build_stats)

    def vector_for(self, unit_id: str) -> list[float]:
        """Return a numeric vector from the active projection when present."""
        position = self._unit_vector_positions.get(unit_id)
        if position is None:
            return []
        if self.unit_matrix is not None and position < len(self.unit_matrix):
            return [float(value) for value in self.unit_matrix[position]]
        if position < len(self.unit_vectors):
            return list(self.unit_vectors[position])
        return []

    def vector_dimensions(self, unit_id: str) -> int:
        position = self._unit_vector_positions.get(unit_id)
        if position is None:
            return 0
        if self.unit_matrix is not None and position < len(self.unit_matrix):
            return int(self.unit_matrix.shape[1])
        if position < len(self.unit_vectors):
            return len(self.unit_vectors[position])
        return 0

    def snapshot_metadata(self, *, include_postings: bool = True) -> dict:
        """Export only JSON-safe query routing state; vectors are stored separately."""
        metadata = {
            "unit_ids": list(self.unit_ids),
            "vector_unit_ids": list(self.vector_unit_ids),
            "unit_tree": dict(self.unit_tree),
            "unit_days": {key: sorted(value) for key, value in self.unit_days.items()},
            "posting_counts": dict(self.posting_counts or {
                key: len(value) for key, value in self.token_units.items()
            }),
            "cluster_ids": list(self.cluster_ids),
            "cluster_tree": dict(self.cluster_tree),
            "cluster_members": {key: list(value) for key, value in self.cluster_members.items()},
            "cluster_days": {key: sorted(value) for key, value in self.cluster_days.items()},
            "cluster_positions_by_tree": {key: list(value) for key, value in self.cluster_positions_by_tree.items()},
            "cluster_days_by_tree": {key: list(value) for key, value in self.cluster_days_by_tree.items()},
            "cluster_positions_by_tree_day": {
                f"{tree_id}\u0000{day}": list(value)
                for (tree_id, day), value in self.cluster_positions_by_tree_day.items()
            },
            "cluster_positions_without_day_by_tree": {
                key: list(value) for key, value in self.cluster_positions_without_day_by_tree.items()
            },
            "last_build_stats": dict(self.last_build_stats),
        }
        if include_postings:
            metadata["token_units"] = {key: sorted(value) for key, value in self.token_units.items()}
        return metadata

    def write_lexical_database(self, path: Path) -> None:
        """Persist immutable token postings without retaining a JSON set graph.

        The caller writes inside a snapshot candidate directory.  The table is
        keyed by ``(token, unit_id)``, so a query performs indexed reads for
        only the handful of terms it contains.  This is an artifact-local
        SQLite file, never the platform's transaction metadata database.
        """
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute(
                "CREATE TABLE postings(token TEXT NOT NULL, unit_id TEXT NOT NULL, "
                "PRIMARY KEY(token, unit_id)) WITHOUT ROWID"
            )
            rows = (
                (token, unit_id)
                for token, unit_ids in self.token_units.items()
                for unit_id in sorted(unit_ids)
            )
            connection.executemany("INSERT INTO postings(token, unit_id) VALUES(?, ?)", rows)
            connection.commit()
        finally:
            connection.close()

    def restore_snapshot(
        self,
        metadata: dict,
        unit_vectors,
        cluster_vectors,
        *,
        vectors_normalised: bool = True,
        lexical_db_path: str | Path | None = None,
    ) -> dict[str, int]:
        """Restore an immutable query projection without touching source assets."""
        self.unit_ids = list(metadata.get("unit_ids") or [])
        self.vector_unit_ids = list(metadata.get("vector_unit_ids") or [])
        self._unit_vector_positions = {unit_id: position for position, unit_id in enumerate(self.vector_unit_ids)}
        self.unit_tree = dict(metadata.get("unit_tree") or {})
        self.unit_days = defaultdict(set, {
            key: set(value) for key, value in (metadata.get("unit_days") or {}).items()
        })
        self.token_units = defaultdict(set, {
            key: set(value) for key, value in (metadata.get("token_units") or {}).items()
        })
        self.posting_counts = {
            key: int(value) for key, value in (metadata.get("posting_counts") or {}).items()
        }
        if not self.posting_counts:
            self.posting_counts = {key: len(value) for key, value in self.token_units.items()}
        self.lexical_db_path = str(lexical_db_path) if lexical_db_path else None
        self.unit_matrix = self._matrix(unit_vectors, normalise=not vectors_normalised)
        self.unit_vectors = [] if self.unit_matrix is not None else [list(row) for row in unit_vectors]
        self.cluster_ids = list(metadata.get("cluster_ids") or [])
        self.cluster_tree = dict(metadata.get("cluster_tree") or {})
        self.cluster_members = {key: list(value) for key, value in (metadata.get("cluster_members") or {}).items()}
        self.cluster_days = {
            key: set(value) for key, value in (metadata.get("cluster_days") or {}).items()
        }
        self.cluster_matrix = self._matrix(cluster_vectors, normalise=not vectors_normalised)
        self.cluster_vectors = [] if self.cluster_matrix is not None else [list(row) for row in cluster_vectors]
        self.cluster_positions_by_tree = defaultdict(list, {
            key: list(value) for key, value in (metadata.get("cluster_positions_by_tree") or {}).items()
        })
        self.cluster_days_by_tree = defaultdict(list, {
            key: list(value) for key, value in (metadata.get("cluster_days_by_tree") or {}).items()
        })
        self.cluster_positions_by_tree_day = defaultdict(list)
        for key, value in (metadata.get("cluster_positions_by_tree_day") or {}).items():
            tree_id, separator, day = key.partition("\u0000")
            if separator:
                self.cluster_positions_by_tree_day[(tree_id, day)] = list(value)
        self.cluster_positions_without_day_by_tree = defaultdict(list, {
            key: list(value) for key, value in (metadata.get("cluster_positions_without_day_by_tree") or {}).items()
        })
        self.last_build_stats = dict(metadata.get("last_build_stats") or self.last_build_stats)
        # Artifact activation, not source-shape scanning, establishes freshness.
        self.signature = ("artifact", tuple(self.vector_unit_ids), tuple(self.cluster_ids))
        return dict(self.last_build_stats)

    def posting_count(self, token: str) -> int:
        return int(self.posting_counts.get(token, len(self.token_units.get(token, ()))))

    def units_for(self, token: str) -> set[str]:
        """Units carrying one token, from whichever lexical projection is live."""
        return self._postings([token]).get(token, set())

    def _postings(self, tokens: Iterable[str]) -> dict[str, set[str]]:
        requested = sorted(set(tokens))
        if not requested:
            return {}
        if self.lexical_db_path is None:
            return {token: set(self.token_units.get(token, ())) for token in requested}
        artifact = Path(self.lexical_db_path).resolve()
        if not artifact.is_file():
            raise RuntimeError("知识词项索引工件缺失，请恢复已验证快照")
        result: dict[str, set[str]] = defaultdict(set)
        # The artifact is immutable after activation.  Open a short-lived
        # read-only connection per request so no cross-thread connection or
        # write lock is retained by the Agent worker.
        uri = artifact.as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            placeholders = ",".join("?" for _ in requested)
            for token, unit_id in connection.execute(
                f"SELECT token, unit_id FROM postings WHERE token IN ({placeholders})",
                requested,
            ):
                result[token].add(unit_id)
        return result

    def ensure(self, store) -> dict[str, int]:
        signature = self._signature(store)
        if signature != self.signature:
            return self.rebuild(store)
        return dict(self.last_build_stats)

    @staticmethod
    def _matrix(values, *, normalise: bool = True):
        if np is None or values is None or len(values) == 0:
            return None
        try:
            matrix = np.asarray(values, dtype=np.float32)
        except (TypeError, ValueError):
            return None
        if matrix.ndim != 2 or not len(matrix) or not matrix.shape[1]:
            return None
        if not normalise:
            return matrix
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return matrix / norms

    @staticmethod
    def _normalise(vector: Iterable) -> list[float]:
        values = [float(value) for value in vector or []]
        norm = math.sqrt(sum(value * value for value in values))
        return [value / norm for value in values] if norm else []

    @staticmethod
    def _top_indices(matrix, vector: list[float], limit: int) -> list[int]:
        if matrix is None or not vector or matrix.shape[1] != len(vector):
            return []
        q = np.asarray(KnowledgeQueryIndex._normalise(vector), dtype=np.float32)
        if not len(q):
            return []
        scores = matrix @ q
        limit = min(max(1, limit), len(scores))
        if limit == len(scores):
            return list(np.argsort(-scores))
        candidates = np.argpartition(-scores, limit - 1)[:limit]
        return list(candidates[np.argsort(-scores[candidates])])

    @staticmethod
    def _fallback_top(values: list[list[float]], vector: list[float], limit: int) -> list[int]:
        query = KnowledgeQueryIndex._normalise(vector)
        if not query:
            return []
        scored = []
        for index, item in enumerate(values):
            if len(item) != len(query):
                continue
            norm = KnowledgeQueryIndex._normalise(item)
            scored.append((sum(a * b for a, b in zip(query, norm)), index))
        return [index for _, index in sorted(scored, reverse=True)[:limit]]

    def candidates(
        self,
        query_vector: list,
        query_text: str,
        *,
        tree_ids: set[str] | None = None,
        start: str | None = None,
        end: str | None = None,
        forced_unit_ids: Iterable[str] | None = None,
        max_units: int = 768,
    ) -> set[str]:
        """Return bounded candidate unit IDs using ring-aware coarse routing."""
        tokens = set(tokenize(query_text))
        selected: set[str] = set(forced_unit_ids or ())

        # A temporal or cross-brain intersection is an explicit candidate
        # boundary.  Do not refill it with global lexical/vector resonance;
        # doing so would silently undo the intersection and could reintroduce
        # the full forest into a narrow query.
        if forced_unit_ids is not None:
            return {
                unit_id for unit_id in selected
                if unit_id in self.unit_tree
                and (not tree_ids or self.unit_tree.get(unit_id) in tree_ids)
                and (not start and not end or any(
                    (not start or day >= start) and (not end or day <= end)
                    for day in self.unit_days.get(unit_id, set())
                ))
            }

        lexical_units: dict[str, set[str]] = {}
        # Exact/lexical anchors are cheap, but a common CJK bigram can occur
        # in every daily ring.  Do not turn a high-frequency posting list
        # into a pseudo full scan; cluster resonance remains the route for it.
        for token, postings in self._postings(tokens).items():
            if len(postings) <= max_units * 2:
                lexical_units[token] = postings
                selected.update(postings)

        cluster_indices: list[int] = []
        if tree_ids:
            # Query only the ontology-selected forest lanes.  This keeps a
            # medical question away from unrelated equipment/finance rings
            # before any unit-level work is attempted.
            for tree_id in tree_ids:
                positions = self._tree_positions(tree_id, start=start, end=end)
                if not positions:
                    continue
                matrix = self.cluster_matrix[positions] if self.cluster_matrix is not None else None
                local = self._top_indices(matrix, query_vector, 64)
                # ``cluster_vectors`` is intentionally empty whenever NumPy
                # owns the matrix.  A query vector from another compatible
                # lexical/runtime path may have a different dimension, in
                # which case _top_indices returns no candidates; that is not
                # a signal to index the empty no-NumPy fallback list.
                if not local and matrix is None and self.cluster_vectors:
                    local = self._fallback_top([self.cluster_vectors[i] for i in positions], query_vector, 64)
                cluster_indices.extend(positions[i] for i in local)
        else:
            if start or end:
                scoped_positions = []
                for tree_id in self.cluster_positions_by_tree:
                    scoped_positions.extend(self._tree_positions(tree_id, start=start, end=end))
                scoped_positions = sorted(set(scoped_positions))
                matrix = self.cluster_matrix[scoped_positions] if self.cluster_matrix is not None and scoped_positions else None
                local = self._top_indices(matrix, query_vector, 64)
                cluster_indices = [scoped_positions[index] for index in local]
                if not cluster_indices and scoped_positions and matrix is None and self.cluster_vectors:
                    cluster_indices = [scoped_positions[index] for index in self._fallback_top(
                        [self.cluster_vectors[i] for i in scoped_positions], query_vector, 64
                    )]
            else:
                cluster_indices = self._top_indices(self.cluster_matrix, query_vector, 64)
                if not cluster_indices and self.cluster_vectors:
                    cluster_indices = self._fallback_top(self.cluster_vectors, query_vector, 64)
        for index in cluster_indices:
            cluster_id = self.cluster_ids[index]
            cluster_days = self.cluster_days.get(cluster_id, set())
            if (start or end) and cluster_days and not any(
                (not start or day >= start) and (not end or day <= end)
                for day in cluster_days
            ):
                continue
            selected.update(self.cluster_members.get(cluster_id, ()))

        # Cluster resonance is the normal route.  A global unit matrix pass is
        # only a cold-start safety lane when no cluster was available at all;
        # once a star-cluster lane has been selected, do not re-scan the full
        # forest just because that lane has a small member set.
        unit_indices: list[int] = []
        if not cluster_indices and not selected:
            unit_indices = self._top_indices(self.unit_matrix, query_vector, max_units)
            if not unit_indices and self.unit_vectors:
                unit_indices = self._fallback_top(self.unit_vectors, query_vector, max_units)
            selected.update(self.vector_unit_ids[index] for index in unit_indices)

        def allowed(unit_id: str) -> bool:
            if tree_ids and self.unit_tree.get(unit_id) not in tree_ids:
                return False
            if start or end:
                days = self.unit_days.get(unit_id, set())
                if not any((not start or day >= start) and (not end or day <= end) for day in days):
                    return False
            return True

        selected = {unit_id for unit_id in selected if unit_id in self.unit_tree and allowed(unit_id)}
        if len(selected) <= max_units:
            return selected

        # Keep lexical/forced candidates first, then use the unit vector score
        # to trim a very large high-frequency posting list.
        preferred = set(forced_unit_ids or ())
        preferred.update(unit_id for postings in lexical_units.values() for unit_id in postings)
        preferred = {unit_id for unit_id in preferred if unit_id in selected}
        ranked_units = [self.vector_unit_ids[index] for index in unit_indices if self.vector_unit_ids[index] in selected]
        cluster_units = [unit_id for index in cluster_indices
                         for unit_id in self.cluster_members.get(self.cluster_ids[index], ())
                         if unit_id in selected]
        ordered = list(sorted(set(forced_unit_ids or ()) & selected))
        ordered.extend(unit_id for unit_id in cluster_units if unit_id not in ordered)
        ordered.extend(unit_id for unit_id in ranked_units if unit_id not in ordered)
        ordered.extend(unit_id for unit_id in sorted(preferred) if unit_id not in ordered)
        # If a caller explicitly requested a small set, preserve the rest;
        # otherwise never refill from arbitrary IDs after resonance ranking.
        if len(ordered) < max_units and len(selected) <= max_units:
            ordered.extend(unit_id for unit_id in sorted(selected) if unit_id not in ordered)
        return set(ordered[:max_units])

    def _tree_positions(self, tree_id: str, *, start: str | None, end: str | None) -> list[int]:
        """Return the tree's cluster lane, optionally narrowed by day rings."""
        all_positions = self.cluster_positions_by_tree.get(tree_id, [])
        if not (start or end):
            return all_positions
        days = self.cluster_days_by_tree.get(tree_id, [])
        if not days:
            return all_positions
        left = bisect_left(days, start) if start else 0
        right = bisect_right(days, end) if end else len(days)
        positions = list(self.cluster_positions_without_day_by_tree.get(tree_id, ()))
        for day in days[left:right]:
            positions.extend(self.cluster_positions_by_tree_day.get((tree_id, day), ()))
        # A cluster can belong to multiple day rings. Preserve the stable
        # index order expected by the bounded resonance scorer.
        return sorted(set(positions))


__all__ = ["KnowledgeQueryIndex"]
