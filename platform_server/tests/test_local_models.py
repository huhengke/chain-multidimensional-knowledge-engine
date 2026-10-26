import os

import httpx
from fastapi.testclient import TestClient

from platform_server.app import create_app
from platform_server.local_models import EMBEDDING, PREFIX, LocalModels
from platform_server.tests.test_platform import identity_server


def test_internal_models_require_service_credential_and_keep_browser_write_guard(tmp_path, monkeypatch):
    monkeypatch.setattr('platform_server.local_models.local_service_token', lambda _: 'isolated-local-model-token')
    app = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(identity_server))
    calls = []
    monkeypatch.setattr(LocalModels, 'embed', lambda _, texts: calls.append(texts) or [[.4, .6] for t in texts])
    body = {'model': EMBEDDING, 'input': ['知识文档']}
    with TestClient(app) as client:
        assert client.post(PREFIX + '/embeddings', json=body).status_code == 401
        assert client.post(PREFIX + '/embeddings', json=body, headers={'Authorization':'Bearer bad'}).status_code == 401
        auth = {'Authorization': 'Bearer isolated-local-model-token'}
        response = client.post(PREFIX + '/embeddings', json=body, headers=auth)
        assert response.status_code == 200 and response.json()['data'][0]['index'] == 0
        assert len(calls) == 1
        assert client.post(PREFIX + '/embeddings', json={**body, 'input': ['x'] * 33}, headers=auth).status_code == 422
        assert client.post(PREFIX + '/embeddings', json=body, headers={**auth,'Origin':'https://elsewhere.example'}).status_code == 403
        assert client.post('/api/v1/auth/login', json={'username':'alice','password':'p'}).status_code == 403


def test_modified_local_artifact_is_rejected_before_loading(tmp_path):
    import json
    import pytest
    folder = tmp_path / 'model'
    folder.mkdir()
    (folder / 'model.onnx').write_bytes(b'changed weights')
    (tmp_path / 'manifest.json').write_text(json.dumps({EMBEDDING: {'path':'model','files':{'model.onnx':'0' * 64}}}))
    with pytest.raises(ValueError, match='校验失败'):
        LocalModels(tmp_path).load(EMBEDDING)


def test_inference_probe_rejects_zero_vectors_nan_and_duplicate_rerank_ids():
    from platform_server.model_provider import validate_inference_probe
    for vector in ([0, 0], [float('nan'), 1], ['.1', '.2'], [1]):
        assert not validate_inference_probe('embedding', 200, 'm', {'data':[{'embedding':vector}]})[0]
    assert validate_inference_probe('embedding', 200, 'm', {'data':[{'embedding':[.1,.2]}]})[0]
    assert not validate_inference_probe('rerank', 200, 'm', {'results':[{'index':0,'relevance_score':.9}]*2})[0]
