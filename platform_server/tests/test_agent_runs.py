import asyncio
import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from platform_server.agent_runs import evidence_context
from platform_server.knowledge_snapshot import KnowledgeSnapshotError
from platform_server.tests.test_platform import create_app, identity_server, login, configure_model, create_source, wait_job


def prepare(client):
    login(client)
    source = create_source(client)
    job = client.post('/api/v1/platform/imports/text', json={'source_id':source, 'title':'果园资料', 'content':'果园中的橙子在秋季采收，注意保持通风。'}).json()
    assert wait_job(client,job['id'])['status']=='succeeded'
    model = configure_model(client,'knowledge','chat')
    session = client.post('/api/v1/platform/sessions',json={'brain':'knowledge'}).json()['id']
    return session, {'query':'果园橙子如何采收？','model_id':model}


@pytest.mark.parametrize('failure,code', [(429,'MODEL_HTTP_429'),(502,'MODEL_HTTP_502'),(404,'MODEL_HTTP_404'),('timeout','MODEL_TIMEOUT'),('invalid','MODEL_RESPONSE_INVALID')])
def test_failure_keeps_question_evidence_trace_and_owner_boundary(tmp_path, failure, code):
    def provider(request):
        if request.url.host!='model': return identity_server(request)
        if failure=='timeout': raise httpx.ReadTimeout('secret-must-not-appear',request=request)
        if failure=='invalid': return httpx.Response(200,json={'choices':[]})
        return httpx.Response(failure,json={'error':{'message':'secret-must-not-appear'}})
    app = create_app(tmp_path,engine_url='http://engine',transport=httpx.MockTransport(provider))
    with TestClient(app,headers={'X-Platform-Request':'1'}) as client:
        session,body = prepare(client)
        response = client.post(f'/api/v1/platform/sessions/{session}/messages',json=body)
        assert response.status_code==200
        result=response.json()
        assert result['messages'][0]['content']==body['query']
        assert result['messages'][-1]['status']=='failed'
        run=result['run']
        assert run['status']=='failed' and run['error']['code']==code
        assert run['steps'][-1]['key']=='model' and run['steps'][-1]['status']=='failed'
        assert all(s['duration_ms']>=0 for s in run['steps'])
        assert run['evidence']['result']['evidence']
        assert 'secret-must-not-appear' not in response.text
        assert client.get(f'/api/v1/platform/sessions/{session}').json()['run']['id']==run['id']
        login(client,'bob')
        assert client.get(f"/api/v1/platform/runs/{run['id']}").status_code==404
        assert client.get(f'/api/v1/platform/sessions/{session}').status_code==404


def test_progress_visible_before_model_completes_and_duplicate_rejected(tmp_path):
    entered, release = threading.Event(),threading.Event()
    async def provider(request):
        if request.url.host!='model': return identity_server(request)
        entered.set()
        await asyncio.to_thread(release.wait, 10)
        return httpx.Response(200,json={'choices':[{'message':{'content':'秋季采收。'}}]})
    app=create_app(tmp_path,engine_url='http://engine',transport=httpx.MockTransport(provider))
    with TestClient(app,headers={'X-Platform-Request':'1'}) as client, ThreadPoolExecutor() as pool:
        session,body=prepare(client)
        future=pool.submit(client.post,f'/api/v1/platform/sessions/{session}/messages',json=body)
        try:
            assert entered.wait(5)
            running=client.get(f'/api/v1/platform/sessions/{session}').json()
            assert running['messages'][0]['content']==body['query']
            assert running['run']['steps'][-1]['status']=='running'
            assert client.post(f'/api/v1/platform/sessions/{session}/messages',json=body).status_code==409
        finally: release.set()
        result=future.result().json()
        assert len(result['messages'])==2 and result['run']['status']=='succeeded'


