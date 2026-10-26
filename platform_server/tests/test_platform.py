import json
import io
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from platform_server.app import create_app as platform_app
from platform_server.ingestion import Ingestion
from platform_server.store import Store

def create_app(*args, **kwargs):
    # Existing deterministic contract tests explicitly opt into the development runtime.
    return platform_app(*args, knowledge_runtime='builtin-dev-v1', **kwargs)


def configure_model(client, brain="memory", purpose="vision"):
    response = client.put("/api/v1/platform/models/new", json={"name": "isolated provider", "brain": brain,
        "purpose": purpose, "model": "verified-model", "base_url": "http://model/v1", "api_key": "provider-secret", "enabled": True})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def identity_server(request):
    if request.url.path == "/api/v1/auth/login":
        username = json.loads(request.content)["username"]
        return httpx.Response(200, json={"access_token": username})
    username = request.headers.get("authorization", "").removeprefix("Bearer ")
    if request.url.path == "/api/v1/auth/me":
        if username not in {"alice", "bob", "reader"}:
            return httpx.Response(401)
        return httpx.Response(200, json={"username": username, "role": "viewer" if username == "reader" else "admin", "display_name": username})
    if request.url.path == "/api/v1/engine/datasets":
        assert "x-service-token" not in request.headers
        assert "x-roles" not in request.headers
        return httpx.Response(200, json={"items": [], "principal": username})
    return httpx.Response(404)


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path, engine_url="http://engine", agent_url="http://agent", transport=httpx.MockTransport(identity_server))


def login(client, name="alice"):
    response = client.post("/api/v1/auth/login", json={"username": name, "password": "local-test-password"})
    assert response.status_code == 200, response.text
    assert "access_token" not in response.json()
    assert "HttpOnly" in response.headers["set-cookie"]


