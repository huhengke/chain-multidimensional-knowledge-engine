"""知识脑受管导入、知识星团与工作台 API 的宿主服务。

本模块只编排 ``KnowledgeBrain``。它不创建第二套事实库、权限系统或
独立 WeKnora 服务；所有索引、星团和图谱对象都从知识脑的证据、留痕和
拓扑 generation 投影而来。
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from brains.knowledge.src.brain import KnowledgeBrain
from brains.knowledge.src.contract import KnowledgeRoute, KnowledgeSource
from brains.knowledge.src.runtime_adapter import KnowledgeInput, RuntimeUnavailableError
from brains.knowledge.src.temporal import (
    KnowledgeTemporalCluster,
    TemporalQueryPlan,
    infer_query_plan,
)
from brains.protocol import BrainName, BrainQuery, Confidence, Granularity, TimeScope
from governance.src import Governance, GovernanceDeniedError, Principal, SCOPE_KNOWLEDGE_PUBLISH
from topology.src.growth import TopologyGeneration


ImportStatus = Literal[
    "draft",
    "validating",
    "queued",
    "parsing",
    "segmenting",
    "indexing",
    "clustering",
    "proposing_graph",
    "ready_to_publish",
    "pending_review",
    "published",
    "failed",
    "cancelled",
]
MAX_MANAGED_SOURCE_BYTES = 10 * 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]


def _fixed(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP).normalize(), "f")


class WorkbenchError(RuntimeError):
    """API 可安全返回的工作台错误。"""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def as_error(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class ImportStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    at: str
    message: str | None = None
    error_code: str | None = None


class KnowledgeImportJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    status: ImportStatus = "draft"
    source_kind: str = Field(min_length=1)
    source_object_ref: str = Field(min_length=1)
    title: str | None = None
    media_kind: str = "text"
    permission_scope: list[str] = Field(default_factory=lambda: ["public"])
    document_id: str | None = None
    index_version: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int = Field(default=0, ge=0)
    created_at: str
    updated_at: str
    steps: list[ImportStep] = Field(default_factory=list)


class KnowledgeIndexVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index_version: str = Field(min_length=1)
    tree_id: str = Field(min_length=1)
    runtime_profile: str = Field(min_length=1)
    runtime_version: str = Field(min_length=1)
    embedding_space_id: str = Field(min_length=1)
    document_ids: list[str] = Field(default_factory=list)
    unit_ids: list[str] = Field(default_factory=list)
    status: str = "active"
    created_at: str
    replaced_by: str | None = None


class KnowledgeClusterProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cluster_id: str = Field(min_length=1)
    tree_id: str = Field(min_length=1)
    embedding_space_id: str = Field(min_length=1)
    index_version: str | None = None
    member_unit_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    independent_source_count: int = Field(ge=0)
    occurrence_count: int = Field(ge=0)
    centroid: list[str] = Field(default_factory=list)
    dispersion: str = "0"
    threshold_events: list[str] = Field(default_factory=list)
    algo_version: str = Field(min_length=1)
    rule_version: str = Field(min_length=1)
    status: str = "active"
    generation: int | None = Field(default=None, ge=1)
    corrections: list[str] = Field(default_factory=list)
    created_at: str


class CrossBrainLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: str = Field(min_length=1)
    source_brain: str = "knowledge"
    target_brain: Literal["memory", "structured"]
    source_ref: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    anchor_type: Literal["time", "entity", "event", "stable_concept", "business_dimension"]
    anchor_value: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    algorithm_version: str = Field(min_length=1)
    status: Literal["candidate", "active", "paused", "revoked"] = "candidate"
    manual_override: str | None = None
    revocation_reason: str | None = None
    created_at: str


class GraphProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    relation_type: str = "concept_cooccurrence"
    source_concept: str = Field(min_length=1)
    target_concept: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    status: str = "candidate"
    runtime_version: str = Field(min_length=1)
    created_at: str


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    expected_unit_ids: list[str] = Field(min_length=1)


class EvaluationRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    index_version: str = Field(min_length=1)
    status: Literal["completed", "failed"]
    recall_at_k: str
    mrr: str
    evaluated_cases: int = Field(ge=0)
    failure_samples: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str


class TextImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    title: str | None = None
    source_id: str | None = None
    version: str | None = None
    lang: str | None = None
    permission_scope: list[str] = Field(default_factory=lambda: ["public"])
    risk_domain: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FileImportRequest(BaseModel):
    """受管文件导入请求。正文以 base64 传输，避免引入独立 multipart 依赖。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    media_kind: str = Field(min_length=1)
    content_base64: str = Field(min_length=1)
    permission_scope: list[str] = Field(default_factory=lambda: ["public"])


class UrlImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    approved: bool = False
    title: str | None = None
    permission_scope: list[str] = Field(default_factory=lambda: ["public"])


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    profile_id: str | None = None
    day_key: str | None = None
    start: str | None = None
    end: str | None = None
    # 内容时间轴；start/end 仍兼容旧调用方，宿主可显式传入 content_*。
    content_start: str | None = None
    content_end: str | None = None
    # 存入/观察时间轴。观察时间为空时，规划器会使用存入时间作为可追溯回退。
    ingest_start: str | None = None
    ingest_end: str | None = None
    observed_start: str | None = None
    observed_end: str | None = None
    temporal_cluster_ids: list[str] = Field(default_factory=list, max_length=256)
    # These are projection identifiers from the star-cluster fast path.  The
    # workbench resolves them to member units locally; callers never need to
    # copy unit text or bypass the knowledge store's permission checks.
    candidate_cluster_ids: list[str] = Field(default_factory=list, max_length=256)
    candidate_unit_ids: list[str] = Field(default_factory=list, max_length=4096)


class ClusterCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    action: Literal["split", "merge", "pause", "rebuild", "link_override"]
    reason: str = Field(min_length=1)
    target_cluster_id: str | None = None
    link_status: Literal["candidate", "active", "paused", "revoked"] | None = None


class DocumentReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    action: Literal["approve", "reject", "rollback"]
    reason: str | None = None


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    index_version: str = Field(min_length=1)
    cases: list[EvaluationCase] = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=100)