def test_async_run_can_pause_and_retract_before_model_returns(tmp_path):
    entered, release = threading.Event(), threading.Event()

    async def provider(request):
        if request.url.host != 'model':
            return identity_server(request)
        entered.set()
        await asyncio.to_thread(release.wait, 10)
        return httpx.Response(200, json={'choices': [{'message': {'content': '迟到回答不应写入会话'}}]})

    app = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(provider))
    with TestClient(app, headers={'X-Platform-Request':'1'}) as client:
        session, body = prepare(client)
        started = client.post(f'/api/v1/platform/sessions/{session}/runs', json=body)
        assert started.status_code == 200, started.text
        run_id = started.json()['run']['id']
        try:
            assert entered.wait(5)
            paused = client.post(f'/api/v1/platform/sessions/{session}/runs/{run_id}/pause')
            assert paused.status_code == 200 and paused.json()['status'] == 'paused'
            current = client.get(f'/api/v1/platform/sessions/{session}').json()
            assert current['run']['status'] == 'paused'
            assert len(current['messages']) == 1
            retracted = client.delete(f'/api/v1/platform/sessions/{session}/runs/{run_id}/question')
            assert retracted.status_code == 200 and retracted.json()['messages'] == []
        finally:
            release.set()


def test_knowledge_query_reports_warming_instead_of_replaying_corpus_in_request(tmp_path, monkeypatch):
    app = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(identity_server))
    with TestClient(app, headers={'X-Platform-Request':'1'}) as client:
        session, body = prepare(client)
        app.state.ingest.workspaces.clear()
        scheduled = []

        def missing_snapshot(*_args, **_kwargs):
            raise KnowledgeSnapshotError('simulated missing snapshot')

        monkeypatch.setattr(app.state.ingest.snapshots, 'load', missing_snapshot)
        monkeypatch.setattr(app.state.ingest, '_schedule_warm', lambda owner: scheduled.append(owner))
        response = client.post(f'/api/v1/platform/sessions/{session}/messages', json=body)
        assert response.status_code == 200, response.text
        run = response.json()['run']
        assert run['status'] == 'failed'
        assert run['error']['code'] == 'KNOWLEDGE_INDEX_WARMING'
        assert scheduled == ['alice']


def test_snapshot_compacts_duplicate_vectors_and_restores_projection(tmp_path, monkeypatch):
    app = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(identity_server))
    with TestClient(app, headers={'X-Platform-Request':'1'}) as client:
        session, body = prepare(client)
        full_workspace = app.state.ingest.full_workspace('alice')
        full_store = full_workspace.knowledge.brain.store
        assert not full_store.unit_vector
        assert full_store.trace_vector_dimensions
        assert all(trace.vector == ['0'] for trace in full_store.traces.values())

        expected = client.get('/api/v1/platform/topology').json()['knowledge_topology']['trees'][0]['vector_count']
        assert expected > 0
        app.state.ingest.workspaces.clear()
        from platform_server import knowledge_snapshot
        original_sha256 = knowledge_snapshot._sha256

        def query_projection_sha256(path):
            assert path.name != 'state.json', 'normal Agent mapping must not hash governance-only state'
            return original_sha256(path)

        monkeypatch.setattr(knowledge_snapshot, '_sha256', query_projection_sha256)
        restored = app.state.ingest.workspace('alice')
        assert restored.knowledge.brain.query_index.last_build_stats['vector_unit_count'] == expected
        assert restored.knowledge.brain.query_index.lexical_db_path
        assert not restored.knowledge.brain.query_index.token_units
        assert not restored.knowledge.brain.store.unit_vector
        # Query state deliberately omits trace history and heavyweight
        # topology.  It must nevertheless answer through the persisted
        # evidence, temporal and query-index projections.
        assert not restored.knowledge.brain.store.traces
        response = client.post(f'/api/v1/platform/sessions/{session}/messages', json=body)
        assert response.status_code == 200, response.text
        # The default test model endpoint intentionally has no chat handler;
        # assert the persisted retrieval projection independently from that
        # provider failure.
        run = response.json()['run']
        assert run['steps'][1]['key'] == 'retrieve'
        assert run['steps'][1]['status'] == 'succeeded'
        assert run['evidence']['result']['evidence']


