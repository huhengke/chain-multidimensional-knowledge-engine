import httpx
from fastapi.testclient import TestClient

from platform_server.tests.test_platform import create_app, identity_server, login, create_source, wait_job


def test_correction_permissions_persistence_evidence_and_rollback(tmp_path, monkeypatch):
    app = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(identity_server))
    with TestClient(app, headers={'X-Platform-Request': '1'}) as client:
        login(client)
        source = create_source(client)
        job = client.post('/api/v1/platform/imports/text', json={'source_id': source,
            'title': '中医基础理论笔记', 'content': '# 阴阳\n温度举例，保养生命。\n\n# 精气\n宇宙万物形成本原。'}).json()
        wait_job(client, job['id'])
        url = '/api/v1/platform/assets/' + job['asset_id']
        before = client.get(url).json()
        assert before['asset']['classification']['domain'] == 'medical'
        assert before['knowledge']['publication']['status'] == 'pending_review'
        evidence = before['knowledge']['evidence']
        origin = client.get(url + '/content').content
        body = {'domain': 'humanities', 'reason': '资料主领域人工核对'}
        login(client, 'bob')
        assert client.post(url + '/classification', json=body).status_code == 404
        login(client, 'reader')
        assert client.post(url + '/classification', json=body).status_code == 403
        login(client)
        assert client.post(url + '/classification', json={**body, 'domain': 'invented'}).status_code == 422
        assert client.post(url + '/classification', json={**body, 'reason': '   '}).status_code == 422
        r = client.post(url + '/classification', json=body)
        assert r.status_code == 200, r.text
        assert r.json()['classification']['status'] == 'manual'
        after = client.get(url).json()
        assert after['asset']['status'] == 'pending_review'
        assert client.get(url + '/content').content == origin
        assert after['knowledge']['evidence'] == evidence
        assert {t['tree_id'] for t in client.get('/api/v1/platform/topology').json()['knowledge_topology']['trees']} == {'knowledge.humanities'}
        assert app.state.store.list('topology_history', 'alice')
        assert app.state.store.list('classification_correction', 'alice')[0]['reason'] == body['reason']
        # A failed replacement must leave the active workspace and durable state intact.
        current = app.state.ingest.workspace('alice')
        original_persist = app.state.ingest.persist_topology
        def fail(*args, **kwargs):
            raise ValueError('模拟重建提交失败')
        monkeypatch.setattr(app.state.ingest, 'persist_topology', fail)
        assert client.post(url + '/classification', json={'domain': None, 'reason': '恢复自动识别'}).status_code == 400
        assert app.state.ingest.workspace('alice') is current
        assert app.state.store.get('asset', 'alice', job['asset_id'])['classification']['domain'] == 'humanities'
        monkeypatch.setattr(app.state.ingest, 'persist_topology', original_persist)
    restarted = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(identity_server))
    with TestClient(restarted, headers={'X-Platform-Request': '1'}) as client:
        login(client)
        detail = client.get(url).json()
        assert detail['asset']['classification']['domain'] == 'humanities'
        assert detail['knowledge']['publication']['status'] == 'pending_review'
        assert detail['knowledge']['evidence'] == evidence
        r = client.post(url + '/classification', json={'domain': None, 'reason': '恢复自动识别'})
        assert r.status_code == 200, r.text
        assert r.json()['classification']['domain'] == 'medical'
        assert r.json()['classification']['status'] == 'automatic'
        assert r.json()['classification_override'] is None


def test_legacy_snapshot_is_archived_on_version_change(tmp_path):
    app = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(identity_server))
    with TestClient(app, headers={'X-Platform-Request': '1'}) as client:
        login(client)
        sid = app.state.ingest.topology_snapshot_id('alice')
        app.state.store.put('topology_snapshot', 'alice', {'id': sid, 'schema_version': 3,
            'projection': {'knowledge_topology': {'trees': [{'tree_id': 'knowledge.equipment'}]}}})
        source = create_source(client)
        j = client.post('/api/v1/platform/imports/text', json={'source_id': source, 'title': '药典', 'content': '临床用药与剂量。'}).json()
        wait_job(client, j['id'])
        history = app.state.store.list('topology_history', 'alice')
        assert len(history) == 1
        assert history[0]['projection']['knowledge_topology']['trees'][0]['tree_id'] == 'knowledge.equipment'
        app.state.ingest.persist_topology('alice', app.state.ingest.workspace('alice'))
        assert len(app.state.store.list('topology_history', 'alice')) == 1
