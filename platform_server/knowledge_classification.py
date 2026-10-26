"""Atomically persist classified assets and replaceable topology projections."""
import hashlib
import json

from brains.knowledge.src.classification import CLASSIFICATION_VERSION
from platform_server.store import now, uid
from platform_server.knowledge_publication import publication_fields, synchronized_job


def persist_classification(store, owner, workspace, record, *, overrides=None, correction=None, review=None):
    overrides = overrides or {}
    knowledge = workspace.knowledge.brain.store
    updates = []
    jobs = store.list('job', owner)
    reviews = {item['document_id']: item for item in store.list('review', owner)}
    if review:
        reviews[review['document_id']] = review
    for asset in store.list('asset', owner):
        document_id = asset.get('document_id')
        # During replay a legacy asset can lack its persisted document ID.
        if not document_id:
            document_id = next((d.document_id for d in knowledge.documents.values() if d.source_id == asset['id']), None)
        classification = knowledge.document_classification.get(document_id)
        if asset['brain'] != 'knowledge' or not classification:
            continue
        if asset['status'] in {'queued', 'running', 'failed', 'cancelled'}:
            continue
        prior_asset = dict(asset)
        asset['classification'] = classification
        if asset['id'] in overrides:
            asset['classification_override'] = overrides[asset['id']]
        # Publication can change without any classification change.
        asset.update(publication_fields(asset, knowledge, document_id, reviews.get(document_id)))
        if prior_asset != asset:
            updates.append(('asset', asset))
        for job in jobs:
            if job['asset_id'] != asset['id'] or job['status'] in {'running', 'queued'}:
                continue
            updated_job = synchronized_job(job, asset)
            if updated_job != job:
                updates.append(('job', updated_job))
    record = {**record, 'classification_version': CLASSIFICATION_VERSION}
    stamp = now()
    with store.connect() as db:
        prior = db.execute("SELECT data FROM records WHERE kind='topology_snapshot' AND owner=? AND id=?", (owner, record['id'])).fetchone()
        if prior:
            previous = json.loads(prior['data'])
            if previous.get('classification_version') != CLASSIFICATION_VERSION or correction:
                archive_id = 'topology-history-' + hashlib.sha256(prior['data'].encode()).hexdigest()[:32]
                updates.append(('topology_history', {**previous, 'id': archive_id, 'superseded_at': stamp,
                                                     'reason': 'document-classification-rebuild'}))
        if correction:
            updates.append(('classification_correction', correction))
        if review:
            updates.append(('review', review))
        updates.append(('topology_snapshot', record))
        for kind, value in updates:
            value = {**value, 'created_at': value.get('created_at', stamp), 'updated_at': stamp}
            # Enforce the same immutable ownership boundary as Store.put.
            prior_owner = db.execute('SELECT kind,owner FROM records WHERE id=?', (value['id'],)).fetchone()
            if prior_owner and (prior_owner['kind'] != kind or prior_owner['owner'] != owner):
                raise ValueError('record ownership is immutable')
            db.execute('INSERT INTO records VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at',
                       (value['id'], kind, owner, json.dumps(value, ensure_ascii=False), value['created_at'], stamp))
        if updates[:-1]:
            detail = {'version': CLASSIFICATION_VERSION, 'assets': [value['id'] for kind, value in updates if kind == 'asset']}
            if correction:
                detail['correction_id'] = correction['id']
            action = 'knowledge.review' if review else 'knowledge.classification.corrected' if correction else 'knowledge.state.synchronized'
            if review:
                detail['review_id'] = review['id']
            db.execute('INSERT INTO audit(at,owner,action,target,detail) VALUES(?,?,?,?,?)',
                       (stamp, owner, action, review['asset_id'] if review else correction['asset_id'] if correction else record['id'],
                        json.dumps(detail, ensure_ascii=False)))


def correction_record(asset, domain, reason):
    return {'id': uid('classification-correction'), 'asset_id': asset['id'],
            'document_id': asset['document_id'], 'before': asset.get('classification'),
            'requested_domain': domain, 'reason': reason, 'created_at': now()}
