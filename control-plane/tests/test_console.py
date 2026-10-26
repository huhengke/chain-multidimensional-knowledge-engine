from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from api import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_console_requires_login_and_runs_three_brain_fixture_query():
    client = _client()
    assert client.get("/console").status_code == 200
    assert client.get("/api/v1/console/overview").status_code == 401
    login = client.post(
        "/api/v1/console/auth/login",
        json={"username": "admin", "password": "brain-demo-2026"},
    )
    assert login.status_code == 200
    assert login.json()["session"]["mode"] == "local_demo"

    loaded = client.post("/api/v1/console/fixtures/load")
    assert loaded.status_code == 200
    assert loaded.json()["fixtures_loaded"] is True
    assert set(loaded.json()["brains"]) == {"memory", "knowledge", "structured"}

    response = client.post(
        "/api/v1/console/query",
        json={
            "request_id": "console-query",
            "trace_id": "console-trace",
            "raw_query": "设备温度巡检与安全风险预警如何处理？",
            "targets": ["memory", "knowledge", "structured"],
            "constraints": {"no_fabricated_precision": True, "merge_mode": "separate"},
        },
    )
    assert response.status_code == 200
    per_brain = response.json()["stages"]["verification"]["per_brain"]
    assert set(per_brain) == {"memory", "knowledge", "structured"}
    assert response.json()["trace_id"] == "console-trace"
    assert response.json()["audit"]["profile_frozen_at"]


def test_console_offline_memory_import_and_rtsp_redaction():
    client = _client()
    client.post(
        "/api/v1/console/auth/login",
        json={"username": "admin", "password": "brain-demo-2026"},
    )
    imported = client.post(
        "/api/v1/console/memory/imports",
        json={
            "request_id": "memory-import",
            "trace_id": "memory-trace",
            "filename": "event.txt",
            "media_kind": "text",
            "text": "控制台离线事件记录",
            "timestamp": "2026-10-12T08:00:00+08:00",
            "place": "测试现场",
        },
    )
    assert imported.status_code == 200
    assert imported.json()["source_kind"] == "manual_import"

    stream = client.post(
        "/api/v1/console/memory/streams",
        json={"kind": "rtsp", "rtsp_url": "rtsp://alice:secret@example.test/live"},
    )
    assert stream.status_code == 200
    assert "secret" not in stream.text
    assert "alice" not in stream.text

    assert client.get("/api/v1/console/memory").status_code == 200
    assert client.get("/api/v1/console/structured").status_code == 200
    assert client.get("/api/v1/console/topology").status_code == 200
    assert client.get("/api/v1/console/governance").status_code == 200


def test_console_structured_brain_requires_manual_input_then_returns_fixed_values():
    client = _client()
    client.post(
        "/api/v1/console/auth/login",
        json={"username": "admin", "password": "brain-demo-2026"},
    )

    initial = client.get("/api/v1/console/structured")
    assert initial.status_code == 200
    assert initial.json()["registrations"] == []

    empty = client.post(
        "/api/v1/console/structured/query",
        json={
            "request_id": "structured-empty",
            "trace_id": "structured-empty-trace",
            "dataset_id": "oil_monthly_sales",
            "raw_query": "查询油品销售月报",
        },
    )
    assert empty.status_code == 200
    assert empty.json()["result"]["status"] == "no_match"

    written = client.post(
        "/api/v1/console/structured/imports",
        json={
            "request_id": "structured-write",
            "trace_id": "structured-write-trace",
            "dataset_id": "oil_monthly_sales",
            "aggregations": {"monthlysales": "123.45", "monthlyamount": "678.90"},
            "record_count": 2,
        },
    )
    assert written.status_code == 200
    assert written.json()["registration"]["source_kind"] == "manual_structured_input"

    queried = client.post(
        "/api/v1/console/structured/query",
        json={
            "request_id": "structured-query",
            "trace_id": "structured-query-trace",
            "dataset_id": "oil_monthly_sales",
            "raw_query": "查询油品销售月报",
        },
    )
    assert queried.status_code == 200
    assert queried.json()["result"]["status"] == "ok"
    assert "123.45" in str(queried.json())


def test_console_imports_a_user_selected_knowledge_file():
    client = _client()
    client.post(
        "/api/v1/console/auth/login",
        json={"username": "admin", "password": "brain-demo-2026"},
    )
    imported = client.post(
        "/api/v1/console/knowledge/imports/files",
        json={
            "request_id": "knowledge-file",
            "trace_id": "knowledge-file-trace",
            "filename": "manual-note.md",
            "media_kind": "markdown",
            "content_base64": base64.b64encode("# 手工资料\n这里是用户选择的知识内容。".encode("utf-8")).decode("ascii"),
            "permission_scope": ["public"],
        },
    )
    assert imported.status_code == 200
    assert imported.json()["job"]["source_kind"] == "file"
