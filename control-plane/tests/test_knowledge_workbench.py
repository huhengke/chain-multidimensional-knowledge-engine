"""M4 受管导入、知识星团和原生工作台 API 验收。"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from api import create_app
from brains.knowledge import ManagedWeKnoraRuntime, RuntimeUnavailableError


def _owner_headers() -> dict[str, str]:
    return {"X-Principal": "alice", "X-Roles": "owner"}


def _create_text_job(client: TestClient, content: str, *, request_id: str = "import-1") -> str:
    response = client.post(
        "/api/v1/knowledge/imports/text",
        headers=_owner_headers(),
        json={
            "request_id": request_id,
            "trace_id": f"trace-{request_id}",
            "title": "设备维护资料",
            "content": content,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["job"]["job_id"]


def test_workbench_auto_import_cluster_search_evaluate_and_authz():
    client = TestClient(create_app())
    runtime = client.get("/api/v1/knowledge/runtime")
    assert runtime.status_code == 200
    assert runtime.json()["runtime_profile"] == "builtin-dev-v1"
    assert runtime.json()["runtime"]["upstream_status"] == "PENDING_LOCK"

    denied = client.post(
        "/api/v1/knowledge/imports/text",
        json={"request_id": "denied", "trace_id": "denied", "content": "资料"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "AUTHZ_DENIED"

    job_id = _create_text_job(client, "# 设备维护\n设备温度监控与润滑保养。")
    started = client.post(f"/api/v1/knowledge/imports/{job_id}/start", headers=_owner_headers())
    assert started.status_code == 200, started.text
    job = started.json()["job"]
    assert job["status"] == "published"
    assert [item["stage"] for item in job["steps"]] == [
        "draft", "validating", "queued", "parsing", "segmenting", "indexing",
        "clustering", "ready_to_publish", "proposing_graph", "published",
    ]
    assert job["index_version"]

    documents = client.get("/api/v1/knowledge/documents").json()["documents"]
    assert len(documents) == 1
    document_id = documents[0]["document"]["document_id"]
    detail = client.get(f"/api/v1/knowledge/documents/{document_id}")
    assert detail.status_code == 200
    units = detail.json()["units"]
    assert any(unit["chunk_level"] == 0 for unit in units)
    child = next(unit for unit in units if unit["chunk_level"] == 1)
    assert child["parent_unit_id"]
    assert child["index_version"] == job["index_version"]

    clusters = client.get("/api/v1/knowledge/clusters").json()["clusters"]
    assert clusters
    cluster = clusters[0]
    assert cluster["embedding_space_id"].startswith("builtin:")
    assert cluster["independent_source_count"] == 1
    assert cluster["member_unit_ids"]
    assert client.get(f"/api/v1/knowledge/clusters/{cluster['cluster_id']}").status_code == 200

    search = client.post(
        "/api/v1/knowledge/search",
        json={"request_id": "search-1", "trace_id": "trace-search", "query": "温度监控"},
    )
    assert search.status_code == 200
    route = search.json()["route"]
    assert child["unit_id"] in route["selected"]
    assert route["lexical_score"] and route["semantic_score"] and route["fusion_score"] and route["rerank_score"]
    assert route["cluster_id"] == cluster["cluster_id"]

    links = client.get("/api/v1/topology/cross-brain-links").json()["links"]
    assert {item["target_brain"] for item in links} == {"memory", "structured"}
    assert all(item["status"] == "candidate" for item in links)
    assert all("独立鉴权" in item["basis"] for item in links)

    evaluation = client.post(
        "/api/v1/knowledge/evaluations",
        headers=_owner_headers(),
        json={
            "request_id": "eval-1",
            "trace_id": "trace-eval",
            "index_version": job["index_version"],
            "k": 3,
            "cases": [{"query": "温度监控", "expected_unit_ids": [child["unit_id"]]}],
        },
    )
    assert evaluation.status_code == 200
    run = evaluation.json()["evaluation"]
    assert run["recall_at_k"] == "1"
    assert run["mrr"] == "1"
    assert client.get(f"/api/v1/knowledge/evaluations/{run['run_id']}").status_code == 200
    correction = client.post(
        f"/api/v1/knowledge/clusters/{cluster['cluster_id']}/corrections",
        headers=_owner_headers(),
        json={
            "request_id": "correction-1",
            "trace_id": "trace-correction",
            "action": "rebuild",
            "reason": "验证受控重建 generation",
        },
    )
    assert correction.status_code == 200
    assert correction.json()["cluster"]["generation"] == cluster["generation"] + 1
    assert correction.json()["cluster"]["status"] == "rebuild_requested"


def test_workbench_high_risk_review_and_unavailable_file_are_explicit():
    client = TestClient(create_app())
    job_id = _create_text_job(client, "# 合同条款\n最终裁决不得绕过合规复核。", request_id="risk-1")
    started = client.post(f"/api/v1/knowledge/imports/{job_id}/start", headers=_owner_headers())
    assert started.status_code == 200
    job = started.json()["job"]
    assert job["status"] == "pending_review"
    review = client.post(
        f"/api/v1/knowledge/documents/{job['document_id']}/review",
        headers=_owner_headers(),
        json={"request_id": "review-1", "trace_id": "trace-review", "action": "approve"},
    )
    assert review.status_code == 200
    assert review.json()["publication"]["status"] == "published"

    content = base64.b64encode(b"not a real pdf").decode("ascii")
    file_job = client.post(
        "/api/v1/knowledge/imports/files",
        headers=_owner_headers(),
        json={
            "request_id": "file-1",
            "trace_id": "trace-file",
            "filename": "manual.pdf",
            "media_kind": "pdf",
            "content_base64": content,
        },
    ).json()["job"]
    failed = client.post(f"/api/v1/knowledge/imports/{file_job['job_id']}/start", headers=_owner_headers())
    assert failed.status_code == 200
    assert failed.json()["job"]["status"] == "failed"
    assert failed.json()["job"]["error_code"] == "MODALITY_UNAVAILABLE"


def test_embedded_runtime_never_falls_back_before_real_lock():
    runtime = ManagedWeKnoraRuntime()
    assert runtime.metadata.runtime == "weknora-embedded-v1"
    assert runtime.metadata.health_status == "unavailable"
    with pytest.raises(RuntimeUnavailableError) as exc:
        runtime.health()
    assert exc.value.code == "RUNTIME_UNAVAILABLE"
