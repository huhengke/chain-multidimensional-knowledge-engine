"""Read-only installation report and optional consistent pre-migration backup."""
import argparse
import collections
import hashlib
import json
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    db = sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True)
    if args.backup:
        if args.backup.exists():
            raise ValueError('Refusing to overwrite an existing backup')
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(args.backup) as backup:
            db.backup(backup)
    records = [(kind, owner, json.loads(data)) for kind, owner, data in db.execute(
        "SELECT kind,owner,data FROM records WHERE kind IN ('asset','job','review','topology_snapshot') ORDER BY created_at,id")]
    assets = []
    for kind, owner, a in records:
        if kind != 'asset':
            continue
        path = args.database.parent / 'objects' / a['sha256']
        assets.append({**{key: a.get(key) for key in ('id','title','brain','status','processing_status','publication_status','document_id','indexed_units','sha256')},
                       'owner': owner, 'original_verified': path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == a['sha256']})
    report = {'assets': assets,
              'reviews': [dict(owner=owner, **value) for kind, owner, value in records if kind == 'review'],
              'jobs': [dict(owner=owner, **{k: value.get(k) for k in ('id','asset_id','status','processing_status','publication_status','result','progress')}) for kind, owner, value in records if kind == 'job']}
    # Report only status fields from potentially large job results.
    for job in report['jobs']:
        job['result'] = {k: (job['result'] or {}).get(k) for k in ('status','processing_status','publication_status','review_reasons')}
    db.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(args.output), 'assets': len(assets), 'originals_verified': sum(a['original_verified'] for a in assets),
                      'publication_pairs': dict(collections.Counter(f"{a['status']} / {a['publication_status']}" for a in assets if a['document_id'])),
                      'reviews': len(report['reviews'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
