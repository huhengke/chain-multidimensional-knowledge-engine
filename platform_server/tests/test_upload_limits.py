import httpx
from fastapi.testclient import TestClient

from platform_server.ingestion import Ingestion
from platform_server.tests.test_platform import create_app, create_source, identity_server, login


def test_upload_limit_is_per_file_and_rejection_names_file(tmp_path, monkeypatch):
    submitted = []

    def submit(_self, _owner, _source, filename, data, _metadata):
        submitted.append((filename, len(data)))
        return {"id": f"job-{len(submitted)}", "status": "queued"}

    monkeypatch.setattr(Ingestion, "submit", submit)
    app = create_app(tmp_path, engine_url="http://engine", transport=httpx.MockTransport(identity_server))
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        assert client.get("/api/v1/platform/imports/limits").status_code == 401
        login(client)
        source = create_source(client)
        assert client.put("/api/v1/platform/settings", json={"upload_limit_mb": 1}).status_code == 200
        assert client.get("/api/v1/platform/imports/limits").json()["per_file_bytes"] == 1048576
        for i in range(17):
            response = client.post("/api/v1/platform/imports/files", data={"source_id": source},
                                   files={"file": (f"part-{i}.txt", b"a" * 65536)})
            assert response.status_code == 200, response.text
        assert sum(size for _, size in submitted) > 1048576
        response = client.post("/api/v1/platform/imports/files", data={"source_id": source},
                               files={"file": ("too-large.pdf", b"a" * 1048577)})
        assert response.status_code == 413
        assert response.json()["code"] == "FILE_TOO_LARGE"
        assert response.json()["filename"] == "too-large.pdf"
        assert response.json()["limit_bytes"] == 1048576
        assert response.json()["file_bytes"] == 1048577
        assert len(submitted) == 17
        assert client.post("/api/v1/platform/imports/files", data={"source_id": source},
                           files={"file": ("exact.txt", b"a" * 1048576)}).status_code == 200
        assert client.post("/api/v1/platform/imports/files", data={"source_id": source},
                           files={"file": ("empty.txt", b"")}).status_code == 400
        login(client, "reader")
        assert client.get("/api/v1/platform/imports/limits").status_code == 200
        assert client.post("/api/v1/platform/imports/files", data={"source_id": source},
                           files={"file": ("no.txt", b"text")}).status_code == 403
