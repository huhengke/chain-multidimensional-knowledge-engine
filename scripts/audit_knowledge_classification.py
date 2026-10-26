"""Read-only document classification preview, optionally make a SQLite backup."""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from brains.knowledge.src.classification import classify_document, CLASSIFICATION_VERSION


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    db = sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True)
    if args.backup:
        if args.backup.exists():
            raise ValueError('Refusing to overwrite a backup')
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(args.backup) as backup:
            db.backup(backup)
    report = {'version': CLASSIFICATION_VERSION, 'documents': [], 'current_topology': []}
    for owner, data in db.execute("SELECT owner,data FROM records WHERE kind='asset' ORDER BY created_at"):
        asset = json.loads(data)
        if asset['brain'] != 'knowledge':
            continue
        path = args.database.parent / 'objects' / asset['sha256']
        report['documents'].append({'owner': owner, 'id': asset['id'], 'title': asset['title'],
            'document_id': asset.get('document_id'), 'status': asset['status'], 'indexed_units': asset.get('indexed_units'),
            'sha256': asset['sha256'], 'original_verified': path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == asset['sha256'],
            'before': asset.get('classification'),
            'proposed': classify_document(asset['title'], asset['extracted_text'], override=asset.get('classification_override')) if asset.get('extracted_text') else None})
    for owner, data in db.execute("SELECT owner,data FROM records WHERE kind='topology_snapshot'"):
        snapshot = json.loads(data)
        report['current_topology'].append({'owner': owner, 'updated_at': snapshot.get('updated_at'),
            'classification_version': snapshot.get('classification_version'),
            'trees': snapshot['projection']['knowledge_topology']['trees']})
    db.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(args.output), 'assets': len(report['documents']),
        'originals_verified': sum(d['original_verified'] for d in report['documents']),
        'subjects': [{'title': d['title'], 'subject': d['proposed']['label']} for d in report['documents'] if d['proposed']]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