def test_schema_three_snapshot_promotes_lexical_postings_without_replaying_source_documents(tmp_path, monkeypatch):
    app = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(identity_server))
    with TestClient(app, headers={'X-Platform-Request':'1'}) as client:
        prepare(client)
        record = app.state.store.get('knowledge_active_revision', 'alice', app.state.ingest.snapshots.record_id('alice'))
        snapshot_root = app.state.store.root / record['path']
        manifest_path = snapshot_root / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        query_index_path = snapshot_root / 'query-index.json'
        metadata = json.loads(query_index_path.read_text(encoding='utf-8'))
        postings = {}
        with sqlite3.connect(snapshot_root / 'lexical.sqlite3') as connection:
            for token, unit_id in connection.execute('SELECT token, unit_id FROM postings'):
                postings.setdefault(token, []).append(unit_id)
        metadata['token_units'] = postings
        query_index_path.write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            encoding='utf-8',
        )
        manifest['schema_version'] = 3
        manifest['files']['query-index.json'] = {
            'sha256': hashlib.sha256(query_index_path.read_bytes()).hexdigest(),
            'bytes': query_index_path.stat().st_size,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            encoding='utf-8',
        )
        app.state.ingest.workspaces.clear()
        monkeypatch.setattr(app.state.ingest, 'index', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('must not replay source documents')))

        workspace = app.state.ingest.workspace('alice')
        assert workspace.knowledge_revision != record['revision']
        assert workspace.knowledge.brain.query_index.last_build_stats['vector_unit_count'] > 0
        upgraded = app.state.store.get('knowledge_active_revision', 'alice', app.state.ingest.snapshots.record_id('alice'))
        assert upgraded['schema_version'] == 4
        assert (app.state.store.root / upgraded['path'] / 'lexical.sqlite3').is_file()


def test_context_is_valid_bounded_json_with_matched_citations():
    raw={'result':{'claims':[{'evidence_id':f'e{i}','value':'资料'*3000} for i in range(100)],
                   'evidence':[{'evidence_id':f'e{i}','ref':f'knowledge://{i}'} for i in range(100)],'warnings':[]},
         'route':{'large_internal_scores':'SHOULD_NOT_GO_TO_MODEL'*10000}}
    context=evidence_context(raw)
    encoded=json.dumps(context,ensure_ascii=False)
    assert len(encoded)<10100
    assert 'SHOULD_NOT_GO_TO_MODEL' not in encoded
    assert 0<len(context['evidence'])<=8
    assert all(e['ref']==f"knowledge://{e['evidence_id'][1:]}" and e['excerpt'] for e in context['evidence'])
    assert len(raw['result']['claims'])==100


def test_medical_answer_keeps_source_title_and_general_knowledge_lane(tmp_path):
    captured = []

    def provider(request):
        if request.url.host != 'model':
            return identity_server(request)
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '资料中记载的方剂线索需要回到原文核对。'}}]})

    app = create_app(tmp_path, engine_url='http://engine', transport=httpx.MockTransport(provider))
    with TestClient(app, headers={'X-Platform-Request':'1'}) as client:
        login(client)
        source = create_source(client)
        job = client.post('/api/v1/platform/imports/text', json={
            'source_id': source, 'title': '高血压资料.txt',
            'content': '文献记载高血压相关辨证和方剂线索，但需由医师辨证。',
        }).json()
        completed = wait_job(client, job['id'])
        assert completed['status'] in {'succeeded', 'pending_review'}
        if completed['status'] == 'pending_review':
            reviewed = client.post(f"/api/v1/platform/assets/{completed['asset_id']}/review", json={
                'action': 'approve', 'reason': '医疗资料引用边界测试', 'expected_status': 'pending_review',
            })
            assert reviewed.status_code == 200, reviewed.text
        model = configure_model(client, 'knowledge', 'chat')
        session = client.post('/api/v1/platform/sessions', json={'brain':'knowledge'}).json()['id']

        response = client.post(f'/api/v1/platform/sessions/{session}/messages', json={
            'query':'高血压是什么原理，应该如何开方？', 'model_id':model,
        })

        assert response.status_code == 200, response.text
        content = response.json()['messages'][-1]['content']
        assert '本次可追溯资料出处' in content
        assert '高血压资料.txt' in content
        assert '医疗资料使用边界' not in content
        prompt = captured[0]['messages'][0]['content']
        assert '通识补充（非本项目证据）' in prompt
        assert '参考剂量与用法' in prompt
        assert '不得据此为用户直接开方' not in prompt
        evidence = response.json()['messages'][-1]['evidence']['result']['evidence']
        assert evidence[0]['document_title'] == '高血压资料.txt'