class KnowledgeWorkbenchService:
    """知识脑的原生工作台服务，持有可重建投影而非第二套知识库。"""

    def __init__(self, brain: KnowledgeBrain | None = None, governance: Governance | None = None) -> None:
        self.brain = brain or KnowledgeBrain()
        self.governance = governance or Governance()
        self.jobs: dict[str, KnowledgeImportJob] = {}
        self._raw_by_job: dict[str, KnowledgeInput] = {}
        self.indices: dict[str, KnowledgeIndexVersion] = {}
        self.active_index_by_tree: dict[str, str] = {}
        self.clusters: dict[str, KnowledgeClusterProjection] = {}
        self.unit_cluster: dict[str, str] = {}
        self.cross_brain_links: dict[str, CrossBrainLink] = {}
        self.temporal_clusters: dict[str, KnowledgeTemporalCluster] = {}
        self.graph_proposals: dict[str, GraphProposal] = {}
        self.evaluations: dict[str, EvaluationRun] = {}
        self._counter = 0

    # ---- 运行时与鉴权 -------------------------------------------------
    def runtime_view(self) -> dict[str, Any]:
        meta = self.brain.runtime_meta
        return {
            "runtime_profile": "builtin-dev-v1" if meta.runtime == "builtin" else meta.runtime,
            "runtime": meta.model_dump(mode="json"),
            "health": meta.health_status,
            "managed_by": "KnowledgeBrain",
            "upstream_locked": meta.upstream_status == "LOCKED",
            "fallback": "forbidden",
            "query_index": dict(self.brain.query_index_stats),
            "query_index_mode": "forest-ring-cluster-bounded",
            "temporal_index": dict(self.brain.temporal_index_stats),
            "temporal_index_mode": "content-time-and-ingest-time-inverted",
            "last_retrieval": dict(self.brain.last_retrieval_stats),
        }

    def _require_write(self, principal: Principal | None) -> None:
        if principal is None:
            raise WorkbenchError("AUTHZ_DENIED", "写操作需要宿主提供已认证 Principal")
        decision = self.governance.authorize(principal, SCOPE_KNOWLEDGE_PUBLISH)
        if not decision.allowed:
            raise WorkbenchError(
                "AUTHZ_DENIED",
                decision.reason,
                details={"missing": decision.missing},
            )

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter:06d}"

    def _set_status(
        self,
        job: KnowledgeImportJob,
        status: ImportStatus,
        *,
        message: str | None = None,
        error_code: str | None = None,
    ) -> None:
        job.status = status
        job.updated_at = _now_iso()
        job.steps.append(ImportStep(stage=status, at=job.updated_at, message=message, error_code=error_code))
        if error_code is not None:
            job.error_code = error_code
            job.error_message = message

    # ---- 导入 ----------------------------------------------------------
    def create_text_import(self, request: TextImportRequest, principal: Principal | None, *, import_id: str | None = None) -> KnowledgeImportJob:
        self._require_write(principal)
        job_id = import_id or self._next_id("kij")
        if job_id in self.jobs:
            raise WorkbenchError("IMPORT_ALREADY_EXISTS", "导入标识已存在")
        content_hash = hashlib.sha256(request.content.encode("utf-8")).hexdigest()
        object_ref = f"object://sha256/{content_hash}"
        raw = KnowledgeInput(
            raw_id=job_id,
            source=KnowledgeSource(
                source_id=request.source_id or f"source-{job_id}",
                kind="text",
                uri=f"managed://imports/{job_id}",
            ),
            content=request.content,
            title=request.title or "未命名文本资料",
            lang=request.lang,
            version=request.version,
            permission_scope=list(request.permission_scope),
            risk_domain=request.risk_domain,
            metadata={
                **request.metadata,
                "source_object_ref": object_ref,
                "ingest_job_id": job_id,
            },
        )
        return self._register_draft(job_id, request.request_id, request.trace_id, raw, object_ref)

    def create_file_import(
        self,
        *,
        request_id: str,
        trace_id: str,
        filename: str,
        media_kind: str,
        content: bytes,
        permission_scope: list[str],
        principal: Principal | None,
    ) -> KnowledgeImportJob:
        self._require_write(principal)
        if len(content) > MAX_MANAGED_SOURCE_BYTES:
            raise WorkbenchError(
                "SOURCE_TOO_LARGE",
                f"受管文件超过 {MAX_MANAGED_SOURCE_BYTES} 字节上限",
            )
        job_id = self._next_id("kij")
        content_hash = hashlib.sha256(content).hexdigest()
        object_ref = f"object://sha256/{content_hash}"
        textual = media_kind in {"text", "markdown", "table", "html", "csv"}
        if textual:
            try:
                decoded = content.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise WorkbenchError("FILE_DECODE_FAILED", "文本文件不是有效 UTF-8 编码") from exc
        else:
            decoded = None
        raw = KnowledgeInput(
            raw_id=job_id,
            source=KnowledgeSource(source_id=f"source-{job_id}", kind="file", uri=f"managed://files/{job_id}/{filename}"),
            content=decoded,
            media_kind=media_kind,
            title=filename,
            permission_scope=list(permission_scope),
            metadata={"source_object_ref": object_ref, "ingest_job_id": job_id, "filename": filename},
        )
        return self._register_draft(job_id, request_id, trace_id, raw, object_ref)

    def create_url_import(self, request: UrlImportRequest, principal: Principal | None) -> KnowledgeImportJob:
        self._require_write(principal)
        job_id = self._next_id("kij")
        object_ref = f"url://sha256/{hashlib.sha256(request.url.encode('utf-8')).hexdigest()}"
        raw = KnowledgeInput(
            raw_id=job_id,
            source=KnowledgeSource(source_id=f"source-{job_id}", kind="url", uri=request.url),
            content=None,
            title=request.title or request.url,
            permission_scope=list(request.permission_scope),
            metadata={"source_object_ref": object_ref, "ingest_job_id": job_id, "url_approved": request.approved},
        )
        return self._register_draft(job_id, request.request_id, request.trace_id, raw, object_ref)

    def _register_draft(
        self,
        job_id: str,
        request_id: str,
        trace_id: str,
        raw: KnowledgeInput,
        object_ref: str,
    ) -> KnowledgeImportJob:
        now = _now_iso()
        job = KnowledgeImportJob(
            job_id=job_id,
            request_id=request_id,
            trace_id=trace_id,
            source_kind=raw.source.kind,
            source_object_ref=object_ref,
            title=raw.title,
            media_kind=raw.media_kind,
            permission_scope=list(raw.permission_scope),
            created_at=now,
            updated_at=now,
            steps=[ImportStep(stage="draft", at=now, message="已保存受管来源引用，尚未启动处理")],
        )
        self.jobs[job_id] = job
        self._raw_by_job[job_id] = raw
        return job

    def get_job(self, job_id: str) -> KnowledgeImportJob:
        job = self.jobs.get(job_id)
        if job is None:
            raise WorkbenchError("IMPORT_NOT_FOUND", f"导入任务不存在：{job_id}")
        return job

    def start_import(
        self,
        job_id: str,
        principal: Principal | None,
        *,
        prepare_index: bool = True,
    ) -> KnowledgeImportJob:
        self._require_write(principal)
        job = self.get_job(job_id)
        if job.status not in {"draft", "failed"}:
            raise WorkbenchError("IMPORT_NOT_STARTABLE", f"任务状态 {job.status} 不可启动")
        raw = self._raw_by_job[job_id]
        self._set_status(job, "validating", message="已完成来源、权限范围和运行时预检")
        if raw.source.kind == "url":
            if not raw.metadata.get("url_approved"):
                job.status = "draft"
                job.updated_at = _now_iso()
                raise WorkbenchError("URL_NOT_APPROVED", "URL 未获批准，只保留草稿，禁止抓取")
            self._fail(job, "EXTERNAL_FETCH_NOT_CONFIGURED", "受管 URL 抓取器尚未配置，未发起任何网络访问")
            return job
        self._set_status(job, "queued", message="任务已进入知识脑受管队列")
        try:
            document = self.brain.ingest(raw, stage_callback=lambda stage: self._set_status(job, stage))
        except RuntimeUnavailableError as exc:
            self._fail(job, exc.code, exc.message)
            return job
        except Exception as exc:  # noqa: BLE001 - 转为安全任务错误，不包含正文
            self._fail(job, "IMPORT_FAILED", str(exc))
            return job

        job.document_id = document.document_id
        if not self.brain.store.document_units(document.document_id):
            self._fail(job, "MODALITY_UNAVAILABLE", "当前运行时未产生可引用文本单元")
            return job

        index = self._build_index(document.document_id)
        job.index_version = index.index_version
        # Single-document imports prepare immediately.  A recovery/candidate
        # rebuild replays many persisted documents and deliberately defers this
        # O(N) projection to one final pass.
        if prepare_index:
            self.brain.prepare_query_index()
        self._refresh_temporal_projections()
        self._refresh_cluster_projections()
        self._set_status(job, "proposing_graph", message="已生成可审计 Wiki/图谱候选")
        self._build_graph_proposals(document.document_id)
        publication = self.brain.store.publications[document.document_id]
        if publication.status == "published":
            self._set_status(job, "published", message="低风险资料已自动发布到知识树")
        else:
            self._set_status(job, "pending_review", message="策略门禁命中，等待例外复核")
        return job

    def retry_import(self, job_id: str, principal: Principal | None) -> KnowledgeImportJob:
        self._require_write(principal)
        job = self.get_job(job_id)
        if job.status != "failed":
            raise WorkbenchError("IMPORT_NOT_RETRYABLE", f"任务状态 {job.status} 不可重试")
        job.retry_count += 1
        job.error_code = None
        job.error_message = None
        job.status = "draft"
        return self.start_import(job_id, principal)

    def cancel_import(self, job_id: str, principal: Principal | None) -> KnowledgeImportJob:
        self._require_write(principal)
        job = self.get_job(job_id)
        if job.status in {"published", "cancelled"}:
            raise WorkbenchError("IMPORT_NOT_CANCELLABLE", f"任务状态 {job.status} 不可取消")
        self._set_status(job, "cancelled", message="任务已取消，未发布新知识单元")
        return job

    def _fail(self, job: KnowledgeImportJob, code: str, message: str) -> None:
        self._set_status(job, "failed", message=message, error_code=code)

    # ---- 索引、星团与图谱投影 -----------------------------------------
    def _refresh_temporal_projections(self) -> None:
        self.temporal_clusters = dict(self.brain.temporal_index.clusters)
        for cluster in self.temporal_clusters.values():
            for target in ("memory", "structured"):
                link_id = "cbl-" + _digest(cluster.temporal_cluster_id, target, cluster.start, cluster.end)
                if link_id in self.cross_brain_links:
                    continue
                self.cross_brain_links[link_id] = CrossBrainLink(
                    link_id=link_id,
                    target_brain=target,
                    source_ref=f"knowledge://temporal-cluster/{cluster.temporal_cluster_id}",
                    target_ref=f"anchor://content-time/{cluster.start}/{cluster.end}",
                    anchor_type="time",
                    anchor_value=f"{cluster.start}/{cluster.end}",
                    basis="知识内容时间维度星团自动投影；目标脑仍须独立鉴权、检索与证据核验",
                    evidence_ids=[],
                    algorithm_version="knowledge-temporal-v1",
                    status="candidate",
                    created_at=_now_iso(),
                )

    def _ensure_indexes(self) -> None:
        # The active workspace is immutable for readers.  A complete
        # projection is prepared by import/review or restored from an artifact;
        # do not compare every unit and time anchor for every user question.
        self.brain.query_index_stats = self.brain.ensure_query_index()
        self._refresh_temporal_projections()

    def _build_index(self, document_id: str) -> KnowledgeIndexVersion:
        document = self.brain.store.documents[document_id]
        children = [unit for unit in self.brain.store.document_units(document_id) if unit.chunk_level == 1]
        tree_ids = sorted({self.brain.store.unit_tree.get(unit.unit_id, "knowledge.general") for unit in children})
        tree_id = tree_ids[0] if tree_ids else "knowledge.general"
        meta = self.brain.runtime_meta
        embedding_space_id = f"{meta.runtime}:{meta.runtime_version or 'deterministic-v1'}"
        index_version = "kiv-" + _digest(document_id, embedding_space_id, ",".join(unit.unit_id for unit in children))
        prior = self.active_index_by_tree.get(tree_id)
        if prior and prior in self.indices:
            self.indices[prior].status = "superseded"
            self.indices[prior].replaced_by = index_version
        index = KnowledgeIndexVersion(
            index_version=index_version,
            tree_id=tree_id,
            runtime_profile="builtin-dev-v1" if meta.runtime == "builtin" else meta.runtime,
            runtime_version=meta.runtime_version or meta.runtime,
            embedding_space_id=embedding_space_id,
            document_ids=[document_id],
            unit_ids=[unit.unit_id for unit in children],
            created_at=_now_iso(),
        )
        self.indices[index_version] = index
        self.active_index_by_tree[tree_id] = index_version
        for unit in children:
            unit.index_version = index_version
            unit.embedding_space_id = embedding_space_id
            for evidence_id in self.brain.store.evidence_by_unit.get(unit.unit_id, []):
                evidence = self.brain.store.evidence[evidence_id]
                evidence.index_version = index_version
                evidence.runtime_version = meta.runtime_version or meta.runtime
                evidence.source_object_ref = document.source_object_ref
        document.document_status = self.brain.store.publications[document_id].status
        return index

    def _refresh_cluster_projections(self) -> None:
        self._refresh_temporal_projections()
        meta = self.brain.runtime_meta
        now = _now_iso()
        self.unit_cluster.clear()
        for tree_id, index in sorted(self.brain.store.clusters.items()):
            active_index = self.active_index_by_tree.get(tree_id)
            embedding_space_id = (
                self.indices[active_index].embedding_space_id if active_index in self.indices else f"{meta.runtime}:{meta.runtime_version or 'deterministic-v1'}"
            )
            generation = None
            history = self.brain.store.generations.history(tree_id)
            if history:
                generation = self.brain.store.generations.current(tree_id).generation
            for raw_cluster in index.clusters.values():
                members: list[str] = []
                for trace_id in raw_cluster.member_trace_ids:
                    trace = self.brain.store.traces.get(trace_id)
                    if trace is None:
                        continue
                    unit_id = trace.source_ref.rsplit("/", 1)[-1]
                    if unit_id in self.brain.store.units:
                        members.append(unit_id)
                members = sorted(set(members))
                evidence_ids = sorted({eid for unit_id in members for eid in self.brain.store.evidence_by_unit.get(unit_id, [])})
                source_ids = {
                    self.brain.store.evidence[eid].source_id
                    for eid in evidence_ids
                    if eid in self.brain.store.evidence
                }
                projection_id = "kcp-" + _digest(tree_id, raw_cluster.cluster_id)
                existing = self.clusters.get(projection_id)
                status = existing.status if existing is not None else "active"
                projection = KnowledgeClusterProjection(
                    cluster_id=projection_id,
                    tree_id=tree_id,
                    embedding_space_id=embedding_space_id,
                    index_version=active_index,
                    member_unit_ids=members,
                    evidence_ids=evidence_ids,
                    independent_source_count=len(source_ids),
                    occurrence_count=raw_cluster.occurrence_count,
                    centroid=list(raw_cluster.centroid),
                    dispersion=raw_cluster.dispersion,
                    threshold_events=list(raw_cluster.growth_events),
                    algo_version=raw_cluster.algo_version,
                    rule_version=raw_cluster.rule_version,
                    status=status,
                    generation=generation,
                    corrections=list(existing.corrections) if existing is not None else [],
                    created_at=existing.created_at if existing is not None else now,
                )
                self.clusters[projection_id] = projection
                for unit_id in members:
                    self.unit_cluster[unit_id] = projection_id
                self._ensure_cross_brain_candidates(projection)

    def _ensure_cross_brain_candidates(self, cluster: KnowledgeClusterProjection) -> None:
        concept = ""
        for unit_id in cluster.member_unit_ids:
            unit = self.brain.store.units[unit_id]
            if unit.concept_path:
                concept = "/".join(unit.concept_path)
                break
        if not concept:
            return
        for target in ("memory", "structured"):
            link_id = "cbl-" + _digest(cluster.cluster_id, target, concept)
            if link_id in self.cross_brain_links:
                continue
            self.cross_brain_links[link_id] = CrossBrainLink(
                link_id=link_id,
                target_brain=target,
                source_ref=f"knowledge://cluster/{cluster.cluster_id}",
                target_ref=f"anchor://stable-concept/{concept}",
                anchor_type="stable_concept",
                anchor_value=concept,
                basis="知识星团概念锚点匹配；目标脑仍须独立鉴权、检索与返回证据",
                evidence_ids=list(cluster.evidence_ids),
                algorithm_version=f"{cluster.algo_version}:{cluster.rule_version}",
                created_at=_now_iso(),
            )

    def _build_graph_proposals(self, document_id: str) -> None:
        children = [unit for unit in self.brain.store.document_units(document_id) if unit.chunk_level == 1]
        concepts = sorted({"/".join(unit.concept_path) for unit in children if unit.concept_path})
        evidence_ids = sorted({eid for unit in children for eid in self.brain.store.evidence_by_unit.get(unit.unit_id, [])})
        for left, right in zip(concepts, concepts[1:]):
            proposal_id = "kgp-" + _digest(document_id, left, right)
            self.graph_proposals[proposal_id] = GraphProposal(
                proposal_id=proposal_id,
                document_id=document_id,
                source_concept=left,
                target_concept=right,
                evidence_ids=evidence_ids,
                runtime_version=self.brain.runtime_meta.runtime_version or self.brain.runtime_meta.runtime,
                created_at=_now_iso(),
            )

    # ---- 查询、评测与纠偏 ---------------------------------------------
    def list_documents(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for document in self.brain.store.documents.values():
            publication = self.brain.store.publications.get(document.document_id)
            output.append(
                {
                    "document": document.model_dump(mode="json"),
                    "publication": publication.model_dump(mode="json") if publication else None,
                    "unit_count": len(self.brain.store.document_units(document.document_id)),
                }
            )
        return sorted(output, key=lambda item: item["document"]["document_id"])

    def document_view(self, document_id: str) -> dict[str, Any]:
        document = self.brain.store.documents.get(document_id)
        if document is None:
            raise WorkbenchError("DOCUMENT_NOT_FOUND", f"文档不存在：{document_id}")
        units = sorted(
            self.brain.store.document_units(document_id),
            key=lambda unit: (unit.chunk_level or 0, unit.chunk_order or 0, unit.unit_id),
        )
        return {
            "document": document.model_dump(mode="json"),
            "publication": self.brain.store.publications[document_id].model_dump(mode="json"),
            "classification": self.brain.store.document_classification.get(document_id),
            "units": [unit.model_dump(mode="json") for unit in units],
            "temporal": {
                "ingested_at": self.brain.store.document_ingested_at.get(document_id),
                "observed_at": self.brain.store.document_observed_at.get(document_id),
                "anchors": [
                    anchor.model_dump(mode="json")
                    for unit in units
                    for anchor in self.brain.store.unit_content_time.get(unit.unit_id, [])
                ],
            },
            "evidence": [
                self.brain.store.evidence[eid].model_dump(mode="json")
                for eid in self.brain.store.evidence_by_document.get(document_id, [])
            ],
            "graph_proposals": [
                proposal.model_dump(mode="json")
                for proposal in self.graph_proposals.values()
                if proposal.document_id == document_id
            ],
        }

    def review_document(
        self,
        document_id: str,
        request: DocumentReviewRequest,
        principal: Principal | None,
    ) -> dict[str, Any]:
        self._require_write(principal)
        publication = self.brain.review(
            document_id,
            action=request.action,
            reviewer=principal.subject if principal is not None else None,
            reason=request.reason,
        )
        for job in self.jobs.values():
            if job.document_id == document_id:
                if publication.status == "published":
                    self._set_status(job, "published", message="复核后发布成功")
                elif publication.status == "rejected":
                    self._set_status(job, "pending_review", message="复核已拒绝，文档未发布")
                else:
                    self._set_status(job, "pending_review", message="文档已回滚到待复核")
        self._refresh_cluster_projections()
        self._refresh_temporal_projections()
        return publication.model_dump(mode="json")

    def list_clusters(self) -> list[KnowledgeClusterProjection]:
        return sorted(self.clusters.values(), key=lambda item: (item.tree_id, item.cluster_id))

    def list_temporal_clusters(self) -> list[KnowledgeTemporalCluster]:
        self._ensure_indexes()
        return sorted(self.temporal_clusters.values(), key=lambda item: (item.start, item.tree_id, item.temporal_cluster_id))

    def cluster_view(self, cluster_id: str) -> dict[str, Any]:
        cluster = self.clusters.get(cluster_id)
        if cluster is None:
            raise WorkbenchError("CLUSTER_NOT_FOUND", f"知识星团不存在：{cluster_id}")
        return {
            "cluster": cluster.model_dump(mode="json"),
            "members": [self.brain.store.units[unit_id].model_dump(mode="json") for unit_id in cluster.member_unit_ids],
            "evidence": [self.brain.store.evidence[eid].model_dump(mode="json") for eid in cluster.evidence_ids],
            "cross_brain_links": [
                link.model_dump(mode="json")
                for link in self.cross_brain_links.values()
                if link.source_ref == f"knowledge://cluster/{cluster.cluster_id}"
            ],
        }

    def correct_cluster(
        self,
        cluster_id: str,
        request: ClusterCorrectionRequest,
        principal: Principal | None,
    ) -> KnowledgeClusterProjection:
        self._require_write(principal)
        cluster = self.clusters.get(cluster_id)
        if cluster is None:
            raise WorkbenchError("CLUSTER_NOT_FOUND", f"知识星团不存在：{cluster_id}")
        correction = f"{request.action}:{request.reason}"
        cluster.corrections = [*cluster.corrections, correction]
        if request.action == "pause":
            cluster.status = "paused"
        elif request.action in {"split", "merge", "rebuild"}:
            # 保留旧代；人工纠偏请求从当前拓扑快照提交一个新的不可变 generation。
            # split/merge 的成员计划仍需调用方在后续重建任务中给出，不能猜测分组。
            history = self.brain.store.generations.history(cluster.tree_id)
            if history:
                current = self.brain.store.generations.current(cluster.tree_id)
                generation = TopologyGeneration(
                    generation=current.generation + 1,
                    tree_id=cluster.tree_id,
                    nodes=current.nodes,
                    parent=current.generation,
                    cause={
                        "reason": "knowledge_cluster_correction",
                        "action": request.action,
                        "cluster_id": cluster.cluster_id,
                        "request_id": request.request_id,
                        "trace_id": request.trace_id,
                    },
                )
                self.brain.store.generations.commit(generation)
                cluster.generation = generation.generation
            cluster.status = "rebuild_requested"
        elif request.action == "link_override":
            if request.target_cluster_id is None or request.link_status is None:
                raise WorkbenchError("INVALID_CORRECTION", "关联覆盖需要 target_cluster_id 与 link_status")
            link = self.cross_brain_links.get(request.target_cluster_id)
            if link is None:
                raise WorkbenchError("CROSS_BRAIN_LINK_NOT_FOUND", "关联边不存在")
            link.status = request.link_status
            link.manual_override = request.reason
        if request.action in {"split", "merge", "rebuild"}:
            self._refresh_cluster_projections()
            cluster = self.clusters[cluster_id]
        return cluster

    def search(self, request: SearchRequest) -> dict[str, Any]:
        self._ensure_indexes()
        started = time.monotonic()
        content_start = request.content_start or request.start
        content_end = request.content_end or request.end
        explicit_temporal = any(
            value is not None
            for value in (
                content_start, content_end, request.ingest_start, request.ingest_end,
                request.observed_start, request.observed_end,
            )
        )
        if explicit_temporal and bool(content_start) != bool(content_end):
            raise WorkbenchError("INVALID_TIME_SCOPE", "内容时间范围必须同时填写起止日期")
        if explicit_temporal and bool(request.ingest_start) != bool(request.ingest_end):
            raise WorkbenchError("INVALID_TIME_SCOPE", "存入时间范围必须同时填写起止日期")
        if explicit_temporal and bool(request.observed_start) != bool(request.observed_end):
            raise WorkbenchError("INVALID_TIME_SCOPE", "观察时间范围必须同时填写起止日期")
        if explicit_temporal:
            has_content = bool(content_start or content_end)
            has_ingest = bool(request.ingest_start or request.ingest_end)
            has_observed = bool(request.observed_start or request.observed_end)
            mode = "dual" if has_content and (has_ingest or has_observed) else "content" if has_content else "ingest" if has_ingest else "observed"
            plan = TemporalQueryPlan(
                content_start=content_start, content_end=content_end,
                ingest_start=request.ingest_start, ingest_end=request.ingest_end,
                observed_start=request.observed_start, observed_end=request.observed_end,
                mode=mode, source="explicit",
            )
        else:
            # ``day_key`` is the request's temporal reference point.  Honour
            # it for relative language such as "昨天" and "去年" so replay,
            # audit and test scenarios do not change when the wall clock
            # moves.  Interactive callers may omit it and use today's ring.
            plan = infer_query_plan(
                request.query,
                reference=date.fromisoformat(request.day_key or self.brain.ruler.day_key()),
            )

        lookup_started = time.monotonic()
        content_candidates = (
            self.brain.temporal_index.content_candidates(plan.content_start, plan.content_end)
            if plan.has_content else None
        )
        ingest_candidates = (
            self.brain.temporal_index.ingest_candidates(plan.ingest_start, plan.ingest_end)
            if plan.has_ingest else None
        )
        observed_candidates = (
            self.brain.temporal_index.observed_candidates(plan.observed_start, plan.observed_end)
            if plan.has_observed else set()
        )
        observation_candidates = None
        if plan.has_ingest or plan.has_observed:
            observation_candidates = set(ingest_candidates or set()) | set(observed_candidates or set())
        lookup_ms = round((time.monotonic() - lookup_started) * 1000)

        temporal_candidates: set[str] | None = None
        intersection_started = time.monotonic()
        temporal_sets = [
            set(candidates)
            for candidates in (content_candidates, observation_candidates)
            if candidates is not None
        ]
        # 交集先从更窄的时间入口开始，避免在大内容时间星团上重复遍历。
        # 倒排查找本身已经是内存集合操作，不额外启动线程，避免调度开销。
        temporal_sets.sort(key=len)
        if temporal_sets:
            temporal_candidates = temporal_sets[0]
            for candidates in temporal_sets[1:]:
                temporal_candidates.intersection_update(candidates)
        for cluster_id in request.temporal_cluster_ids:
            cluster = self.temporal_clusters.get(cluster_id)
            if cluster is not None:
                cluster_units = set(cluster.member_unit_ids)
                temporal_candidates = cluster_units if temporal_candidates is None else temporal_candidates & cluster_units
        intersection_ms = round((time.monotonic() - intersection_started) * 1000)
        if plan.mode == "dual" and len(temporal_sets) >= 2:
            temporal_strategy = "narrow-side-first" if len(temporal_sets[0]) != len(temporal_sets[1]) else "parallel-intersection"
        elif temporal_sets or request.temporal_cluster_ids:
            temporal_strategy = "single-index"
        else:
            temporal_strategy = "none"
        candidate_unit_ids = set(request.candidate_unit_ids)
        for cluster_id in request.candidate_cluster_ids:
            cluster = self.clusters.get(cluster_id)
            if cluster is not None:
                candidate_unit_ids.update(cluster.member_unit_ids)
        if temporal_candidates is not None:
            candidate_unit_ids = (
                temporal_candidates if not candidate_unit_ids else candidate_unit_ids & temporal_candidates
            )
        query = BrainQuery(
            request_id=request.request_id,
            trace_id=request.trace_id,
            day_key=request.day_key or request.start or self.brain.ruler.day_key(),
            raw_query=request.query,
            profile_id=request.profile_id,
            # 时间已经由内容时间/存入时间倒排投影完成候选收窄；不再把内容日期
            # 错误地传给旧的拓扑日圈过滤器。
            time_scope=None,
            constraints={"candidate_unit_ids": sorted(candidate_unit_ids)} if candidate_unit_ids else {},
        )
        if temporal_candidates is not None and not temporal_candidates:
            query.constraints = {"candidate_unit_ids": []}
        result = self.brain.query(query)
        route = self.brain.last_route or KnowledgeRoute(route_id=f"kr-{_digest(request.trace_id, request.query)}")
        for unit_id in route.selected:
            cluster_id = self.unit_cluster.get(unit_id)
            if cluster_id is None:
                continue
            cluster = self.clusters.get(cluster_id)
            if cluster is not None and cluster.status == "active":
                route.cluster_id = cluster_id
                route.cluster_generation = cluster.generation
                route.cross_brain_link_ids = [
                    link.link_id
                    for link in self.cross_brain_links.values()
                    if link.source_ref == f"knowledge://cluster/{cluster_id}" and link.status == "active"
                ]
            break
        return {
            "request_id": request.request_id,
            "trace_id": request.trace_id,
            "runtime_profile": self.runtime_view()["runtime_profile"],
            "result": result.model_dump(mode="json"),
            "route": route.model_dump(mode="json"),
            "temporal": {
                "mode": plan.mode,
                "source": plan.source,
                "content_start": plan.content_start,
                "content_end": plan.content_end,
                "ingest_start": plan.ingest_start,
                "ingest_end": plan.ingest_end,
                "observed_start": plan.observed_start,
                "observed_end": plan.observed_end,
                "content_candidate_count": len(content_candidates) if content_candidates is not None else None,
                "observation_candidate_count": len(observation_candidates) if observation_candidates is not None else None,
                "intersection_candidate_count": len(temporal_candidates) if temporal_candidates is not None else None,
                "index_lookup_ms": lookup_ms,
                "intersection_ms": intersection_ms,
                "total_planning_ms": round((time.monotonic() - started) * 1000),
                "strategy": temporal_strategy,
                "temporal_cluster_ids": self.brain.temporal_index.clusters_for_units(temporal_candidates or set()),
            },
            "retrieval": dict(self.brain.last_retrieval_stats),
        }

    def evaluate(self, request: EvaluationRequest, principal: Principal | None) -> EvaluationRun:
        self._require_write(principal)
        if request.index_version not in self.indices:
            raise WorkbenchError("INDEX_NOT_FOUND", f"索引版本不存在：{request.index_version}")
        reciprocal_total = Decimal(0)
        recall_total = Decimal(0)
        failures: list[dict[str, Any]] = []
        for case in request.cases:
            response = self.search(
                SearchRequest(
                    request_id=f"{request.request_id}:{_digest(case.query)}",
                    trace_id=request.trace_id,
                    query=case.query,
                )
            )
            selected = response["route"]["selected"][: request.k]
            expected = set(case.expected_unit_ids)
            hit_count = len(expected & set(selected))
            recall_total += Decimal(hit_count) / Decimal(len(expected))
            first_rank = next((index + 1 for index, unit_id in enumerate(selected) if unit_id in expected), None)
            if first_rank is not None:
                reciprocal_total += Decimal(1) / Decimal(first_rank)
            else:
                failures.append({"query": case.query, "expected_unit_ids": sorted(expected), "returned_unit_ids": selected})
        denominator = Decimal(len(request.cases))
        run = EvaluationRun(
            run_id=self._next_id("ker"),
            request_id=request.request_id,
            trace_id=request.trace_id,
            index_version=request.index_version,
            status="completed",
            recall_at_k=_fixed(recall_total / denominator),
            mrr=_fixed(reciprocal_total / denominator),
            evaluated_cases=len(request.cases),
            failure_samples=failures,
            created_at=_now_iso(),
        )
        self.evaluations[run.run_id] = run
        return run

    def evaluation_view(self, run_id: str) -> EvaluationRun:
        run = self.evaluations.get(run_id)
        if run is None:
            raise WorkbenchError("EVALUATION_NOT_FOUND", f"评测运行不存在：{run_id}")
        return run

    def list_cross_brain_links(self) -> list[CrossBrainLink]:
        return sorted(self.cross_brain_links.values(), key=lambda item: item.link_id)


__all__ = [
    "WorkbenchError",
    "MAX_MANAGED_SOURCE_BYTES",
    "ImportStep",
    "KnowledgeImportJob",
    "KnowledgeIndexVersion",
    "KnowledgeClusterProjection",
    "CrossBrainLink",
    "GraphProposal",
    "EvaluationCase",
    "EvaluationRun",
    "TextImportRequest",
    "FileImportRequest",
    "UrlImportRequest",
    "SearchRequest",
    "ClusterCorrectionRequest",
    "DocumentReviewRequest",
    "EvaluationRequest",
    "KnowledgeWorkbenchService",
]
