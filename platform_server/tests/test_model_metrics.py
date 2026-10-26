from platform_server.model_metrics import model_inference_stats
from platform_server.store import Store


def test_model_stats_use_deployment_id_and_keep_timeouts_separate(tmp_path):
    store = Store(tmp_path)
    store.put('agent_run', 'alice', {
        'id': 'unified-1', 'brain': 'unified', 'status': 'partial', 'started_at': '2026-10-15T00:00:00+00:00',
        'brain_runs': {
            'knowledge': {'model_deployment_id': 'knowledge-chat', 'status': 'failed', 'duration_ms': 90000,
                          'ended_at': '2026-10-15T00:01:30+00:00', 'error': {'code': 'MODEL_TIMEOUT'}},
            'structured': {'model_deployment_id': 'structured-chat', 'status': 'succeeded', 'duration_ms': 1200,
                           'ended_at': '2026-10-15T00:00:02+00:00', 'error': None},
        },
    })
    store.put('agent_run', 'alice', {
        'id': 'knowledge-2', 'brain': 'knowledge', 'model_deployment_id': 'knowledge-chat', 'status': 'succeeded',
        'elapsed_ms': 1000, 'ended_at': '2026-10-15T00:02:00+00:00', 'started_at': '2026-10-15T00:01:59+00:00',
    })

    stats = model_inference_stats(store, 'knowledge-chat')

    assert stats['attempt_count'] == 2
    assert stats['success_count'] == 1
    assert stats['timeout_count'] == 1
    assert stats['timeout_rate'] == 50.0
    assert stats['average_duration_ms'] == 45500
    assert stats['p95_duration_ms'] == 90000
    assert stats['last_timeout_at'] == '2026-10-15T00:01:30+00:00'