def test_topology_exposes_real_concept_paths_units_and_versions(tmp_path):
    app=create_app(tmp_path,engine_url='http://engine',transport=httpx.MockTransport(identity_server))
    with TestClient(app,headers={'X-Platform-Request':'1'}) as client:
        prepare(client)
        response=client.get('/api/v1/platform/topology')
        assert response.status_code==200,response.text
        projection=response.json()['knowledge_topology']
        assert projection['trees'][0]['unit_count']>0
        assert projection['trees'][0]['concept_nodes']>0
        assert projection['trees'][0]['concepts'][0]['documents'][0]['title']=='果园资料.txt'
        assert projection['payload_counts']['middle_store']>0
        body=response.json()
        assert body['knowledge_cluster_total'] >= len(body['knowledge_clusters'])
        assert all('member_unit_ids' not in item and 'centroid' not in item for item in body['knowledge_clusters'])


def test_knowledge_tree_graph_is_independent_real_and_vector_safe(tmp_path):
    app=create_app(tmp_path,engine_url='http://engine',transport=httpx.MockTransport(identity_server))
    with TestClient(app,headers={'X-Platform-Request':'1'}) as client:
        prepare(client)
        summary=client.get('/api/v1/platform/topology').json()['knowledge_topology']
        tree_id=summary['trees'][0]['tree_id']
        response=client.get(f'/api/v1/platform/topology/knowledge/trees/{tree_id}?limit=500')
        assert response.status_code==200,response.text
        graph=response.json()
        assert graph['schema_version']==3 and graph['tree_id']==tree_id
        types={node['type'] for node in graph['nodes']}
        assert {'source','runtime','gate','concept','knowledge_unit','vector_trace','cluster','generation'} <= types
        assert graph['edges'] and graph['page']['total']==len(graph['nodes'])
        encoded=json.dumps(graph,ensure_ascii=False)
        assert 'prototype_vector' not in encoded and 'centroid' not in encoded
        assert not any('vector' in node.get('detail', {}) for node in graph['nodes'])
        assert any(node['type']=='runtime' and 'WeKnora' in node['label'] for node in graph['nodes'])

        page=client.get(f'/api/v1/platform/topology/knowledge/trees/{tree_id}?limit=3').json()
        assert len(page['nodes'])==3 and page['page']['next_cursor']==3
        assert client.get('/api/v1/platform/topology/knowledge/trees/not-owned').status_code==404


def test_topology_uses_durable_snapshot_without_rebuilding_workspace(tmp_path, monkeypatch):
    app=create_app(tmp_path,engine_url='http://engine',transport=httpx.MockTransport(identity_server))
    with TestClient(app,headers={'X-Platform-Request':'1'}) as client:
        prepare(client)
        expected=client.get('/api/v1/platform/topology').json()
        app.state.ingest.workspaces.clear()
        monkeypatch.setattr(app.state.ingest, 'workspace', lambda _owner: (_ for _ in ()).throw(AssertionError('must use snapshot')))
        restored=client.get('/api/v1/platform/topology')
        assert restored.status_code==200
        assert restored.json()==expected


