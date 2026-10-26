"""本地三脑交互演示后台的服务层。

该模块只为 ``/console`` 提供短生命周期会话和明确标注的 demo runtime。默认
``CognitionService()`` 的生产保守装配不被改变；所有演示数据与真实流片段也仍走
原有的三脑契约、治理和审计边界。
"""

from __future__ import annotations

import hmac
import secrets
import uuid
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from brains.knowledge import KnowledgeBrain
from brains.knowledge.src.workbench import (
    DocumentReviewRequest,
    FileImportRequest,
    SearchRequest as KnowledgeSearchRequest,
    TextImportRequest,
    WorkbenchError,
)
from brains.memory import IngestItem, MemoryBrain, StreamFailure, StreamSessionService
from brains.protocol import BrainName, BrainQuery
from brains.structured import DATASETS, StructuredBrain
from governance.src import Principal, SCOPE_COGNITION_QUERY, SCOPE_STRUCTURED_QUERY
from service import CognitiveRequest, CognitionService


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


class ConsoleError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def as_error(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


class LocalSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    role: Literal["admin", "reviewer", "observer"]
    created_at: str = Field(default_factory=_now_iso)
    expires_at: str = Field(min_length=1)


class ConsoleLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class StreamCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["camera", "rtsp"]
    device_index: int | None = Field(default=None, ge=0)
    rtsp_url: str | None = None
    permission_scope: str = Field(default="owner", min_length=1)
    sample_interval_seconds: float = Field(default=2.0, ge=0.25, le=60.0)


class StreamAnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)


class MemoryImportRequest(BaseModel):
    """离线媒体/事件统一接入请求；正文或受管字节至少存在其一。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    filename: str = Field(min_length=1, max_length=240)
    media_kind: Literal["text", "image", "video", "audio"] = "text"
    text: str | None = Field(default=None, max_length=10_000)
    content_base64: str | None = None
    timestamp: str | None = None
    place: str | None = Field(default=None, max_length=240)
    person: str | None = Field(default=None, max_length=240)
    permission_scope: str = Field(default="owner", min_length=1)


class StructuredInputRequest(BaseModel):
    """手工写入本地结构化验收数据，不接入外部业务库。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    aggregations: dict[str, str] = Field(min_length=1)
    record_count: int = Field(default=1, ge=1, le=1_000_000)


