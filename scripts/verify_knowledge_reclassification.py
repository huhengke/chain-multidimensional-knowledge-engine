"""Replay actual knowledge inputs against read-only caches, with no provider calls."""
import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from brains.knowledge import KnowledgeBrain
from brains.knowledge.src.runtime_adapter import ManagedWeKnoraRuntime
from brains.knowledge.src.workbench import KnowledgeWorkbenchService, DocumentReviewRequest
from platform_server.ingestion import Ingestion, Workspace
from platform_server.knowledge_runtime import COMMIT
from brains.memory.src.brain import MemoryBrain


class CacheOnlyRuntime(ManagedWeKnoraRuntime):
    def __init__(self, db, owner):
        super().__init__([str(ROOT / 'third_party/weknora/bin/cognition-runtime.exe')])
        self.db, self.owner = db, owner
        models = [json.loads(r[0]) for r in db.execute("SELECT data FROM records WHERE kind='model' AND owner='system'")]
        self.embedding = next(m for m in models if m['brain'] == 'knowledge' and m['purpose'] == 'embedding' and m['enabled'])
        identity = {k: self.embedding.get(k) for k in ('id', 'base_url', 'model')}
        if self.embedding.get('provider_revision'):
            identity['provider_revision'] = self.embedding['provider_revision']
        self.space = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
        self._meta.runtime_version = COMMIT[:12] + ':' + self.space
        self.calls = Counter()

    def _invoke(self, operation, payload, **kwargs):
        assert operation in {'parse', 'segment', 'index'}, operation
        key = dict(payload)
        if operation == 'index':
            key['model'] = {k: self.embedding[k] for k in ('base_url', 'model')}
        digest = hashlib.sha256(json.dumps(key, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        saved = self.db.execute('SELECT result FROM knowledge_cache WHERE owner=? AND namespace=? AND operation=? AND digest=?',
                               (self.owner, self.space, operation, digest)).fetchone()
        if not saved:
            raise ValueError('Read-only cache miss: ' + operation)
        self.calls[operation] += 1
        return json.loads(saved[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    db = sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON')
    runtime = CacheOnlyRuntime(db, args.owner)
    workspace = Workspace(KnowledgeWorkbenchService(KnowledgeBrain(runtime=runtime)), MemoryBrain())
    ingest = Ingestion(None)  # index only; no persistence API or worker is used.
    documents = []
    try:
        for raw, in db.execute("SELECT data FROM records WHERE kind='asset' AND owner=? ORDER BY created_at,id", (args.owner,)):
            asset = json.loads(raw)
            if asset['brain'] != 'knowledge' or asset['status'] not in {'ready', 'partial', 'pending_review', 'rejected'}:
                continue
            result = ingest.index(workspace, args.owner, asset)
            assert result['document_id'] == asset['document_id'], asset['title']
            assert result['indexed_units'] == asset['indexed_units'], asset['title']
            documents.append({'title': asset['title'], 'document_id': result['document_id'],
                              'indexed_units': result['indexed_units'], 'classification': result['classification']})
            print(asset['title'], result['classification']['label'], result['indexed_units'], flush=True)
        for raw, in db.execute("SELECT data FROM records WHERE kind='review' AND owner=? ORDER BY created_at,id", (args.owner,)):
            review = json.loads(raw)
            workspace.knowledge.review_document(review['document_id'], DocumentReviewRequest(
                request_id=review['id'], trace_id=review['id'], action=review['action'], reason=review['reason']), ingest.principal(args.owner))
        store = workspace.knowledge.brain.store
        report = {'documents': documents, 'units_by_tree': dict(Counter(store.unit_tree.values())),
                  'evidence_count': len(store.evidence), 'cache_hits': dict(runtime.calls), 'provider_calls': 0,
                  'publications': {d: p.status for d, p in store.publications.items()},
                  'unit_ids': sorted(store.unit_tree), 'evidence_ids': sorted(store.evidence)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: v for k, v in report.items() if k not in {'unit_ids', 'evidence_ids', 'documents', 'publications'}}, ensure_ascii=False))
    finally:
        ingest.close()
        db.close()


if __name__ == '__main__':
    main()