def create_source(client, brain="knowledge"):
    response = client.post("/api/v1/platform/sources", json={"name": "isolated test source", "brain": brain, "kind": "text"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def wait_job(client, job_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        job = next(j for j in client.get("/api/v1/platform/jobs").json()["items"] if j["id"] == job_id)
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(.03)
    raise AssertionError("import did not settle")


def test_auth_csrf_logout_and_forwarded_identity(app):
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        assert client.get("/api/v1/platform/assets").status_code == 401
        assert client.post("/api/v1/auth/login", headers={"Origin": "https://attacker.example"}, json={"username": "alice", "password": "p"}).status_code == 403
        login(client)
        response = client.get("/api/v1/engine/datasets", headers={"X-Service-Token": "forged", "X-Roles": "admin"})
        assert response.status_code == 200
        assert response.json()["principal"] == "alice"
        client.post("/api/v1/auth/logout")
        assert client.get("/api/v1/platform/assets").status_code == 401


def test_owner_isolation_import_and_restart(app, tmp_path):
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        source = create_source(client)
        payload = {"source_id": source, "title": "橙色果园.txt", "content": "# 橙色果园\n橙色果园种植柑橘，春季开花，秋季采收。"}
        job = client.post("/api/v1/platform/imports/text", json=payload).json()
        completed = wait_job(client, job["id"])
        assert completed["status"] == "succeeded", completed
        assert client.post("/api/v1/platform/imports/text", json=payload).json()["id"] == job["id"]
        asset = client.get("/api/v1/platform/assets").json()["items"][0]
        before = client.post("/api/v1/platform/search/knowledge", json={"query": "橙色果园柑橘"}).json()
        assert before["result"]["evidence"], before
        assert client.get(f"/api/v1/platform/assets/{asset['id']}/content").content == payload["content"].encode()
        login(client, "bob")
        assert client.get("/api/v1/platform/assets").json()["items"] == []
        assert client.get(f"/api/v1/platform/assets/{asset['id']}").status_code == 404
        assert client.post("/api/v1/platform/imports/text", json=payload).status_code == 404
        login(client, "reader")
        assert client.post("/api/v1/platform/sources", json={"name": "no", "brain": "memory", "kind": "text"}).status_code == 403
    restarted = create_app(tmp_path, engine_url="http://engine", transport=httpx.MockTransport(identity_server))
    with TestClient(restarted, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        after = client.post("/api/v1/platform/search/knowledge", json={"query": "橙色果园柑橘"}).json()
        assert [e["evidence_id"] for e in after["result"]["evidence"]] == [e["evidence_id"] for e in before["result"]["evidence"]]
        assert len(client.get("/api/v1/platform/assets").json()["items"]) == 1


def test_model_secrets_and_validation_are_not_echoed(app):
    secret = "private-test-api-key"
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        body = {"name": "chat", "brain": "knowledge", "purpose": "chat", "model": "model", "base_url": "http://model/v1", "api_key": secret}
        response = client.put("/api/v1/platform/models/new", json=body)
        assert response.status_code == 200, response.text
        assert secret not in response.text
        assert secret not in client.get("/api/v1/platform/models").text
        invalid = client.put("/api/v1/platform/models/new", json=dict(body, base_url="invalid", api_key=secret * 1000))
        assert invalid.status_code == 422
        assert secret not in invalid.text
        with app.state.store.connect() as db:
            assert secret.encode() not in db.execute("SELECT value FROM secrets").fetchone()[0]


def test_media_time_is_not_invented_and_failure_retains_original(app):
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        source = create_source(client, "memory")
        body = {"source_id": source, "title": "旅行记录.txt", "content": "今天观察到山间瀑布，地点未确认。"}
        job = client.post("/api/v1/platform/imports/text", json=body).json()
        assert wait_job(client, job["id"])["status"] == "succeeded"
        asset = client.get("/api/v1/platform/assets").json()["items"][0]
        assert asset["unit"]["day_key"] is None
        ksource = create_source(client)
        failed = client.post("/api/v1/platform/imports/files", data={"source_id": ksource}, files={"file": ("unsupported.xyz", b"retained-original", "application/octet-stream")}).json()
        failed = wait_job(client, failed["id"])
        assert failed["status"] == "failed"
        assert client.get(f"/api/v1/platform/assets/{failed['asset_id']}/content").content == b"retained-original"


def test_visual_provider_is_used_once_and_restored_without_reinference(app, tmp_path, monkeypatch):
    from PIL import Image
    from platform_server.media_models import extract_media
    import platform_server.ingestion as ingestion_module
    calls = []

    def provider(request):
        assert request.headers["authorization"] == "Bearer provider-secret"
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        calls.append(request.url.path)
        return httpx.Response(200, json={"choices": [{"message": {"content": "画面中有橙色果实，具体地点未确认。"}}]})

    monkeypatch.setattr(ingestion_module, "extract_media", lambda *args: extract_media(*args, transport=httpx.MockTransport(provider)))
    image = io.BytesIO()
    Image.new("RGB", (10, 10), "orange").save(image, "PNG")
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        configure_model(client)
        source = create_source(client, "memory")
        job = client.post("/api/v1/platform/imports/files", data={"source_id": source}, files={"file": ("photo.png", image.getvalue(), "image/png")}).json()
        assert wait_job(client, job["id"])["status"] == "succeeded"
        detail = client.get(f"/api/v1/platform/assets/{job['asset_id']}").json()["asset"]
        assert detail["model_extraction"]["status"] == "inferred"
        assert detail["unit"]["day_key"] is None
        assert any(f["name"] == "keyframe.model_description" and f["status"] == "available" for f in detail["unit"]["features"])
    restarted = create_app(tmp_path, engine_url="http://engine", transport=httpx.MockTransport(identity_server))
    with TestClient(restarted, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        assert client.post("/api/v1/platform/search/memory", json={"query": "橙色果实"}).status_code == 200
        units = client.get("/api/v1/platform/topology").json()["memory_units"]
        assert any(f["name"] == "keyframe.model_description" for u in units for f in u["features"])
    assert len(calls) == 1


def test_structured_agent_sessions_and_runs_are_owner_scoped(tmp_path):
    forwarded = []

    def upstream(request):
        path = request.url.path
        if not path.startswith("/api/v1/agent/"):
            return identity_server(request)
        forwarded.append(path)
        if path == "/api/v1/agent/sessions" and request.method == "POST":
            return httpx.Response(200, json={"id": "session-alice", "title": "mine"})
        if path == "/api/v1/agent/sessions" and request.method == "GET":
            return httpx.Response(200, json={"items": [{"id": "session-alice", "last_run_id": "run-alice"}, {"id": "legacy-private"}]})
        if path.endswith("/messages") and request.method == "POST":
            return httpx.Response(200, json={"id": "run-alice"})
        return httpx.Response(200, json={"items": []})

    application = create_app(tmp_path, engine_url="http://engine", agent_url="http://agent", transport=httpx.MockTransport(upstream))
    with TestClient(application, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        assert client.post("/api/v1/agent/sessions", json={"title": "mine"}).status_code == 200
        assert client.post("/api/v1/agent/sessions/session-alice/messages", json={"message": "query"}).status_code == 200
        assert [i["id"] for i in client.get("/api/v1/agent/sessions").json()["items"]] == ["session-alice"]
        assert client.get("/api/v1/agent/runs/run-alice").status_code == 200
        login(client, "bob")
        assert client.get("/api/v1/agent/sessions").json()["items"] == []
        calls_before = len(forwarded)
        for path in ("sessions/session-alice", "sessions/session-alice/messages", "runs/run-alice"):
            assert client.get("/api/v1/agent/" + path).status_code == 404
        assert client.post("/api/v1/agent/attachments", json={"session_id": "session-alice"}).status_code == 404
        assert len(forwarded) == calls_before
        assert application.state.store.events("alice")[0]["action"] == "structured.post"


def test_chat_uses_authorized_evidence_and_saves_conversation(tmp_path):
    requests = []

    def upstream(request):
        if request.url.host != "model":
            return identity_server(request)
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "只有本次已授权的材料可以支持回答。"}}]})

    application = create_app(tmp_path, engine_url="http://engine", transport=httpx.MockTransport(upstream))
    with TestClient(application, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        source = create_source(client)
        job = client.post("/api/v1/platform/imports/text", json={"source_id": source, "title": "果园知识", "content": "橙色果园的柑橘在秋季采收。"}).json()
        assert wait_job(client, job["id"])["status"] == "succeeded"
        model = configure_model(client, "knowledge", "chat")
        session = client.post("/api/v1/platform/sessions", json={"brain": "knowledge"}).json()["id"]
        result = client.post(f"/api/v1/platform/sessions/{session}/messages", json={"query": "橙色果园采收", "model_id": model})
        assert result.status_code == 200, result.text
        assert result.json()["messages"][-1]["evidence"]["result"]["evidence"]
        assert "橙色果园" in requests[0]["messages"][-1]["content"]
        login(client, "bob")
        assert client.get(f"/api/v1/platform/sessions/{session}").status_code == 404


def test_unimplemented_model_activation_is_rejected_and_review_replayed(app, tmp_path):
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        result = client.put("/api/v1/platform/models/new", json={"name": "not-supported", "brain": "knowledge", "purpose": "embedding", "base_url": "http://model/v1", "model": "embed", "enabled": True})
        assert result.status_code == 400
        source = create_source(client)
        job = client.post("/api/v1/platform/imports/text", json={"source_id": source, "title": "撤销资料", "content": "橙色果园的柑橘在秋季采收。"}).json()
        assert wait_job(client, job["id"])["status"] == "succeeded"
        result = client.post(f"/api/v1/platform/assets/{job['asset_id']}/review", json={"action": "rollback", "reason": "需要核对原文"})
        assert result.status_code == 200, result.text
        assert wait_job(client, job["id"])["status"] == "pending_review"
    restarted = create_app(tmp_path, engine_url="http://engine", transport=httpx.MockTransport(identity_server))
    with TestClient(restarted, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        result = client.post("/api/v1/platform/search/knowledge", json={"query": "橙色果园"})
        assert result.status_code == 200, result.text
        assert not result.json()["result"]["claims"]
        assert not result.json()["route"]["selected"]
        assert not result.json()["passages"]


def test_stream_frames_are_durable_bounded_and_not_restarted(tmp_path):
    from platform_server.streams import DurableStreamService, Streams
    from brains.memory.src.streaming import StreamStatus

    class Adapter:
        closed = False
        def connect(self):
            pass
        def read_frame(self):
            return b"isolated-frame"
        def close(self):
            self.closed = True

    store = Store(tmp_path)
    ingest = Ingestion(store)
    source = store.put("source", "alice", {"name": "isolated-camera", "brain": "memory", "kind": "camera"})
    adapter = Adapter()
    service = DurableStreamService(ingest, "alice", source, 2, camera_builder=lambda *_: adapter)
    session = service.create_camera(owner="alice", device_index=0, sample_interval_seconds=.25)
    try:
        service.start(session.session_id)
        deadline = time.monotonic() + 5
        while service.get(session.session_id).status != StreamStatus.stopped and time.monotonic() < deadline:
            time.sleep(.03)
        assert service.get(session.session_id).frames_captured == 2
        assert service.get(session.session_id).status == StreamStatus.stopped
    finally:
        service.stop(session.session_id)
        service.persist(session.session_id)
        ingest.close()
    assert adapter.closed
    assert len(store.list("asset", "alice")) == 2
    assert all(job["status"] == "partial" for job in store.list("job", "alice"))
    assert all(store.object_path(a["sha256"]).read_bytes() == b"isolated-frame" for a in store.list("asset", "alice"))
    restored = Ingestion(store)
    streams = Streams(restored)
    try:
        assert streams.live == {}
        assert streams.list("alice")[0]["status"] == "stopped"
    finally:
        restored.close()
