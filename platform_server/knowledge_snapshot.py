"""Versioned, verified query snapshots for the knowledge brain.

The platform database remains the owner of configuration, import jobs and
audit records.  This module writes the *read projection* needed for a
knowledge query to a content-checked external directory.  It deliberately
does not serialize credentials, executable objects or managed source files.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from brains.knowledge.src.brain import KnowledgeBrain
from brains.knowledge.src.contract import CanonicalDocument, KnowledgeEvidence, KnowledgeUnit
from brains.knowledge.src.ontology import Annotation
from brains.knowledge.src.pipeline import ConflictRecord, DocumentPublication, GateRecord, KnowledgeStore
from brains.knowledge.src.temporal import ContentTimeAnchor, KnowledgeTemporalCluster, KnowledgeTemporalIndex
from brains.knowledge.src.workbench import (
    CrossBrainLink,
    EvaluationRun,
    GraphProposal,
    KnowledgeClusterProjection,
    KnowledgeIndexVersion,
    KnowledgeWorkbenchService,
)
from platform_server.store import Store, now
from topology.src.growth import TopologyGeneration
from topology.src.node import TopologyNode
from topology.src.trace import TraceRecord
from topology.src.star_cluster import StarCluster
from brains.knowledge.src.clustering import KnowledgeClusterIndex


SCHEMA_VERSION = 4
_QUERY_STATE_SCHEMAS = {3, SCHEMA_VERSION}
_NORMALISED_VECTOR_SCHEMAS = {2, 3, SCHEMA_VERSION}


class KnowledgeSnapshotError(RuntimeError):
    """The on-disk snapshot is absent, corrupt or incompatible."""


def _owner_hash(owner: str) -> str:
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


def _encode_tuple_map(value: dict[tuple[str, str], list[str]]) -> dict[str, list[str]]:
    return {f"{left}\u0000{right}": list(items) for (left, right), items in value.items()}


def _decode_tuple_map(value: dict[str, list[str]]) -> defaultdict[tuple[str, str], list[str]]:
    result: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    for key, items in value.items():
        left, marker, right = key.partition("\u0000")
        if marker:
            result[(left, right)] = list(items)
    return result


class KnowledgeSnapshotStore:
    """Safe external storage for read-only knowledge workspaces."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.root = store.root / "knowledge-artifacts"
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def record_id(owner: str) -> str:
        return "knowledge-active-revision-" + _owner_hash(owner)[:24]

    def _owner_root(self, owner: str) -> Path:
        return self.root / _owner_hash(owner)

    @staticmethod
    def _state(workbench: KnowledgeWorkbenchService) -> dict[str, Any]:
        store = workbench.brain.store
        generations: dict[str, list[dict[str, Any]]] = {}
        for tree_id in store.generations.tree_ids():
            generations[tree_id] = [
                {
                    "generation": generation.generation,
                    "tree_id": generation.tree_id,
                    "parent": generation.parent,
                    "created_at": generation.created_at,
                    "cause": dict(generation.cause),
                    "nodes": [node.model_dump(mode="json") for node in generation.nodes.values()],
                }
                for generation in store.generations.history(tree_id)
            ]
        cluster_indexes = {}
        for tree_id, index in store.clusters.items():
            cluster_indexes[tree_id] = {
                "tolerance": index.tolerance,
                "algo_version": index.algo_version,
                "rule_version": index.rule_version,
                "counter": index._counter,
                "clusters": [cluster.model_dump(mode="json") for cluster in index.clusters.values()],
                "ss": {key: str(value) for key, value in index._ss.items()},
            }
        temporal = store.temporal_index
        return {
            "documents": {key: item.model_dump(mode="json") for key, item in store.documents.items()},
            "sources": _json_value(store.sources),
            "document_published": dict(store.document_published),
            "document_classification": _json_value(store.document_classification),
            "units": {key: item.model_dump(mode="json") for key, item in store.units.items()},
            "unit_text": dict(store.unit_text),
            "unit_tree": dict(store.unit_tree),
            "unit_annotation": {key: item.model_dump(mode="json") for key, item in store.unit_annotation.items()},
            "unit_attr_claims": {key: [list(pair) for pair in items] for key, items in store.unit_attr_claims.items()},
            "evidence": {key: item.model_dump(mode="json") for key, item in store.evidence.items()},
            "evidence_by_unit": {key: list(items) for key, items in store.evidence_by_unit.items()},
            "evidence_by_document": {key: list(items) for key, items in store.evidence_by_document.items()},
            "traces": {
                key: {
                    **item.model_dump(mode="json", exclude={"vector"}),
                    "vector_dimensions": store.trace_vector_dimensions.get(key, len(item.vector)),
                }
                for key, item in store.traces.items()
            },
            "trace_vector_dimensions": dict(store.trace_vector_dimensions),
            "trace_by_aggregate": {key: list(items) for key, items in store.trace_by_aggregate.items()},
            "trace_by_unit": dict(store.trace_by_unit),
            "cluster_indexes": cluster_indexes,
            "nodes": {key: item.model_dump(mode="json") for key, item in store.nodes.items()},
            "ring_nodes": _encode_tuple_map(store.ring_nodes),
            "payload": dict(store.payload._blobs),
            "publications": {key: item.model_dump(mode="json") for key, item in store.publications.items()},
            "gates": {key: item.model_dump(mode="json") for key, item in store.gates.items()},
            "gate_by_document": {key: list(items) for key, items in store.gate_by_document.items()},
            "conflicts": [item.model_dump(mode="json") for item in store.conflicts],
            "versions": {key: list(items) for key, items in store.versions.items()},
            "unit_content_time": {
                key: [anchor.model_dump(mode="json") for anchor in items]
                for key, items in store.unit_content_time.items()
            },
            "unit_ingested_at": dict(store.unit_ingested_at),
            "unit_observed_at": dict(store.unit_observed_at),
            "document_ingested_at": dict(store.document_ingested_at),
            "document_observed_at": dict(store.document_observed_at),
            "temporal": {
                "anchors_by_unit": {
                    key: [anchor.model_dump(mode="json") for anchor in items]
                    for key, items in temporal.anchors_by_unit.items()
                },
                "content_units_by_day": {key: sorted(items) for key, items in temporal.content_units_by_day.items()},
                "ingest_units_by_day": {key: sorted(items) for key, items in temporal.ingest_units_by_day.items()},
                "observed_units_by_day": {key: sorted(items) for key, items in temporal.observed_units_by_day.items()},
                "content_days": list(temporal.content_days),
                "ingest_days": list(temporal.ingest_days),
                "observed_days": list(temporal.observed_days),
                "clusters": {key: item.model_dump(mode="json") for key, item in temporal.clusters.items()},
                "cluster_members_by_bucket": {key: sorted(items) for key, items in temporal.cluster_members_by_bucket.items()},
                "unit_cluster_ids": {key: sorted(items) for key, items in temporal.unit_cluster_ids.items()},
                "last_build_stats": dict(temporal.last_build_stats),
            },
            "generations": generations,
            "workbench": {
                "indices": {key: item.model_dump(mode="json") for key, item in workbench.indices.items()},
                "active_index_by_tree": dict(workbench.active_index_by_tree),
                "clusters": {key: item.model_dump(mode="json") for key, item in workbench.clusters.items()},
                "unit_cluster": dict(workbench.unit_cluster),
                "cross_brain_links": {key: item.model_dump(mode="json") for key, item in workbench.cross_brain_links.items()},
                "temporal_clusters": {key: item.model_dump(mode="json") for key, item in workbench.temporal_clusters.items()},
                "graph_proposals": {key: item.model_dump(mode="json") for key, item in workbench.graph_proposals.items()},
                "evaluations": {key: item.model_dump(mode="json") for key, item in workbench.evaluations.items()},
                "counter": workbench._counter,
            },
        }

    @staticmethod
    def _query_state(workbench: KnowledgeWorkbenchService) -> dict[str, Any]:
        """Return the read projection required by one knowledge query.

        Full topology generations, payload blobs, mutable clustering records,
        graph proposals and cross-brain candidates stay in ``state.json`` for
        governance and canvas inspection.  Loading them for every platform
        restart made the query worker retain an avoidable multi-gigabyte
        object graph.
        """
        store = workbench.brain.store
        temporal = store.temporal_index
        return {
            "documents": {key: item.model_dump(mode="json") for key, item in store.documents.items()},
            "sources": _json_value(store.sources),
            "document_published": dict(store.document_published),
            "document_classification": _json_value(store.document_classification),
            "units": {key: item.model_dump(mode="json") for key, item in store.units.items()},
            "unit_text": dict(store.unit_text),
            "unit_tree": dict(store.unit_tree),
            "unit_annotation": {key: item.model_dump(mode="json") for key, item in store.unit_annotation.items()},
            "unit_attr_claims": {key: [list(pair) for pair in items] for key, items in store.unit_attr_claims.items()},
            "evidence": {key: item.model_dump(mode="json") for key, item in store.evidence.items()},
            "evidence_by_unit": {key: list(items) for key, items in store.evidence_by_unit.items()},
            "evidence_by_document": {key: list(items) for key, items in store.evidence_by_document.items()},
            "publications": {key: item.model_dump(mode="json") for key, item in store.publications.items()},
            "conflicts": [item.model_dump(mode="json") for item in store.conflicts],
            "unit_content_time": {
                key: [anchor.model_dump(mode="json") for anchor in items]
                for key, items in store.unit_content_time.items()
            },
            "unit_ingested_at": dict(store.unit_ingested_at),
            "unit_observed_at": dict(store.unit_observed_at),
            "document_ingested_at": dict(store.document_ingested_at),
            "document_observed_at": dict(store.document_observed_at),
            "temporal": {
                "anchors_by_unit": {
                    key: [anchor.model_dump(mode="json") for anchor in items]
                    for key, items in temporal.anchors_by_unit.items()
                },
                "content_units_by_day": {key: sorted(items) for key, items in temporal.content_units_by_day.items()},
                "ingest_units_by_day": {key: sorted(items) for key, items in temporal.ingest_units_by_day.items()},
                "observed_units_by_day": {key: sorted(items) for key, items in temporal.observed_units_by_day.items()},
                "content_days": list(temporal.content_days),
                "ingest_days": list(temporal.ingest_days),
                "observed_days": list(temporal.observed_days),
                "clusters": {key: item.model_dump(mode="json") for key, item in temporal.clusters.items()},
                "cluster_members_by_bucket": {key: sorted(items) for key, items in temporal.cluster_members_by_bucket.items()},
                "unit_cluster_ids": {key: sorted(items) for key, items in temporal.unit_cluster_ids.items()},
                "last_build_stats": dict(temporal.last_build_stats),
            },
            "workbench": {
                "unit_cluster": dict(workbench.unit_cluster),
                "temporal_clusters": {key: item.model_dump(mode="json") for key, item in workbench.temporal_clusters.items()},
            },
        }

    def write(self, owner: str, workspace) -> dict[str, Any]:
        """Write and activate a complete candidate snapshot atomically."""
        workspace.knowledge.brain.compact_query_payload()
        owner_root = self._owner_root(owner)
        owner_root.mkdir(parents=True, exist_ok=True)
        revision = "kr-" + uuid.uuid4().hex
        candidate = owner_root / (".candidate-" + revision)
        final = owner_root / revision
        candidate.mkdir()
        try:
            state = self._state(workspace.knowledge)
            _write_json(candidate / "state.json", state)
            _write_json(candidate / "query-state.json", self._query_state(workspace.knowledge))
            query_index = workspace.knowledge.brain.query_index
            query_index.write_lexical_database(candidate / "lexical.sqlite3")
            _write_json(candidate / "query-index.json", query_index.snapshot_metadata(include_postings=False))
            unit_vectors = query_index.unit_matrix
            if unit_vectors is None:
                unit_vectors = np.asarray(query_index.unit_vectors, dtype=np.float32)
            cluster_vectors = query_index.cluster_matrix
            if cluster_vectors is None:
                cluster_vectors = np.asarray(query_index.cluster_vectors, dtype=np.float32)
            # Matrices are already L2-normalised by the query index.  Persist
            # them directly rather than expanding them back into Python lists.
            np.save(candidate / "unit-vectors.npy", unit_vectors, allow_pickle=False)
            np.save(candidate / "cluster-vectors.npy", cluster_vectors, allow_pickle=False)
            files = {
                path.name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
                for path in sorted(candidate.iterdir()) if path.is_file()
            }
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "owner_hash": _owner_hash(owner),
                "revision": revision,
                "created_at": now(),
                "files": files,
                "counts": {
                    "units": len(workspace.knowledge.brain.query_index.unit_ids),
                    "vector_units": len(workspace.knowledge.brain.query_index.vector_unit_ids),
                    "clusters": len(workspace.knowledge.brain.query_index.cluster_ids),
                },
            }
            _write_json(candidate / "manifest.json", manifest)
            os.replace(candidate, final)
            record = self.store.put("knowledge_active_revision", owner, {
                "id": self.record_id(owner),
                "revision": revision,
                "schema_version": SCHEMA_VERSION,
                "path": str(final.relative_to(self.store.root)),
                "counts": manifest["counts"],
            }, action="knowledge.snapshot.activate")
            return record
        except Exception:
            shutil.rmtree(candidate, ignore_errors=True)
            raise

    @staticmethod
    def _link_or_copy(source: Path, target: Path) -> None:
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)

    def promote_external_lexical_index(self, owner: str, query_index) -> dict[str, Any]:
        """Promote a schema-3 snapshot without replaying sources or embeddings.

        Schema 3 already has compact vectors and a query-state projection but
        retains token postings in JSON.  Reuse its immutable state/NPY files
        through hard links, write only the lexical artifact and slim metadata,
        then atomically advance the active revision.
        """
        manifest, source_root = self._load_files(owner)
        if manifest.get("schema_version") == SCHEMA_VERSION:
            return self.store.get("knowledge_active_revision", owner, self.record_id(owner))
        if manifest.get("schema_version") != 3:
            raise KnowledgeSnapshotError("只有 schema 3 快照可无重放提升词项工件")
        if not query_index.token_units:
            raise KnowledgeSnapshotError("schema 3 快照缺少词项 posting，无法无重放提升")
        owner_root = self._owner_root(owner)
        revision = "kr-" + uuid.uuid4().hex
        candidate = owner_root / (".candidate-" + revision)
        final = owner_root / revision
        candidate.mkdir()
        try:
            for source in source_root.iterdir():
                if source.is_file() and source.name not in {"manifest.json", "query-index.json", "lexical.sqlite3"}:
                    self._link_or_copy(source, candidate / source.name)
            query_index.write_lexical_database(candidate / "lexical.sqlite3")
            _write_json(candidate / "query-index.json", query_index.snapshot_metadata(include_postings=False))
            files = {
                path.name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
                for path in sorted(candidate.iterdir()) if path.is_file()
            }
            promoted = {
                "schema_version": SCHEMA_VERSION,
                "owner_hash": _owner_hash(owner),
                "revision": revision,
                "created_at": now(),
                "files": files,
                "counts": dict(manifest.get("counts") or {}),
            }
            _write_json(candidate / "manifest.json", promoted)
            os.replace(candidate, final)
            return self.store.put("knowledge_active_revision", owner, {
                "id": self.record_id(owner),
                "revision": revision,
                "schema_version": SCHEMA_VERSION,
                "path": str(final.relative_to(self.store.root)),
                "counts": promoted["counts"],
            }, action="knowledge.snapshot.lexical_promote")
        except Exception:
            shutil.rmtree(candidate, ignore_errors=True)
            raise

    def _load_files(self, owner: str, *, query_only: bool = False) -> tuple[dict[str, Any], Path]:
        active = self.store.get("knowledge_active_revision", owner, self.record_id(owner))
        if not active:
            raise KnowledgeSnapshotError("没有已激活的知识查询快照")
        path = (self.store.root / active["path"]).resolve()
        if path.parent != self._owner_root(owner).resolve() or not path.is_dir():
            raise KnowledgeSnapshotError("知识查询快照路径无效")
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            raise KnowledgeSnapshotError("知识查询快照缺少清单")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        # Schema 1 used the same external artifact boundary but persisted raw
        # vectors.  It can be promoted in-place to the compact matrix schema
        # without replaying source documents or calling the embedding runtime.
        if manifest.get("schema_version") not in {1, 2, 3, SCHEMA_VERSION} or manifest.get("owner_hash") != _owner_hash(owner):
            raise KnowledgeSnapshotError("知识查询快照版本或归属不匹配")
        files = manifest.get("files") or {}
        if query_only:
            state_name = "query-state.json" if manifest.get("schema_version") in _QUERY_STATE_SCHEMAS else "state.json"
            required = {state_name, "query-index.json", "unit-vectors.npy", "cluster-vectors.npy"}
            if manifest.get("schema_version") == SCHEMA_VERSION:
                required.add("lexical.sqlite3")
            missing = required - set(files)
            if missing:
                raise KnowledgeSnapshotError("知识查询快照缺少必要工件：" + "、".join(sorted(missing)))
            checked = {filename: files[filename] for filename in required}
        else:
            checked = files
        for filename, expected in checked.items():
            file_path = path / filename
            if file_path.name != filename or not file_path.is_file() or _sha256(file_path) != expected.get("sha256"):
                raise KnowledgeSnapshotError("知识查询快照校验失败")
        return manifest, path

    @staticmethod
    def _restore_store(state: dict[str, Any]) -> KnowledgeStore:
        store = KnowledgeStore()
        store.payload._blobs = dict(state.get("payload") or {})
        store.documents = {key: CanonicalDocument(**value) for key, value in (state.get("documents") or {}).items()}
        store.sources = dict(state.get("sources") or {})
        store.document_published = dict(state.get("document_published") or {})
        store.document_classification = dict(state.get("document_classification") or {})
        store.units = {key: KnowledgeUnit(**value) for key, value in (state.get("units") or {}).items()}
        store.unit_text = dict(state.get("unit_text") or {})
        store.unit_tree = dict(state.get("unit_tree") or {})
        store.unit_annotation = {key: Annotation(**value) for key, value in (state.get("unit_annotation") or {}).items()}
        store.unit_attr_claims = {key: [tuple(pair) for pair in values] for key, values in (state.get("unit_attr_claims") or {}).items()}
        store.evidence = {key: KnowledgeEvidence(**value) for key, value in (state.get("evidence") or {}).items()}
        store.evidence_by_unit = defaultdict(list, {key: list(value) for key, value in (state.get("evidence_by_unit") or {}).items()})
        store.evidence_by_document = defaultdict(list, {key: list(value) for key, value in (state.get("evidence_by_document") or {}).items()})
        trace_dimensions = {
            key: int(value)
            for key, value in (state.get("trace_vector_dimensions") or {}).items()
        }
        store.traces = {
            key: TraceRecord(**{
                **{field: item for field, item in value.items() if field != "vector_dimensions"},
                "vector": ["0"],
            })
            for key, value in (state.get("traces") or {}).items()
        }
        store.trace_vector_dimensions = {
            key: trace_dimensions.get(key, int(value.get("vector_dimensions") or 1))
            for key, value in (state.get("traces") or {}).items()
        }
        store.trace_by_aggregate = defaultdict(list, {key: list(value) for key, value in (state.get("trace_by_aggregate") or {}).items()})
        store.trace_by_unit = dict(state.get("trace_by_unit") or {})
        for tree_id, saved in (state.get("cluster_indexes") or {}).items():
            index = KnowledgeClusterIndex(tree_id, tolerance=saved.get("tolerance", "eps=0.05"), algo_version=saved.get("algo_version", "v1"), rule_version=saved.get("rule_version", "v1"))
            index._clusters = {item["cluster_id"]: StarCluster(**item) for item in saved.get("clusters", [])}
            index._ss = {key: Decimal(value) for key, value in (saved.get("ss") or {}).items()}
            index._counter = int(saved.get("counter", len(index._clusters)))
            store.clusters[tree_id] = index
        store.nodes = {key: TopologyNode(**value) for key, value in (state.get("nodes") or {}).items()}
        store.ring_nodes = _decode_tuple_map(state.get("ring_nodes") or {})
        store.publications = {key: DocumentPublication(**value) for key, value in (state.get("publications") or {}).items()}
        store.gates = {key: GateRecord(**value) for key, value in (state.get("gates") or {}).items()}
        store.gate_by_document = defaultdict(list, {key: list(value) for key, value in (state.get("gate_by_document") or {}).items()})
        store.conflicts = [ConflictRecord(**item) for item in state.get("conflicts", [])]
        store.versions = defaultdict(list, {key: list(value) for key, value in (state.get("versions") or {}).items()})
        store.unit_content_time = {key: [ContentTimeAnchor(**item) for item in values] for key, values in (state.get("unit_content_time") or {}).items()}
        store.unit_ingested_at = dict(state.get("unit_ingested_at") or {})
        store.unit_observed_at = dict(state.get("unit_observed_at") or {})
        store.document_ingested_at = dict(state.get("document_ingested_at") or {})
        store.document_observed_at = dict(state.get("document_observed_at") or {})
        temporal_state = state.get("temporal") or {}
        temporal = KnowledgeTemporalIndex()
        temporal.anchors_by_unit = {key: [ContentTimeAnchor(**item) for item in values] for key, values in (temporal_state.get("anchors_by_unit") or {}).items()}
        temporal.content_units_by_day = defaultdict(set, {key: set(value) for key, value in (temporal_state.get("content_units_by_day") or {}).items()})
        temporal.ingest_units_by_day = defaultdict(set, {key: set(value) for key, value in (temporal_state.get("ingest_units_by_day") or {}).items()})
        temporal.observed_units_by_day = defaultdict(set, {key: set(value) for key, value in (temporal_state.get("observed_units_by_day") or {}).items()})
        temporal.content_days = list(temporal_state.get("content_days") or [])
        temporal.ingest_days = list(temporal_state.get("ingest_days") or [])
        temporal.observed_days = list(temporal_state.get("observed_days") or [])
        temporal.clusters = {key: KnowledgeTemporalCluster(**value) for key, value in (temporal_state.get("clusters") or {}).items()}
        temporal.cluster_members_by_bucket = defaultdict(set, {key: set(value) for key, value in (temporal_state.get("cluster_members_by_bucket") or {}).items()})
        temporal.unit_cluster_ids = defaultdict(set, {key: set(value) for key, value in (temporal_state.get("unit_cluster_ids") or {}).items()})
        temporal.last_build_stats = dict(temporal_state.get("last_build_stats") or {})
        store.temporal_index = temporal
        for tree_id, generations in (state.get("generations") or {}).items():
            for saved in generations:
                nodes = {item["node_id"]: TopologyNode(**item) for item in saved.get("nodes", [])}
                store.generations.commit(TopologyGeneration(
                    generation=int(saved["generation"]), tree_id=tree_id, nodes=nodes,
                    parent=saved.get("parent"), created_at=saved.get("created_at"), cause=saved.get("cause") or {},
                ))
        return store

    def load(self, owner: str, runtime, *, query_only: bool = True) -> tuple[KnowledgeWorkbenchService, dict[str, Any]]:
        manifest, path = self._load_files(owner, query_only=query_only)
        state_name = "query-state.json" if query_only and manifest.get("schema_version") in _QUERY_STATE_SCHEMAS else "state.json"
        state = json.loads((path / state_name).read_text(encoding="utf-8"))
        metadata = json.loads((path / "query-index.json").read_text(encoding="utf-8"))
        # Keep vector artifacts as compact NumPy arrays.  Calling ``tolist``
        # here would create a Python float object for every dimension and
        # defeat the memory boundary established by this snapshot.
        unit_vectors = np.load(path / "unit-vectors.npy", allow_pickle=False)
        cluster_vectors = np.load(path / "cluster-vectors.npy", allow_pickle=False)
        brain = KnowledgeBrain(runtime=runtime)
        brain.store = self._restore_store(state)
        brain.pipeline.store = brain.store
        brain.pipeline.growth.store = brain.store
        brain.temporal_index = brain.store.temporal_index
        brain.query_index.restore_snapshot(
            metadata,
            unit_vectors,
            cluster_vectors,
            vectors_normalised=manifest.get("schema_version") in _NORMALISED_VECTOR_SCHEMAS,
            lexical_db_path=(path / "lexical.sqlite3") if manifest.get("schema_version") == SCHEMA_VERSION else None,
        )
        brain.query_index_stats = dict(brain.query_index.last_build_stats)
        brain.temporal_index_stats = dict(brain.temporal_index.last_build_stats)
        brain.query_index_ready = True
        workbench = KnowledgeWorkbenchService(brain)
        saved = state.get("workbench") or {}
        workbench.indices = {key: KnowledgeIndexVersion(**value) for key, value in (saved.get("indices") or {}).items()}
        workbench.active_index_by_tree = dict(saved.get("active_index_by_tree") or {})
        workbench.clusters = {key: KnowledgeClusterProjection(**value) for key, value in (saved.get("clusters") or {}).items()}
        workbench.unit_cluster = dict(saved.get("unit_cluster") or {})
        workbench.cross_brain_links = {key: CrossBrainLink(**value) for key, value in (saved.get("cross_brain_links") or {}).items()}
        workbench.temporal_clusters = {key: KnowledgeTemporalCluster(**value) for key, value in (saved.get("temporal_clusters") or {}).items()}
        workbench.graph_proposals = {key: GraphProposal(**value) for key, value in (saved.get("graph_proposals") or {}).items()}
        workbench.evaluations = {key: EvaluationRun(**value) for key, value in (saved.get("evaluations") or {}).items()}
        workbench._counter = int(saved.get("counter", 0))
        return workbench, manifest
