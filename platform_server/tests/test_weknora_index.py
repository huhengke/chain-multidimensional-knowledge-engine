import threading
from types import SimpleNamespace

import pytest

from platform_server import weknora_index as native
from platform_server.ingestion import Ingestion
from platform_server.local_models import RERANK, LocalModels
from platform_server.store import Store


class FakeSidecar:
    def __init__(self, content_by_knowledge=None):
        self.healthy_value = True
        self.created = []
        self.deleted = []
        self.content_by_knowledge = content_by_knowledge or {}
        self.search_hits = []
        self.chunk_generation = 1
        self.parse_status = 'completed'
        self.knowledge_bases = []

    def healthy(self):
        return self.healthy_value

    def ensure_models(self):
        return 'embedding-1', 'rerank-1'

    def ensure_chat_model(self):
        return 'wiki-model-1'

    def ensure_knowledge_base(self, name, embedding_id, wiki_model_id=None):
        self.knowledge_bases.append((name, embedding_id, wiki_model_id))
        return 'kb-1'

    def create_manual(self, kb_id, title, content):
        knowledge_id = f'knowledge-{len(self.created) + 1}'
        self.created.append({'id': knowledge_id, 'kb_id': kb_id, 'title': title, 'content': content})
        return knowledge_id

    def delete_knowledges(self, kb_id, knowledge_ids):
        self.deleted.append((kb_id, list(knowledge_ids)))

    def knowledge_statuses(self, kb_id, knowledge_ids):
        return {knowledge_id: {'id': knowledge_id, 'parse_status': self.parse_status}
                for knowledge_id in knowledge_ids}

    def chunks(self, knowledge_id):
        content = next(item['content'] for item in self.created if item['id'] == knowledge_id)
        prefix = 'chunk' if self.chunk_generation == 1 else f'chunk{self.chunk_generation}'
        return [{'id': f'{prefix}-{knowledge_id}', 'start_at': 0, 'end_at': len(content), 'content': content}]

    def hybrid_search(self, query_text, knowledge_base_ids, limit):
        return list(self.search_hits), 3.5


class FakeStore:
    def __init__(self):
        self.units = {}
        self.unit_text = {}
        self.unit_tree = {}
        self.documents = {}
        self.published = set()

    def is_published(self, document_id):
        return document_id in self.published

    def add(self, document_id, title, units, tree='knowledge.general'):
        self.documents[document_id] = SimpleNamespace(title=title)
        self.published.add(document_id)
        for order, (unit_id, text) in enumerate(units):
            self.units[unit_id] = SimpleNamespace(
                unit_id=unit_id, document_id=document_id, chunk_level=1,
                chunk_order=order, locator=SimpleNamespace(page=1, anchor=unit_id), version='v1',
            )
            self.unit_text[unit_id] = text
            self.unit_tree[unit_id] = tree


@pytest.fixture
def platform_store(tmp_path):
    return Store(tmp_path)


def test_packing_and_offset_mapping(monkeypatch):
    monkeypatch.setattr(native, 'PART_LIMIT', 20)
    texts = {'a': '1234567890', 'b': 'abcdefghij', 'c': 'K'}
    assert native._pack_parts(['a', 'b', 'c'], texts, 'T') == [
        ([('a', 0, 10)], 'T'), ([('b', 0, 10), ('c', 0, 1)], 'T'),
    ]
    content, spans = native._unit_spans(['a', 'b'], texts, 'T\n\n')
    assert content == 'T\n\n1234567890\n\nabcdefghij'
    assert spans == [(3, 13, 'a'), (15, 25, 'b')]
    assert native._map_span(spans, 0, 14) == ['a']
    assert native._map_span(spans, 10, 25) == ['b']
    assert native._map_text(content, 'abcdefghij', spans) == ['b']
    assert native._map_span(spans, None, None) == []


def test_chunk_mapping_keeps_every_covered_unit():
    texts = {str(index): 'x' for index in range(4)}
    _, spans = native._unit_spans(['0', '1', '2', '3'], texts)
    assert native._map_span(spans, 0, len('xxxxxxxxxx')) == ['0', '1', '2', '3']