class StructuredQueryRequest(BaseModel):
    """显式指定数据集的 M5 验收查询。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    raw_query: str = Field(min_length=1, max_length=2_000)


class GovernanceActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject", "rollback"]
    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    reason: str | None = None


class LocalSessionStore:
    """本地演示身份。口令只适用于 loopback 开发环境，不是生产认证。"""

    _credentials = {
        "admin": ("brain-demo-2026", "admin"),
        "reviewer": ("review-demo-2026", "reviewer"),
        "observer": ("observe-demo-2026", "observer"),
    }

    def __init__(self, ttl_hours: int = 8) -> None:
        self._ttl = timedelta(hours=ttl_hours)
        self._sessions: dict[str, LocalSession] = {}

    def login(self, username: str, password: str) -> LocalSession:
        record = self._credentials.get(username.strip().lower())
        if record is None or not hmac.compare_digest(password, record[0]):
            raise ConsoleError("LOGIN_FAILED", "账号或本地演示口令不正确", status_code=401)
        token = secrets.token_urlsafe(32)
        session = LocalSession(
            token=token,
            subject=username.strip().lower(),
            role=record[1],
            expires_at=(_now() + self._ttl).isoformat(),
        )
        self._sessions[token] = session
        return session

    def current(self, token: str | None) -> LocalSession:
        if not token:
            raise ConsoleError("AUTH_REQUIRED", "请先登录本地演示后台", status_code=401)
        session = self._sessions.get(token)
        if session is None:
            raise ConsoleError("SESSION_INVALID", "本地会话无效，请重新登录", status_code=401)
        if datetime.fromisoformat(session.expires_at) <= _now():
            self._sessions.pop(token, None)
            raise ConsoleError("SESSION_EXPIRED", "本地会话已过期，请重新登录", status_code=401)
        return session

    def logout(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)


class LocalStructuredEngine:
    """本地可写的 M5 验收引擎，实现既有 ``EngineClient`` 查询协议。

    该引擎绝不在启动时伪造业务数值。用户写入的数据和显式加载的夹具都保留来源
    标签，供 ``StructuredBrain`` 的映射、定点数约束与证据输出使用。
    """

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _fixed_decimal(value: str) -> str:
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("聚合值必须是十进制定点数字符串") from exc
        if not parsed.is_finite():
            raise ValueError("聚合值必须是有限十进制数")
        return format(parsed, "f")

    def register(
        self,
        *,
        dataset_id: str,
        aggregations: dict[str, str],
        record_count: int,
        source_kind: str,
    ) -> dict[str, Any]:
        dataset = DATASETS.get(dataset_id)
        if dataset is None:
            raise ValueError(f"未知结构化数据集：{dataset_id}")
        allowed_metrics = set(dataset.get("metrics", {}))
        if not allowed_metrics:
            raise ValueError(f"数据集 {dataset_id} 没有可写入的聚合指标")
        unknown = sorted(set(aggregations) - allowed_metrics)
        if unknown:
            raise ValueError(f"数据集 {dataset_id} 不包含指标：{', '.join(unknown)}")
        fixed = {metric: self._fixed_decimal(value) for metric, value in aggregations.items()}
        record = {
            "dataset_id": dataset_id,
            "aggregations": fixed,
            "record_count": record_count,
            "source_kind": source_kind,
            "updated_at": _now_iso(),
        }
        self._records[dataset_id] = record
        return dict(record)

    def load_fixture(self) -> None:
        self.register(
            dataset_id="oil_monthly_sales",
            aggregations={"monthlysales": "12840.50", "monthlyqty": "15422.00", "monthlyamount": "987654.32"},
            record_count=3,
            source_kind="demo_fixture",
        )
        self.register(
            dataset_id="oil_sales_daily",
            aggregations={"salesvol": "412.25", "qty": "496300.00", "amount": "3851200.80"},
            record_count=3,
            source_kind="demo_fixture",
        )
        self.register(
            dataset_id="non_oil_sales",
            aggregations={"sale_amount": "84520.00", "real_amount": "81234.50"},
            record_count=3,
            source_kind="demo_fixture",
        )

    def registrations(self) -> list[dict[str, Any]]:
        return [dict(record) for _, record in sorted(self._records.items())]

    def query(self, payload: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
        dataset = str(payload.get("dataset") or "oil_monthly_sales")
        record = self._records.get(dataset)
        if record is None:
            return {
                "trace_id": trace_id,
                "warnings": ["该数据集尚无手工写入数据；请先在结构化推理脑页面写入本地测试数据"],
                "actual": {"dataset": dataset, "available": False, "record_count": 0, "aggregations": {}},
            }
        return {
            "trace_id": trace_id,
            "warnings": [f"数据来源：{record['source_kind']}；仅用于本地交互验收"],
            "actual": {
                "dataset": dataset,
                "available": True,
                "record_count": record["record_count"],
                "aggregations": record["aggregations"],
            },
        }


class ConsoleService:
    def __init__(self) -> None:
        self.sessions = LocalSessionStore()
        self.fixtures_loaded = False
        self._build_runtime()

    def _build_runtime(self) -> None:
        self.memory = MemoryBrain(growth_threshold=2)
        self.knowledge = KnowledgeBrain()
        self.structured_engine = LocalStructuredEngine()
        self.structured = StructuredBrain(self.structured_engine)
        self.control = CognitionService(
            brains={
                BrainName.memory: self.memory,
                BrainName.knowledge: self.knowledge,
                BrainName.structured: self.structured,
            }
        )
        self.streams = StreamSessionService(self.memory)

    @staticmethod
    def principal(session: LocalSession) -> Principal:
        return Principal(subject=session.subject, roles=[session.role])

    def session_view(self, session: LocalSession) -> dict[str, Any]:
        principal = self.principal(session)
        granted = sorted(self.control.governance.policy.effective_scopes(principal))
        return {
            "subject": session.subject,
            "role": session.role,
            "expires_at": session.expires_at,
            "scopes": granted,
            "mode": "local_demo",
        }

    def require_admin(self, session: LocalSession) -> Principal:
        if session.role != "admin":
            raise ConsoleError("AUTHZ_DENIED", "该操作仅本地演示管理员可执行", status_code=403)
        return self.principal(session)

    def load_fixtures(self, session: LocalSession) -> dict[str, Any]:
        principal = self.require_admin(session)
        if self.fixtures_loaded:
            return self.overview()

        for day in ("2026-10-08", "2026-10-09", "2026-10-10"):
            self.memory.ingest(
                IngestItem(
                    media_ref=f"memory://demo/maintenance/{day}",
                    modality="text",
                    content="设备温度巡检与润滑保养，现场记录运行状态。",
                    timestamp=f"{day}T08:30:00+08:00",
                    place="演示设备间",
                    person="巡检员",
                    source_ref="demo_fixture:memory-maintenance",
                    source_kind="demo_fixture",
                    trust="user",
                    permission_scope="owner",
                    metadata={"captured_at": f"{day}T08:30:00+08:00", "duration": "0"},
                )
            )

        workbench = self.control.knowledge_workbench
        if workbench is None:  # pragma: no cover - constructor above guarantees it
            raise ConsoleError("KNOWLEDGE_UNAVAILABLE", "知识脑工作台未初始化", status_code=503)
        requests = [
            TextImportRequest(
                request_id="fixture-knowledge-1",
                trace_id="fixture-knowledge-1",
                title="设备温度维护规程（演示）",
                content="# 设备维护\n设备温度监控应记录异常阈值，并按维护周期执行润滑保养。",
                source_id="demo_fixture:maintenance-rule",
            ),
            TextImportRequest(
                request_id="fixture-knowledge-2",
                trace_id="fixture-knowledge-2",
                title="安全例外复核（演示）",
                content="# 安全例外\n安全风险预警与质量放行必须经过人工复核。",
                source_id="demo_fixture:safety-review",
                risk_domain="safety",
            ),
        ]
        for request in requests:
            job = workbench.create_text_import(request, principal)
            workbench.start_import(job.job_id, principal)
        self.structured_engine.load_fixture()

        self.fixtures_loaded = True
        return self.overview()

    def reset_fixtures(self, session: LocalSession) -> dict[str, Any]:
        self.require_admin(session)
        if self.streams.sessions():
            raise ConsoleError(
                "LIVE_STREAM_PRESENT",
                "存在实时流会话，为防止删除真实片段，不能清空当前本地运行态；请先停止流会话。",
                status_code=409,
            )
        # 控制台是纯内存验收环境；重建会清空夹具及当前手工输入，页面会要求二次确认。
        self._build_runtime()
        self.fixtures_loaded = False
        return self.overview()

    def logout(self, session: LocalSession) -> None:
        """登出即停止本人流会话，从内存删除 RTSP 凭据。"""
        self.streams.stop_for_owner(session.subject)
        self.sessions.logout(session.token)

    def overview(self) -> dict[str, Any]:
        workbench = self.control.knowledge_workbench
        documents = workbench.list_documents() if workbench else []
        clusters = workbench.list_clusters() if workbench else []
        generations = {
            tree_id: len(self.memory.store.generations.history(tree_id))
            for tree_id in self.memory.store.generations.tree_ids()
        }
        return {
            "mode": "local_demo",
            "fixtures_loaded": self.fixtures_loaded,
            "brains": {
                "memory": {"status": "demo_ready", "units": len(self.memory.store.units), "generations": generations},
                "knowledge": {"status": "demo_ready", "documents": len(documents), "clusters": len(clusters)},
                "structured": {
                    "status": "demo_ready",
                    "datasets": len(DATASETS),
                    "registrations": len(self.structured_engine.registrations()),
                },
            },
            "profile": self.control.profile_view()["active"],
            "runtime": workbench.runtime_view() if workbench else None,
            "pending_review": [
                item["document"].get("document_id")
                for item in documents
                if (item.get("publication") or {}).get("status") == "pending_review"
            ],
            "recent_audit": [item.model_dump(mode="json") for item in self.control.audit.all()[-8:]],
        }

    def query(self, session: LocalSession, request: CognitiveRequest) -> dict[str, Any]:
        principal = self.principal(session)
        decision = self.control.governance.authorize(
            principal, SCOPE_COGNITION_QUERY, request_id=request.request_id, trace_id=request.trace_id
        )
        if not decision.allowed:
            raise ConsoleError("AUTHZ_DENIED", decision.reason, status_code=403)
        return self.control.query(request, principal).model_dump(mode="json")

    def import_memory(
        self, session: LocalSession, request: MemoryImportRequest, raw_content: bytes | None
    ) -> dict[str, Any]:
        """让离线文件和手工事件进入与实时流相同的 M3 接入契约。"""
        self.require_admin(session)
        text = (request.text or "").strip()
        if request.media_kind == "text" and not text:
            raise ConsoleError("MEMORY_CONTENT_REQUIRED", "文本事件需要填写内容")
        if request.media_kind != "text" and raw_content is None:
            raise ConsoleError("MEMORY_CONTENT_REQUIRED", "离线媒体需要上传文件")

        modality = {
            "text": "text",
            "image": "keyframe",
            "video": "keyframe",
            "audio": "waveform",
        }[request.media_kind]
        item_id = uuid.uuid4().hex[:16]
        captured_at = request.timestamp or _now_iso()
        unit = self.memory.ingest(
            IngestItem(
                media_ref=f"memory://manual/{item_id}/{request.filename}",
                modality=modality,
                content=text or None,
                raw_content=raw_content,
                timestamp=captured_at,
                place=request.place,
                person=request.person,
                permission_scope=request.permission_scope,
                source_ref=f"manual_import:{request.filename}",
                source_kind="manual_import",
                trust="user",
                metadata={
                    "captured_at": captured_at,
                    "duration": "0",
                    "original_filename": request.filename,
                    "media_kind": request.media_kind,
                    "source_kind": "manual_import",
                    "request_id": request.request_id,
                    "trace_id": request.trace_id,
                },
            )
        )
        return {
            "request_id": request.request_id,
            "trace_id": request.trace_id,
            "source_kind": "manual_import",
            "unit": unit.model_dump(mode="json"),
        }

    def knowledge_view(self) -> dict[str, Any]:
        workbench = self.control.knowledge_workbench
        if workbench is None:
            raise ConsoleError("KNOWLEDGE_UNAVAILABLE", "知识脑工作台未初始化", status_code=503)
        return {
            "runtime": workbench.runtime_view(),
            "documents": workbench.list_documents(),
            "clusters": [item.model_dump(mode="json") for item in workbench.list_clusters()],
            "cross_brain_links": [item.model_dump(mode="json") for item in workbench.list_cross_brain_links()],
        }

    def import_knowledge_text(self, session: LocalSession, request: TextImportRequest) -> dict[str, Any]:
        principal = self.require_admin(session)
        workbench = self.control.knowledge_workbench
        if workbench is None:
            raise ConsoleError("KNOWLEDGE_UNAVAILABLE", "知识脑工作台未初始化", status_code=503)
        try:
            job = workbench.create_text_import(request, principal)
            started = workbench.start_import(job.job_id, principal)
        except WorkbenchError as exc:
            raise ConsoleError(exc.code, exc.message) from exc
        return {
            "request_id": request.request_id,
            "trace_id": request.trace_id,
            "job": started.model_dump(mode="json"),
        }

    def import_knowledge_file(
        self, session: LocalSession, request: FileImportRequest, content: bytes
    ) -> dict[str, Any]:
        principal = self.require_admin(session)
        workbench = self.control.knowledge_workbench
        if workbench is None:
            raise ConsoleError("KNOWLEDGE_UNAVAILABLE", "知识脑工作台未初始化", status_code=503)
        try:
            job = workbench.create_file_import(
                request_id=request.request_id,
                trace_id=request.trace_id,
                filename=request.filename,
                media_kind=request.media_kind,
                content=content,
                permission_scope=request.permission_scope,
                principal=principal,
            )
            started = workbench.start_import(job.job_id, principal)
        except WorkbenchError as exc:
            raise ConsoleError(exc.code, exc.message) from exc
        return {
            "request_id": request.request_id,
            "trace_id": request.trace_id,
            "job": started.model_dump(mode="json"),
        }

    def search_knowledge(self, session: LocalSession, request: KnowledgeSearchRequest) -> dict[str, Any]:
        principal = self.principal(session)
        decision = self.control.governance.authorize(
            principal, SCOPE_COGNITION_QUERY, request_id=request.request_id, trace_id=request.trace_id
        )
        if not decision.allowed:
            raise ConsoleError("AUTHZ_DENIED", decision.reason, status_code=403)
        workbench = self.control.knowledge_workbench
        if workbench is None:
            raise ConsoleError("KNOWLEDGE_UNAVAILABLE", "知识脑工作台未初始化", status_code=503)
        return workbench.search(request)

    def review_knowledge_document(
        self, session: LocalSession, document_id: str, request: DocumentReviewRequest
    ) -> dict[str, Any]:
        principal = self.principal(session)
        workbench = self.control.knowledge_workbench
        if workbench is None:
            raise ConsoleError("KNOWLEDGE_UNAVAILABLE", "知识脑工作台未初始化", status_code=503)
        try:
            return workbench.review_document(document_id, request, principal)
        except WorkbenchError as exc:
            raise ConsoleError(exc.code, exc.message, status_code=403 if exc.code == "AUTHZ_DENIED" else 400) from exc

    def memory_view(self) -> dict[str, Any]:
        clusters = []
        for tree_id, index in self.memory.store.clusters.items():
            for cluster in index.clusters.values():
                clusters.append(cluster.model_dump(mode="json"))
        generations = {
            tree_id: [item.snapshot() for item in self.memory.store.generations.history(tree_id)]
            for tree_id in self.memory.store.generations.tree_ids()
        }
        return {
            "source_kinds": sorted(
                {
                    item.source_ref.split(":", 1)[0] if item.source_ref else "unknown"
                    for item in self.memory.store.units.values()
                }
            ),
            "units": [item.model_dump(mode="json") for item in self.memory.store.units.values()],
            "clusters": clusters,
            "generations": generations,
            "streams": [item.model_dump(mode="json") for item in self.streams.sessions()],
        }

    def structured_view(self) -> dict[str, Any]:
        return {
            "datasets": [{"dataset_id": key, **value} for key, value in DATASETS.items()],
            "registrations": self.structured_engine.registrations(),
        }

    def import_structured(self, session: LocalSession, request: StructuredInputRequest) -> dict[str, Any]:
        self.require_admin(session)
        try:
            registration = self.structured_engine.register(
                dataset_id=request.dataset_id,
                aggregations=request.aggregations,
                record_count=request.record_count,
                source_kind="manual_structured_input",
            )
        except ValueError as exc:
            raise ConsoleError("STRUCTURED_INPUT_INVALID", str(exc)) from exc
        return {"request_id": request.request_id, "trace_id": request.trace_id, "registration": registration}

    def query_structured(self, session: LocalSession, request: StructuredQueryRequest) -> dict[str, Any]:
        principal = self.principal(session)
        decision = self.control.governance.authorize(
            principal, SCOPE_STRUCTURED_QUERY, request_id=request.request_id, trace_id=request.trace_id
        )
        if not decision.allowed:
            raise ConsoleError("AUTHZ_DENIED", decision.reason, status_code=403)
        result = self.structured.query(
            BrainQuery(
                request_id=request.request_id,
                trace_id=request.trace_id,
                day_key=_now().date().isoformat(),
                raw_query=request.raw_query,
                constraints={"dataset": request.dataset_id},
            )
        )
        return {
            "request_id": request.request_id,
            "trace_id": request.trace_id,
            "result": result.model_dump(mode="json"),
        }

    def topology_view(self) -> dict[str, Any]:
        workbench = self.control.knowledge_workbench
        return {
            "memory_generations": {
                tree_id: [item.snapshot() for item in self.memory.store.generations.history(tree_id)]
                for tree_id in self.memory.store.generations.tree_ids()
            },
            "knowledge_clusters": [item.model_dump(mode="json") for item in (workbench.list_clusters() if workbench else [])],
            "cross_brain_links": [item.model_dump(mode="json") for item in (workbench.list_cross_brain_links() if workbench else [])],
        }

    def governance_view(self) -> dict[str, Any]:
        return {
            "gate_decisions": [item.model_dump(mode="json") for item in self.control.governance.gate.all()],
            "governance_events": [item.model_dump(mode="json") for item in self.control.governance.audit.all()],
            "control_audit": [item.model_dump(mode="json") for item in self.control.audit.all()],
        }

    def governance_action(
        self, session: LocalSession, object_id: str, request: GovernanceActionRequest
    ) -> dict[str, Any]:
        principal = self.principal(session)
        decision = self.control.governance.authorize(
            principal, "knowledge:publish", request_id=request.request_id, trace_id=request.trace_id
        )
        if not decision.allowed:
            raise ConsoleError("AUTHZ_DENIED", decision.reason, status_code=403)
        try:
            if request.action == "approve":
                result = self.control.governance.approve(
                    object_id, approver=session.subject, request_id=request.request_id, trace_id=request.trace_id
                )
            elif request.action == "reject":
                result = self.control.governance.reject(
                    object_id,
                    approver=session.subject,
                    reason=request.reason or "控制台拒绝",
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                )
            else:
                result = self.control.governance.rollback(
                    object_id, actor=session.subject, request_id=request.request_id, trace_id=request.trace_id
                )
        except (KeyError, ValueError) as exc:
            raise ConsoleError("GATE_ACTION_REJECTED", str(exc), status_code=409) from exc
        return result.model_dump(mode="json")

    def create_stream(self, session: LocalSession, request: StreamCreateRequest) -> dict[str, Any]:
        self.require_admin(session)
        try:
            if request.kind == "camera":
                if request.device_index is None:
                    raise ConsoleError("INVALID_STREAM_SOURCE", "本机摄像头需要 device_index")
                item = self.streams.create_camera(
                    owner=session.subject,
                    device_index=request.device_index,
                    permission_scope=request.permission_scope,
                    sample_interval_seconds=request.sample_interval_seconds,
                )
            else:
                if not request.rtsp_url:
                    raise ConsoleError("INVALID_STREAM_SOURCE", "RTSP 会话需要 rtsp_url")
                item = self.streams.create_rtsp(
                    owner=session.subject,
                    url=request.rtsp_url,
                    permission_scope=request.permission_scope,
                    sample_interval_seconds=request.sample_interval_seconds,
                )
        except StreamFailure as exc:
            raise ConsoleError(exc.code, exc.message) from exc
        return item.model_dump(mode="json")

    def stream_view(self, session_id: str) -> dict[str, Any]:
        try:
            item = self.streams.get(session_id)
            return {
                "session": item.model_dump(mode="json"),
                "segments": [segment.model_dump(mode="json") for segment in self.streams.segments(session_id)],
            }
        except StreamFailure as exc:
            raise ConsoleError(exc.code, exc.message, status_code=404 if exc.code == "STREAM_NOT_FOUND" else 409) from exc

    def start_stream(self, session: LocalSession, session_id: str) -> dict[str, Any]:
        self.require_admin(session)
        try:
            return self.streams.start(session_id).model_dump(mode="json")
        except StreamFailure as exc:
            raise ConsoleError(exc.code, exc.message, status_code=404 if exc.code == "STREAM_NOT_FOUND" else 409) from exc

    def stop_stream(self, session: LocalSession, session_id: str) -> dict[str, Any]:
        self.require_admin(session)
        try:
            return self.streams.stop(session_id).model_dump(mode="json")
        except StreamFailure as exc:
            raise ConsoleError(exc.code, exc.message, status_code=404 if exc.code == "STREAM_NOT_FOUND" else 409) from exc

    def annotate_stream(self, session: LocalSession, session_id: str, text: str) -> dict[str, Any]:
        self.require_admin(session)
        try:
            return self.streams.annotate(session_id, text).model_dump(mode="json")
        except StreamFailure as exc:
            raise ConsoleError(exc.code, exc.message, status_code=404 if exc.code == "STREAM_NOT_FOUND" else 409) from exc


__all__ = [
    "ConsoleError",
    "ConsoleLoginRequest",
    "ConsoleService",
    "GovernanceActionRequest",
    "LocalSession",
    "MemoryImportRequest",
    "StructuredInputRequest",
    "StructuredQueryRequest",
    "StreamAnnotationRequest",
    "StreamCreateRequest",
]
