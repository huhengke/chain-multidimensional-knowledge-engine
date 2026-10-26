"""Project-local, offline ONNX models exposed to the embedded WeKnora bridge."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from platform_server.contracts import Input

PREFIX = '/api/internal/knowledge-model/v1'
EMBEDDING = 'BAAI/bge-small-zh-v1.5'
RERANK = 'BAAI/bge-reranker-base'


def local_service_token(root, *, create=False):
    path = Path(root) / '.api-key'
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, 'w', encoding='ascii') as output:
                output.write(secrets.token_urlsafe(48))
    return path.read_text(encoding='ascii').strip() if path.is_file() else ''


def file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class LocalModels:
    def __init__(self, root):
        self.root = Path(root)
        self.models = {}
        # Loading is rare; inference is the hot path.  Embedding and rerank
        # use independent gates so a long rerank no longer blocks the query
        # embedding that WeKnora's native hybrid search needs first.
        self.load_lock = threading.Lock()
        self.inference_locks = {EMBEDDING: threading.Lock(), RERANK: threading.Lock()}

    def manifest(self):
        path = self.root / 'manifest.json'
        if not path.exists():
            raise ValueError('本地知识模型尚未安装，请运行 scripts/setup-knowledge-local.ps1')
        return json.loads(path.read_text(encoding='utf-8'))

    def load(self, model):
        with self.load_lock:
            if model in self.models:
                return self.models[model]
            record = self.manifest().get(model)
            if not record or model not in {EMBEDDING, RERANK}:
                raise ValueError('未安装所选本地模型')
            folder = (self.root / record['path']).resolve()
            if not folder.is_relative_to(self.root.resolve()):
                raise ValueError('本地模型路径无效')
            for name, expected in record['files'].items():
                path = (folder / name).resolve()
                if not path.is_relative_to(folder) or not path.is_file() or file_digest(path) != expected:
                    raise ValueError('本地模型文件校验失败，请修复模型文件；禁止混用不同向量空间')
            from fastembed import TextEmbedding
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            cls = TextEmbedding if model == EMBEDDING else TextCrossEncoder
            loaded = cls(model, specific_model_path=str(folder), threads=4, local_files_only=True)
            self.models[model] = loaded
            return loaded

    def embed(self, texts):
        model = self.load(EMBEDDING)
        with self.inference_locks[EMBEDDING]:
            return [v.tolist() for v in model.embed(texts, batch_size=8)]

    def rerank(self, query, documents):
        model = self.load(RERANK)
        with self.inference_locks[RERANK]:
            values = list(model.rerank(query, documents, batch_size=min(16, len(documents))))
        return sorted([{'index': i, 'relevance_score': float(value)} for i, value in enumerate(values)], key=lambda r: -r['relevance_score'])


class EmbeddingInput(Input):
    model: str
    input: list[str] = Field(min_length=1, max_length=32)


class RerankInput(Input):
    model: str
    query: str = Field(min_length=1, max_length=16000)
    documents: list[str] = Field(min_length=1, max_length=100)
    top_n: int = Field(default=100, ge=1, le=100)


def router(root):
    models = LocalModels(root)
    service_token = local_service_token(root)
    def authorized(request: Request):
        value = request.headers.get('authorization', '').removeprefix('Bearer ')
        if not service_token or not hmac.compare_digest(value, service_token):
            raise HTTPException(401, '本地模型接口需要受管服务凭据')
    api = APIRouter(prefix=PREFIX, dependencies=[Depends(authorized)])

    @api.get('/models')
    def catalog():
        return {'data': [{'id': model} for model in models.manifest() if model in {EMBEDDING, RERANK}]}

    @api.post('/embeddings')
    def embeddings(body: EmbeddingInput):
        if body.model != EMBEDDING or any(not t.strip() or len(t) > 16000 for t in body.input):
            raise ValueError('向量模型或输入文本无效')
        return {'model': body.model, 'data': [{'index': i, 'embedding': vector} for i, vector in enumerate(models.embed(body.input))]}

    @api.post('/rerank')
    def rerank(body: RerankInput):
        if body.model != RERANK or any(len(text) > 16000 for text in body.documents):
            raise ValueError('重排模型或输入文本无效')
        return {'results': models.rerank(body.query, body.documents)[:body.top_n]}

    return api
