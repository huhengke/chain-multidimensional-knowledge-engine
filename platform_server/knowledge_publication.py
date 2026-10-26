"""Keep processing progress independent from knowledge publication decisions."""

PUBLICATION_NAMES = {'published': '已发布', 'pending_review': '待复核', 'rejected': '已拒绝'}


def processing_status(asset):
    status = asset.get('status', 'queued')
    if status in {'queued', 'running', 'failed', 'cancelled'}:
        return status
    if asset.get('graph_error') or asset.get('pdf_extraction', {}).get('unresolved_pages') or status == 'partial':
        return 'partial'
    if asset.get('indexing_complete') or asset.get('document_id') or status in {'ready', 'succeeded'}:
        return 'completed'
    return 'unknown'


def asset_view(asset):
    value = {**asset, 'processing_status': processing_status(asset)}
    if asset.get('brain') == 'knowledge':
        value.setdefault('publication_status', 'unknown' if asset.get('document_id') else 'not_ready')
    else:
        value['publication_status'] = 'not_applicable'
    return value


def publication_fields(asset, knowledge, document_id, latest_review=None):
    publication = knowledge.publications[document_id].status
    processed = processing_status(asset)
    status = ('partial' if processed == 'partial' else 'ready') if publication == 'published' else publication
    reasons = [g.reason for g in knowledge.gates.values()
               if g.document_id == document_id and g.status == 'pending_review']
    if publication == 'pending_review' and latest_review and latest_review['action'] == 'rollback':
        reasons = [latest_review['reason'], *reasons]
    return {'status': status, 'processing_status': processed, 'publication_status': publication,
            'review_reasons': list(dict.fromkeys(reasons))}


def synchronized_job(job, asset):
    result = {**(job.get('result') or {}), **{key: asset.get(key) for key in (
        'status', 'processing_status', 'publication_status', 'review_reasons', 'classification')}}
    label = PUBLICATION_NAMES.get(asset['publication_status'], '发布状态待确认')
    message = ('解析与索引部分完成' if asset['processing_status'] == 'partial' else '解析与索引已完成') + '；' + label
    if asset['review_reasons']:
        message += '：' + '；'.join(asset['review_reasons'])
    return {**job, 'status': 'succeeded' if asset['status'] == 'ready' else asset['status'],
            'processing_status': asset['processing_status'], 'publication_status': asset['publication_status'],
            'result': result, 'progress': {**(job.get('progress') or {}), 'stage': 'complete', 'message': message}}
