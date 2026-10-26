from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from engine.api.schemas_unified import UnifiedQueryRequest
from engine.core.errors import BadRequestError
from engine.query.unified import _batch_totals, execute_unified_query
from engine.storage.dataset_registry import registry
from engine.storage.topology import topology_summary


def test_unqualified_sales_discovers_current_registry_without_industry_default():
    assert UnifiedQueryRequest().dataset == ""
    specs = {key: SimpleNamespace(id=key, kind=kind, label=label, aliases=[]) for key, kind, label in [
        ('shop', 'generic', '门店销售'), ('online', 'generic', '线上销售'),
        ('budget', 'generic', '销售预算'), ('org', 'dimension', '组织'),
    ]}
    calls = []

    def execute(_session, request, trace):
        calls.append(request['dataset'])
        return {'query_plan': {'dataset': request['dataset']}, 'actual': {'available': False}, 'warnings': []}

    with patch.object(registry, 'specs', specs), patch('engine.query.unified.actual_sales_scope', return_value={'include_kinds':['generic'], 'exclude_terms':['预算']}):
        result = execute_unified_query(None, {'dataset_scope':'all_actual', 'period':'2024'}, 'scope-test', dataset_executor=execute)
        assert calls == ['shop', 'online']
        assert result['actual']['dataset_count'] == 2
        calls.clear()
        execute_unified_query(None, {'dataset':'online'}, 'explicit', dataset_executor=execute)
        assert calls == ['online']
        with pytest.raises(BadRequestError, match='发现当前注册'):
            execute_unified_query(None, {}, 'no-default', dataset_executor=execute)
    with patch.object(registry, 'specs', {}):
        with pytest.raises(BadRequestError, match='没有符合'):
            execute_unified_query(None, {'dataset_scope':'all_actual', 'dataset':'stale_id'}, 'empty', dataset_executor=execute)


def test_topology_explains_threshold_without_inventing_branches():
    engine = create_engine('sqlite://')
    with Session(engine) as session:
        session.execute(text('''CREATE TABLE spiral_nodes (
            dataset TEXT, topology_version INT, temporal_layer INT, pair_node_id TEXT,
            strand_a_next TEXT, strand_b_next TEXT, topology_role TEXT,
            grown INT, level INT, record_count INT)'''))
        session.execute(text("INSERT INTO spiral_nodes VALUES ('shop',3,1,NULL,NULL,NULL,'trunk',0,2,5909)"))
        plan = SimpleNamespace(physical_levels=('year','season','month','store'), branch_levels=('store',))
        with patch('engine.storage.topology.registry.get', return_value=object()), patch('engine.storage.topology.build_growth_plan', return_value=plan), patch('engine.storage.topology.get_param', return_value=10000):
            summary = topology_summary(session, 'shop').as_dict()
            assert summary['branch_nodes'] == 0
            assert summary['trunk_nodes'] == 1
            assert summary['growth_status']['code'] == 'below_threshold'
            assert summary['growth_status']['max_pending_records'] == 5909
            session.execute(text("UPDATE spiral_nodes SET record_count=10000"))
            assert topology_summary(session, 'shop').growth_status['code'] == 'pending_materialization'
            session.execute(text("INSERT INTO spiral_nodes VALUES ('shop',3,1,NULL,NULL,NULL,'branch',0,4,200)"))
            assert topology_summary(session, 'shop').growth_status['code'] == 'branched'
            assert topology_summary(session, 'empty').growth_status['code'] == 'empty'


def test_multiple_trees_do_not_guess_sales_from_first_amount_field():
    totals = _batch_totals({
        'shop': {'current': {'available':True, 'aggregations':{'cost_amount':'90', 'sale_amount':'100'}}},
        'online': {'current': {'available':True, 'aggregations':{'revenue':'80'}}},
    })
    assert 'sales_amount' not in totals
    assert totals['aggregation_policy'] == 'per_dataset_only'
    assert totals['metrics_by_dataset']['shop']['aggregations']['sale_amount'] == '100'