def test_sidecar_balances_rrf_weights_only_for_untouched_defaults():
    sidecar = native.WeKnoraSidecar.__new__(native.WeKnoraSidecar)
    current = {'embedding_top_k': 0, 'vector_threshold': 0, 'keyword_threshold': 0,
               'rerank_top_k': 0, 'rerank_threshold': 0, 'rerank_model_id': ''}
    calls = []

    def request(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        return {'data': current} if method == 'GET' else {'data': body}

    sidecar._request = request
    sidecar.ensure_retrieval_config()
    put = calls[-1]
    assert put[0] == 'PUT' and put[1].endswith('/retrieval-config')
    assert put[2]['rrf_vector_weight'] == 0.5
    assert put[2]['rrf_keyword_weight'] == 0.5

    current.update({'rrf_vector_weight': 0.8, 'rrf_keyword_weight': 0.2})
    calls.clear()
    sidecar.ensure_retrieval_config()
    assert calls == [('GET', '/api/v1/tenants/kv/retrieval-config', None)]


def test_search_maps_units_beyond_the_native_chunk_limit(platform_store, monkeypatch):
    monkeypatch.setattr(native, 'MAPPED_UNIT_LIMIT', 10)
    owner = 'owner@example.com'
    knowledge = FakeStore()
    knowledge.add('doc-1', '文档一', [('u1', '一'), ('u2', '二'), ('u3', '三')])
    platform_store.put('knowledge_active_revision', owner, {
        'id': native.owner_record_id(owner), 'revision': 'rev-1', 'schema_version': 4,
        'path': 'unused', 'counts': {},
    })
    sidecar = FakeSidecar()
    index = native.WeKnoraIndex(platform_store, owner, sidecar=sidecar)
    index.sync(knowledge)
    sidecar.search_hits = [{'id': 'chunk-knowledge-1', 'score': 0.9}]

    found = index.search('一', limit=1)

    assert [hit['id'] for hit in found['hits']] == ['u1', 'u2', 'u3']
    assert found['stats']['chunk_hits'] == 1
    assert found['stats']['mapped_units'] == 3


def test_sync_indexes_published_units_and_search_maps_chunks(platform_store):
    owner = 'owner@example.com'
    knowledge = FakeStore()
    knowledge.add('doc-1', '文档一', [('u1', '第一段内容'), ('u2', '第二段内容')])
    knowledge.add('doc-2', '未发布', [('u3', '草稿内容')])
    knowledge.published.discard('doc-2')
    platform_store.put('knowledge_active_revision', owner, {
        'id': native.owner_record_id(owner), 'revision': 'rev-1', 'schema_version': 4,
        'path': 'unused', 'counts': {},
    })
    sidecar = FakeSidecar()
    index = native.WeKnoraIndex(platform_store, owner, sidecar=sidecar)

    result = index.sync(knowledge)
    assert result['status'] == 'ready'
    assert result['documents'] == 1 and result['indexed'] == 1 and result['units'] == 2
    assert len(sidecar.created) == 1
    assert '第一段内容' in sidecar.created[0]['content']
    assert [(call[1], call[2]) for call in sidecar.knowledge_bases] == [('embedding-1', 'wiki-model-1')]
    assert index._mapping_count('doc-1') == 2

    sidecar.search_hits = [{'id': 'chunk-knowledge-1', 'score': 0.87}]
    found = index.search('第一段', limit=10)
    assert found is not None
    assert [hit['id'] for hit in found['hits']] == ['u1', 'u2']
    assert found['hits'][0]['score'] == pytest.approx(0.87)
    assert found['stats']['mapped_units'] == 2

    assert index.search('第一段', tree_ids=['knowledge.medical'])['hits'] == []


def test_sync_maps_chunks_while_wiki_is_finalizing(platform_store):
    owner = 'owner@example.com'
    knowledge = FakeStore()
    knowledge.add('doc-1', '文档一', [('u1', '第一段内容'), ('u2', '第二段内容')])
    platform_store.put('knowledge_active_revision', owner, {
        'id': native.owner_record_id(owner), 'revision': 'rev-1', 'schema_version': 4,
        'path': 'unused', 'counts': {},
    })
    sidecar = FakeSidecar()
    sidecar.parse_status = 'finalizing'
    index = native.WeKnoraIndex(platform_store, owner, sidecar=sidecar)

    result = index.sync(knowledge)
    assert result['status'] == 'ready'
    assert index._mapping_count('doc-1') == 2


def test_remap_refreshes_chunk_ids_after_a_native_reparse(platform_store):
    owner = 'owner@example.com'
    knowledge = FakeStore()
    knowledge.add('doc-1', '文档一', [('u1', '第一段内容'), ('u2', '第二段内容')])
    platform_store.put('knowledge_active_revision', owner, {
        'id': native.owner_record_id(owner), 'revision': 'rev-1', 'schema_version': 4,
        'path': 'unused', 'counts': {},
    })
    sidecar = FakeSidecar()
    index = native.WeKnoraIndex(platform_store, owner, sidecar=sidecar)
    assert index.sync(knowledge)['status'] == 'ready'

    with platform_store.connect() as db:
        before = [row['chunk_id'] for row in db.execute(
            'SELECT chunk_id FROM weknora_chunk_units WHERE owner=?', (owner,))]
    sidecar.chunk_generation = 2
    assert index.remap_documents(knowledge) == {'remapped': 1, 'documents': 1, 'failed': []}

    with platform_store.connect() as db:
        after = [row['chunk_id'] for row in db.execute(
            'SELECT chunk_id FROM weknora_chunk_units WHERE owner=?', (owner,))]
    assert before and all(value.startswith('chunk-') for value in before)
    assert len(after) == 2 and all(value.startswith('chunk2-') for value in after)


def test_sync_rebuilds_changed_document_and_removes_unpublished(platform_store):
    owner = 'owner@example.com'
    knowledge = FakeStore()
    knowledge.add('doc-1', '文档一', [('u1', '旧内容')])
    platform_store.put('knowledge_active_revision', owner, {
        'id': native.owner_record_id(owner), 'revision': 'rev-1', 'schema_version': 4,
        'path': 'unused', 'counts': {},
    })
    sidecar = FakeSidecar()
    index = native.WeKnoraIndex(platform_store, owner, sidecar=sidecar)
    assert index.sync(knowledge)['indexed'] == 1

    knowledge.unit_text['u1'] = '新内容'
    knowledge.published.clear()
    rebuilt = index.sync(knowledge)
    assert rebuilt['status'] == 'ready'
    assert rebuilt['documents'] == 0
    assert sidecar.deleted and sidecar.deleted[0][1] == ['knowledge-1']
    assert index.search('新内容')['hits'] == []


def test_stale_revision_or_unhealthy_sidecar_disables_native_search(platform_store):
    owner = 'owner@example.com'
    knowledge = FakeStore()
    knowledge.add('doc-1', '文档一', [('u1', '内容')])
    platform_store.put('knowledge_active_revision', owner, {
        'id': native.owner_record_id(owner), 'revision': 'rev-1', 'schema_version': 4,
        'path': 'unused', 'counts': {},
    })
    sidecar = FakeSidecar()
    index = native.WeKnoraIndex(platform_store, owner, sidecar=sidecar)
    index.sync(knowledge)
    sidecar.search_hits = [{'id': 'chunk-knowledge-1', 'score': 0.5}]
    assert index.search('内容') is not None

    platform_store.put('knowledge_active_revision', owner, {
        'id': native.owner_record_id(owner), 'revision': 'rev-2', 'schema_version': 4,
        'path': 'unused', 'counts': {},
    })
    assert index.search('内容') is None

    platform_store.put('knowledge_active_revision', owner, {
        'id': native.owner_record_id(owner), 'revision': 'rev-1', 'schema_version': 4,
        'path': 'unused', 'counts': {},
    })
    sidecar.healthy_value = False
    assert index.search('内容') is None


def test_rerank_does_not_block_query_embedding(monkeypatch, tmp_path):
    models = LocalModels(tmp_path)
    rerank_started = threading.Event()
    release_rerank = threading.Event()

    class RerankModel:
        def rerank(self, query, documents, batch_size):
            rerank_started.set()
            assert release_rerank.wait(5)
            return [0.9 for _ in documents]

    class Vector(list):
        def tolist(self):
            return list(self)

    class EmbeddingModel:
        def embed(self, texts, batch_size):
            return [Vector([0.1, 0.2]) for _ in texts]

    monkeypatch.setattr(models, 'load',
                        lambda model: RerankModel() if model == RERANK else EmbeddingModel())
    results = []
    worker = threading.Thread(target=lambda: results.append(models.rerank('q', ['a'])))
    worker.start()
    assert rerank_started.wait(2)
    assert models.embed(['query']) == [[0.1, 0.2]]
    release_rerank.set()
    worker.join(5)
    assert results and results[0][0]['index'] == 0


def test_sync_scheduled_during_running_sync_triggers_followup(monkeypatch, tmp_path):
    monkeypatch.setattr(Ingestion, '_wait_for_platform', staticmethod(lambda: True))
    calls = []
    started = threading.Event()
    release = threading.Event()

    def fake_sync(store, owner, knowledge_store):
        calls.append(owner)
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        return {'status': 'ready'}

    monkeypatch.setattr(native, 'synchronize', fake_sync)
    ingestion = Ingestion(Store(tmp_path))
    monkeypatch.setattr(ingestion, 'workspace',
                        lambda owner: SimpleNamespace(knowledge=SimpleNamespace(brain=SimpleNamespace(store=object()))))
    worker = threading.Thread(target=ingestion._sidecar_sync, args=('owner',))
    worker.start()
    assert started.wait(2)
    ingestion._schedule_sidecar_sync('owner')
    release.set()
    worker.join(5)
    ingestion.close()
    assert calls == ['owner', 'owner']


def test_control_character_only_units_are_not_indexed(platform_store):
    owner = 'owner@example.com'
    knowledge = FakeStore()
    knowledge.add('doc-1', '文档一', [('u1', '正常内容'), ('u2', '\x00 \x00\n\x00'), ('u3', '更多内容')])
    platform_store.put('knowledge_active_revision', owner, {
        'id': native.owner_record_id(owner), 'revision': 'rev-1', 'schema_version': 4,
        'path': 'unused', 'counts': {},
    })
    sidecar = FakeSidecar()
    index = native.WeKnoraIndex(platform_store, owner, sidecar=sidecar)
    result = index.sync(knowledge)
    assert result['status'] == 'ready' and result['units'] == 2
    assert '正常内容' in sidecar.created[0]['content']
    assert '\x00' not in sidecar.created[0]['content']
    assert index._mapping_count('doc-1') == 2


def test_embedded_runtime_rerank_uses_local_endpoint(monkeypatch, tmp_path):
    import httpx
    from platform_server.knowledge_runtime import EmbeddedRuntime
    model_root = tmp_path / 'models'
    model_root.mkdir()
    (model_root / '.api-key').write_text('local-token', encoding='ascii')
    runtime = EmbeddedRuntime.__new__(EmbeddedRuntime)
    runtime.store = SimpleNamespace(root=tmp_path / 'platform')
    runtime.model = lambda purpose: {
        'base_url': 'http://127.0.0.1:8079/api/internal/knowledge-model/v1',
        'model': RERANK,
    }
    requests = []

    def fake_post(url, **kwargs):
        requests.append((url, kwargs))
        return httpx.Response(200, json={'results': [
            {'index': 2, 'relevance_score': 0.9},
            {'index': 0, 'relevance_score': 0.5},
        ]}, request=httpx.Request('POST', url))

    monkeypatch.setattr('platform_server.knowledge_runtime.httpx.post', fake_post)
    candidates = [('a', 'doc a'), ('b', 'doc b'), ('c', 'doc c')]
    assert runtime.rerank('q', candidates) == [candidates[2], candidates[0], candidates[1]]
    assert requests[0][1]['headers'] == {'Authorization': 'Bearer local-token'}
    assert requests[0][1]['json']['top_n'] == 3


def test_sliced_unit_offsets_are_preserved(monkeypatch):
    monkeypatch.setattr(native, 'PART_LIMIT', 6)
    parts = native._pack_parts(['a'], {'a': 'abcdefgh'}, '')
    assert parts == [([('a', 0, 6)], ''), ([('a', 6, 8)], '')]
    content, spans = native._unit_spans(parts[1][0], {'a': 'abcdefgh'})
    assert content == 'gh'
    assert native._map_span(spans, 0, 2) == ['a']


def test_chat_model_config_follows_the_platform_default(platform_store):
    deepseek = {
        'name': 'DeepSeek', 'brain': 'knowledge', 'purpose': 'chat', 'enabled': True,
        'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash',
        'deployment': 'deepseek', 'is_default': False,
        'secret_ref': platform_store.secret('deepseek-key'),
    }
    codex = {
        'name': 'Codex', 'brain': 'knowledge', 'purpose': 'chat', 'enabled': True,
        'base_url': 'http://codex.example/v1', 'model': 'gpt-5.6-sol',
        'deployment': 'codex', 'is_default': True,
        'secret_ref': platform_store.secret('codex-key'),
    }
    platform_store.put('model', 'system', deepseek)
    platform_store.put('model', 'system', codex)

    config = native._chat_model_config(platform_store)

    assert config['model'] == 'gpt-5.6-sol'
    assert config['base_url'] == 'http://codex.example/v1'
    assert config['api_key'] == 'codex-key'


def test_ensure_chat_model_updates_row_and_rotates_credentials():
    sidecar = native.WeKnoraSidecar.__new__(native.WeKnoraSidecar)
    sidecar.chat_model = {
        'id': 'model-codex', 'model': 'gpt-5.6-sol', 'display_name': 'Codex',
        'base_url': 'http://codex.example/v1', 'api_key': 'rotated-key',
    }
    calls = []

    def request(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        if method == 'GET':
            return {'data': [{'id': 'weknora-model-1', 'name': 'gpt-5.6-sol', 'type': 'KnowledgeQA'}]}
        return {'data': {'id': 'weknora-model-1'}}

    sidecar._request = request
    assert sidecar.ensure_chat_model() == 'weknora-model-1'
    update = next(call for call in calls if call[:2] == ('PUT', '/api/v1/models/weknora-model-1'))
    assert update[2]['parameters']['base_url'] == 'http://codex.example/v1'
    assert update[2]['parameters']['spec']['compat'] == {
        'max_tokens_field': 'max_completion_tokens',
        'supports_temperature': False,
        'thinking_format': 'none',
        'supports_store': False,
    }
    credentials = next(call for call in calls if call[1].endswith('/credentials'))
    assert credentials[2] == {'api_key': 'rotated-key'}


def test_ensure_chat_model_creates_the_wiki_model_when_missing():
    sidecar = native.WeKnoraSidecar.__new__(native.WeKnoraSidecar)
    sidecar.chat_model = {
        'id': 'model-codex', 'model': 'gpt-5.6-sol', 'display_name': 'Codex',
        'base_url': 'http://codex.example/v1', 'api_key': 'secret',
    }
    calls = []

    def request(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        if method == 'GET':
            return {'data': []}
        return {'data': {'id': 'weknora-model-new'}}

    sidecar._request = request
    assert sidecar.ensure_chat_model() == 'weknora-model-new'
    assert calls[-1][0] == 'POST' and calls[-1][1] == '/api/v1/models'
    assert calls[-1][2]['type'] == 'KnowledgeQA'
    assert calls[-1][2]['description'] == native.WIKI_MODEL_DESCRIPTION


def test_existing_knowledge_base_is_enabled_for_wiki_in_place():
    sidecar = native.WeKnoraSidecar.__new__(native.WeKnoraSidecar)
    item = {
        'id': 'kb-1', 'name': 'three-brain::owner::knowledge.medical::e68f281c',
        'description': '原生索引',
        'chunking_config': {'chunk_size': 512, 'chunk_overlap': 64, 'separators': []},
        'indexing_strategy': {
            'vector_enabled': True, 'keyword_enabled': True,
            'wiki_enabled': False, 'graph_enabled': False,
        },
        'wiki_config': None,
    }
    calls = []
    sidecar.list_knowledge_bases = lambda: [item]

    def request(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        return {'data': {}}

    sidecar._request = request
    assert sidecar.ensure_knowledge_base(item['name'], 'embedding-1', 'wiki-model-1') == 'kb-1'
    assert len(calls) == 1 and calls[0][0] == 'PUT' and calls[0][1] == '/api/v1/knowledge-bases/kb-1'
    config = calls[0][2]['config']
    assert config['wiki_config']['synthesis_model_id'] == 'wiki-model-1'
    assert config['wiki_config']['ingest_max_inflight'] == 1
    assert config['indexing_strategy']['wiki_enabled'] is True
    assert config['indexing_strategy']['graph_enabled'] is False
    assert config['chunking_config'] == item['chunking_config']


def test_knowledge_base_is_created_with_wiki_config():
    sidecar = native.WeKnoraSidecar.__new__(native.WeKnoraSidecar)
    calls = []
    sidecar.list_knowledge_bases = lambda: []

    def request(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        return {'data': {'id': 'kb-new'}}

    sidecar._request = request
    assert sidecar.ensure_knowledge_base('three-brain::owner::knowledge.general::e68f281c',
                                         'embedding-1', 'wiki-model-1') == 'kb-new'
    body = calls[-1][2]
    assert calls[-1][0] == 'POST' and calls[-1][1] == '/api/v1/knowledge-bases'
    assert body['indexing_strategy']['wiki_enabled'] is True
    assert body['wiki_config']['synthesis_model_id'] == 'wiki-model-1'
    assert body['chunking_config']['chunk_size'] == 512

