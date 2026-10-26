"""Durable import jobs; original objects are authoritative, indexes are rebuildable."""
from __future__ import annotations

import io
import hashlib
import json
import logging
import mimetypes
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from brains.knowledge.src.workbench import (
    DocumentReviewRequest, KnowledgeWorkbenchService, SearchRequest, TextImportRequest,
)
from brains.knowledge.src.brain import KnowledgeBrain
from brains.knowledge.src.classification import CLASSIFICATION_VERSION, DOMAIN_NAMES
from brains.knowledge.src.runtime_adapter import RuntimeUnavailableError, Segment
from brains.memory.src.brain import MemoryBrain
from brains.memory.src.ingest import IngestItem
from brains.protocol import BrainQuery, TimeScope
from governance.src import Principal
from platform_server.store import Store, now, uid
from platform_server.media_models import extract_media, StoredMediaExtractor
from platform_server.knowledge_publication import processing_status, synchronized_job, publication_fields
from platform_server.knowledge_snapshot import SCHEMA_VERSION as KNOWLEDGE_SNAPSHOT_SCHEMA_VERSION, KnowledgeSnapshotError, KnowledgeSnapshotStore
from platform_server.text_safety import sanitize_unicode_text

log = logging.getLogger(__name__)


def extract_document(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".json", ".log"}:
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text, _ = sanitize_unicode_text(data.decode(encoding))
                return text or ""
            except UnicodeDecodeError:
                continue
        raise ValueError("文本编码无法识别，请保存为 UTF-8 后重试")
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > 500:
            raise ValueError("单次 PDF 导入最多 500 页，请拆分文档")
        pages = [page.extract_text() or "" for page in reader.pages]
        if not any(p.strip() for p in pages):
            raise ValueError("PDF 无文本层，需要 OCR；原件已保存")
        text, _ = sanitize_unicode_text("\f".join(pages))
        return text or ""
    if suffix == ".docx":
        from docx import Document
        document = Document(io.BytesIO(data))
        text = [p.text for p in document.paragraphs]
        for table in document.tables:
            text.extend(" | ".join(c.text for c in row.cells) for row in table.rows)
        value, _ = sanitize_unicode_text("\n".join(text))
        return value or ""
    raise ValueError("当前支持 TXT、Markdown、CSV、JSON、PDF 和 DOCX；该格式尚未配置解析器")


@dataclass
class Workspace:
    knowledge: KnowledgeWorkbenchService
    memory: MemoryBrain
    knowledge_revision: str | None = None


class KnowledgeWorkspaceWarmingError(RuntimeError):
    """The first compatible snapshot is being prepared outside query handling."""


