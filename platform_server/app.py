"""Production-oriented platform host. Never mounts the demonstration console."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from brains.memory.src.streaming import StreamFailure, mask_rtsp_url
from brains.knowledge.src.runtime_adapter import RuntimeUnavailableError
from platform_server.contracts import (
    ImportInput, Input, MessageInput, ModelInput, QueryInput, ReviewInput, EvaluationInput, ClassificationInput,
    MessageFeedbackInput, SessionArchiveInput, SessionInput, SessionPinInput,
    SessionTitleInput, SettingsInput, SourceInput, UnifiedMessageInput,
)
from platform_server.ingestion import Ingestion, KnowledgeWorkspaceWarmingError
from platform_server.knowledge_publication import asset_view, publication_fields
from platform_server.store import Store, uid
from platform_server.streams import Streams
from platform_server.model_provider import ModelDiscoveryInput, discover_models, inference_error

ROOT = Path(__file__).resolve().parents[1]
COOKIE = "three_brain_session"


class LoginInput(Input):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1000, repr=False)


def require_role(user, *roles):
    if user.get("role") not in roles:
        raise HTTPException(403, "当前角色无权执行此操作")


def public_source(source):
    return {k: v for k, v in source.items() if k != "secret_ref"}


def public_model(model, inference_stats=None):
    value = {**{k: v for k, v in model.items() if k != "secret_ref"}, "key_configured": bool(model.get("secret_ref"))}
    value.setdefault("deployment", "")
    value.setdefault("deployment_name", "")
    value.setdefault("is_default", False)
    if value.get("purpose") == "chat":
        # The UI reflects the actual server-side wall-clock contract even for
        # historical records created before the 90-second policy.
        value["timeout"] = 90
        value["timeout_policy"] = "hard_wall_clock"
    if inference_stats is not None:
        value["inference_stats"] = inference_stats
    return value


def create_app(data_root: Path | None = None, *, engine_url: str | None = None, agent_url: str | None = None, transport=None, knowledge_runtime: str = "weknora-embedded-v1"):
    from platform_server.installation import configure
    installation = configure()
    store = Store(data_root or Path(os.environ.get("PLATFORM_DATA_DIR", str(installation / "platform"))))
    engine = (engine_url or os.environ.get("STRUCTURED_ENGINE_URL", "http://127.0.0.1:18000")).rstrip("/")
    agent = (agent_url or os.environ.get("STRUCTURED_AGENT_URL", "http://127.0.0.1:18001")).rstrip("/")
    ingest = Ingestion(store, runtime_profile=knowledge_runtime)
    streams = Streams(ingest)
    login_attempts = {}

    @asynccontextmanager
    async def lifespan(_app):
        agent_runs.recover()
        ingest.recover()
        yield
        await run_in_threadpool(streams.close)
        await run_in_threadpool(ingest.close)

    app = FastAPI(title="链式多维时空知识库引擎", version="0.2.0", lifespan=lifespan)
    app.state.store, app.state.ingest, app.state.streams = store, ingest, streams
    from platform_server.local_models import PREFIX as LOCAL_MODEL_PREFIX, router as local_model_router
    app.include_router(local_model_router(store.root.parent / 'models'))

    def settings():
        return store.get("settings", "system", "settings") or SettingsInput().model_dump()

    def client(timeout=30):
        return httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False)

    from platform_server.agent_runs import AgentRuns
    from platform_server.unified_agent import UnifiedAgentRuns
    agent_runs = AgentRuns(store, ingest, client)
    unified_agent = UnifiedAgentRuns(store, ingest, client, agent_url=agent)
    app.state.agent_runs, app.state.unified_agent = agent_runs, unified_agent

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc):
        # Pydantic errors otherwise echo password, API key and RTSP input.
        return JSONResponse(status_code=422, content={"message": "参数不正确", "errors": [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]})

    @app.exception_handler(ValueError)
    async def invalid(_request, exc):
        return JSONResponse(status_code=400, content={"message": str(exc)})

    @app.exception_handler(RuntimeUnavailableError)
    async def runtime_error(_request, exc):
        return JSONResponse(status_code=503, content={"message": exc.message, "code": exc.code, "operation": exc.operation})

    @app.exception_handler(StreamFailure)
    async def stream_error(_request, exc):
        return JSONResponse(status_code=400, content={"message": exc.message, "code": exc.code})

    @app.middleware("http")
    async def origin_guard(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse(status_code=403, content={"message": "跨站写入请求被拒绝"})
            # The internal model bridge uses its own Bearer service credential.
            # Only this router is exempt; its dependency still enforces authentication.
            if request.headers.get("x-platform-request") != "1" and not request.url.path.startswith(LOCAL_MODEL_PREFIX + '/'):
                return JSONResponse(status_code=403, content={"message": "缺少平台请求校验头"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    async def upstream_identity(token):
        try:
            async with client(10) as conn:
                result = await conn.get(engine + "/api/v1/auth/me", headers={"Authorization": "Bearer " + token})
        except httpx.HTTPError:
            raise HTTPException(503, "结构化引擎身份服务不可达，请检查引擎连接")
        if result.status_code != 200:
            raise HTTPException(401 if result.status_code in {401, 403} else 503, "登录已失效或身份服务不可用")
        user = result.json()
        if not isinstance(user, dict) or not user.get("username") or user.get("role") not in {"admin", "operator", "viewer"}:
            raise HTTPException(503, "身份服务返回的用户信息无效")
        return user

    async def current(request: Request):
        token = request.cookies.get(COOKIE, "")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with store.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE token_hash=? AND expires>?", (digest, time.time())).fetchone()
        if not token or not row:
            raise HTTPException(401, "请登录平台")
        upstream = store.cipher.decrypt(row["upstream"]).decode()
        user = await upstream_identity(upstream)
        if user["username"] != row["owner"]:
            raise HTTPException(401, "会话主体不匹配")
        request.state.upstream_token = upstream
        return user

    def owned(kind, owner, record_id):
        record = store.get(kind, owner, record_id)
        if not record:
            raise HTTPException(404, "记录不存在或无访问权限")
        return record

    def wiki_index(owner):
        from platform_server.weknora_index import WeKnoraIndex
        return WeKnoraIndex(store, owner)

    def unified_agent_session_view(conversation):
        """Adapt a platform unified conversation to the existing SkillCenter contract."""
        return {
            "id": conversation["id"],
            "brain": "unified",
            "harness_session_id": None,
            "model_id": None,
            "model_name": None,
            "pinned": bool(conversation.get("pinned")),
            "archived": bool(conversation.get("archived")),
            "skill_mode": conversation.get("skill_mode", "auto"),
            "selected_skills": conversation.get("selected_skills", []),
            "title": conversation.get("title", "统一会话"),
            "created_at": conversation.get("created_at"),
            "updated_at": conversation.get("updated_at"),
            "message_count": len(conversation.get("messages", [])),
            "last_run_id": conversation.get("last_run_id"),
            "messages": conversation.get("messages", []),
        }

    @app.get("/healthz")
    def health():
        return {"status": "ok", "service": "three-brain-platform", "storage": "persistent", "version": "0.3.0", "installation":str(installation)}

    @app.post("/api/v1/auth/login")
    async def login(body: LoginInput, request: Request, response: Response):
        remote = request.client.host if request.client else "unknown"
        attempts = login_attempts.setdefault(remote, [])
        attempts[:] = [t for t in attempts if t > time.time() - 60]
        if len(attempts) >= 10:
            raise HTTPException(429, "尝试过于频繁，请稍后重试")
        attempts.append(time.time())
        try:
            async with client(15) as conn:
                result = await conn.post(engine + "/api/v1/auth/login", json=body.model_dump())
        except httpx.HTTPError:
            raise HTTPException(503, "身份服务不可达，请先启动结构化引擎")
        if result.status_code != 200:
            store.audit(body.username, "login.denied", "identity")
            raise HTTPException(401, "用户名或密码不正确，或账号已停用")
        identity = result.json()
        user = await upstream_identity(identity["access_token"])
        session = secrets.token_urlsafe(40)
        hours = settings()["session_hours"]
        with store.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            old = request.cookies.get(COOKIE)
            if old:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(old.encode()).hexdigest(),))
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(session.encode()).hexdigest(),
                store.cipher.encrypt(identity["access_token"].encode()), time.time() + hours * 3600, user["username"]))
        response.set_cookie(COOKIE, session, httponly=True, samesite="strict", secure=request.url.scheme == "https", max_age=hours * 3600)
        store.audit(user["username"], "login.success", "identity")
        return {"user": user}

    @app.get("/api/v1/auth/status")
    async def setup_status():
        try:
            async with client(5) as conn:
                result = await conn.get(engine + "/api/v1/auth/status")
            result.raise_for_status()
            return result.json()
        except httpx.HTTPError:
            raise HTTPException(503, "本项目账户服务尚未就绪")

    @app.post("/api/v1/auth/setup")
    async def setup(body: LoginInput):
        async with client(10) as conn:
            result = await conn.post(engine + "/api/v1/auth/setup", json=body.model_dump(), headers={"X-Service-Token": os.environ["ENGINE_SERVICE_TOKEN"]})
        if result.status_code != 200:
            raise HTTPException(result.status_code, result.json().get("detail", "初始化失败"))
        store.audit(body.username, "installation.setup", "identity")
        return result.json()

    @app.get("/api/internal/structured-models")
    def internal_models(request: Request):
        if not hmac.compare_digest(request.headers.get("x-service-token", ""), os.environ["ENGINE_SERVICE_TOKEN"]):
            raise HTTPException(403)
        return {"items": [{**public_model(m), "api_key": store.reveal(m["secret_ref"]) if m.get("secret_ref") else ""}
            for m in store.list("model", "system") if m["brain"] == "structured" and m["purpose"] == "chat"]}

    @app.get("/api/v1/auth/me")
    async def me(user=Depends(current)):
        return user

    @app.post("/api/v1/auth/logout")
    async def logout(request: Request, response: Response, user=Depends(current)):
        token = request.cookies.get(COOKIE, "")
        with store.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
        response.delete_cookie(COOKIE)
        store.audit(user["username"], "logout", "identity")
        return {"ok": True}

    @app.get("/api/v1/platform/overview")
    async def overview(user=Depends(current)):
        statuses = {}
        async def check(name, target, path):
            try:
                async with client(3) as conn:
                    response = await conn.get(target + path)
                statuses[name] = "connected" if response.status_code == 200 else "unavailable"
            except httpx.HTTPError:
                statuses[name] = "unavailable"
        await asyncio.gather(check("structured", engine, "/healthz"), check("structured_agent", agent, "/healthz"))
        owner = user["username"]
        return {"services": statuses, "assets": len(store.list("asset", owner)), "sources": len(store.list("source", owner)),
            "jobs": store.list("job", owner)[-8:][::-1], "knowledge_runtime": knowledge_runtime,
            "weknora": ingest.native_index_status(owner),
            "knowledge_models": {purpose: any(m['brain']=='knowledge' and m['purpose']==purpose and m['enabled'] for m in store.list('model','system')) for purpose in ('embedding','rerank','chat')},
            "storage": "SQLite WAL + managed files", "deployment": "single-host", "version": "0.2.0"}

    @app.get("/api/v1/platform/sources")
    def sources(user=Depends(current)):
        return {"items": [public_source(s) for s in store.list("source", user["username"])]}

    @app.post("/api/v1/platform/sources")
    def add_source(body: SourceInput, user=Depends(current)):
        require_role(user, "admin", "operator")
        if body.kind in {"camera", "rtsp"} and body.brain != "memory":
            raise ValueError("实时视频来源只属于时空记忆推理脑")
        data = body.model_dump(exclude={"rtsp_url"})
        if body.kind == "rtsp":
            data["summary"] = mask_rtsp_url(body.rtsp_url)
            data["secret_ref"] = store.secret(body.rtsp_url)
        return public_source(store.put("source", user["username"], data, action="source.create"))

    @app.post("/api/v1/platform/imports/text")
    def import_text(body: ImportInput, user=Depends(current)):
        require_role(user, "admin", "operator")
        source = owned("source", user["username"], body.source_id)
        return ingest.submit(user["username"], source, body.title if Path(body.title).suffix else body.title + ".txt", body.content.encode("utf-8"),
            {k: v for k, v in body.model_dump(include={"timestamp", "person", "place"}).items() if v})

    @app.get("/api/v1/platform/imports/limits")
    def import_limits(user=Depends(current)):
        limit_mb = settings()["upload_limit_mb"]
        return {"per_file_mb": limit_mb, "per_file_bytes": limit_mb * 1024 * 1024, "scope": "per_file"}

    @app.post("/api/v1/platform/imports/files")
    async def import_file(source_id: str = Form(...), timestamp: str = Form(""), place: str = Form(""), person: str = Form(""), description: str = Form(""), file: UploadFile = File(...), user=Depends(current)):
        require_role(user, "admin", "operator")
        source = owned("source", user["username"], source_id)
        limit = settings()["upload_limit_mb"] * 1024 * 1024
        filename = Path(file.filename or "unnamed").name
        try:
            content = b"" if file.size is not None and file.size > limit else await file.read(limit + 1)
        finally:
            await file.close()
        size = file.size if file.size is not None else len(content)
        if size > limit:
            return JSONResponse(status_code=413, content={"code": "FILE_TOO_LARGE", "filename": filename,
                "limit_bytes": limit, "file_bytes": size,
                "message": f"文件「{filename}」大小 {size / 1024 / 1024:.2f} MB，超过单文件上限 {limit // 1024 // 1024} MB；不是整批文件合计限制。可由管理员在系统设置调整，或拆分此文件。"})
        if not content:
            raise ValueError("不能导入空文件")
        metadata = {k: v for k, v in dict(timestamp=timestamp, place=place, person=person, description=description).items() if v}
        return await run_in_threadpool(ingest.submit, user["username"], source, filename, content, metadata)

    @app.get("/api/v1/platform/jobs")
    def jobs(user=Depends(current)):
        assets = {a['id']: a for a in store.list('asset', user['username'])}
        items = []
        for job in store.list('job', user['username'])[::-1]:
            asset = assets.get(job['asset_id'])
            view = asset_view(asset) if asset else {}
            items.append({**job, 'processing_status': job['status'] if job['status'] in {'running', 'queued', 'failed', 'cancelled'} else view.get('processing_status', 'unknown'),
                          'publication_status': view.get('publication_status', 'not_ready')})
        return {'items': items}

    @app.post("/api/v1/platform/jobs/{job_id}/retry")
    def retry(job_id: str, user=Depends(current)):
        require_role(user, "admin", "operator")
        owned("job", user["username"], job_id)
        return ingest.retry(user["username"], job_id)

    @app.get("/api/v1/platform/assets")
    def assets(user=Depends(current)):
        return {"items": [{k: v for k, v in asset_view(a).items() if k not in {"extracted_text", "steps"}} for a in store.list("asset", user["username"])[::-1]]}

    @app.get("/api/v1/platform/assets/{asset_id}")
    def asset_detail(asset_id: str, user=Depends(current)):
        asset = owned("asset", user["username"], asset_id)
        with ingest.lock:
            if asset.get("document_id"):
                ws = ingest.workspace(user["username"])
                return {"asset": asset_view(asset), "knowledge": ws.knowledge.document_view(asset["document_id"])}
        return {"asset": asset_view(asset)}

    @app.get('/api/v1/platform/assets/{asset_id}/publication')
    def publication_detail(asset_id: str, user=Depends(current)):
        with ingest.lock:
            asset = owned('asset', user['username'], asset_id)
            if asset['brain'] != 'knowledge' or not asset.get('document_id'):
                raise ValueError('当前资料尚未产生知识文档')
            history = [r for r in store.list('review', user['username']) if r['document_id'] == asset['document_id']]
            value = asset_view(asset)
            if asset['status'] not in {'failed', 'queued', 'running', 'cancelled'}:
                ws = ingest.workspace(user['username'])
                value.update(publication_fields(asset, ws.knowledge.brain.store, asset['document_id'], history[-1] if history else None))
            return {'asset': {k: value.get(k) for k in ('id', 'title', 'brain', 'document_id', 'status', 'processing_status', 'publication_status', 'review_reasons')},
                    'history': [{**r, 'actor': r.get('actor') or user['username']} for r in reversed(history)]}

    @app.get("/api/v1/platform/assets/{asset_id}/content")
    def asset_content(asset_id: str, user=Depends(current)):
        asset = owned("asset", user["username"], asset_id)
        return FileResponse(store.object_path(asset["sha256"]), filename=asset["filename"], media_type="application/octet-stream")

    @app.post("/api/v1/platform/assets/{asset_id}/review")
    def review(asset_id: str, body: ReviewInput, user=Depends(current)):
        require_role(user, "admin")
        asset = owned("asset", user["username"], asset_id)
        if asset['brain'] != 'knowledge' or not asset.get("document_id"):
            raise ValueError("当前资料尚未产生知识文档")
        return ingest.review(user["username"], asset, body.model_dump())

    @app.post("/api/v1/platform/assets/{asset_id}/classification")
    def correct_classification(asset_id: str, body: ClassificationInput, user=Depends(current)):
        require_role(user, 'admin', 'operator')
        asset = owned('asset', user['username'], asset_id)
        return ingest.reclassify(user['username'], asset, body.domain, body.reason)

    @app.get("/api/v1/platform/assets/{asset_id}/preview")
    def asset_preview(asset_id: str, user=Depends(current)):
        asset = owned("asset", user["username"], asset_id)
        if asset["mime"] not in {"image/jpeg", "image/png", "image/webp", "image/gif", "video/mp4", "video/webm", "audio/mpeg", "audio/wav", "audio/x-wav", "audio/ogg"}:
            raise HTTPException(415, "此格式请下载原件查看")
        return FileResponse(store.object_path(asset["sha256"]), media_type=asset["mime"])

    @app.post("/api/v1/platform/search/{brain}")
    def search(brain: str, body: QueryInput, user=Depends(current)):
        if brain not in {"knowledge", "memory"}:
            raise HTTPException(404)
        try:
            return ingest.search(user["username"], brain, body.model_dump())
        except KnowledgeWorkspaceWarmingError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get('/api/v1/platform/knowledge/wiki')
    def wiki_summary(user=Depends(current)):
        from platform_server.weknora_index import SidecarUnavailable
        try:
            return wiki_index(user['username']).wiki_summary()
        except SidecarUnavailable as exc:
            raise HTTPException(503, f'WeKnora Wiki 当前不可用：{exc}') from exc

    @app.get('/api/v1/platform/knowledge/wiki/pages')
    def wiki_pages(tree: str, page: int = 1, page_size: int = 20, page_type: str = '',
                   status: str = '', query: str = '', category_path: str = '',
                   user=Depends(current)):
        from platform_server.weknora_index import SidecarUnavailable
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError('Wiki 分页参数无效')
        try:
            return wiki_index(user['username']).wiki_pages(
                tree, page=page, page_size=page_size, page_type=page_type,
                status=status, query=query, category_path=category_path)
        except KeyError as exc:
            raise HTTPException(404, '该知识树没有对应的 WeKnora Wiki') from exc
        except SidecarUnavailable as exc:
            raise HTTPException(503, f'WeKnora Wiki 当前不可用：{exc}') from exc

    @app.get('/api/v1/platform/knowledge/wiki/pages/{slug:path}')
    def wiki_page(tree: str, slug: str, user=Depends(current)):
        from platform_server.weknora_index import SidecarUnavailable
        try:
            return wiki_index(user['username']).wiki_page(tree, slug)
        except KeyError as exc:
            raise HTTPException(404, '该知识树没有对应的 WeKnora Wiki') from exc
        except SidecarUnavailable as exc:
            raise HTTPException(404, 'Wiki 页面不存在') from exc

    @app.get('/api/v1/platform/knowledge/wiki/graph')
    def wiki_graph(tree: str, mode: str = 'overview', center: str = '', depth: int = 1,
                   limit: int = 500, types: str = '', user=Depends(current)):
        from platform_server.weknora_index import SidecarUnavailable
        if mode not in {'overview', 'ego'}:
            raise ValueError("Wiki 图谱模式必须为 overview 或 ego")
        if mode == 'ego' and not center:
            raise ValueError('Wiki 图谱 ego 模式必须指定中心页面')
        try:
            return wiki_index(user['username']).wiki_graph(
                tree, mode=mode, center=center, depth=depth, limit=limit, types=types)
        except KeyError as exc:
            raise HTTPException(404, '该知识树没有对应的 WeKnora Wiki') from exc
        except SidecarUnavailable as exc:
            raise HTTPException(503, f'WeKnora Wiki 当前不可用：{exc}') from exc

    @app.get("/api/v1/platform/topology")
    def topology(cluster_page: int = 1, link_page: int = 1, page_size: int = 20, user=Depends(current)):
        if cluster_page < 1 or link_page < 1 or not 1 <= page_size <= 100:
            raise ValueError('拓扑分页参数无效')
        from platform_server.knowledge_topology import paged_topology
        return paged_topology(ingest.topology(user["username"]), cluster_page, link_page, page_size)

    @app.get("/api/v1/platform/topology/knowledge/trees/{tree_id}")
    async def knowledge_tree_graph(tree_id: str, generation: int | None = None, cursor: int = 0,
                                   limit: int = 200, node_types: str = "", run_id: str = "",
                                   user=Depends(current)):
        if cursor < 0 or not 1 <= limit <= 500 or generation is not None and generation < 1:
            raise ValueError('知识树分页或版本参数无效')
        owner = user['username']
        query_node_ids = []
        if run_id:
            run = owned('agent_run', owner, run_id)
            if run.get('brain') == 'unified':
                knowledge_run = (run.get('brain_runs') or {}).get('knowledge') or {}
                if tree_id not in (knowledge_run.get('tree_ids') or []):
                    raise HTTPException(404, '该运行未命中此知识树')
                query_node_ids = knowledge_run.get('node_ids') or []
            elif run.get('brain') == 'knowledge':
                query_node_ids = ((run.get('evidence') or {}).get('route') or {}).get('selected') or []
            else:
                raise HTTPException(400, '只有知识脑或统一 Agent 运行可以叠加知识树路径')

        def build_graph():
            from platform_server.knowledge_topology import knowledge_tree_slice
            with ingest.lock:
                workspace = ingest.full_workspace(owner)
                return knowledge_tree_slice(
                    workspace.knowledge, tree_id, generation=generation, cursor=cursor, limit=limit,
                    node_types=[item for item in node_types.split(',') if item],
                    query_node_ids=query_node_ids,
                )
        try:
            return await run_in_threadpool(build_graph)
        except KeyError:
            raise HTTPException(404, '知识树或生长版本不存在')

    @app.get("/api/v1/platform/models")
    def models(user=Depends(current)):
        from platform_server.model_metrics import model_inference_stats
        return {"items": [public_model(m, model_inference_stats(store, m["id"]))
                          for m in store.list("model", "system")]}

    @app.get("/api/v1/platform/model-deployments")
    def model_deployments(user=Depends(current)):
        grouped = {}
        for row in store.list("model", "system"):
            if row.get("purpose") != "chat" or not row.get("enabled") or not row.get("deployment"):
                continue
            item = grouped.setdefault(row["deployment"], {
                "id": row["deployment"], "name": row.get("deployment_name") or row["deployment"],
                "default": False, "brains": [],
            })
            if row.get("deployment_name"):
                item["name"] = row["deployment_name"]
            if row["brain"] not in item["brains"]:
                item["brains"].append(row["brain"])
            item["default"] = item["default"] or bool(row.get("is_default"))
        items = sorted(grouped.values(), key=lambda item: (not item["default"], item["id"]))
        if items and not any(item["default"] for item in items):
            items[0]["default"] = True
        return {"items": items, "default": next((item["id"] for item in items if item["default"]), "")}

    @app.get('/api/v1/platform/knowledge/evaluations')
    def evaluation_list(user=Depends(current)):
        return {'items':store.list('evaluation',user['username'])[::-1]}

    @app.post('/api/v1/platform/knowledge/evaluations')
    def evaluate(body:EvaluationInput,user=Depends(current)):
        from brains.protocol import BrainQuery
        require_role(user,'admin','operator')
        owner=user['username']
        expected=[owned('asset',owner,i) for i in body.expected_asset_ids]
        if any(a['brain']!='knowledge' or not a.get('document_id') for a in expected):
            raise ValueError('请选择已完成索引的知识资产作为标准答案')
        with ingest.lock:
            ws=ingest.workspace(owner)
            if not hasattr(ws.knowledge.brain.runtime,'retrieve'):
                raise ValueError('评测需要已接入的 WeKnora 运行时')
            started=time.perf_counter()
            _,matches=ws.knowledge.brain._retrieve(BrainQuery(request_id=uid('eval'),trace_id=uid('trace'),day_key='',raw_query=body.query,profile_id='owner'), candidate_limit=max(50, body.k))
            retrieved=list(dict.fromkeys(ws.knowledge.brain.store.units[u].document_id for u,_,allowed in matches if allowed))[:body.k]
            relevant=list(dict.fromkeys(a['document_id'] for a in expected))
            mapping={d:i for i,d in enumerate(dict.fromkeys(retrieved+relevant))}
            scores=ws.knowledge.brain.runtime._invoke('evaluate',{'retrieved':[mapping[d] for d in retrieved],'relevant':[mapping[d] for d in relevant],'k':body.k})
            return store.put('evaluation',owner,{'query':body.query,'expected_asset_ids':body.expected_asset_ids,'k':body.k,'retrieved_document_ids':retrieved,'metrics':scores,'duration_ms':round((time.perf_counter()-started)*1000,2),'runtime_version':ws.knowledge.brain.runtime_meta.runtime_version},action='knowledge.evaluate')

    @app.put("/api/v1/platform/models/{model_id}")
    def save_model(model_id: str, body: ModelInput, user=Depends(current)):
        require_role(user, "admin")
        if body.enabled and body.purpose in {"embedding", "rerank"} and knowledge_runtime == 'builtin-dev-v1':
            raise ValueError("当前内建知识运行时尚未接入模型化向量与重排；可先保存为未启用配置，运行时接入后再激活")
        if body.brain != 'knowledge' and body.purpose in {'embedding','rerank'}:
            raise ValueError('WeKnora 仅影响知识脑；其他两个脑不使用这一向量与重排配置')
        if body.brain == 'structured' and body.purpose != 'chat':
            raise ValueError('结构化脑通过 Agent 对话模型调用原有查询工具')
        if body.brain == 'orchestrator' and body.purpose != 'chat':
            raise ValueError('统一编排控制面只使用对话模型，不建立独立知识索引')
        old = store.get("model", "system", model_id)
        if old and old.get('secret_ref') and body.base_url != old['base_url'] and not body.api_key:
            raise ValueError('模型地址已改变，请填写新地址对应的 API Key；无密钥服务请新建配置，避免把旧凭据发送到其他地址')
        if body.brain == 'knowledge' and body.purpose == 'embedding':
            active = next((m for m in store.list('model','system') if m['brain']=='knowledge' and m['purpose']=='embedding' and m['enabled']), None)
            if active and any(a['brain']=='knowledge' and a['status'] in {'ready','partial','pending_review','rejected'} for a in store.list('asset')):
                changing = (body.enabled and (model_id != active['id'] or body.model != active['model'] or body.base_url != active['base_url'])) or (model_id == active['id'] and not body.enabled)
                if changing:
                    raise HTTPException(409, '当前向量模型已有索引。请先执行受控索引迁移；禁止直接切换并混用向量空间')
        data = body.model_dump(exclude={"api_key"})
        if old:
            # Older clients do not submit deployment fields; keep the stored
            # grouping instead of silently detaching a row from its family.
            for key, fallback in (("deployment", ""), ("deployment_name", ""), ("is_default", False)):
                if key not in body.model_fields_set:
                    data[key] = old.get(key, fallback)
        if data["purpose"] == "chat":
            data["is_default"] = bool(data.get("is_default") and data["enabled"])
            if data["is_default"] and not data.get("deployment"):
                raise ValueError("设为默认的对话模型必须填写部署标识（例如 codex）")
        else:
            data.update(deployment="", deployment_name="", is_default=False)
        data["id"] = uid("model") if model_id == "new" else model_id
        if model_id != "new" and not old:
            raise HTTPException(404)
        data["secret_ref"] = store.secret(body.api_key) if body.api_key else (old or {}).get("secret_ref")
        if old and old.get('managed_local') and body.base_url == old['base_url'] and body.model == old['model']:
            data.update(managed_local=True, provider_revision=old['provider_revision'])
        result = store.put("model", "system", data)
        if body.purpose == "chat":
            # Chat rows may stay enabled together so the Agent can switch per
            # question; only the default marker is exclusive within a brain.
            if result.get("is_default"):
                for prior in store.list("model", "system"):
                    if prior["id"] != result["id"] and prior["brain"] == body.brain and prior["purpose"] == "chat" and prior.get("is_default"):
                        prior["is_default"] = False
                        store.put("model", "system", prior)
        elif body.enabled:
            for prior in store.list("model", "system"):
                if prior["id"] != result["id"] and prior["brain"] == body.brain and prior["purpose"] == body.purpose and prior["enabled"]:
                    prior["enabled"] = False
                    store.put("model", "system", prior)
        store.audit(user["username"], "model.save", result["id"])
        # A chat-model edit changes only future generation requests.  It must
        # not discard the active knowledge projection or make a settings save
        # wait behind a historical corpus rebuild.  Vector-space changes are
        # already rejected above while indexed knowledge exists; they require
        # an explicit, versioned migration rather than an implicit clear.
        return public_model(result)

    @app.post('/api/v1/platform/models/discover')
    async def discover_model_options(body: ModelDiscoveryInput, user=Depends(current)):
        require_role(user, 'admin')
        key = body.api_key
        if not key and body.model_id:
            saved = owned('model', 'system', body.model_id)
            # Existing credentials must never be forwarded to a newly typed address.
            if body.base_url.rstrip('/') != saved['base_url'].rstrip('/'):
                raise ValueError('模型地址已改变，请重新填写此地址对应的 API Key 后读取模型')
            key = store.reveal(saved['secret_ref']) if saved.get('secret_ref') else ''
        async with client(10) as conn:
            return await discover_models(conn, body.base_url, key)

    @app.post("/api/v1/platform/models/{model_id}/check")
    async def check_model(model_id: str, user=Depends(current)):
        require_role(user, "admin")
        model = owned("model", "system", model_id)
        key = store.reveal(model['secret_ref']) if model.get('secret_ref') else ''
        async with client(10) as conn:
            result = await discover_models(conn, model['base_url'], key)
        if result['ok']:
            found = model['model'] in result['model_ids']
            result.update(ok=found, reachable=True, model_found=found,
                code='MODEL_LISTED' if found else 'MODEL_NOT_FOUND',
                message='连接正常，模型 ID 已确认；请再验证推理。' if found else f'地址可达，但模型“{model["model"]}”不在可用列表中。请编辑配置并读取可用模型，厂商名称不能代替模型 ID。')
        store.audit(user['username'], 'model.check', model_id, {'ok': result['ok']})
        return result

    @app.post('/api/v1/platform/models/{model_id}/probe')
    async def probe_model(model_id: str, user=Depends(current)):
        require_role(user, 'admin')
        model = owned('model', 'system', model_id)
        if model['purpose'] not in {'chat', 'embedding', 'rerank'}:
            raise ValueError('此用途请通过对应媒体导入流程验收')
        headers = {'Authorization': 'Bearer ' + store.reveal(model['secret_ref'])} if model.get('secret_ref') else {}
        try:
            purpose = model['purpose']
            endpoint = {'chat': '/chat/completions', 'embedding': '/embeddings', 'rerank': '/rerank'}[purpose]
            request_body = {'model': model['model']}
            if purpose == 'embedding':
                request_body['input'] = ['知识文档检索连接验证']
            elif purpose == 'rerank':
                request_body.update(query='知识文档检索', documents=['知识文档的查找与引用', '天气和出行'], top_n=2)
            else:
                request_body['messages'] = [{'role': 'user', 'content': '连接验证，只回复 OK。'}]
            async with client(model.get('timeout', 90)) as conn:
                response = await conn.post(model['base_url'] + endpoint, headers=headers, json=request_body)
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            from platform_server.model_provider import validate_inference_probe
            ok, message = validate_inference_probe(purpose, response.status_code, model['model'], payload)
        except httpx.HTTPError:
            ok, message = False, '推理请求超时或网络不可达，请检查模型服务。'
        store.audit(user['username'], 'model.probe', model_id, {'ok': ok})
        return {'ok': ok, 'message': message}

    @app.get("/api/v1/platform/settings")
    def get_settings(user=Depends(current)):
        require_role(user, "admin", "operator")
        return settings()

    @app.put("/api/v1/platform/settings")
    def save_settings(body: SettingsInput, user=Depends(current)):
        require_role(user, "admin")
        result = store.put("settings", "system", dict(id="settings", **body.model_dump()))
        store.audit(user["username"], "settings.save", "settings")
        return result

    @app.get("/api/v1/platform/audit")
    def audit(user=Depends(current)):
        return {"items": store.events(None if user["role"] == "admin" else user["username"])}

    @app.get("/api/v1/platform/streams")
    def stream_list(user=Depends(current)):
        return {"items": streams.list(user["username"])}

    @app.post("/api/v1/platform/sources/{source_id}/start")
    def stream_start(source_id: str, user=Depends(current)):
        require_role(user, "admin", "operator")
        return streams.start(user["username"], owned("source", user["username"], source_id), settings()["stream_frame_limit"])

    @app.post("/api/v1/platform/streams/{session_id}/stop")
    def stream_stop(session_id: str, user=Depends(current)):
        require_role(user, "admin", "operator")
        return streams.stop(user["username"], session_id)

    @app.get("/api/v1/platform/sessions")
    def sessions(archived: bool = False, user=Depends(current)):
        return {"items": agent_runs.list_conversations(user["username"], archived)}

    @app.post("/api/v1/platform/sessions")
    def add_session(body: SessionInput, user=Depends(current)):
        return store.put("conversation", user["username"],
                         dict(**body.model_dump(), messages=[], pinned=False, archived=False), action="session.create")

    @app.get("/api/v1/platform/sessions/{session_id}")
    async def get_session(session_id: str, user=Depends(current)):
        conversation = agent_runs.conversation(user['username'], session_id)
        await unified_agent.enrich_visualizations(user['username'], conversation)
        return agent_runs.conversation(user['username'], session_id)

    @app.post("/api/v1/platform/sessions/{session_id}/rename")
    def rename_session(session_id: str, body: SessionTitleInput, user=Depends(current)):
        return agent_runs.update_conversation(user['username'], session_id,
                                              {'title': body.title.strip()}, 'session.rename')

    @app.post("/api/v1/platform/sessions/{session_id}/pin")
    def pin_session(session_id: str, body: SessionPinInput, user=Depends(current)):
        return agent_runs.update_conversation(user['username'], session_id,
                                              {'pinned': body.pinned}, 'session.pin', allow_running=True)

    @app.post("/api/v1/platform/sessions/{session_id}/archive")
    def archive_session(session_id: str, body: SessionArchiveInput, user=Depends(current)):
        return agent_runs.update_conversation(user['username'], session_id,
                                              {'archived': body.archived}, 'session.archive')

    @app.delete("/api/v1/platform/sessions/{session_id}")
    def delete_session(session_id: str, user=Depends(current)):
        return agent_runs.delete_conversation(user['username'], session_id)

    @app.post("/api/v1/platform/sessions/{session_id}/messages/{message_id}/feedback")
    def message_feedback(session_id: str, message_id: str, body: MessageFeedbackInput, user=Depends(current)):
        return agent_runs.feedback(user['username'], session_id, message_id, body.feedback)

    @app.post("/api/v1/platform/sessions/{session_id}/messages/{message_id}/branch", status_code=201)
    def branch_session(session_id: str, message_id: str, user=Depends(current)):
        return agent_runs.branch(user['username'], session_id, message_id)

    @app.get('/api/v1/platform/runs/{run_id}')
    def get_run(run_id: str, user=Depends(current)):
        return owned('agent_run', user['username'], run_id)

    @app.post("/api/v1/platform/sessions/{session_id}/runs")
    async def submit_message(session_id: str, body: MessageInput, user=Depends(current)):
        """Cancellable specialist run; legacy messages endpoint remains synchronous."""
        return await agent_runs.submit(user['username'], session_id, body)

    @app.post("/api/v1/platform/unified/sessions/{session_id}/runs")
    async def submit_unified_message(session_id: str, body: UnifiedMessageInput, user=Depends(current)):
        if body.route_mode == 'scoped':
            require_role(user, 'admin', 'operator')
        return await unified_agent.submit(user['username'], session_id, body)

    @app.post("/api/v1/platform/sessions/{session_id}/runs/{run_id}/pause")
    def pause_run(session_id: str, run_id: str, user=Depends(current)):
        run = owned('agent_run', user['username'], run_id)
        if run.get('session_id') != session_id:
            raise HTTPException(404, '运行记录不存在或无访问权限')
        handler = unified_agent if run.get('brain') == 'unified' else agent_runs
        return handler.pause(user['username'], session_id, run_id)

    @app.delete("/api/v1/platform/sessions/{session_id}/runs/{run_id}/question")
    def retract_run_question(session_id: str, run_id: str, user=Depends(current)):
        run = owned('agent_run', user['username'], run_id)
        if run.get('session_id') != session_id:
            raise HTTPException(404, '运行记录不存在或无访问权限')
        handler = unified_agent if run.get('brain') == 'unified' else agent_runs
        return handler.retract(user['username'], session_id, run_id)

    @app.post("/api/v1/platform/sessions/{session_id}/messages")
    async def message(session_id: str, body: MessageInput, user=Depends(current)):
        return await agent_runs.execute(user['username'], session_id, body)

    @app.post("/api/v1/platform/unified/sessions/{session_id}/messages")
    async def unified_message(session_id: str, body: UnifiedMessageInput, user=Depends(current)):
        if body.route_mode == 'scoped':
            require_role(user, 'admin', 'operator')
        return await unified_agent.execute(user['username'], session_id, body)

    async def proxy(path: str, request: Request, user, target):
        # Only a server-validated user token is forwarded. Browser-supplied
        # X-Service-Token / X-Principal / X-Roles can never elevate privileges.
        if ".." in path.split("/") or "\\" in path:
            raise HTTPException(400, "无效的服务路径")
        headers = {"Authorization": "Bearer " + request.state.upstream_token}
        if target == agent:
            headers["X-Service-Token"] = os.environ["ENGINE_SERVICE_TOKEN"]
        for name in ("content-type", "accept", "range"):
            if name in request.headers:
                headers[name] = request.headers[name]
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 100 * 1024 * 1024:
                raise HTTPException(413, "请求超过 100 MB")
            chunks.append(chunk)
        if path.startswith("/api/v1/agent/"):
            if path.startswith("/api/v1/agent/skills") and request.method != "GET":
                require_role(user, "admin")
            if request.headers.get("content-type", "").startswith("application/json") and chunks:
                try:
                    body = json.loads(b"".join(chunks))
                except ValueError:
                    raise HTTPException(400, "无效的 JSON")
                if isinstance(body, dict) and body.get("session_id"):
                    unified = store.get("conversation", user["username"], body["session_id"])
                    if unified and unified.get("brain") == "unified":
                        # The Node Agent cannot read a platform conversation by id.
                        # Forward only this owner's bounded recent dialogue as
                        # untrusted drafting context.
                        messages = unified.get("messages", [])[-12:]
                        body["context"] = "\n".join(
                            f"{('用户' if item.get('role') == 'user' else '助手')}：{str(item.get('content', ''))[:2000]}"
                            for item in messages if item.get("role") in {"user", "assistant"}
                        )[:16000]
                        body["session_id"] = None
                        chunks = [json.dumps(body, ensure_ascii=False).encode("utf-8")]
                    else:
                        owned("structured_session", user["username"], body["session_id"])
        conn = client(180)
        try:
            upstream = await conn.send(conn.build_request(request.method, target + path,
                params=request.query_params, headers=headers, content=b"".join(chunks)), stream=True)
        except httpx.HTTPError:
            await conn.aclose()
            raise HTTPException(503, "结构化服务不可达，请检查引擎或 Agent 服务")
        async def close():
            await upstream.aclose()
            await conn.aclose()
        if path.startswith("/api/v1/agent/") and upstream.status_code < 300 and "application/json" in upstream.headers.get("content-type", ""):
            try:
                await upstream.aread()
                payload = upstream.json()
                if path == "/api/v1/agent/sessions" and request.method == "GET":
                    visible = []
                    for item in payload.get("items", []):
                        owner = store.owner_of("structured_session", item["id"])
                        # Unassigned sessions predate platform ownership. Only
                        # the deployment's legacy administrator can adopt them.
                        if owner is None and user["username"] == os.environ.get("LEGACY_AGENT_OWNER", "admin") and user["role"] == "admin":
                            store.put("structured_session", user["username"], {"id": item["id"]})
                            owner = user["username"]
                        if owner == user["username"]:
                            visible.append(item)
                            if item.get("last_run_id"):
                                store.put("structured_run", owner, {"id": item["last_run_id"], "session_id": item["id"]})
                    payload["items"] = visible
                elif request.method == "GET" and re.fullmatch(r"/api/v1/agent/sessions/[^/]+", path):
                    for entry in payload.get("messages", []):
                        if entry.get("run_id"):
                            store.put("structured_run", user["username"], {"id": entry["run_id"], "session_id": path.split("/")[5]})
                elif request.method == "POST" and isinstance(payload, dict) and payload.get("id"):
                    if path == "/api/v1/agent/sessions" or path.endswith("/branch"):
                        store.put("structured_session", user["username"], {"id": payload["id"]}, action="structured.session.create")
                    elif re.fullmatch(r"/api/v1/agent/sessions/[^/]+/messages", path):
                        store.put("structured_run", user["username"], {"id": payload["id"], "session_id": path.split("/")[5]})
                if request.method not in {"GET", "HEAD"}:
                    store.audit(user["username"], "structured." + request.method.lower(), path, {"status": upstream.status_code})
                return JSONResponse(status_code=upstream.status_code, content=payload)
            finally:
                await close()
        safe_headers = {k: v for k, v in upstream.headers.items() if k.lower() in {"content-type", "content-disposition", "content-range", "accept-ranges"}}
        if request.method not in {"GET", "HEAD"}:
            store.audit(user["username"], "structured." + request.method.lower(), path, {"status": upstream.status_code})
        return StreamingResponse(upstream.aiter_bytes(), status_code=upstream.status_code, headers=safe_headers, background=BackgroundTask(close))

    @app.api_route("/api/v1/engine/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def engine_proxy(path: str, request: Request, user=Depends(current)):
        return await proxy("/api/v1/engine/" + path, request, user, engine)

    @app.api_route("/api/v1/agent/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def agent_proxy(path: str, request: Request, user=Depends(current)):
        session_match = re.match(r"sessions/([^/]+)(?:/|$)", path)
        run_match = re.match(r"runs/([^/]+)(?:/|$)", path)
        if session_match:
            conversation = store.get("conversation", user["username"], session_match[1])
            if conversation and conversation.get("brain") == "unified":
                if request.method == "POST" and path.endswith("/skills"):
                    payload = await request.json()
                    mode = payload.get("mode", "auto")
                    skills = payload.get("skills", [])
                    if mode not in {"auto", "manual"} or not isinstance(skills, list) or len(skills) > 20:
                        raise HTTPException(422, "Skill 会话配置不正确")
                    conversation["skill_mode"] = mode
                    conversation["selected_skills"] = [str(item) for item in skills[:20]] if mode == "manual" else []
                    store.put("conversation", user["username"], conversation, action="unified_agent.skills.updated")
                    return unified_agent_session_view(conversation)
                if request.method == "GET" and re.fullmatch(r"sessions/[^/]+", path):
                    return unified_agent_session_view(conversation)
            else:
                owned("structured_session", user["username"], session_match[1])
        if run_match:
            owned("structured_run", user["username"], run_match[1])
        if request.query_params.get("run_id"):
            run = owned("structured_run", user["username"], request.query_params["run_id"])
            if session_match and run.get("session_id") != session_match[1]:
                raise HTTPException(404, "运行不属于当前会话")
        return await proxy("/api/v1/agent/" + path, request, user, agent)

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        dist = (ROOT / "frontend/dist").resolve()
        file = (dist / path).resolve()
        if file.is_relative_to(dist) and file.is_file():
            return FileResponse(file)
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return JSONResponse(status_code=503, content={"message": "前端尚未构建，请在 frontend 中执行 npm run build"})

    return app


app = create_app()
