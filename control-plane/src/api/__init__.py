"""M2 控制面与 M4 原生知识工作台 HTTP 接口。"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, Header, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from brains.knowledge.src.workbench import (
    ClusterCorrectionRequest,
    DocumentReviewRequest,
    EvaluationRequest,
    FileImportRequest,
    SearchRequest,
    TextImportRequest,
    UrlImportRequest,
    WorkbenchError,
)
from brains.knowledge.src.runtime_adapter import RuntimeUnavailableError
from brains.memory import StreamFailure
from console import (
    ConsoleError,
    ConsoleLoginRequest,
    ConsoleService,
    GovernanceActionRequest,
    MemoryImportRequest,
    StructuredInputRequest,
    StructuredQueryRequest,
    StreamAnnotationRequest,
    StreamCreateRequest,
)
from governance.src import Principal
from profile import ProfileUnavailableError
from service import CognitiveRequest, CognitionService


def _principal_from_headers(
    x_principal: Annotated[str | None, Header(alias="X-Principal")] = None,
    x_roles: Annotated[str | None, Header(alias="X-Roles")] = None,
    x_scopes: Annotated[str | None, Header(alias="X-Scopes")] = None,
) -> Principal | None:
    if not x_principal:
        return None
    return Principal(
        subject=x_principal,
        roles=[item.strip() for item in (x_roles or "").split(",") if item.strip()],
        scopes=[item.strip() for item in (x_scopes or "").split(",") if item.strip()],
    )


def _workbench_error(exc: WorkbenchError) -> JSONResponse:
    status = 403 if exc.code == "AUTHZ_DENIED" else 404 if exc.code.endswith("NOT_FOUND") else 400
    return JSONResponse(status_code=status, content=exc.as_error())


def create_app(service: CognitionService | None = None) -> FastAPI:
    svc = service or CognitionService()
    console = ConsoleService()
    app = FastAPI(
        title="链式多维时空知识库引擎 · 统一认知控制面（M2）",
        version="0.1.0",
    )
    static_dir = Path(__file__).resolve().parents[2] / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="app-static")

    @app.exception_handler(ConsoleError)
    async def _console_error_handler(_request: Request, exc: ConsoleError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.as_error())

    def _console_current(
        console_session: Annotated[str | None, Cookie(alias="console_session")] = None,
    ):
        return console.sessions.current(console_session)

    @app.get("/api/v1/cognition/healthz")
    def healthz() -> dict:
        return svc.healthz()

    @app.get("/api/v1/cognition/profile")
    def profile() -> dict:
        return svc.profile_view()

    @app.post("/api/v1/cognition/query")
    def query(request: CognitiveRequest):
        try:
            return svc.query(request)
        except ProfileUnavailableError as exc:
            return JSONResponse(
                status_code=503,
                content={"error": {"code": exc.code, "message": exc.message}},
            )

    workbench = svc.knowledge_workbench
    if workbench is not None:
        @app.get("/knowledge/workbench", include_in_schema=False)
        def workbench_page():
            page = static_dir / "knowledge-workbench.html"
            if not page.exists():
                return JSONResponse(status_code=503, content={"error": {"code": "WORKBENCH_ASSETS_MISSING"}})
            return FileResponse(page)

        @app.get("/api/v1/knowledge/runtime")
        def knowledge_runtime() -> dict:
            return workbench.runtime_view()

        @app.post("/api/v1/knowledge/imports/text")
        def create_text_import(request: TextImportRequest, principal: Annotated[Principal | None, Depends(_principal_from_headers)] = None):
            try:
                job = workbench.create_text_import(request, principal)
                return {"request_id": request.request_id, "trace_id": request.trace_id, "runtime_profile": workbench.runtime_view()["runtime_profile"], "job": job.model_dump(mode="json")}
            except WorkbenchError as exc:
                return _workbench_error(exc)

        @app.post("/api/v1/knowledge/imports/files")
        def create_file_import(request: FileImportRequest, principal: Annotated[Principal | None, Depends(_principal_from_headers)] = None):
            try:
                content = base64.b64decode(request.content_base64, validate=True)
            except (binascii.Error, ValueError):
                return JSONResponse(status_code=400, content={"error": {"code": "INVALID_BASE64", "message": "content_base64 不是有效 Base64"}})
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
                return {"request_id": request.request_id, "trace_id": request.trace_id, "runtime_profile": workbench.runtime_view()["runtime_profile"], "job": job.model_dump(mode="json")}
            except WorkbenchError as exc:
                return _workbench_error(exc)

        @app.post("/api/v1/knowledge/imports/urls")
        def create_url_import(request: UrlImportRequest, principal: Annotated[Principal | None, Depends(_principal_from_headers)] = None):
            try:
                job = workbench.create_url_import(request, principal)
                return {"request_id": request.request_id, "trace_id": request.trace_id, "runtime_profile": workbench.runtime_view()["runtime_profile"], "job": job.model_dump(mode="json")}
            except WorkbenchError as exc:
                return _workbench_error(exc)

        @app.post("/api/v1/knowledge/imports/{job_id}/start")
        def start_import(job_id: str, principal: Annotated[Principal | None, Depends(_principal_from_headers)] = None):
            try:
                job = workbench.start_import(job_id, principal)
                return {"request_id": job.request_id, "trace_id": job.trace_id, "runtime_profile": workbench.runtime_view()["runtime_profile"], "job": job.model_dump(mode="json")}
            except WorkbenchError as exc:
                return _workbench_error(exc)

        @app.get("/api/v1/knowledge/imports/{job_id}")
        def import_detail(job_id: str):
            try:
                job = workbench.get_job(job_id)
                return {"request_id": job.request_id, "trace_id": job.trace_id, "runtime_profile": workbench.runtime_view()["runtime_profile"], "job": job.model_dump(mode="json")}
            except WorkbenchError as exc:
                return _workbench_error(exc)

        @app.post("/api/v1/knowledge/imports/{job_id}/retry")
        def retry_import(job_id: str, principal: Annotated[Principal | None, Depends(_principal_from_headers)] = None):
            try:
                job = workbench.retry_import(job_id, principal)
                return {"request_id": job.request_id, "trace_id": job.trace_id, "runtime_profile": workbench.runtime_view()["runtime_profile"], "job": job.model_dump(mode="json")}
            except WorkbenchError as exc:
                return _workbench_error(exc)

        @app.post("/api/v1/knowledge/imports/{job_id}/cancel")
        def cancel_import(job_id: str, principal: Annotated[Principal | None, Depends(_principal_from_headers)] = None):
            try:
                job = workbench.cancel_import(job_id, principal)
                return {"request_id": job.request_id, "trace_id": job.trace_id, "runtime_profile": workbench.runtime_view()["runtime_profile"], "job": job.model_dump(mode="json")}
            except WorkbenchError as exc:
                return _workbench_error(exc)

        @app.get("/api/v1/knowledge/documents")
        def documents() -> dict:
            return {"runtime_profile": workbench.runtime_view()["runtime_profile"], "documents": workbench.list_documents()}

        @app.get("/api/v1/knowledge/documents/{document_id}")
        def document_detail(document_id: str):
            try:
                return {"runtime_profile": workbench.runtime_view()["runtime_profile"], **workbench.document_view(document_id)}
            except WorkbenchError as exc:
                return _workbench_error(exc)

        @app.post("/api/v1/knowledge/documents/{document_id}/review")
        def review_document(document_id: str, request: DocumentReviewRequest, principal: Annotated[Principal | None, Depends(_principal_from_headers)] = None):
            try:
                return {"request_id": request.request_id, "trace_id": request.trace_id, "runtime_profile": workbench.runtime_view()["runtime_profile"], "publication": workbench.review_document(document_id, request, principal)}
            except WorkbenchError as exc:
                return _workbench_error(exc)

        @app.get("/api/v1/knowledge/clusters")
        def clusters() -> dict:
            return {"runtime_profile": workbench.runtime_view()["runtime_profile"], "clusters": [item.model_dump(mode="json") for item in workbench.list_clusters()]}

        @app.get("/api/v1/knowledge/clusters/{cluster_id}")
        def cluster_detail(cluster_id: str):
            try:
                return {"runtime_profile": workbench.runtime_view()["runtime_profile"], **workbench.cluster_view(cluster_id)}
            except WorkbenchError as exc:
                return _workbench_error(exc)

        @app.post("/api/v1/knowledge/clusters/{cluster_id}/corrections")
        def correct_cluster(cluster_id: str, request: ClusterCorrectionRequest, principal: Annotated[Principal | None, Depends(_principal_from_headers)] = None):
            try:
                cluster = workbench.correct_cluster(cluster_id, request, principal)
                return {"request_id": request.request_id, "trace_id": request.trace_id, "runtime_profile": workbench.runtime_view()["runtime_profile"], "cluster": cluster.model_dump(mode="json")}
            except WorkbenchError as exc:
                return _workbench_error(exc)

        @app.post("/api/v1/knowledge/search")
        def knowledge_search(request: SearchRequest):
            try:
                return workbench.search(request)
            except RuntimeUnavailableError as exc:
                return JSONResponse(status_code=503, content=exc.as_error())

        @app.post("/api/v1/knowledge/evaluations")
        def evaluate(request: EvaluationRequest, principal: Annotated[Principal | None, Depends(_principal_from_headers)] = None):
            try:
                run = workbench.evaluate(request, principal)
                return {"request_id": request.request_id, "trace_id": request.trace_id, "runtime_profile": workbench.runtime_view()["runtime_profile"], "evaluation": run.model_dump(mode="json")}
            except WorkbenchError as exc:
                return _workbench_error(exc)

        @app.get("/api/v1/knowledge/evaluations/{run_id}")
        def evaluation_detail(run_id: str):
            try:
                return {"runtime_profile": workbench.runtime_view()["runtime_profile"], "evaluation": workbench.evaluation_view(run_id).model_dump(mode="json")}
            except WorkbenchError as exc:
                return _workbench_error(exc)

        @app.get("/api/v1/topology/cross-brain-links")
        def cross_brain_links() -> dict:
            return {"runtime_profile": workbench.runtime_view()["runtime_profile"], "links": [item.model_dump(mode="json") for item in workbench.list_cross_brain_links()]}

    @app.get("/console/login", include_in_schema=False)
    @app.get("/console", include_in_schema=False)
    def console_page():
        page = static_dir / "console.html"
        if not page.exists():
            return JSONResponse(status_code=503, content={"error": {"code": "CONSOLE_ASSETS_MISSING", "message": "控制台静态资源缺失"}})
        return FileResponse(page)

    @app.post("/api/v1/console/auth/login")
    def console_login(request: ConsoleLoginRequest, response: Response) -> dict:
        session = console.sessions.login(request.username, request.password)
        response.set_cookie(
            key="console_session",
            value=session.token,
            httponly=True,
            samesite="lax",
            secure=False,
            path="/",
            max_age=8 * 60 * 60,
        )
        return {"session": console.session_view(session)}

    @app.post("/api/v1/console/auth/logout")
    def console_logout(
        response: Response,
        session=Depends(_console_current),
    ) -> dict:
        console.logout(session)
        response.delete_cookie("console_session", path="/")
        return {"ok": True}

    @app.get("/api/v1/console/session")
    def console_session(session=Depends(_console_current)) -> dict:
        return {"session": console.session_view(session)}

    @app.get("/api/v1/console/overview")
    def console_overview(session=Depends(_console_current)) -> dict:
        del session
        return console.overview()

    @app.post("/api/v1/console/fixtures/load")
    def console_load_fixtures(session=Depends(_console_current)) -> dict:
        return console.load_fixtures(session)

    @app.post("/api/v1/console/fixtures/reset")
    def console_reset_fixtures(session=Depends(_console_current)) -> dict:
        return console.reset_fixtures(session)

    @app.post("/api/v1/console/query")
    def console_query(request: CognitiveRequest, session=Depends(_console_current)) -> dict:
        return console.query(session, request)

    @app.get("/api/v1/console/knowledge")
    def console_knowledge(session=Depends(_console_current)) -> dict:
        del session
        return console.knowledge_view()

    @app.post("/api/v1/console/knowledge/imports/text")
    def console_knowledge_import_text(request: TextImportRequest, session=Depends(_console_current)) -> dict:
        return console.import_knowledge_text(session, request)

    @app.post("/api/v1/console/knowledge/imports/files")
    def console_knowledge_import_file(request: FileImportRequest, session=Depends(_console_current)) -> dict:
        try:
            content = base64.b64decode(request.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ConsoleError("INVALID_BASE64", "content_base64 不是有效 Base64") from exc
        if len(content) > 8 * 1024 * 1024:
            raise ConsoleError("SOURCE_TOO_LARGE", "本地控制台文件上限为 8 MiB", status_code=413)
        return console.import_knowledge_file(session, request, content)

    @app.post("/api/v1/console/knowledge/search")
    def console_knowledge_search(request: SearchRequest, session=Depends(_console_current)) -> dict:
        return console.search_knowledge(session, request)

    @app.post("/api/v1/console/knowledge/documents/{document_id}/review")
    def console_knowledge_review(
        document_id: str,
        request: DocumentReviewRequest,
        session=Depends(_console_current),
    ) -> dict:
        return console.review_knowledge_document(session, document_id, request)

    @app.get("/api/v1/console/memory")
    def console_memory(session=Depends(_console_current)) -> dict:
        del session
        return console.memory_view()

    @app.post("/api/v1/console/memory/imports")
    def console_memory_import(request: MemoryImportRequest, session=Depends(_console_current)) -> dict:
        raw_content: bytes | None = None
        if request.content_base64:
            try:
                raw_content = base64.b64decode(request.content_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ConsoleError("INVALID_BASE64", "content_base64 不是有效 Base64") from exc
            if len(raw_content) > 8 * 1024 * 1024:
                raise ConsoleError("MEDIA_TOO_LARGE", "本地演示离线媒体上限为 8 MiB", status_code=413)
        return console.import_memory(session, request, raw_content)

    @app.get("/api/v1/console/memory/stream-sources")
    def console_stream_sources(session=Depends(_console_current)) -> dict:
        console.require_admin(session)
        return {"sources": console.streams.camera_sources(), "opens_device": False}

    @app.post("/api/v1/console/memory/streams")
    def console_create_stream(request: StreamCreateRequest, session=Depends(_console_current)) -> dict:
        return console.create_stream(session, request)

    @app.get("/api/v1/console/memory/streams/{session_id}")
    def console_stream_view(session_id: str, session=Depends(_console_current)) -> dict:
        del session
        return console.stream_view(session_id)

    @app.post("/api/v1/console/memory/streams/{session_id}/start")
    def console_start_stream(session_id: str, session=Depends(_console_current)) -> dict:
        return console.start_stream(session, session_id)

    @app.post("/api/v1/console/memory/streams/{session_id}/stop")
    def console_stop_stream(session_id: str, session=Depends(_console_current)) -> dict:
        return console.stop_stream(session, session_id)

    @app.get("/api/v1/console/memory/streams/{session_id}/preview")
    def console_stream_preview(session_id: str, session=Depends(_console_current)) -> Response:
        del session
        try:
            return Response(content=console.streams.preview(session_id), media_type="image/jpeg")
        except StreamFailure as exc:
            raise ConsoleError(exc.code, exc.message, status_code=404 if exc.code == "STREAM_NOT_FOUND" else 409) from exc

    @app.post("/api/v1/console/memory/streams/{session_id}/annotations")
    def console_stream_annotation(
        session_id: str,
        request: StreamAnnotationRequest,
        session=Depends(_console_current),
    ) -> dict:
        return console.annotate_stream(session, session_id, request.text)

    @app.get("/api/v1/console/structured")
    def console_structured(session=Depends(_console_current)) -> dict:
        del session
        return console.structured_view()

    @app.post("/api/v1/console/structured/imports")
    def console_structured_import(request: StructuredInputRequest, session=Depends(_console_current)) -> dict:
        return console.import_structured(session, request)

    @app.post("/api/v1/console/structured/query")
    def console_structured_query(request: StructuredQueryRequest, session=Depends(_console_current)) -> dict:
        return console.query_structured(session, request)

    @app.get("/api/v1/console/topology")
    def console_topology(session=Depends(_console_current)) -> dict:
        del session
        return console.topology_view()

    @app.get("/api/v1/console/governance")
    def console_governance(session=Depends(_console_current)) -> dict:
        del session
        return console.governance_view()

    @app.post("/api/v1/console/governance/{object_id}")
    def console_governance_action(
        object_id: str,
        request: GovernanceActionRequest,
        session=Depends(_console_current),
    ) -> dict:
        return console.governance_action(session, object_id, request)

    return app


app = create_app()
