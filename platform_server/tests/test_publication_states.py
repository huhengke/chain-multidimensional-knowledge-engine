import httpx
import pytest
from fastapi.testclient import TestClient

from platform_server.tests.test_platform import create_app, identity_server, login, create_source, wait_job


def imported(client, title='中医诊断笔记', content='中医诊断结合经络、针灸与临床证据。'):
    source = create_source(client)
    job = client.post('/api/v1/platform/imports/text', json={'source_id': source, 'title': title, 'content': content}).json()
    wait_job(client, job['id'])
    return job, '/api/v1/platform/assets/' + job['asset_id']


def assert_states(client, url, job, publication, processing='completed'):
    detail = client.get(url).json()
    asset = next(a for a in client.get('/api/v1/platform/assets').json()['items'] if a['id'] == job['asset_id'])
    task = next(j for j in client.get('/api/v1/platform/jobs').json()['items'] if j['id'] == job['id'])
    for value in (detail['asset'], asset, task, task['result']):
        assert value['publication_status'] == publication, value
        assert value['processing_status'] == processing, value
    assert detail['knowledge']['publication']['status'] == publication
    if publication == 'published':
        assert '待复核' not in task['progress']['message']
        assert not task['result']['review_reasons']
    return detail


def test_publication_lifecycle_and_restart(tmp_path):
    app = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(identity_server))
    with TestClient(app, headers={'X-Platform-Request': '1'}) as client:
        login(client)
        job, url = imported(client)
        first = assert_states(client, url, job, 'pending_review')
        original = client.get(url + '/content').content
        for action, expected in [('approve', 'published'), ('rollback', 'pending_review'), ('reject', 'rejected'), ('rollback', 'pending_review'), ('approve', 'published')]:
            response = client.post(url + '/review', json={'action': action, 'reason': '核对原文并记录发布决定'})
            assert response.status_code == 200, response.text
            detail = assert_states(client, url, job, expected)
            assert detail['knowledge']['evidence'] == first['knowledge']['evidence']
            found = client.post('/api/v1/platform/search/knowledge', json={'query': '中医诊断经络针灸'}).json()
            assert bool(found['result']['claims']) == (expected == 'published')
            assert bool(found['route']['selected']) == (expected == 'published')
            assert bool(found['passages']) == (expected == 'published')
        assert client.get(url + '/content').content == original
        panel = client.get(url + '/publication').json()
        assert len(panel['history']) == 5
        assert all(r['actor'] == 'alice' and r['created_at'] and r['reason'] for r in panel['history'])
        # Simulate the exact legacy bug: class unchanged, asset/job publication stale.
        asset = app.state.store.get('asset', 'alice', job['asset_id'])
        asset['publication_status'] = 'pending_review'
        app.state.store.put('asset', 'alice', asset)
        task = app.state.store.get('job', 'alice', job['id'])
        task['result']['publication_status'] = 'pending_review'
        task['progress']['message'] = '发布待复核'
        app.state.store.put('job', 'alice', task)
    restored = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(identity_server))
    with TestClient(restored, headers={'X-Platform-Request': '1'}) as client:
        login(client)
        ws = restored.state.ingest.workspace('alice')
        restored.state.ingest.persist_topology('alice', ws)
        assert_states(client, url, job, 'published')
        assert len(client.get(url + '/publication').json()['history']) == 5


@pytest.mark.parametrize('defect', ['graph_error', 'ocr_pages'])
def test_approval_preserves_partial_processing(tmp_path, defect):
    app = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(identity_server))
    with TestClient(app, headers={'X-Platform-Request': '1'}) as client:
        login(client)
        job, url = imported(client)
        asset = app.state.store.get('asset', 'alice', job['asset_id'])
        if defect == 'graph_error':
            asset['graph_error'] = '摘要生成失败'
        else:
            asset['pdf_extraction'] = {'unresolved_pages': [3]}
        app.state.store.put('asset', 'alice', asset)
        assert client.post(url + '/review', json={'action': 'approve', 'reason': '已知缺页保留记录'}).status_code == 200
        detail = assert_states(client, url, job, 'published', 'partial')
        assert detail['asset']['status'] == 'partial'


def test_review_permissions_stale_actions_and_blank_reason(tmp_path):
    app = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(identity_server))
    with TestClient(app, headers={'X-Platform-Request': '1'}) as client:
        login(client)
        job, url = imported(client)
        payload = {'action': 'approve', 'reason': '核对完成'}
        login(client, 'bob')
        assert client.get(url + '/publication').status_code == 404
        assert client.post(url + '/review', json=payload).status_code == 404
        login(client, 'reader')
        assert client.post(url + '/review', json=payload).status_code == 403
        login(client)
        assert client.post(url + '/review', json={**payload, 'reason': '   '}).status_code == 422
        assert client.post(url + '/review', json={**payload, 'expected_status': 'published'}).status_code == 400
        assert client.post(url + '/review', json=payload).status_code == 200
        assert client.post(url + '/review', json=payload).status_code == 400
        assert len(client.get(url + '/publication').json()['history']) == 1


def test_failed_review_commit_restores_committed_query_visibility(tmp_path, monkeypatch):
    app = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(identity_server))
    with TestClient(app, headers={'X-Platform-Request': '1'}) as client:
        login(client)
        job, url = imported(client)
        def fail(*args, **kwargs):
            raise ValueError('模拟持久化失败')
        monkeypatch.setattr(app.state.ingest, 'persist_topology', fail)
        assert client.post(url + '/review', json={'action': 'approve', 'reason': '检查事务失败'}).status_code == 400
        assert not app.state.store.list('review', 'alice')
        assert_states(client, url, job, 'pending_review')
        found = client.post('/api/v1/platform/search/knowledge', json={'query': '中医诊断'}).json()
        assert not found['result']['claims']
        assert not found['route']['selected']
        assert not found['passages']


def test_retry_partial_document_retains_rejection(tmp_path):
    app = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(identity_server))
    with TestClient(app, headers={'X-Platform-Request': '1'}) as client:
        login(client)
        job, url = imported(client)
        asset = app.state.store.get('asset', 'alice', job['asset_id'])
        asset['pdf_extraction'] = {'unresolved_pages': [3]}
        app.state.store.put('asset', 'alice', asset)
        assert client.post(url + '/review', json={'action': 'reject', 'reason': '缺页需要补齐'}).status_code == 200
        response = client.post('/api/v1/platform/jobs/' + job['id'] + '/retry')
        assert response.status_code == 200, response.text
        wait_job(client, job['id'])
        assert_states(client, url, job, 'rejected', 'partial')
        assert len(client.get(url + '/publication').json()['history']) == 1