class Ingestion:
    def __init__(self, store: Store, runtime_profile: str = "weknora-embedded-v1"):
        self.store = store
        self.runtime_profile = runtime_profile
        self.lock = threading.RLock()
        self.workspaces: dict[str, Workspace] = {}
        self.snapshots = KnowledgeSnapshotStore(store)
        self.topology_warming: set[str] = set()
        self.topology_errors: dict[str, str] = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="platform-import")
        # Native WeKnora indexing is a separate background lane: it must not
        # delay imports or occupy the query path while WeKnora vectorises.
        self.index_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weknora-index")
        self.index_pending: set[str] = set()
        self.index_rerun: set[str] = set()
        self.index_status: dict[str, dict] = {}
        self.closed = False

    def recover(self):
        active_owners = set()
        for job in self.store.list("job"):
            if job["status"] == "running":
                asset = self.store.get('asset', job['owner'], job['asset_id'])
                if asset and asset.get('extracted_text') and self.store.object_path(asset['sha256']).is_file():
                    self.workspaces.pop(job['owner'], None)
                    asset['status'] = 'queued'
                    self.store.put('asset', job['owner'], asset)
                    job.update(status='queued', error=None, error_code=None,
                        progress={'stage':'queued', 'message':'服务已恢复；复用已保存正文与向量缓存继续索引'})
                    self.store.put('job', job['owner'], job, action='import.recovered')
                    self.pool.submit(self.run, job['owner'], job['id'])
                    active_owners.add(job['owner'])
                else:
                    job.update(status="failed", error="服务在处理过程中退出；原件已保存，可点击重试", error_code="INTERRUPTED")
                    self.store.put("job", job["owner"], job)
            elif job["status"] == "queued":
                self.pool.submit(self.run, job["owner"], job["id"])
                active_owners.add(job['owner'])
        # Warm query workspaces in the background. Health checks and topology
        # snapshot reads stay responsive while cached indexes are replayed.
        for owner in self.store.owners('asset'):
            if owner in active_owners:
                continue
            assets = self.store.list('asset', owner)
            if owner not in self.topology_warming and any(asset.get('status') in {'ready', 'partial', 'pending_review', 'rejected'} for asset in assets):
                self.topology_warming.add(owner)
                self.pool.submit(self._warm_topology, owner)

    def close(self):
        self.closed = True
        self.pool.shutdown(wait=True, cancel_futures=False)
        self.index_pool.shutdown(wait=False, cancel_futures=True)

    def _schedule_sidecar_sync(self, owner: str) -> None:
        if self.runtime_profile == "builtin-dev-v1" or self.closed:
            return
        with self.lock:
            if owner in self.index_pending:
                # An import may activate a newer revision while the current
                # sync is still waiting for WeKnora; queue one follow-up run.
                self.index_rerun.add(owner)
                return
            self.index_pending.add(owner)
            self.index_rerun.discard(owner)
        self.index_pool.submit(self._sidecar_sync, owner)

    @staticmethod
    def _wait_for_platform() -> bool:
        """WeKnora vectorisation calls this platform's local model endpoint."""
        import urllib.request
        base = os.environ.get("PLATFORM_BASE_URL")
        if not base:
            return True
        for _ in range(60):
            try:
                with urllib.request.urlopen(base.rstrip("/") + "/healthz", timeout=1) as response:
                    if response.status == 200:
                        return True
            except OSError:
                pass
            except Exception:
                pass
            time.sleep(1)
        return False

    def _sidecar_sync(self, owner: str) -> None:
        try:
            while True:
                if not self._wait_for_platform():
                    self.index_status[owner] = {"status": "error", "error": "平台服务未就绪"}
                    return
                workspace = self.workspace(owner)
                from platform_server.weknora_index import synchronize
                result = {}
                for attempt in range(2):
                    result = synchronize(self.store, owner, workspace.knowledge.brain.store)
                    if result.get("status") != "error":
                        break
                    if attempt == 0 and result.get("reason") in {"sidecar-unavailable", "models-unavailable", "index-submit-failed"}:
                        time.sleep(15)
                        continue
                    break
                self.index_status[owner] = result
                if result.get("status") == "error":
                    log.warning("Native WeKnora index incomplete owner=%s reason=%s", owner, result.get("reason"))
                with self.lock:
                    if owner not in self.index_rerun:
                        return
                    self.index_rerun.discard(owner)
        except Exception as exc:  # never fail imports or queries from background indexing
            self.index_status[owner] = {"status": "error", "error": type(exc).__name__}
            log.exception("Native WeKnora index failed owner=%s", owner)
        finally:
            with self.lock:
                self.index_pending.discard(owner)
                again = owner in self.index_rerun
                self.index_rerun.discard(owner)
            if again:
                self._schedule_sidecar_sync(owner)

    def native_index_status(self, owner: str) -> dict:
        if self.runtime_profile == "builtin-dev-v1":
            return {"status": "builtin", "upstream": "builtin"}
        try:
            from platform_server.weknora_index import WeKnoraIndex
            index = WeKnoraIndex(self.store, owner)
            state = index._state()
            return {
                "status": (state or {}).get("status") or "not-built",
                "revision": (state or {}).get("revision"),
                "active_revision": index.active_revision(),
                "error": (state or {}).get("error"),
                "sync": dict(self.index_status.get(owner) or {}),
                "upstream": "LOCKED",
            }
        except Exception:
            return {"status": "unavailable", "upstream": "LOCKED"}

    @staticmethod
    def principal(owner: str) -> Principal:
        # Access to this internal service is checked by the authenticated API.
        return Principal(subject=owner, scopes=["knowledge:publish"])

    def _runtime(self, owner: str):
        if self.runtime_profile == 'builtin-dev-v1':
            return None
        from platform_server.knowledge_runtime import EmbeddedRuntime
        return EmbeddedRuntime(self.store, owner)

    def _build_knowledge_candidate(self, owner: str, incoming: dict, memory: MemoryBrain, progress=None) -> tuple[Workspace, dict]:
        """Replay persisted knowledge outside the read path, then return a whole candidate.

        The caller atomically installs this workspace only after every source,
        review, forest projection and query index has completed.  Readers keep
        using the previous immutable workspace during this work.
        """
        candidate = Workspace(KnowledgeWorkbenchService(KnowledgeBrain(runtime=self._runtime(owner))), memory)
        assets = [
            item for item in self.store.list("asset", owner)
            if item.get("brain") == "knowledge" and item.get("status") in {"ready", "partial", "pending_review", "rejected"}
        ]
        assets = [item for item in assets if item["id"] != incoming["id"]] + [incoming]
        result = None
        for position, item in enumerate(assets, start=1):
            built = self.index(candidate, owner, item, progress if item["id"] == incoming["id"] else None, prepare_index=False)
            if item["id"] == incoming["id"]:
                result = built
            # Bound the one-time legacy migration peak.  The next batch can
            # rebuild from this immutable query projection without restoring
            # prior Decimal-string vectors.
            if position % 4 == 0:
                candidate.knowledge.brain.prepare_query_index()
                candidate.knowledge.brain.compact_query_payload()
        for review in self.store.list("review", owner):
            if review["document_id"] not in candidate.knowledge.brain.store.publications:
                continue
            candidate.knowledge.review_document(review["document_id"], DocumentReviewRequest(
                request_id=review["id"], trace_id=review["id"], action=review["action"], reason=review["reason"]), self.principal(owner))
        candidate.knowledge.brain.prepare_query_index()
        candidate.knowledge.brain.compact_query_payload()
        if result is None:
            raise RuntimeError("候选知识工作区未产生导入结果")
        history = [record for record in self.store.list("review", owner) if record["document_id"] == result["document_id"]]
        result.update(publication_fields({**incoming, **result}, candidate.knowledge.brain.store,
                                        result["document_id"], history[-1] if history else None))
        return candidate, result

    def _schedule_warm(self, owner: str) -> None:
        with self.lock:
            if owner in self.topology_warming:
                return
            self.topology_warming.add(owner)
            self.pool.submit(self._warm_topology, owner)

    def workspace(self, owner: str, *, allow_compat_rebuild: bool = True) -> Workspace:
        with self.lock:
            if owner in self.workspaces:
                return self.workspaces[owner]
        runtime = self._runtime(owner)
        # A verified snapshot contains only the immutable read projection.
        # It avoids replaying every historical document under a query lock.
        # Source objects and credentials are never present in the snapshot.
        try:
            knowledge, manifest = self.snapshots.load(owner, runtime)
            if manifest.get("schema_version") == 3:
                # Schema 3 already has the query projection and NPY vectors.
                # Promote its high-cardinality lexical JSON to an immutable
                # artifact without touching imported source material, then
                # remap the new compact projection.
                self.snapshots.promote_external_lexical_index(owner, knowledge.brain.query_index)
                del knowledge
                return self.workspace(owner, allow_compat_rebuild=allow_compat_rebuild)
            # A legacy artifact is queryable immediately, then promoted by the
            # background worker to the compact matrix schema without replaying
            # every imported source document.
            revision = manifest["revision"] if manifest.get("schema_version") == KNOWLEDGE_SNAPSHOT_SCHEMA_VERSION else None
            ws = Workspace(knowledge, MemoryBrain(), revision)
            for asset in self.store.list("asset", owner):
                if asset.get("brain") != "knowledge" and asset["status"] in {"ready", "partial", "pending_review", "rejected"}:
                    self.index(ws, owner, asset)
            with self.lock:
                return self.workspaces.setdefault(owner, ws)
        except KnowledgeSnapshotError:
            if not allow_compat_rebuild:
                self._schedule_warm(owner)
                raise KnowledgeWorkspaceWarmingError(
                    "知识索引正在从已有资料生成首个只读快照；资料未丢失，请稍后重试。"
                )

        # Compatibility reconstruction is intentionally performed only by a
        # background warm-up or import worker.  It never holds the global lock,
        # and user queries request the non-blocking branch above.
        ws = Workspace(KnowledgeWorkbenchService(KnowledgeBrain(runtime=runtime)), MemoryBrain())
        knowledge_count = 0
        for asset in self.store.list("asset", owner):
            if asset["status"] in {"ready", "partial", "pending_review", "rejected"}:
                self.index(ws, owner, asset, prepare_index=False)
                if asset.get("brain") == "knowledge":
                    knowledge_count += 1
                    if knowledge_count % 4 == 0:
                        ws.knowledge.brain.prepare_query_index()
                        ws.knowledge.brain.compact_query_payload()
        for review in self.store.list("review", owner):
            if review['document_id'] not in ws.knowledge.brain.store.publications:
                continue
            ws.knowledge.review_document(review["document_id"], DocumentReviewRequest(
                request_id=review["id"], trace_id=review["id"], action=review["action"], reason=review["reason"]), self.principal(owner))
        ws.knowledge.brain.prepare_query_index()
        ws.knowledge.brain.compact_query_payload()
        self.activate_snapshot(owner, ws)
        with self.lock:
            # A compatibility rebuild has just produced a complete mutable
            # workspace.  Do not retain it on the hot path: the next caller
            # must map the compact query projection that was just activated.
            self.workspaces.pop(owner, None)
        return self.workspace(owner)

    def full_workspace(self, owner: str) -> Workspace:
        """Load the complete mutable topology only for canvas/governance work.

        Normal Agent retrieval uses ``workspace`` and its compact query
        projection.  This path intentionally is not cached as the hot query
        workspace: a topology canvas or review may need the large generation
        history, payload graph and cluster details, while a chat request does
        not.
        """
        runtime = self._runtime(owner)
        try:
            knowledge, manifest = self.snapshots.load(owner, runtime, query_only=False)
        except KnowledgeSnapshotError:
            return self.workspace(owner)
        revision = manifest["revision"] if manifest.get("schema_version") == KNOWLEDGE_SNAPSHOT_SCHEMA_VERSION else None
        return Workspace(knowledge, MemoryBrain(), revision)

    def activate_snapshot(self, owner: str, workspace: Workspace) -> dict:
        """Persist an already-consistent read projection before publishing it."""
        record = self.snapshots.write(owner, workspace)
        workspace.knowledge_revision = record["revision"]
        return record

    @staticmethod
    def topology_snapshot_id(owner: str) -> str:
        return 'topology-snapshot-' + hashlib.sha256(owner.encode('utf-8')).hexdigest()[:24]

    def persist_topology(self, owner: str, workspace: Workspace, *, overrides=None, correction=None, review=None) -> dict:
        from platform_server.knowledge_topology import workspace_topology
        from platform_server.knowledge_classification import persist_classification
        value = workspace_topology(workspace)
        record = {'id': self.topology_snapshot_id(owner), 'schema_version': 3, 'projection': value}
        persist_classification(self.store, owner, workspace, record, overrides=overrides, correction=correction, review=review)
        return value

    def _warm_topology(self, owner: str):
        try:
            workspace = self.workspace(owner, allow_compat_rebuild=True)
            # Build the read-only forest/ring/cluster query projection while
            # the owner is already in the background warm-up path.  The first
            # user question then pays only candidate scoring, never a full
            # index rebuild across every historical day ring.
            workspace.knowledge.brain.ensure_query_index()
            with self.lock:
                self.persist_topology(owner, workspace)
                if workspace.knowledge_revision is None:
                    self.activate_snapshot(owner, workspace)
                self.topology_errors.pop(owner, None)
            self._schedule_sidecar_sync(owner)
        except Exception:
            self.topology_errors[owner] = '知识分类或拓扑恢复失败；旧快照保留，请查看服务运行日志'
            log.exception('Topology warm-up failed owner=%s', owner)
        finally:
            with self.lock:
                self.topology_warming.discard(owner)

    def topology(self, owner: str) -> dict:
        """Return a durable snapshot immediately and rebuild missing legacy snapshots."""
        saved = self.store.get('topology_snapshot', owner, self.topology_snapshot_id(owner))
        if saved and saved.get('schema_version') == 3:
            projection = dict(saved['projection'])
            projection['_topology_version'] = saved.get('updated_at') or saved.get('created_at')
            projection['knowledge_topology'] = dict(projection['knowledge_topology'])
            projection['knowledge_topology']['workspace_status'] = 'warming' if owner in self.topology_warming else 'ready'
            projection['knowledge_topology']['classification_status'] = 'current' if saved.get('classification_version') == CLASSIFICATION_VERSION else 'rebuilding'
            if owner in self.topology_errors:
                projection['knowledge_topology']['classification_status'] = 'failed'
                projection['knowledge_topology']['classification_error'] = self.topology_errors[owner]
            return projection
        from platform_server.knowledge_topology import rebuilding_topology
        projection = rebuilding_topology(self.store, owner)
        # A cold replay intentionally holds the workspace lock for consistency.
        # Topology reads must remain responsive and expose durable facts meanwhile.
        if not self.lock.acquire(blocking=False):
            return projection
        try:
            workspace = self.workspaces.get(owner)
            if workspace is not None:
                return self.persist_topology(owner, workspace)
            if projection['knowledge_topology']['status'] == 'rebuilding' and owner not in self.topology_warming:
                self.topology_warming.add(owner)
                self.pool.submit(self._warm_topology, owner)
            return projection
        finally:
            self.lock.release()

    def index(self, ws: Workspace, owner: str, asset: dict, progress=None, *, prepare_index: bool = True) -> dict:
        if asset["brain"] == "knowledge":
            job = ws.knowledge.create_text_import(TextImportRequest(
                request_id=asset["id"], trace_id=asset["id"], content=asset["extracted_text"],
                title=asset["title"], source_id=asset["id"], version=asset["sha256"],
                permission_scope=["owner"], metadata={"original_sha256": asset["sha256"], "platform_asset_id": asset["id"],
                    "classification_override": asset.get("classification_override"),
                    "model_extraction": asset.get("model_extraction"), "pdf_extraction": asset.get("pdf_extraction"),
                    "extraction_status": "ocr" if asset.get("pdf_extraction", {}).get("ocr_pages") else "inferred" if asset.get("model_extraction") else "source_text",
                    # 两条时间轴由导入资产的持久化时间和用户明确的观察时间分别提供。
                    "ingested_at": asset.get("created_at"),
                    "observed_at": asset.get("observed_at") or asset.get("timestamp")}),
                self.principal(owner), import_id=asset["id"])
            runtime = ws.knowledge.brain.runtime
            if hasattr(runtime, 'progress'):
                runtime.progress = progress
                runtime.indexed_count = 0
            try:
                job = ws.knowledge.start_import(job.job_id, self.principal(owner), prepare_index=prepare_index)
            finally:
                if hasattr(runtime, 'progress'):
                    runtime.progress = None
            if job.status == "failed":
                raise ValueError(job.error_message or "知识处理失败")
            graph = asset.get('graph_candidates')
            wiki = asset.get('wiki')
            graph_error = asset.get('graph_error')
            gates = [ws.knowledge.brain.store.gates[g] for g in ws.knowledge.brain.store.gate_by_document.get(job.document_id, [])]
            indexed_units = [u for u in ws.knowledge.brain.store.document_units(job.document_id) if u.chunk_level == 1]
            return {"document_id": job.document_id, "index_version": job.index_version,
                    "indexing_complete": True, "publication_status": job.status,
                    "classification": ws.knowledge.brain.store.document_classification.get(job.document_id),
                    "indexed_units": len(indexed_units),
                    "embedding_dimensions": len(ws.knowledge.brain.store.unit_vector[indexed_units[0].unit_id]) if indexed_units else 0,
                    "review_reasons": [gate.reason for gate in gates if gate.status == 'pending_review'],
                    "runtime_profile": self.runtime_profile, "graph_candidates": graph, "wiki": wiki, "graph_error": graph_error,
                    "status": ("partial" if graph_error or asset.get('pdf_extraction', {}).get('unresolved_pages') else "ready") if job.status == "published" else "pending_review",
                    "steps": [s.model_dump(mode="json") for s in job.steps]}
        data = self.store.object_path(asset["sha256"]).read_bytes()
        mime = asset.get("mime", "text/plain")
        modality = "keyframe" if mime.startswith("image/") else "asr" if mime.startswith("audio/") else "video" if mime.startswith("video/") else "text"
        extraction = asset.get("model_extraction")
        if extraction:
            ws.memory.registry.register(StoredMediaExtractor(modality))
        item = IngestItem(media_ref=f"asset://{asset['id']}", modality=modality,
            raw_content=data, content=asset.get("extracted_text") or asset.get("description"),
            timestamp=asset.get("timestamp"), place=asset.get("place"), person=asset.get("person"),
            permission_scope="owner", source_ref=asset["source_id"], source_kind="managed_import", trust="model" if extraction else "user",
            metadata={"model_extraction": extraction} if extraction else {})
        unit = ws.memory.ingest(item)
        partial = modality in {"keyframe", "asr", "video"} and not extraction
        return {"unit": unit.model_dump(mode="json"), "status": "partial" if partial else "ready",
                "unavailable_features": [f.reason for f in unit.features if f.status == "unavailable"]}

    def enrich_knowledge(self, ws, result, progress):
        if self.runtime_profile == 'builtin-dev-v1':
            return
        units = ws.knowledge.brain.store.document_units(result['document_id'])
        segments = [Segment(text=ws.knowledge.brain.store.unit_text[u.unit_id], anchor=u.unit_id)
                    for u in units if u.unit_id in ws.knowledge.brain.store.unit_text]
        runtime = ws.knowledge.brain.runtime
        errors = []
        runtime.enrichment_progress = progress
        try:
            for field, call in [('graph_candidates', runtime.concept_graph), ('wiki', runtime.wiki)]:
                try:
                    result[field] = call(segments)
                except RuntimeUnavailableError as exc:
                    detail = '对话模型连接超时或未响应，已保存正文与向量索引' if exc.message == 'MODEL_CONNECTION_FAILED' or exc.code == 'RUNTIME_TIMEOUT' else exc.message
                    errors.append(('概念提取' if field=='graph_candidates' else '知识摘要') + '：' + detail)
                    if '429' in exc.message:
                        errors[-1] = '对话模型服务限流（HTTP 429），已完成的分批结果保留；请稍后重新处理'
                        break
        finally:
            runtime.enrichment_progress = None
        result['graph_error'] = '；'.join(errors) or None
        if result['publication_status'] == 'published':
            result['status'] = 'partial' if errors else 'ready'

    def submit(self, owner: str, source: dict, filename: str, data: bytes, metadata: dict | None = None) -> dict:
        metadata = metadata or {}
        timestamp = metadata.get("timestamp")
        if timestamp:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        digest = self.store.object(data)
        with self.lock:
            for prior in self.store.list("job", owner):
                if prior.get("source_id") == source["id"] and prior.get("sha256") == digest and prior.get("metadata", {}) == metadata and prior["status"] in {"queued", "running", "succeeded", "partial", "pending_review"}:
                    return prior
            asset = self.store.put("asset", owner, dict(id=uid("asset"), brain=source["brain"], source_id=source["id"],
                title=filename, filename=Path(filename).name, sha256=digest, bytes=len(data),
                mime=mimetypes.guess_type(filename)[0] or "application/octet-stream", status="queued", **metadata), action="asset.register")
            job = self.store.put("job", owner, dict(id=uid("job"), owner=owner, asset_id=asset["id"],
                source_id=source["id"], brain=source["brain"], title=filename, sha256=digest, metadata=metadata,
                status="queued", attempts=0, error=None), action="import.queued")
            self.pool.submit(self.run, owner, job["id"])
            return job

    def run(self, owner: str, job_id: str):
        # Claim briefly. OCR/network work must not hold the workspace lock:
        # otherwise a long scan blocks uploads, retries and all knowledge queries.
        with self.lock:
            job = self.store.get("job", owner, job_id)
            if not job or job["status"] != "queued":
                return
            asset = self.store.get("asset", owner, job["asset_id"])
            asset.update(status='running', processing_status='running')
            self.store.put('asset', owner, asset)
            job.update(status="running", attempts=job["attempts"] + 1, error=None, error_code=None, result=None)
            self.store.put("job", owner, job, action="import.running")
        def progress(value):
            job['progress'] = value
            self.store.put('job', owner, job)
        try:
            data = self.store.object_path(asset["sha256"]).read_bytes()
            reused_extraction = bool(asset.get('extracted_text'))
            if reused_extraction:
                progress({'stage':'extracting', 'message':'复用已保存的正文解析结果'})
            else:
                progress({'stage': 'extracting', 'message': '正在读取原件与解析正文'})
                extraction = asset.get("model_extraction") or extract_media(self.store, asset, data)
                if extraction:
                    asset["model_extraction"] = extraction
                    asset["extracted_text"] = extraction["text"]
                elif asset["brain"] == "knowledge" or asset["mime"].startswith("text/"):
                    if asset['brain'] == 'knowledge' and Path(asset['filename']).suffix.lower() == '.pdf':
                        from platform_server.pdf_extraction import extract_pdf
                        asset['extracted_text'], asset['pdf_extraction'] = extract_pdf(self.store, owner, asset['sha256'], data, progress)
                    else:
                        asset["extracted_text"] = extract_document(asset["filename"], data)
                    if not asset["extracted_text"].strip() or len(asset["extracted_text"]) > 2_000_000:
                        raise ValueError("文档无可提取文本或超过 200 万字符，请检查原件或拆分文档")
            cleaned, replaced_count = sanitize_unicode_text(asset.get("extracted_text"))
            if cleaned is not None:
                asset["extracted_text"] = cleaned
            replaced_count = max(
                replaced_count,
                int((asset.get("pdf_extraction") or {}).get("unicode_replacements", 0) or 0),
            )
            if replaced_count:
                asset["text_sanitization"] = {
                    "replaced_count": replaced_count,
                    "replacement_character": "�",
                    "reason": "解析器返回了无法编码的 Unicode 代理字符；原始文件未修改",
                }
            # Persist successful extraction independently of indexing/enrichment.
            # Reused assets only need a write when the safety pass changed text.
            if not reused_extraction or replaced_count:
                self.store.put('asset', owner, asset, action='asset.extracted')
            progress({'stage': 'indexing', 'message': '正在分块并建立向量索引'})
            if asset['brain'] == 'knowledge':
                # Do not mutate the workspace currently serving queries.  A
                # full candidate is built without the global lock and becomes
                # visible only after its index and governance replay succeed.
                active_memory = self.workspace(owner).memory
                ws, result = self._build_knowledge_candidate(owner, asset, active_memory, progress)
                progress({'stage': 'enriching', 'message': '向量索引已完成，正在分批生成知识摘要与概念候选'})
                self.enrich_knowledge(ws, result, progress)
                if asset.get('pdf_extraction', {}).get('unresolved_pages') and result['status'] == 'ready':
                    result['status'] = 'partial'
            else:
                with self.lock:
                    ws = self.workspace(owner)
                    result = self.index(ws, owner, asset, progress)
            asset.update(result)
            asset['processing_status'] = processing_status(asset)
            result['processing_status'] = asset['processing_status']
            self.store.put("asset", owner, asset, action="asset.indexed")
            with self.lock:
                self.persist_topology(owner, ws)
                if asset['brain'] == 'knowledge':
                    self.activate_snapshot(owner, ws)
                    # ``ws`` contains the complete import/governance graph.
                    # The immutable compact query projection is reloaded only
                    # when the next Agent request arrives.
                    self.workspaces.pop(owner, None)
            if asset['brain'] == 'knowledge':
                self._schedule_sidecar_sync(owner)
            job.update(status=asset["status"] if asset["status"] in {"pending_review", "partial"} else "succeeded", result=result)
            if asset.get('graph_error'):
                job['error'] = '正文与向量索引已保存，知识摘要 / 概念提取未完成：' + asset['graph_error']
            elif asset["status"] == "partial":
                missing = asset.get('pdf_extraction', {}).get('unresolved_pages', [])
                job["error"] = f'OCR 未识别页面：{missing}；其余页面已索引，请核对原件' if missing else "原件和已有特征已保存；媒体语义提取未配置或尚不支持，可补齐配置后重新处理"
            message = '解析和索引已完成'
            if job['status'] == 'pending_review':
                message += '；发布待复核：' + '；'.join(result.get('review_reasons', []))
            progress({'stage': 'complete', 'message': message})
            if asset['brain'] == 'knowledge':
                job = synchronized_job(job, asset)
        except Exception as exc:
            asset["status"] = "failed"
            self.store.put("asset", owner, asset)
            job.update(status="failed", error=str(exc)[:400] if isinstance(exc, (ValueError, RuntimeUnavailableError)) else "处理失败，请检查文件格式与运行日志", error_code=getattr(exc, 'code', type(exc).__name__))
            log.warning("Import failed job=%s type=%s", job_id, type(exc).__name__)
        self.store.put("job", owner, job, action="import." + job["status"])

    def retry(self, owner: str, job_id: str) -> dict:
        with self.lock:
            job = self.store.get("job", owner, job_id)
            asset = self.store.get("asset", owner, job["asset_id"]) if job else None
            if not job or job['status'] in {'queued', 'running'} or not (job['status'] in {'failed', 'cancelled'} or processing_status(asset) == 'partial'):
                raise ValueError("只有失败、部分完成或取消的任务可以重试")
            asset["status"] = "queued"
            self.store.put("asset", owner, asset)
            self.workspaces.pop(owner, None)
            job.update(status="queued", error=None, error_code=None, result=None, progress={'stage': 'queued', 'message': '等待处理；复用已保存的逐页解析结果'})
            job = self.store.put("job", owner, job, action="import.retry")
            self.pool.submit(self.run, owner, job_id)
            return job

    def search(self, owner: str, brain: str, request: dict) -> dict:
        # Workspace replacement is atomic at the dictionary-reference level.
        # A query retains its captured, immutable read workspace while an
        # import prepares a later candidate; never hold the ingestion lock
        # across retrieval, reranking or provider generation preparation.
        ws = self.workspace(owner, allow_compat_rebuild=brain != "knowledge")
        trace = uid("query")
        if brain == "knowledge":
            response = ws.knowledge.search(SearchRequest(
                    request_id=trace,
                    trace_id=trace,
                    query=request["query"],
                    profile_id="owner",
                    day_key=request.get("day_key"),
                    start=request.get("start"),
                    end=request.get("end"),
                    content_start=request.get("content_start"),
                    content_end=request.get("content_end"),
                    ingest_start=request.get("ingest_start"),
                    ingest_end=request.get("ingest_end"),
                    observed_start=request.get("observed_start"),
                    observed_end=request.get("observed_end"),
                    temporal_cluster_ids=request.get("temporal_cluster_ids") or [],
                    candidate_cluster_ids=request.get("candidate_cluster_ids") or [],
                    candidate_unit_ids=request.get("candidate_unit_ids") or [],
                ))
            # Preserve human-readable citation data beside the immutable evidence ID.
            # This is only a response projection; the source-of-truth remains the knowledge store.
            for evidence in response.get("result", {}).get("evidence", []):
                item = ws.knowledge.brain.store.evidence.get(evidence.get("evidence_id"))
                if not item:
                    continue
                document = ws.knowledge.brain.store.documents.get(item.document_id)
                evidence["document_title"] = document.title if document else item.document_id
                evidence["locator"] = dict(item.locator or {})
                evidence["ingested_at"] = ws.knowledge.brain.store.unit_ingested_at.get(item.unit_id)
                evidence["observed_at"] = ws.knowledge.brain.store.unit_observed_at.get(item.unit_id)
                evidence["content_time"] = [
                    anchor.model_dump(mode="json")
                    for anchor in ws.knowledge.brain.store.unit_content_time.get(item.unit_id, [])
                ]
            selected = response["route"].get("selected", [])
            response["passages"] = [{"unit_id": item, "text": ws.knowledge.brain.store.unit_text.get(item, ""),
                                     "document_title": ws.knowledge.brain.store.documents[ws.knowledge.brain.store.units[item].document_id].title,
                                     "locator": ws.knowledge.brain.store.units[item].locator.model_dump(mode="json")}
                                    for item in selected[:20]]
        else:
            scope = None
            if request.get("start") or request.get("end"):
                if not request.get("start") or not request.get("end") or request["start"] > request["end"]:
                    raise ValueError("请填写完整且顺序正确的起止日期")
                scope = TimeScope(start=request["start"], end=request["end"], granularity="day", confidence="exact")
            result = ws.memory.query(BrainQuery(request_id=trace, trace_id=trace, raw_query=request["query"],
                profile_id="owner", day_key=ws.memory.ruler.day_key(), time_scope=scope,
                constraints={k: request[k] for k in ("place", "person") if request.get(k)}))
            response = {"result": result.model_dump(mode="json"), "trace_id": trace}
        self.store.audit(owner, "query." + brain, trace)
        return response

    def review(self, owner: str, asset: dict, body: dict) -> dict:
        with self.lock:
            asset = self.store.get('asset', owner, asset['id'])
            if asset['status'] in {'failed', 'queued', 'running', 'cancelled'}:
                raise ValueError('请先完成资料处理，再操作发布状态')
            if any(j['asset_id'] == asset['id'] and j['status'] in {'running', 'queued'} for j in self.store.list('job', owner)):
                raise ValueError('资料正在处理，请完成后再操作发布状态')
            ws = self.full_workspace(owner)
            state = ws.knowledge.brain.store.publications[asset['document_id']].status
            if body.get('expected_status') and body['expected_status'] != state:
                raise ValueError('发布状态已变化，请刷新后重新操作')
            allowed = {'pending_review': {'approve', 'reject'}, 'published': {'rollback'}, 'rejected': {'rollback'}}
            if body['action'] not in allowed.get(state, set()):
                raise ValueError('该操作不适用于当前发布状态，请刷新后重新操作')
            record = dict(id=uid('review'), asset_id=asset['id'], document_id=asset['document_id'],
                          action=body['action'], reason=body['reason'], actor=owner, before_status=state, created_at=now())
            try:
                result = ws.knowledge.review_document(asset['document_id'], DocumentReviewRequest(
                    request_id=record['id'], trace_id=record['id'], action=body['action'], reason=body['reason']), self.principal(owner))
                record['after_status'] = result['status']
                self.persist_topology(owner, ws, review=record)
                self.activate_snapshot(owner, ws)
                # A review needs the complete topology but a query does not.
                # Never retain this governance workspace as the hot cache.
                self.workspaces.pop(owner, None)
            except Exception:
                # Uncommitted decisions must never influence a later query.
                self.workspaces.pop(owner, None)
                raise
            self._schedule_sidecar_sync(owner)
            return result

    def reclassify(self, owner: str, asset: dict, domain: str | None, reason: str) -> dict:
        """Build a replacement knowledge workspace before switching any live state."""
        from platform_server.knowledge_classification import correction_record
        if domain is not None and domain not in DOMAIN_NAMES:
            raise ValueError('不支持的知识主领域')
        if asset['brain'] != 'knowledge' or not asset.get('document_id'):
            raise ValueError('只能纠偏已索引的知识资料')
        with self.lock:
            if any(j['status'] in {'queued', 'running'} for j in self.store.list('job', owner)):
                raise ValueError('请等待当前导入任务完成后再纠偏分类')
            current = self.full_workspace(owner)
            asset = self.store.get('asset', owner, asset['id'])
            record = correction_record(asset, domain, reason)
            override = {'domain': domain, 'reason': reason, 'at': record['created_at'], 'actor': owner} if domain else None
            replacement = Workspace(KnowledgeWorkbenchService(KnowledgeBrain(runtime=current.knowledge.brain.runtime)), current.memory)
            for original in self.store.list('asset', owner):
                if original['brain'] != 'knowledge' or original['status'] not in {'ready', 'partial', 'pending_review', 'rejected'}:
                    continue
                if original['id'] == asset['id']:
                    original = {**original, 'classification_override': override}
                self.index(replacement, owner, original)
            for review in self.store.list('review', owner):
                replacement.knowledge.review_document(review['document_id'], DocumentReviewRequest(
                    request_id=review['id'], trace_id=review['id'], action=review['action'], reason=review['reason']), self.principal(owner))
            # Preserve existing publication restrictions even if a new subject
            # has weaker rules; explicit review remains a separate operation.
            for doc_id, publication in current.knowledge.brain.store.publications.items():
                if publication.status != 'published' and doc_id in replacement.knowledge.brain.store.publications:
                    replacement.knowledge.brain.store.publications[doc_id].status = publication.status
            replacement.knowledge.brain.prepare_query_index()
            replacement.knowledge.brain.compact_query_payload()
            record['after'] = replacement.knowledge.brain.store.document_classification[asset['document_id']]
            self.persist_topology(owner, replacement, overrides={asset['id']: override}, correction=record)
            self.activate_snapshot(owner, replacement)
            # Publish first, then force the following query to map only the
            # compact read projection.  This also makes the correction survive
            # a process restart rather than living only in this worker.
            self.workspaces.pop(owner, None)
            self._schedule_sidecar_sync(owner)
            return self.store.get('asset', owner, asset['id'])
