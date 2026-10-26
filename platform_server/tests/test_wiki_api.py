import httpx
import pytest
from fastapi.testclient import TestClient

from platform_server import weknora_index
from platform_server.app import create_app
from platform_server.weknora_index import SidecarUnavailable


def identity_server(request):
    if request.url.path == "/api/v1/auth/login":
        return httpx.Response(200, json={"access_token": "alice"})
    if request.url.path == "/api/v1/auth/me":
        return httpx.Response(200, json={"username": "alice", "role": "admin", "display_name": "alice"})
    return httpx.Response(404)


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path, engine_url="http://engine", agent_url="http://agent",
                       knowledge_runtime="builtin-dev-v1",
                       transport=httpx.MockTransport(identity_server))


def login(client):
    response = client.post("/api/v1/auth/login", json={"username": "alice", "password": "local-test-password"})
    assert response.status_code == 200, response.text


def test_wiki_read_apis_forward_real_filters(app, monkeypatch):
    calls = []

    class FakeIndex:
        def __init__(self, store, owner):
            self.owner = owner

        def wiki_summary(self):
            calls.append(('summary', self.owner))
            return {'items': [{'tree': 'knowledge.medical', 'wiki_enabled': True}], 'revision': 'rev-1'}

        def wiki_pages(self, tree, **kwargs):
            calls.append(('pages', self.owner, tree, kwargs))
            return {'pages': [{'slug': 'entity/foo', 'title': 'Foo'}], 'total': 1,
                    'page': kwargs['page'], 'page_size': kwargs['page_size'], 'total_pages': 1}

        def wiki_page(self, tree, slug):
            calls.append(('page', self.owner, tree, slug))
            return {'slug': slug, 'title': 'Foo', 'content': '# Foo'}

        def wiki_graph(self, tree, **kwargs):
            calls.append(('graph', self.owner, tree, kwargs))
            return {'nodes': [{'slug': 'entity/foo'}], 'edges': [], 'meta': {'total': 1}}

    monkeypatch.setattr(weknora_index, 'WeKnoraIndex', FakeIndex)
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        summary = client.get('/api/v1/platform/knowledge/wiki')
        assert summary.status_code == 200 and summary.json()['items'][0]['tree'] == 'knowledge.medical'

        pages = client.get('/api/v1/platform/knowledge/wiki/pages', params={
            'tree': 'knowledge.medical', 'page': 2, 'page_size': 50,
            'page_type': 'entity', 'status': 'published', 'query': 'foo',
            'category_path': '经典/医案',
        })
        assert pages.status_code == 200 and pages.json()['total'] == 1
        assert calls[-1][0] == 'pages' and calls[-1][3] == {
            'page': 2, 'page_size': 50, 'page_type': 'entity', 'status': 'published',
            'query': 'foo', 'category_path': '经典/医案',
        }

        page = client.get('/api/v1/platform/knowledge/wiki/pages/entity/foo',
                          params={'tree': 'knowledge.medical'})
        assert page.status_code == 200 and page.json()['content'] == '# Foo'
        assert calls[-1] == ('page', 'alice', 'knowledge.medical', 'entity/foo')

        graph = client.get('/api/v1/platform/knowledge/wiki/graph', params={
            'tree': 'knowledge.medical', 'mode': 'ego', 'center': 'entity/foo',
            'depth': 2, 'limit': 2000, 'types': 'entity,concept',
        })
        assert graph.status_code == 200 and graph.json()['meta']['total'] == 1
        assert calls[-1] == ('graph', 'alice', 'knowledge.medical', {
            'mode': 'ego', 'center': 'entity/foo', 'depth': 2, 'limit': 2000,
            'types': 'entity,concept',
        })


def test_wiki_read_apis_map_tree_and_sidecar_failures(app, monkeypatch):
    class MissingIndex:
        def __init__(self, store, owner):
            pass

        def wiki_summary(self):
            raise SidecarUnavailable('sidecar down')

        def wiki_pages(self, tree, **kwargs):
            raise KeyError(tree)

        def wiki_graph(self, tree, **kwargs):
            raise KeyError(tree)

    monkeypatch.setattr(weknora_index, 'WeKnoraIndex', MissingIndex)
    with TestClient(app, headers={"X-Platform-Request": "1"}) as client:
        login(client)
        assert client.get('/api/v1/platform/knowledge/wiki').status_code == 503
        assert client.get('/api/v1/platform/knowledge/wiki/pages',
                          params={'tree': 'knowledge.nope'}).status_code == 404
        assert client.get('/api/v1/platform/knowledge/wiki/graph',
                          params={'tree': 'knowledge.nope'}).status_code == 404
