"""Execute the real compiled WeKnora bridge against a local protocol fixture.

Fixtures verify model I/O contracts, not semantic quality or a live provider.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from brains.knowledge.src.runtime_adapter import KnowledgeInput, RuntimeUnavailableError
from brains.knowledge.src.contract import KnowledgeSource
from platform_server.knowledge_runtime import EmbeddedRuntime
from platform_server.store import Store

@pytest.fixture
def provider():
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path,payload))
            if self.path.endswith('/embeddings'):
                response={'data':[{'index':0,'embedding':[0.5,0.1,0.4]}]}
            elif self.path.endswith('/rerank'):
                response={'results':[{'index':i,'relevance_score':1-i/10} for i in reversed(range(len(payload['documents'])))]}
            elif 'document profiling expert' in payload['messages'][0]['content']:
                response={'choices':[{'message':{'content':json.dumps({'summary':'智慧树存储并检索文档，证据来自原文。','topics':['智慧树']},ensure_ascii=False)}}]}
            else:
                response={'choices':[{'message':{'content':'[{"title":"智慧树","type":"Concept"}]'}}]}
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(response).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    yield f'http://127.0.0.1:{server.server_port}',calls
    server.shutdown();server.server_close();worker.join()

def runtime(tmp_path, provider):
    store=Store(tmp_path)
    for purpose in ('embedding','rerank','chat'):
        store.put('model','system',dict(id=purpose,brain='knowledge',purpose=purpose,model='contract-fixture',base_url=provider[0],enabled=True))
    return EmbeddedRuntime(store,'alice')

def test_real_bridge_chunking_vectors_retrieval_graph_metrics_restart(tmp_path,provider):
    rt=runtime(tmp_path,provider)
    assert rt.health()['upstream_commit']=='97796c637aa53713edc846bd0a39470d687f0fdf'
    text='# 智慧树\n'+('智慧树存储并检索文档，证据来自原文。\n'*140)+'\f# 第二页\n独立内容'
    parsed=rt.parse(KnowledgeInput(raw_id='document',source=KnowledgeSource(source_id='s',kind='document'),content=text))
    segments=rt.segment(parsed)
    assert len(segments)>3 and {s.page for s in segments}=={1,2}
    assert any(s.parent_text and s.parent_index>=0 for s in segments)
    vector=rt.embed('智慧树')
    assert vector==['0.500000000','0.100000000','0.400000000']
    restarted=EmbeddedRuntime(rt.store,'alice');assert restarted.embed('智慧树')==vector
    assert sum(path.endswith('/embeddings') for path,_ in provider[1])==1
    ranked=rt.retrieve('智慧树',[{'id':'a','text':'智慧树','lexical':'1','semantic':'1'},{'id':'b','text':'其他','lexical':'0','semantic':'0.6'}])
    assert [x['id'] for x in ranked]==['b','a']
    candidates=[{'id':f'c{i:03d}','text':f'候选 {i}','lexical':'1','semantic':str(1-i/100)} for i in range(80)]
    assert len(rt.retrieve('智慧树',candidates))==20
    assert len([body for path,body in provider[1] if path.endswith('/rerank')][-1]['documents'])==20
    assert len(rt.retrieve('智慧树',candidates,candidate_limit=80))==80
    assert '智慧树' in rt.concept_graph(segments[:1])
    wiki=rt.wiki(segments[:1])
    assert wiki['status']=='inferred' and '智慧树' in wiki['wiki']['summary']
    score=rt._invoke('evaluate',{'retrieved':[2,1],'relevant':[1],'k':2})
    assert score['recall']==1 and score['mrr']==0.5

def test_chat_model_prefers_the_default_deployment(tmp_path):
    store = Store(tmp_path)
    secret = store.secret('provider-key')
    store.put('model', 'system', dict(id='deepseek', brain='knowledge', purpose='chat', model='deepseek-model',
                                     base_url='http://deepseek/v1', enabled=True, deployment='deepseek',
                                     deployment_name='DeepSeek', is_default=False, secret_ref=secret))
    store.put('model', 'system', dict(id='codex', brain='knowledge', purpose='chat', model='codex-model',
                                     base_url='http://codex/v1', enabled=True, deployment='codex',
                                     deployment_name='Codex', is_default=True, secret_ref=secret))
    rt = EmbeddedRuntime(store, 'alice')
    assert rt.model('chat')['model'] == 'codex-model'


def test_missing_embedding_fails_without_hash_fallback(tmp_path):
    rt=EmbeddedRuntime(Store(tmp_path),'alice')
    with pytest.raises(RuntimeUnavailableError,match='embedding'):
        rt.embed('这是必须通过真实模型生成的向量')


def test_long_document_enrichment_covers_all_batches_and_reuses_completed_work(tmp_path, provider):
    from brains.knowledge.src.runtime_adapter import Segment
    rt = runtime(tmp_path, provider)
    segments = [Segment(text='智慧树保存原文证据。' * 100, anchor=f'page-{i}') for i in range(9)]
    graph = rt.concept_graph(segments)
    assert set(graph['智慧树']) == {f'page-{i}' for i in range(9)}
    graph_calls = [payload for _, payload in provider[1] if 'EntityTypes' in payload['messages'][0]['content']]
    assert len(graph_calls) > 1
    assert all(len(p['messages'][1]['content'].strip()) <= 1800 for p in graph_calls)
    before = len(provider[1])
    assert rt.concept_graph(segments) == graph
    assert len(provider[1]) == before
    result = rt.wiki(segments)
    assert result['coverage']['source_segments'] == 9 and result['coverage']['source_batches'] > 1
    before = len(provider[1])
    assert rt.wiki(segments) == result and len(provider[1]) == before


def test_rate_limited_graph_stops_before_remaining_batches(tmp_path, monkeypatch):
    from brains.knowledge.src.runtime_adapter import Segment
    rt = EmbeddedRuntime(Store(tmp_path), 'alice')
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(1)
        raise RuntimeUnavailableError('RUNTIME_OPERATION_FAILED', 'MODEL_HTTP_429', operation='graph')
    monkeypatch.setattr(rt, '_invoke', unavailable)
    with pytest.raises(RuntimeUnavailableError, match='429'):
        rt.concept_graph([Segment(text='正文' * 3000, anchor='one-page')])
    assert len(calls) == 1

def test_embedding_cache_does_not_cross_owners(tmp_path,provider):
    rt=runtime(tmp_path,provider);rt.embed('相同资料')
    EmbeddedRuntime(rt.store,'bob').embed('相同资料')
    assert len(provider[1])==2

def test_platform_import_search_evaluation_and_restart_use_compiled_runtime(tmp_path,provider):
    import httpx
    from fastapi.testclient import TestClient
    from platform_server.app import create_app
    from platform_server.tests.test_platform import identity_server,login,create_source,wait_job
    def app():
        return create_app(tmp_path,engine_url='http://engine',transport=httpx.MockTransport(identity_server))
    with TestClient(app(),headers={'X-Platform-Request':'1'}) as client:
        login(client)
        for purpose in ('embedding','rerank','chat'):
            r=client.put('/api/v1/platform/models/new',json={'name':purpose,'brain':'knowledge','purpose':purpose,'model':'protocol-fixture','base_url':provider[0],'enabled':True})
            assert r.status_code==200,r.text
        source=create_source(client)
        job=client.post('/api/v1/platform/imports/text',json={'source_id':source,'title':'智慧树资料','content':'# 智慧树\n智慧树保存知识文档并引用原文证据。'}).json()
        done=wait_job(client,job['id'])
        assert done['status']=='succeeded',done
        asset=client.get('/api/v1/platform/assets').json()['items'][0]
        assert asset['runtime_profile']=='weknora-embedded-v1'
        assert asset['indexing_complete'] and asset['indexed_units'] > 0 and asset['embedding_dimensions'] == 3
        assert '智慧树' in asset['wiki']['wiki']['summary']
        response=client.post('/api/v1/platform/search/knowledge',json={'query':'智慧树文档'})
        assert response.status_code==200,response.text
        assert response.json()['result']['evidence'],response.json()
        evaluated=client.post('/api/v1/platform/knowledge/evaluations',json={'query':'智慧树文档','expected_asset_ids':[asset['id']],'k':5})
        assert evaluated.status_code==200,evaluated.text
        assert evaluated.json()['metrics']['recall']==1
        assert evaluated.json()['retrieved_document_ids']==[asset['document_id']]
        login(client,'bob')
        assert client.post('/api/v1/platform/knowledge/evaluations',json={'query':'智慧树文档','expected_asset_ids':[asset['id']]}).status_code==404
    embedding_calls=sum(path.endswith('/embeddings') for path,_ in provider[1])
    with TestClient(app(),headers={'X-Platform-Request':'1'}) as client:
        login(client)
        result=client.post('/api/v1/platform/search/knowledge',json={'query':'智慧树文档'})
        assert result.status_code==200 and result.json()['result']['evidence'],result.text
        assert len(client.get('/api/v1/platform/knowledge/evaluations').json()['items'])==1
    assert sum(path.endswith('/embeddings') for path,_ in provider[1])==embedding_calls