def test_restart_marks_running_record_interrupted_once(tmp_path):
    app=create_app(tmp_path,engine_url='http://engine',transport=httpx.MockTransport(identity_server))
    with TestClient(app,headers={'X-Platform-Request':'1'}) as client:
        session,body=prepare(client)
        store=app.state.store
        import time
        from platform_server.store import now
        store.put('agent_run','alice',{'id':'interrupted-run','owner':'alice','session_id':session,'brain':'knowledge',
            'model':'test','status':'running','started_at':now(),'started_epoch':time.time(),'steps':[]})
        c=store.get('conversation','alice',session)
        c['last_run_id']='interrupted-run'
        store.put('conversation','alice',c)
        app.state.agent_runs.recover()
        app.state.agent_runs.recover()
        restored=client.get(f'/api/v1/platform/sessions/{session}').json()
        assert restored['run']['error']['code']=='INTERRUPTED'
        assert len(restored['messages'])==1


def test_independent_brain_session_management_feedback_branch_and_delete(tmp_path):
    app=create_app(tmp_path,engine_url='http://engine',transport=httpx.MockTransport(identity_server))
    with TestClient(app,headers={'X-Platform-Request':'1'}) as client:
        login(client)
        knowledge=client.post('/api/v1/platform/sessions',json={'brain':'knowledge','title':'知识会话'}).json()
        memory=client.post('/api/v1/platform/sessions',json={'brain':'memory','title':'时空会话'}).json()
        store=app.state.store
        run={'id':'run-source','owner':'alice','session_id':knowledge['id'],'brain':'knowledge',
             'model':'test','status':'succeeded','started_at':'2026-10-14T00:00:00+00:00',
             'ended_at':'2026-10-14T00:00:01+00:00','started_epoch':0,'elapsed_ms':1000,'steps':[]}
        store.put('agent_run','alice',run)
        source=store.get('conversation','alice',knowledge['id'])
        source.update(last_run_id=run['id'],messages=[
            {'id':'question-1','role':'user','content':'原问题','run_id':run['id']},
            {'id':'answer-1','role':'assistant','content':'原回答','run_id':run['id'],'feedback':None,
             'evidence':{'result':{'claims':[],'evidence':[]}}},
            {'id':'question-2','role':'user','content':'后续问题'},
        ])
        store.put('conversation','alice',source)

        assert client.post(f"/api/v1/platform/sessions/{memory['id']}/pin",json={'pinned':True}).status_code==200
        active=client.get('/api/v1/platform/sessions').json()['items']
        assert active[0]['id']==memory['id'] and active[0]['pinned'] is True
        renamed=client.post(f"/api/v1/platform/sessions/{knowledge['id']}/rename",json={'title':'  已重命名  '}).json()
        assert renamed['title']=='已重命名'
        assert client.post(f"/api/v1/platform/sessions/{knowledge['id']}/rename",json={'title':'   '}).status_code==422

        feedback=client.post(
            f"/api/v1/platform/sessions/{knowledge['id']}/messages/answer-1/feedback",
            json={'feedback':'up'},
        )
        assert feedback.status_code==200 and feedback.json()['feedback']=='up'
        branch=client.post(f"/api/v1/platform/sessions/{knowledge['id']}/messages/answer-1/branch")
        assert branch.status_code==201
        branch=branch.json()
        assert branch['brain']=='knowledge' and len(branch['messages'])==2
        assert branch['messages'][1]['content']=='原回答'
        assert branch['messages'][1]['id']!='answer-1'
        assert branch['messages'][1]['run_id']!='run-source'
        copied_run=store.get('agent_run','alice',branch['messages'][1]['run_id'])
        assert copied_run['source_run_id']=='run-source' and copied_run['session_id']==branch['id']

        memory_record=store.get('conversation','alice',memory['id'])
        memory_record['messages']=[
            {'id':'memory-question','role':'user','content':'昨晚发生了什么'},
            {'id':'memory-answer','role':'assistant','content':'依据时空证据回答','feedback':None},
        ]
        store.put('conversation','alice',memory_record)
        memory_feedback=client.post(
            f"/api/v1/platform/sessions/{memory['id']}/messages/memory-answer/feedback",
            json={'feedback':'down'},
        )
        assert memory_feedback.status_code==200 and memory_feedback.json()['feedback']=='down'
        memory_branch=client.post(f"/api/v1/platform/sessions/{memory['id']}/messages/memory-answer/branch")
        assert memory_branch.status_code==201 and memory_branch.json()['brain']=='memory'
        assert client.delete(f"/api/v1/platform/sessions/{memory_branch.json()['id']}").status_code==200

        archived=client.post(f"/api/v1/platform/sessions/{knowledge['id']}/archive",json={'archived':True})
        assert archived.status_code==200 and archived.json()['archived'] is True
        assert client.post(f"/api/v1/platform/sessions/{knowledge['id']}/messages/answer-1/branch").status_code==409
        assert knowledge['id'] not in [item['id'] for item in client.get('/api/v1/platform/sessions').json()['items']]
        assert knowledge['id'] in [item['id'] for item in client.get('/api/v1/platform/sessions?archived=true').json()['items']]

        login(client,'bob')
        assert client.post(f"/api/v1/platform/sessions/{branch['id']}/pin",json={'pinned':True}).status_code==404
        login(client)
        assert client.delete(f"/api/v1/platform/sessions/{knowledge['id']}").json()=={'deleted':True}
        assert store.get('agent_run','alice','run-source') is None
        assert store.get('agent_run','alice',branch['messages'][1]['run_id']) is not None
        assert client.delete(f"/api/v1/platform/sessions/{branch['id']}").json()=={'deleted':True}
        actions={event['action'] for event in store.events('alice')}
        assert {'session.pin','session.rename','session.feedback','session.branch','session.archive','session.delete'} <= actions


