"""WeKnora Lite sidecar index for canonical knowledge units.

The platform database stays authoritative for publication, permissions and
audit.  This module mirrors published canonical units into WeKnora's native
SQLite FTS5 + sqlite-vec index, retrieves through its RRF hybrid search, and
maps native chunk hits back to canonical unit IDs.  The native index is
rebuildable: when the sidecar, its models or the mapping are unavailable the
caller falls back to the Python query projection instead of failing a query.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

import httpx

from platform_server.store import now

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
PART_LIMIT = 180_000
SEPARATOR = "\n\n"
# A 512-char native chunk can cover many canonical units.  Expand enough of
# them for the brain's lexical ordering without letting one chunk's score
# truncate the unit pool before the exact match is seen.
MAPPED_UNIT_LIMIT = 200
SYNC_TIMEOUT_SECONDS = 1800
SCHEMA_KEY = 'weknora-native-v1'
WIKI_MODEL_DESCRIPTION = '知识数据推理脑 Wiki 合成模型（跟随平台默认对话模型）'
WIKI_CONFIG = {
    'max_pages_per_ingest': 0,
    'extraction_granularity': 'standard',
    # One document per batch: the synthesis gateway serializes every LLM
    # call, so a multi-document batch would hold the Lite per-KB lock for
    # hours and starve its own follow-up triggers.
    'ingest_batch_size': 1,
    'ingest_map_parallel': 1,
    'ingest_reduce_parallel': 1,
    'ingest_max_inflight': 1,
}

_SCHEMA_READY: set[str] = set()
_SCHEMA_LOCK = threading.Lock()
_SYNC_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_SYNC_LOCKS_LOCK = threading.Lock()
_SIDECAR_LOCK = threading.Lock()
_SIDECARS: dict[str, 'WeKnoraSidecar'] = {}


class SidecarUnavailable(RuntimeError):
    pass


def owner_record_id(owner: str) -> str:
    return 'knowledge-active-revision-' + hashlib.sha256(owner.encode('utf-8')).hexdigest()[:24]


def _sidecar_base_url() -> str:
    return os.environ.get('WEKNORA_BASE_URL', 'http://127.0.0.1:18090').rstrip('/')


def _platform_base_url() -> str:
    return os.environ.get('PLATFORM_BASE_URL', 'http://127.0.0.1:8079').rstrip('/')


def _env_value(name: str) -> str:
    path = ROOT / '.runtime/weknora/weknora.env'
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            key, marker, value = line.partition('=')
            if marker and key.strip() == name:
                return value.strip()
    except OSError:
        pass
    return ''


def _chat_model_config(platform_store) -> dict | None:
    """Resolve the enabled default knowledge chat model for Wiki synthesis.

    The platform default is Codex.  A manually enabled DeepSeek row is only
    used after an operator deliberately makes it the default; this function
    never falls back to an arbitrary enabled vendor.
    """
    rows = [
        row for row in platform_store.list('model', 'system')
        if row.get('brain') == 'knowledge' and row.get('purpose') == 'chat' and row.get('enabled')
    ]
    model = next((row for row in rows if row.get('is_default')), None)
    if model is None:
        model = next((row for row in rows if row.get('deployment') == 'codex'), None)
    if model is None or not model.get('base_url') or not model.get('model'):
        return None
    try:
        api_key = platform_store.reveal(model['secret_ref']) if model.get('secret_ref') else ''
    except ValueError:
        return None
    return {
        'id': model.get('id'),
        'model': model['model'],
        'display_name': model.get('deployment_name') or model.get('name') or model['model'],
        'base_url': model['base_url'].rstrip('/'),
        'api_key': api_key,
    }


def sidecar_for(platform_store) -> 'WeKnoraSidecar':
    key = str(platform_store.root.resolve())
    chat_model = _chat_model_config(platform_store)
    with _SIDECAR_LOCK:
        client = _SIDECARS.get(key)
        if client is None:
            model_root = platform_store.root.parent / 'models'
            client = WeKnoraSidecar(
                base_url=_sidecar_base_url(),
                account_path=ROOT / '.runtime/weknora/service-account.json',
                model_base_url=_platform_base_url() + '/api/internal/knowledge-model/v1',
                model_api_key_path=model_root / '.api-key',
                chat_model=chat_model,
            )
            _SIDECARS[key] = client
        else:
            # The platform row can be edited while the process is running.
            client.chat_model = chat_model
        return client


class WeKnoraSidecar:
    """Minimal authenticated client for the project-owned WeKnora Lite server."""

    def __init__(self, base_url, account_path, model_base_url, model_api_key_path, chat_model=None):
        self.base_url = base_url.rstrip('/')
        self.account_path = Path(account_path)
        self.model_base_url = model_base_url.rstrip('/')
        self.model_api_key_path = Path(model_api_key_path)
        self.chat_model = chat_model
        self._token = ''
        self._token_lock = threading.Lock()
        self._health = (0.0, False)

    # ---- transport --------------------------------------------------------
    def healthy(self) -> bool:
        checked, ready = self._health
        if time.monotonic() - checked < 3:
            return ready
        try:
            response = httpx.get(self.base_url + '/health', timeout=1.5)
            ready = response.status_code == 200 and (response.json() or {}).get('status') == 'ok'
        except (httpx.HTTPError, ValueError):
            ready = False
        self._health = (time.monotonic(), ready)
        return ready

    def _request(self, method, path, body=None, *, params=None, authenticated=True, timeout=30, retry_auth=True):
        headers = {'Content-Type': 'application/json'}
        if authenticated:
            headers['Authorization'] = 'Bearer ' + self.login()
        try:
            response = httpx.request(method, self.base_url + path, params=params, json=body,
                                     headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise SidecarUnavailable(f'WeKnora sidecar request failed: {type(exc).__name__}') from exc
        if response.status_code == 401 and authenticated and retry_auth:
            self._token = ''
            return self._request(method, path, body, params=params, authenticated=authenticated,
                                 timeout=timeout, retry_auth=False)
        if response.status_code >= 400:
            detail = response.text[:400]
            raise SidecarUnavailable(f'WeKnora {method} {path} -> HTTP {response.status_code}: {detail}')
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise SidecarUnavailable('WeKnora sidecar returned invalid JSON') from exc

    @staticmethod
    def _data(response):
        return response.get('data') if isinstance(response, dict) else None

    # ---- account ----------------------------------------------------------
    def _account(self) -> dict:
        if self.account_path.is_file():
            try:
                value = json.loads(self.account_path.read_text(encoding='utf-8'))
                if value.get('email') and value.get('password'):
                    return value
            except (OSError, ValueError):
                pass
        return {
            'email': 'service@three-brain.local',
            'username': 'threebrain',
            'password': secrets.token_urlsafe(24),
        }

    def login(self) -> str:
        with self._token_lock:
            if self._token:
                return self._token
            account = self._account()
            token = ''
            try:
                token = self._request(
                    'POST', '/api/v1/auth/login',
                    {'email': account['email'], 'password': account['password']},
                    authenticated=False, retry_auth=False,
                ).get('token', '')
            except SidecarUnavailable:
                token = ''
            if not token:
                try:
                    self._request(
                        'POST', '/api/v1/auth/register',
                        {'username': account['username'], 'email': account['email'], 'password': account['password']},
                        authenticated=False, retry_auth=False,
                    )
                except SidecarUnavailable as exc:
                    if 'HTTP 409' not in str(exc) and 'exist' not in str(exc).lower():
                        raise
                token = self._request(
                    'POST', '/api/v1/auth/login',
                    {'email': account['email'], 'password': account['password']},
                    authenticated=False, retry_auth=False,
                ).get('token', '')
            if not token:
                raise SidecarUnavailable('WeKnora service account login failed')
            self.account_path.parent.mkdir(parents=True, exist_ok=True)
            self.account_path.write_text(json.dumps(account, ensure_ascii=False, indent=2), encoding='utf-8')
            self._token = token
            return token

    # ---- setup ------------------------------------------------------------
    def ensure_models(self) -> tuple[str, str]:
        if not self.model_api_key_path.is_file():
            raise SidecarUnavailable('本地知识模型服务凭据不存在；请先安装本地模型')
        try:
            api_key = self.model_api_key_path.read_text(encoding='ascii').strip()
        except OSError as exc:
            raise SidecarUnavailable('无法读取本地知识模型服务凭据') from exc
        existing = {item.get('name'): item for item in (self._data(self._request('GET', '/api/v1/models')) or [])}
        # The local model endpoint validates the canonical model name, and
        # WeKnora sends this record name verbatim in its OpenAI-compatible
        # request body.  Do not add an alias layer here.
        specs = [
            ('BAAI/bge-small-zh-v1.5', 'Embedding', {
                'embedding_parameters': {'dimension': 512},
                'spec': {'compat': {'send_encoding_format': False, 'accepts_truncate_prompt_tokens': False}},
            }),
            ('BAAI/bge-reranker-base', 'Rerank', {'extra_config': {'score_scale': 'logit'}}),
        ]
        ids = []
        for name, kind, extra in specs:
            parameters = {'base_url': self.model_base_url, 'api_key': api_key, 'provider': 'generic', **extra}
            body = {'name': name, 'display_name': name, 'type': kind, 'source': 'remote', 'parameters': parameters}
            current = existing.get(name)
            if current:
                self._request('PUT', f"/api/v1/models/{current['id']}", body)
                ids.append(current['id'])
            else:
                created = self._data(self._request('POST', '/api/v1/models', body)) or {}
                if not created.get('id'):
                    raise SidecarUnavailable('WeKnora model registration returned no id')
                ids.append(created['id'])
        return ids[0], ids[1]

    def ensure_chat_model(self) -> str | None:
        """Register the platform default chat model for Wiki synthesis.

        Wiki generation must never call a vendor that the operator did not
        select.  The row is updated in place so every enabled knowledge base
        keeps a stable ``synthesis_model_id``; credential rotation is applied
        through the dedicated credentials subresource because the model PUT
        endpoint deliberately never writes secrets.
        """
        model = self.chat_model
        if not model:
            return None
        existing = {item.get('name'): item for item in (self._data(self._request('GET', '/api/v1/models')) or [])}
        parameters = {
            'base_url': model['base_url'],
            'api_key': model['api_key'],
            'provider': 'generic',
            # The Codex gateway enforces a per-user concurrency limit and
            # answers 429 to parallel completions; serialize Wiki synthesis.
            'max_concurrency': 1,
            'spec': {'compat': {
                'max_tokens_field': 'max_completion_tokens',
                'supports_temperature': False,
                'thinking_format': 'none',
                'supports_store': False,
            }},
        }
        body = {
            'name': model['model'],
            'display_name': f"{model['display_name']} · {model['model']}",
            'description': WIKI_MODEL_DESCRIPTION,
            'type': 'KnowledgeQA',
            'source': 'remote',
            'parameters': parameters,
        }
        current = existing.get(model['model'])
        if current and current.get('type') != 'KnowledgeQA':
            current = None
        if current:
            model_id = current['id']
            self._request('PUT', f'/api/v1/models/{model_id}', body)
            self._request('PUT', f'/api/v1/models/{model_id}/credentials', {'api_key': model['api_key']})
        else:
            created = self._data(self._request('POST', '/api/v1/models', body)) or {}
            model_id = created.get('id')
            if not model_id:
                raise SidecarUnavailable('WeKnora Wiki model registration returned no id')
        return model_id

    def list_knowledge_bases(self) -> list[dict]:
        items: list[dict] = []
        for page in range(1, 11):
            body = self._request('GET', f'/api/v1/knowledge-bases?page={page}&page_size=100')
            batch = self._data(body) or []
            items.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < 100:
                break
        return items

    def knowledge_base(self, kb_id: str) -> dict | None:
        return self._data(self._request('GET', f'/api/v1/knowledge-bases/{kb_id}'))

    def ensure_knowledge_base(self, name: str, embedding_model_id: str, wiki_model_id: str | None = None) -> str:
        wiki_config = dict(WIKI_CONFIG)
        if wiki_model_id:
            wiki_config['synthesis_model_id'] = wiki_model_id
        for item in self.list_knowledge_bases():
            if item.get('name') != name:
                continue
            strategy = dict(item.get('indexing_strategy') or {})
            current_wiki = item.get('wiki_config') or {}
            needs_update = (
                strategy.get('wiki_enabled') is not True
                or any(current_wiki.get(key) != value for key, value in wiki_config.items())
            )
            if wiki_model_id and needs_update:
                strategy.update({'vector_enabled': True, 'keyword_enabled': True,
                                 'wiki_enabled': True, 'graph_enabled': False})
                body = {
                    'name': name,
                    'description': item.get('description') or '知识数据推理脑原生检索索引（可重建）',
                    'config': {
                        'chunking_config': item.get('chunking_config') or
                        {'chunk_size': 512, 'chunk_overlap': 64, 'separators': []},
                        'wiki_config': wiki_config,
                        'indexing_strategy': strategy,
                    },
                }
                self._request('PUT', f"/api/v1/knowledge-bases/{item['id']}", body)
            return item['id']
        body = {
            'name': name,
            'type': 'document',
            'description': '知识数据推理脑原生检索索引（可重建）',
            'embedding_model_id': embedding_model_id,
            'chunking_config': {'chunk_size': 512, 'chunk_overlap': 64, 'separators': []},
            'indexing_strategy': {
                'vector_enabled': True, 'keyword_enabled': True,
                'wiki_enabled': bool(wiki_model_id), 'graph_enabled': False,
            },
        }
        if wiki_model_id:
            body['wiki_config'] = wiki_config
        created = self._data(self._request('POST', '/api/v1/knowledge-bases', body)) or {}
        if not created.get('id'):
            raise SidecarUnavailable('WeKnora knowledge base creation returned no id')
        return created['id']

    def ensure_retrieval_config(self) -> None:
        """Balance RRF retrieval for this OCR-heavy classical Chinese corpus.

        WeKnora defaults to vector 0.7 / keyword 0.3.  The local benchmark
        showed exact classical phrases were outranked by generic vector hits;
        0.5/0.5 lifted unit recall@5 from 0.40 to 0.60 without hurting the
        document-level result.  Only untouched defaults are replaced so an
        operator's explicit weights always win.
        """
        current = self._data(self._request('GET', '/api/v1/tenants/kv/retrieval-config')) or {}
        if current.get('rrf_vector_weight') or current.get('rrf_keyword_weight'):
            return
        body = dict(current)
        body.update({'rrf_k': 60, 'rrf_vector_weight': 0.5, 'rrf_keyword_weight': 0.5})
        self._request('PUT', '/api/v1/tenants/kv/retrieval-config', body)

    def create_manual(self, kb_id: str, title: str, content: str) -> str:
        body = {'title': title, 'content': content, 'status': 'publish', 'channel': 'api'}
        created = self._data(self._request('POST', f'/api/v1/knowledge-bases/{kb_id}/knowledge/manual', body)) or {}
        if not created.get('id'):
            raise SidecarUnavailable('WeKnora manual knowledge creation returned no id')
        return created['id']

    # ---- cleanup / inspection --------------------------------------------
    def delete_knowledges(self, kb_id: str, knowledge_ids: list[str]) -> None:
        for offset in range(0, len(knowledge_ids), 100):
            ids = knowledge_ids[offset:offset + 100]
            try:
                self._request('POST', '/api/v1/knowledge/batch-delete', {'kb_id': kb_id, 'ids': ids})
            except SidecarUnavailable as exc:
                log.warning('WeKnora knowledge cleanup failed kb=%s ids=%d: %s', kb_id, len(ids), exc)

    def knowledge_statuses(self, kb_id: str, knowledge_ids: list[str]) -> dict[str, dict]:
        if not knowledge_ids:
            return {}
        params = [('ids', value) for value in knowledge_ids] + [('kb_id', kb_id)]
        try:
            items = self._data(self._request(
                'GET', '/api/v1/knowledge/batch', params=params, timeout=20)) or []
        except SidecarUnavailable as exc:
            log.warning('WeKnora batch status failed kb=%s: %s', kb_id, exc)
            items = []
        result = {item.get('id'): item for item in items if isinstance(item, dict)}
        for knowledge_id in knowledge_ids:
            if knowledge_id in result:
                continue
            try:
                item = self._data(self._request('GET', f'/api/v1/knowledge/{knowledge_id}', timeout=15))
            except SidecarUnavailable:
                item = None
            if isinstance(item, dict):
                result[knowledge_id] = item
        return result

    def chunks(self, knowledge_id: str) -> list[dict]:
        items: list[dict] = []
        for page in range(1, 101):
            body = self._request('GET', f'/api/v1/chunks/{knowledge_id}?page={page}&page_size=100', timeout=30)
            batch = self._data(body) or []
            items.extend(item for item in batch if isinstance(item, dict))
            total = int(body.get('total') or 0)
            if len(batch) < 100 or (total and len(items) >= total):
                break
        return items

    def hybrid_search(self, query_text: str, knowledge_base_ids: list[str], limit: int) -> tuple[list[dict], float]:
        started = time.perf_counter()
        payload = {
            'query_text': query_text,
            'match_count': max(1, min(100, limit)),
            'vector_threshold': 0,
            'keyword_threshold': 0,
            'knowledge_base_ids': list(knowledge_base_ids),
            'skip_context_enrichment': True,
        }
        body = self._request(
            'POST', f'/api/v1/knowledge-bases/{knowledge_base_ids[0]}/hybrid-search',
            payload, timeout=8,
        )
        hits = self._data(body) or []
        return [item for item in hits if isinstance(item, dict)], (time.perf_counter() - started) * 1000

    # ---- wiki / reprocessing --------------------------------------------
    def list_knowledge(self, kb_id: str) -> list[dict]:
        items: list[dict] = []
        for page in range(1, 101):
            body = self._request(
                'GET', f'/api/v1/knowledge-bases/{kb_id}/knowledge?page={page}&page_size=100', timeout=30)
            batch = self._data(body) or []
            items.extend(item for item in batch if isinstance(item, dict))
            total = int(body.get('total') or 0)
            if len(batch) < 100 or (total and len(items) >= total):
                break
        return items

    def reparse_knowledges(self, kb_id: str, knowledge_ids: list[str]) -> list[str]:
        task_ids: list[str] = []
        for offset in range(0, len(knowledge_ids), 200):
            ids = knowledge_ids[offset:offset + 200]
            if not ids:
                continue
            created = self._data(self._request(
                'POST', '/api/v1/knowledge/batch-reparse', {'kb_id': kb_id, 'ids': ids}, timeout=60)) or {}
            if created.get('task_id'):
                task_ids.append(created['task_id'])
        return task_ids

    def wiki_stats(self, kb_id: str) -> dict:
        return self._request('GET', f'/api/v1/knowledgebase/{kb_id}/wiki/stats', timeout=20)

    def wiki_pages(self, kb_id: str, *, page: int = 1, page_size: int = 20, page_type: str = '',
                   status: str = '', query: str = '', category_path: str = '') -> dict:
        params = {'page': max(1, page), 'page_size': max(1, min(100, page_size))}
        for key, value in (('page_type', page_type), ('status', status), ('query', query),
                           ('category_path', category_path)):
            if value:
                params[key] = value
        return self._request('GET', f'/api/v1/knowledgebase/{kb_id}/wiki/pages', params=params, timeout=30)

    def wiki_page(self, kb_id: str, slug: str) -> dict:
        return self._request(
            'GET', f"/api/v1/knowledgebase/{kb_id}/wiki/pages/{quote(slug, safe='/')}", timeout=20)

    def wiki_graph(self, kb_id: str, *, mode: str = 'overview', center: str = '', depth: int = 1,
                   limit: int = 500, types: str = '') -> dict:
        params = {'mode': mode, 'depth': max(1, min(3, depth)), 'limit': max(1, min(2000, limit))}
        if center:
            params['center'] = center
        if types:
            params['types'] = types
        return self._request('GET', f'/api/v1/knowledgebase/{kb_id}/wiki/graph', params=params, timeout=30)


_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


def _has_content(text: str) -> bool:
    """Reject PDF extraction debris that is only NUL/control characters."""
    return bool(_CONTROL_CHARS.sub('', text).strip())


def _unit_key(unit) -> tuple:
    if unit.chunk_order is not None:
        return (0, unit.chunk_order, unit.unit_id)
    locator = unit.locator
    return (1, locator.page or 0, str(locator.anchor or ''), unit.unit_id)


def _unit_spans(pieces, texts, prefix: str = '') -> tuple[str, list[tuple[int, int, str]]]:
    output = [prefix] if prefix else []
    cursor = len(prefix)
    spans = []
    for piece in pieces:
        if isinstance(piece, str):
            unit_id, start, end = piece, 0, None
        else:
            unit_id, start, end = piece
        text = texts.get(unit_id, '')[start:end]
        if not text:
            continue
        if spans:
            output.append(SEPARATOR)
            cursor += len(SEPARATOR)
        span_start = cursor
        output.append(text)
        cursor += len(text)
        spans.append((span_start, cursor, unit_id))
    return ''.join(output), spans


def _pack_parts(unit_ids, texts, title: str) -> list[tuple[list[tuple[str, int, int]], str]]:
    prefix = f'{title}\n\n' if title else ''
    limit = PART_LIMIT - len(prefix)
    parts: list[tuple[list[tuple[str, int, int]], str]] = []
    current: list[tuple[str, int, int]] = []
    size = 0
    for unit_id in unit_ids:
        text = texts.get(unit_id, '')
        if not text:
            continue
        pieces = (
            [(offset, min(offset + limit, len(text))) for offset in range(0, len(text), limit)]
            if len(text) > limit else [(0, len(text))]
        )
        for start, end in pieces:
            extra = end - start + (len(SEPARATOR) if current else 0)
            if current and size + extra > limit:
                parts.append((current, title))
                current, size = [], 0
                extra = end - start
            current.append((unit_id, start, end))
            size += extra
    if current:
        parts.append((current, title))
    return parts


def _map_span(spans, start, end) -> list[str]:
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return []
    if end <= start:
        return []
    overlaps = []
    for span_start, span_end, unit_id in spans:
        overlap = max(0, min(end, span_end) - max(start, span_start))
        if overlap:
            overlaps.append((overlap, span_end - span_start, unit_id))
    if not overlaps:
        return []
    overlaps.sort(key=lambda item: (-item[0], item[2]))
    chosen = []
    for overlap, length, unit_id in overlaps:
        if overlap >= max(1, length // 2):
            chosen.append(unit_id)
    return chosen or [overlaps[0][2]]


def _map_text(content: str, chunk_text: str, spans) -> list[str]:
    needle = (chunk_text or '').strip()
    if not needle:
        return []
    for size in (240, 120, 60):
        head = needle[:size]
        position = content.find(head)
        if position >= 0:
            return _map_span(spans, position, position + len(needle))
    return []


def _document_fingerprint(document, groups) -> str:
    value = {
        'title': document.title or '',
        'groups': [
            {'tree': tree, 'units': [[unit.unit_id, unit.version or ''] for unit in units]}
            for tree, units in groups
        ],
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


class WeKnoraIndex:
    """Per-owner native index and chunk-to-unit mapping."""

    def __init__(self, platform_store, owner: str, *, sidecar=None):
        self.store = platform_store
        self.owner = owner
        self.sidecar = sidecar or sidecar_for(platform_store)
        self._ensure_schema()

    def _ensure_schema(self):
        key = str(self.store.root.resolve())
        with _SCHEMA_LOCK:
            if key in _SCHEMA_READY:
                return
            with self.store.connect() as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS weknora_index_state(
                        owner TEXT PRIMARY KEY,
                        revision TEXT,
                        status TEXT NOT NULL,
                        error TEXT,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS weknora_documents(
                        owner TEXT NOT NULL,
                        document_id TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        trees TEXT NOT NULL,
                        kb_ids TEXT NOT NULL,
                        parts TEXT NOT NULL,
                        unit_count INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        error TEXT,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(owner, document_id)
                    );
                    CREATE TABLE IF NOT EXISTS weknora_chunk_units(
                        owner TEXT NOT NULL,
                        chunk_id TEXT NOT NULL,
                        knowledge_id TEXT NOT NULL,
                        document_id TEXT NOT NULL,
                        kb_id TEXT NOT NULL,
                        unit_id TEXT NOT NULL,
                        PRIMARY KEY(owner, chunk_id, unit_id)
                    );
                    CREATE INDEX IF NOT EXISTS weknora_chunk_units_unit
                        ON weknora_chunk_units(owner, unit_id);
                """)
            _SCHEMA_READY.add(key)

    # ---- query ------------------------------------------------------------
    def active_revision(self) -> str | None:
        record = self.store.get('knowledge_active_revision', self.owner, owner_record_id(self.owner))
        return record.get('revision') if record else None

    def _state(self) -> dict | None:
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM weknora_index_state WHERE owner=?', (self.owner,)).fetchone()
        return dict(row) if row else None

    def ready(self) -> bool:
        state = self._state()
        return bool(state and state['status'] == 'ready' and state['revision'] == self.active_revision())

    def search(self, query_text: str, *, tree_ids=None, limit: int = 40) -> dict | None:
        """Map the top ``limit`` native chunks to a bounded canonical unit pool.

        A chunk can contain many units.  Returning only ``limit`` units let
        one high-scoring chunk hide the exact unit inside a lower-ranked
        chunk, so the mapped pool has its own larger cap.
        """
        active = self.active_revision()
        state = self._state()
        if not active or not state or state['status'] != 'ready' or state['revision'] != active:
            return None
        if not self.sidecar.healthy():
            return None
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT document_id,trees,kb_ids FROM weknora_documents WHERE owner=? AND status='ready'",
                (self.owner,),
            ).fetchall()
        wanted = set(tree_ids or [])
        kb_ids: list[str] = []
        for row in rows:
            trees = {value for value in row['trees'].split(',') if value}
            if wanted and not trees & wanted:
                continue
            for kb_id in row['kb_ids'].split(','):
                if kb_id and kb_id not in kb_ids:
                    kb_ids.append(kb_id)
        if not kb_ids:
            return {'hits': [], 'stats': {'search_ms': 0.0, 'chunk_hits': 0, 'mapped_units': 0,
                                          'knowledge_bases': 0, 'revision': active}}
        try:
            hits, search_ms = self.sidecar.hybrid_search(query_text, kb_ids, limit)
        except SidecarUnavailable as exc:
            log.warning('Native WeKnora search unavailable owner=%s: %s', self.owner, exc)
            return None
        chunk_ids = [hit.get('id') for hit in hits if hit.get('id')]
        by_chunk: dict[str, list[str]] = defaultdict(list)
        if chunk_ids:
            marks = ','.join('?' for _ in chunk_ids)
            with self.store.connect() as db:
                mapped = db.execute(
                    f'SELECT chunk_id,unit_id FROM weknora_chunk_units WHERE owner=? AND chunk_id IN ({marks})',
                    (self.owner, *chunk_ids),
                ).fetchall()
            for row in mapped:
                by_chunk[row['chunk_id']].append(row['unit_id'])
        ordered: list[dict] = []
        seen = set()
        unit_limit = max(limit, MAPPED_UNIT_LIMIT)
        for hit in hits:
            score = float(hit.get('score') or 0)
            for unit_id in by_chunk.get(hit.get('id'), []):
                if unit_id in seen:
                    continue
                seen.add(unit_id)
                ordered.append({'id': unit_id, 'score': score, 'chunk_id': hit.get('id')})
                if len(ordered) >= unit_limit:
                    break
            if len(ordered) >= unit_limit:
                break
        return {
            'hits': ordered,
            'stats': {
                'search_ms': round(search_ms, 1),
                'chunk_hits': len(hits),
                'mapped_units': len(ordered),
                'knowledge_bases': len(kb_ids),
                'revision': active,
            },
        }

    # ---- wiki -------------------------------------------------------------
    def tree_knowledge_bases(self) -> dict[str, dict]:
        """Map active knowledge trees to their live WeKnora knowledge base."""
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT trees,kb_ids FROM weknora_documents WHERE owner=? AND status IN ('ready','processing')",
                (self.owner,),
            ).fetchall()
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for row in rows:
            kb_ids = [value for value in row['kb_ids'].split(',') if value]
            if not kb_ids:
                continue
            for tree in (value for value in row['trees'].split(',') if value):
                for kb_id in kb_ids:
                    counts[tree][kb_id] += 1
        if not counts:
            # The first sync creates the mapping only after every part is
            # submitted; fall back to the deterministic KB name prefix.
            prefix = f"three-brain::{hashlib.sha256(self.owner.encode()).hexdigest()[:12]}::"
            for item in self.sidecar.list_knowledge_bases():
                name = str(item.get('name') or '')
                if not name.startswith(prefix):
                    continue
                tree = name[len(prefix):].rsplit('::', 1)[0]
                counts[tree][item['id']] += 1
        try:
            known = {item['id']: item for item in self.sidecar.list_knowledge_bases()}
        except SidecarUnavailable:
            return {}
        result = {}
        for tree, kb_counts in counts.items():
            kb_id = max(kb_counts, key=lambda value: (kb_counts[value], value))
            if kb_id in known:
                result[tree] = known[kb_id]
        return result

    def wiki_knowledge_base(self, tree: str) -> dict:
        kb = self.tree_knowledge_bases().get(tree)
        if not kb:
            raise KeyError(tree)
        if not (kb.get('indexing_strategy') or {}).get('wiki_enabled'):
            raise ValueError('该知识树尚未启用 Wiki 自动生成')
        return kb

    def wiki_summary(self) -> dict:
        items = []
        for tree, kb in sorted(self.tree_knowledge_bases().items()):
            strategy = kb.get('indexing_strategy') or {}
            wiki_config = kb.get('wiki_config') or {}
            stats = None
            error = None
            if strategy.get('wiki_enabled'):
                try:
                    stats = self.sidecar.wiki_stats(kb['id'])
                except SidecarUnavailable as exc:
                    error = str(exc)
            items.append({
                'tree': tree,
                'knowledge_base_id': kb['id'],
                'knowledge_base_name': kb.get('name'),
                'wiki_enabled': bool(strategy.get('wiki_enabled')),
                'synthesis_model_id': wiki_config.get('synthesis_model_id') or '',
                'stats': stats,
                'error': error,
            })
        return {'items': items, 'revision': self.active_revision(), 'state': self._state()}

    def wiki_pages(self, tree: str, **kwargs) -> dict:
        kb = self.wiki_knowledge_base(tree)
        return self.sidecar.wiki_pages(kb['id'], **kwargs)

    def wiki_page(self, tree: str, slug: str) -> dict:
        kb = self.wiki_knowledge_base(tree)
        return self.sidecar.wiki_page(kb['id'], slug)

    def wiki_graph(self, tree: str, **kwargs) -> dict:
        kb = self.wiki_knowledge_base(tree)
        return self.sidecar.wiki_graph(kb['id'], **kwargs)

    # ---- synchronisation --------------------------------------------------
    @staticmethod
    def _desired(knowledge_store) -> dict[str, dict]:
        by_document = defaultdict(lambda: defaultdict(list))
        for unit in knowledge_store.units.values():
            if unit.chunk_level != 1 or not _has_content(knowledge_store.unit_text.get(unit.unit_id, '')):
                continue
            if not knowledge_store.is_published(unit.document_id):
                continue
            tree = knowledge_store.unit_tree.get(unit.unit_id, 'knowledge.general')
            by_document[unit.document_id][tree].append(unit)
        desired = {}
        for document_id, tree_units in by_document.items():
            document = knowledge_store.documents[document_id]
            groups = []
            for tree in sorted(tree_units):
                units = sorted(tree_units[tree], key=_unit_key)
                groups.append((tree, units))
            if groups:
                desired[document_id] = {
                    'document': document,
                    'groups': groups,
                    'fingerprint': _document_fingerprint(document, groups),
                    'unit_count': sum(len(units) for _, units in groups),
                }
        return desired

    def _mapping_count(self, document_id: str) -> int:
        with self.store.connect() as db:
            row = db.execute(
                'SELECT COUNT(*) AS n FROM weknora_chunk_units WHERE owner=? AND document_id=?',
                (self.owner, document_id),
            ).fetchone()
        return int(row['n'] if row else 0)

    def _set_state(self, status: str, revision: str | None, error: str | None = None):
        with self.store.connect() as db:
            db.execute(
                """INSERT INTO weknora_index_state(owner,revision,status,error,updated_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET
                   revision=excluded.revision,status=excluded.status,error=excluded.error,updated_at=excluded.updated_at""",
                (self.owner, revision, status, error, now()),
            )

    def _put_document(self, document_id: str, item: dict, status: str, error: str | None,
                      parts: list[dict], kb_ids: list[str], trees: list[str]):
        with self.store.connect() as db:
            db.execute(
                """INSERT INTO weknora_documents
                   (owner,document_id,fingerprint,trees,kb_ids,parts,unit_count,status,error,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner,document_id) DO UPDATE SET
                   fingerprint=excluded.fingerprint,trees=excluded.trees,kb_ids=excluded.kb_ids,
                   parts=excluded.parts,unit_count=excluded.unit_count,status=excluded.status,
                   error=excluded.error,updated_at=excluded.updated_at""",
                (self.owner, document_id, item['fingerprint'], ','.join(trees), ','.join(kb_ids),
                 json.dumps(parts, ensure_ascii=False), item['unit_count'], status, error, now()),
            )

    def _drop_document(self, document_id: str, row: dict | None):
        if row:
            try:
                parts = json.loads(row.get('parts') or '[]')
            except ValueError:
                parts = []
            grouped: dict[str, list[str]] = defaultdict(list)
            for part in parts:
                if part.get('knowledge_id') and part.get('kb_id'):
                    grouped[part['kb_id']].append(part['knowledge_id'])
            for kb_id, knowledge_ids in grouped.items():
                self.sidecar.delete_knowledges(kb_id, knowledge_ids)
        with self.store.connect() as db:
            db.execute('DELETE FROM weknora_chunk_units WHERE owner=? AND document_id=?', (self.owner, document_id))
            db.execute('DELETE FROM weknora_documents WHERE owner=? AND document_id=?', (self.owner, document_id))

    def sync(self, knowledge_store, *, progress=None) -> dict:
        lock_key = (str(self.store.root.resolve()), self.owner)
        with _SYNC_LOCKS_LOCK:
            lock = _SYNC_LOCKS.setdefault(lock_key, threading.Lock())
        if not lock.acquire(blocking=False):
            return {'status': 'busy'}
        try:
            return self._sync(knowledge_store, progress=progress)
        finally:
            lock.release()

    def _sync(self, knowledge_store, *, progress=None) -> dict:
        revision = self.active_revision()
        if not revision:
            return {'status': 'skipped', 'reason': 'no-active-revision'}
        desired = self._desired(knowledge_store)
        with self.store.connect() as db:
            existing = {
                row['document_id']: dict(row)
                for row in db.execute('SELECT * FROM weknora_documents WHERE owner=?', (self.owner,)).fetchall()
            }
            state = db.execute('SELECT * FROM weknora_index_state WHERE owner=?', (self.owner,)).fetchone()
        reusable = {
            document_id for document_id, item in desired.items()
            if (row := existing.get(document_id))
            and row['fingerprint'] == item['fingerprint']
            and row['status'] == 'ready'
            and self._mapping_count(document_id) > 0
        }
        changed = [document_id for document_id in desired if document_id not in reusable]
        removed = [document_id for document_id in existing if document_id not in desired]
        if not changed and not removed and state and state['status'] == 'ready' and state['revision'] == revision:
            return {'status': 'ready', 'documents': len(desired), 'units': sum(
                item['unit_count'] for item in desired.values()), 'reused': len(desired), 'indexed': 0}
        self._set_state('syncing', revision)
        if not self.sidecar.healthy():
            self._set_state('error', revision, 'WeKnora sidecar unavailable')
            return {'status': 'error', 'reason': 'sidecar-unavailable'}
        try:
            embedding_id, _rerank_id = self.sidecar.ensure_models()
        except SidecarUnavailable as exc:
            self._set_state('error', revision, str(exc))
            return {'status': 'error', 'reason': 'models-unavailable', 'error': str(exc)}
        # Wiki synthesis follows the platform's declared default chat model.
        # A registration failure must not take native retrieval down, so it is
        # reported and the index continues without Wiki for this pass.
        wiki_model_id = None
        ensure_chat = getattr(self.sidecar, 'ensure_chat_model', None)
        if ensure_chat:
            try:
                wiki_model_id = ensure_chat()
            except SidecarUnavailable as exc:
                log.warning('WeKnora Wiki model registration skipped owner=%s: %s', self.owner, exc)
        ensure_config = getattr(self.sidecar, 'ensure_retrieval_config', None)
        if ensure_config:
            try:
                ensure_config()
            except SidecarUnavailable as exc:
                log.warning('WeKnora RRF config update skipped owner=%s: %s', self.owner, exc)
        if progress:
            progress({'stage': 'native-index', 'message': '正在同步 WeKnora 原生索引',
                      'documents': len(desired), 'changed': len(changed), 'removed': len(removed)})

        for document_id in removed:
            self._drop_document(document_id, existing.get(document_id))
        for document_id in changed:
            if document_id in existing:
                self._drop_document(document_id, existing[document_id])

        pending: dict[str, list[dict]] = {}
        failures: dict[str, str] = {}
        try:
            for document_id in changed:
                item = desired[document_id]
                texts = knowledge_store.unit_text
                document = item['document']
                title = document.title or document_id
                parts_payload = []
                for tree, units in item['groups']:
                    kb_name = f"three-brain::{hashlib.sha256(self.owner.encode()).hexdigest()[:12]}::{tree}::{embedding_id[:8]}"
                    kb_id = self.sidecar.ensure_knowledge_base(kb_name, embedding_id, wiki_model_id)
                    for index, (pieces, part_title) in enumerate(_pack_parts([u.unit_id for u in units], texts, title), start=1):
                        content, _spans = _unit_spans(pieces, texts, f'{title}\n\n' if part_title else '')
                        knowledge_id = self.sidecar.create_manual(
                            kb_id, f'{title} · {tree.rsplit(".", 1)[-1]} {index}', content)
                        parts_payload.append({
                            'kb_id': kb_id, 'knowledge_id': knowledge_id, 'tree': tree,
                            'pieces': pieces,
                        })
                        pending.setdefault(document_id, []).append({
                            'kb_id': kb_id, 'knowledge_id': knowledge_id, 'pieces': pieces,
                        })
                self._put_document(
                    document_id, item, 'processing', None, parts_payload,
                    sorted({part['kb_id'] for part in parts_payload}),
                    sorted({tree for tree, _ in item['groups']}),
                )
                if progress:
                    progress({'stage': 'native-index', 'message': 'WeKnora 原生索引已提交，等待向量化',
                              'documents_done': len(reusable) + len(changed) - len(pending),
                              'documents_total': len(desired)})
        except SidecarUnavailable as exc:
            self._set_state('error', revision, str(exc))
            for document_id in pending:
                failures[document_id] = str(exc)
                existing_row = existing.get(document_id)
                if existing_row:
                    with self.store.connect() as db:
                        db.execute(
                            "UPDATE weknora_documents SET status='failed',error=?,updated_at=? WHERE owner=? AND document_id=?",
                            (str(exc), now(), self.owner, document_id),
                        )
            return {'status': 'error', 'reason': 'index-submit-failed', 'error': str(exc)}

        deadline = time.monotonic() + SYNC_TIMEOUT_SECONDS
        while pending and time.monotonic() < deadline:
            for document_id in list(pending):
                parts = pending[document_id]
                grouped: dict[str, list[str]] = defaultdict(list)
                for part in parts:
                    grouped[part['kb_id']].append(part['knowledge_id'])
                statuses = {}
                for kb_id, ids in grouped.items():
                    try:
                        statuses.update(self.sidecar.knowledge_statuses(kb_id, ids))
                    except SidecarUnavailable as exc:
                        failures[document_id] = str(exc)
                if document_id in failures:
                    pending.pop(document_id, None)
                    continue
                states = {statuses.get(part['knowledge_id'], {}).get('parse_status') for part in parts}
                if any(value in {'failed', 'cancelled'} for value in states):
                    message = next(
                        (statuses.get(part['knowledge_id'], {}).get('error_message') for part in parts
                         if statuses.get(part['knowledge_id'], {}).get('parse_status') in {'failed', 'cancelled'}),
                        'WeKnora 处理失败',
                    )
                    failures[document_id] = str(message or 'WeKnora 处理失败')
                    pending.pop(document_id, None)
                    continue
                # `finalizing` means WeKnora finished chunks/embeddings and
                # is still synthesising Wiki pages.  Native retrieval only
                # needs the chunks, so map immediately instead of waiting
                # out the 30-minute sync timeout.
                if states and states <= {'completed', 'finalizing'}:
                    try:
                        self._map_document(document_id, parts, knowledge_store, item=desired[document_id])
                    except SidecarUnavailable as exc:
                        failures[document_id] = str(exc)
                    pending.pop(document_id, None)
                    if document_id not in failures and progress:
                        progress({'stage': 'native-index', 'message': 'WeKnora 原生索引已就绪'})
            if pending:
                time.sleep(2)

        for document_id in pending:
            failures[document_id] = '等待 WeKnora 向量化超时'
        if failures:
            with self.store.connect() as db:
                for document_id, message in failures.items():
                    db.execute(
                        "UPDATE weknora_documents SET status='failed',error=?,updated_at=? WHERE owner=? AND document_id=?",
                        (message, now(), self.owner, document_id),
                    )
            summary = '；'.join(f'{key}: {value}' for key, value in list(failures.items())[:5])
            self._set_state('error', revision, summary)
            return {'status': 'error', 'reason': 'index-incomplete', 'error': summary,
                    'documents': len(desired), 'failed': len(failures)}
        self._set_state('ready', revision)
        return {
            'status': 'ready',
            'documents': len(desired),
            'units': sum(item['unit_count'] for item in desired.values()),
            'reused': len(reusable),
            'indexed': len(changed),
        }

    def remap_documents(self, knowledge_store, *, document_ids=None) -> dict:
        """Refresh chunk→unit mappings after a native reparse.

        Reparsing replaces every native chunk id while the canonical unit ids
        stay stable.  Without this pass the stored mapping would point at
        deleted chunks and native retrieval would return no units even though
        the index reports ready.
        """
        desired = self._desired(knowledge_store)
        with self.store.connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT * FROM weknora_documents WHERE owner=?', (self.owner,)).fetchall()]
        selected = set(document_ids or ())
        remapped, failed = [], []
        for row in rows:
            document_id = row['document_id']
            if (selected and document_id not in selected) or document_id not in desired:
                continue
            try:
                parts = json.loads(row.get('parts') or '[]')
                if not parts:
                    raise ValueError('索引记录缺少知识分片')
                self._map_document(document_id, parts, knowledge_store, item=desired[document_id])
            except (SidecarUnavailable, ValueError) as exc:
                failed.append({'document_id': document_id, 'error': str(exc)})
            else:
                remapped.append(document_id)
        return {'remapped': len(remapped), 'documents': len(remapped) + len(failed), 'failed': failed}

    def _map_document(self, document_id: str, parts: list[dict], knowledge_store, *, item: dict):
        document = item['document']
        title = document.title or document_id
        prefix = f'{title}\n\n' if title else ''
        rows = []
        for part in parts:
            # The spans are rebuilt from the same pieces and separators that
            # were submitted, so native start_at/end_at offsets stay canonical.
            content, spans = _unit_spans(part.get('pieces') or part.get('unit_ids'), knowledge_store.unit_text, prefix)
            chunks = self.sidecar.chunks(part['knowledge_id'])
            if not chunks:
                raise SidecarUnavailable('WeKnora 未返回任何分块')
            for chunk in chunks:
                unit_ids = _map_span(spans, chunk.get('start_at'), chunk.get('end_at'))
                if not unit_ids:
                    unit_ids = _map_text(content, chunk.get('content', ''), spans)
                for unit_id in unit_ids:
                    rows.append((self.owner, chunk['id'], part['knowledge_id'], document_id,
                                 part['kb_id'], unit_id))
        if not rows:
            raise SidecarUnavailable('WeKnora 分块无法映射回知识单元')
        with self.store.connect() as db:
            db.execute('DELETE FROM weknora_chunk_units WHERE owner=? AND document_id=?',
                       (self.owner, document_id))
            db.executemany(
                'INSERT OR REPLACE INTO weknora_chunk_units(owner,chunk_id,knowledge_id,document_id,kb_id,unit_id) VALUES(?,?,?,?,?,?)',
                rows,
            )
            db.execute(
                "UPDATE weknora_documents SET status='ready',error=NULL,updated_at=? WHERE owner=? AND document_id=?",
                (now(), self.owner, document_id),
            )


def synchronize(platform_store, owner: str, knowledge_store, *, sidecar=None, progress=None) -> dict:
    return WeKnoraIndex(platform_store, owner, sidecar=sidecar).sync(knowledge_store, progress=progress)