def test_running_session_blocks_context_changing_actions(tmp_path):
    app=create_app(tmp_path,engine_url='http://engine',transport=httpx.MockTransport(identity_server))
    with TestClient(app,headers={'X-Platform-Request':'1'}) as client:
        login(client)
        session=client.post('/api/v1/platform/sessions',json={'brain':'memory'}).json()
        store=app.state.store
        store.put('agent_run','alice',{'id':'running-run','owner':'alice','session_id':session['id'],
            'brain':'memory','model':'test','status':'running','started_at':'2026-10-14T00:00:00+00:00',
            'started_epoch':0,'steps':[]})
        session_record=store.get('conversation','alice',session['id'])
        session_record.update(last_run_id='running-run',messages=[
            {'id':'answer-running','role':'assistant','content':'处理中','run_id':'running-run'}])
        store.put('conversation','alice',session_record)
        assert client.post(f"/api/v1/platform/sessions/{session['id']}/pin",json={'pinned':True}).status_code==200
        assert client.post(f"/api/v1/platform/sessions/{session['id']}/rename",json={'title':'不能改'}).status_code==409
        assert client.post(f"/api/v1/platform/sessions/{session['id']}/archive",json={'archived':True}).status_code==409
        assert client.post(f"/api/v1/platform/sessions/{session['id']}/messages/answer-running/branch").status_code==409
        assert client.delete(f"/api/v1/platform/sessions/{session['id']}").status_code==409


def test_interrupted_import_with_saved_text_is_resumed_without_reextracting(tmp_path, monkeypatch):
    app=create_app(tmp_path,engine_url='http://engine',transport=httpx.MockTransport(identity_server))
    with TestClient(app,headers={'X-Platform-Request':'1'}) as client:
        login(client)
        source=create_source(client)
        job=client.post('/api/v1/platform/imports/text',json={'source_id':source,'title':'可恢复资料','content':'秋季果园采收。'}).json()
        assert wait_job(client,job['id'])['status']=='succeeded'
        store=app.state.store
        asset=store.get('asset','alice',job['asset_id'])
        asset['status']='queued'
        store.put('asset','alice',asset)
        interrupted=store.get('job','alice',job['id'])
        interrupted['status']='running'
        store.put('job','alice',interrupted)
        monkeypatch.setattr('platform_server.ingestion.extract_document',lambda *_: (_ for _ in ()).throw(AssertionError('must reuse extraction')))
        app.state.ingest.recover()
        assert wait_job(client,job['id'])['status']=='succeeded'
        assert any(event['action']=='import.recovered' and event['target']==job['id'] for event in store.events('alice'))
