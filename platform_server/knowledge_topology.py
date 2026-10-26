"""Readable projections of persisted assets and the materialized brain topology."""
from collections import Counter, defaultdict
from brains.knowledge.src.classification import DOMAIN_NAMES, CLASSIFICATION_VERSION


def _short(value, limit=72):
    value = str(value or '').strip()
    return value if len(value) <= limit else value[:limit - 1] + '…'


def _graph_node(node_id, node_type, label, rank, **extra):
    return {
        'id': node_id,
        'type': node_type,
        'label': _short(label),
        'rank': rank,
        **extra,
    }


def _graph_edge(source, target, relation, label=''):
    return {
        'id': f'{source}|{relation}|{target}',
        'source': source,
        'target': target,
        'relation': relation,
        'label': label,
    }


def knowledge_topology(workbench):
    brain = workbench.brain
    store = brain.store
    trees = []
    for tree_id in sorted(set(store.unit_tree.values())):
        units = [u for uid, u in store.units.items() if store.unit_tree.get(uid)==tree_id]
        nodes = {nid:n for nid,n in store.nodes.items() if n.tree_id==tree_id}
        history = store.generations.history(tree_id)
        current = store.generations.current(tree_id) if history else None
        if current:
            nodes.update(current.nodes)
        layers = Counter(n.layer.value for n in nodes.values())
        branches = []
        for node in nodes.values():
            payload = store.payload.get(node.payload_ref)
            if payload.get('kind')=='growth-cluster':
                branches.append({'node_id':node.node_id, 'generation':node.generation,
                    'cluster_id':payload['cluster_id'], 'occurrence_count':payload['occurrence_count'],
                    'children':list(node.children)})
        paths = defaultdict(list)
        for unit in units:
            paths[' / '.join(unit.concept_path) or tree_id].append(unit)
        concepts = []
        for path, members in sorted(paths.items()):
            documents = []
            for document_id in sorted({u.document_id for u in members}):
                doc = store.documents[document_id]
                documents.append({'document_id':document_id, 'title':doc.title, 'status':store.publications[document_id].status,
                    'unit_count':sum(u.document_id==document_id for u in members)})
            concepts.append({'path':path, 'unit_count':len(members), 'documents':documents})
        trees.append({'tree_id':tree_id, 'label': DOMAIN_NAMES.get(tree_id.removeprefix('knowledge.'), tree_id) + '知识树',
            'generation': current.generation if current else None,
            'history_count':len(history), 'documents':len({u.document_id for u in units}),
            'published_units':sum(store.is_published(u.document_id) for u in units), 'unit_count':len(units),
            'vector_count':sum(brain.query_index.vector_dimensions(u.unit_id) > 0 for u in units),
            'concept_nodes':layers['inner'], 'middle_nodes':layers['middle'], 'outer_nodes':layers['outer'],
            'growth_branches':branches, 'concepts':concepts,
            'cluster_count':len(store.clusters[tree_id]) if tree_id in store.clusters else 0})
    return {'status':'ready', 'schema_version':3, 'classification_version': CLASSIFICATION_VERSION,
        'trees':trees, 'runtime':workbench.runtime_view(),
        'payload_counts':dict(Counter(ref.split('/')[2] for ref in store.payload.refs())),
        'retrieval_mode':'hybrid_then_rerank',
        'notes':['周存知识单元、向量索引与拓扑节点是不同计数；未发布资料不参与回答。',
                 '统一 Agent 先使用星团提供快速线索，再由命中的目标脑独立执行检索、鉴权与证据核验。',
                 '原件、解析结果和向量缓存持久保存；内存拓扑由这些记录重建，尚未单独持久化完整生长历史。']}


def knowledge_tree_slice(workbench, tree_id, *, generation=None, cursor=0, limit=200,
                         node_types=None, query_node_ids=None):
    """Return one independently rendered knowledge-brain graph without raw vectors."""
    brain = workbench.brain
    store = brain.store
    tree_ids = sorted(set(store.unit_tree.values()) | set(store.generations.tree_ids()))
    if tree_id not in tree_ids:
        raise KeyError(tree_id)
    history = store.generations.history(tree_id)
    if generation is None:
        topology_generation = store.generations.current(tree_id) if history else None
    else:
        topology_generation = store.generations.get(tree_id, generation)
    generation_number = topology_generation.generation if topology_generation else None
    generation_nodes = dict(topology_generation.nodes) if topology_generation else {
        node_id: node for node_id, node in store.nodes.items() if node.tree_id == tree_id
    }
    runtime = workbench.runtime_view()
    hit_ids = set(query_node_ids or [])

    child_units = sorted(
        (unit for unit_id, unit in store.units.items() if store.unit_tree.get(unit_id) == tree_id),
        key=lambda item: (item.document_id, item.chunk_order or 0, item.unit_id),
    )
    child_ids = {unit.unit_id for unit in child_units}
    parent_ids = {unit.parent_unit_id for unit in child_units if unit.parent_unit_id}
    parent_units = [store.units[unit_id] for unit_id in sorted(parent_ids) if unit_id in store.units]
    document_ids = sorted({unit.document_id for unit in child_units})

    nodes = []
    edges = []

    # These nodes describe the actual configured runtime, rather than a second product layer.
    stage_status = runtime.get('health') or 'unknown'
    stages = [
        ('parse', 'WeKnora 解析与父子分块'),
        ('hybrid', 'WeKnora 混合索引与召回'),
        ('rerank', 'WeKnora 重排与证据定位'),
    ]
    previous = None
    for key, label in stages:
        node_id = f'{tree_id}:runtime:{key}'
        nodes.append(_graph_node(node_id, 'runtime', label, 1, layer='runtime', status=stage_status,
                                 detail={'runtime_profile': runtime.get('runtime_profile'),
                                         'runtime_version': (runtime.get('runtime') or {}).get('runtime_version')}))
        if previous:
            edges.append(_graph_edge(previous, node_id, 'runtime_flow', '运行链'))
        previous = node_id

    gate_id = f'{tree_id}:evidence-gate'
    gates = [gate for gate in store.gates.values() if gate.document_id in document_ids]
    gate_status = 'pending_review' if any(gate.status == 'pending_review' for gate in gates) else 'ready'
    nodes.append(_graph_node(gate_id, 'gate', '证据、权限与发布门禁', 2, layer='governance', status=gate_status,
                             metrics={'gate_records': len(gates), 'published_documents': sum(
                                 store.is_published(document_id) for document_id in document_ids)}))
    edges.append(_graph_edge(previous, gate_id, 'governed_by', '受控发布'))

    for document_id in document_ids:
        document = store.documents[document_id]
        publication = store.publications.get(document_id)
        source_id = f'document:{document_id}'
        nodes.append(_graph_node(source_id, 'source', document.title or document_id, 0, layer='source',
                                 status=publication.status if publication else 'unknown',
                                 detail={'document_id': document_id, 'source_id': document.source_id,
                                         'classification': store.document_classification.get(document_id),
                                         'parse_version': document.parse_version},
                                 metrics={'unit_count': sum(unit.document_id == document_id for unit in child_units)}))
        edges.append(_graph_edge(source_id, f'{tree_id}:runtime:parse', 'ingested_by', '导入'))

    concept_by_unit = {}
    growth_ids = []
    growth_by_event = defaultdict(list)
    for event in (dict(topology_generation.cause).get('events', []) if topology_generation else []):
        for branched_node_id in event.get('branched_node_ids', []):
            growth_by_event[event.get('event_id')].append(f'growth:{branched_node_id}')
    for node_id, topology_node in sorted(generation_nodes.items()):
        payload = store.payload.get(topology_node.payload_ref)
        kind = payload.get('kind')
        if kind == 'knowledge-concept-anchor':
            unit_id = payload.get('unit_id')
            if unit_id not in child_ids:
                continue
            concept_id = f'concept:{node_id}'
            concept_by_unit[unit_id] = concept_id
            path = payload.get('concept_path') or [tree_id]
            nodes.append(_graph_node(concept_id, 'concept', ' / '.join(path), 3, layer='inner',
                                     status='active', query_hit=unit_id in hit_ids,
                                     detail={'topology_node_id': node_id, 'day_key': topology_node.day_key,
                                             'ring_index': topology_node.ring_index,
                                             'conditions': payload.get('conditions') or [],
                                             'entities': payload.get('entities') or []},
                                     metrics={'evidence_count': len(topology_node.evidence_refs)}))
            edges.append(_graph_edge(gate_id, concept_id, 'admits', '发布后进入内链'))
        elif kind in {'growth-cluster', 'growth-dimension'}:
            growth_id = f'growth:{node_id}'
            growth_ids.append(growth_id)
            label = (f"自动分枝 · {payload.get('cluster_id')}" if kind == 'growth-cluster'
                     else f"分枝维度 · {payload.get('dimension')}")
            nodes.append(_graph_node(growth_id, 'growth', label, 7, layer=topology_node.layer.value,
                                     status='active', detail={'topology_node_id': node_id,
                                                              'cluster_id': payload.get('cluster_id'),
                                                              'dimension': payload.get('dimension')},
                                     metrics={'generation': topology_node.generation,
                                              'occurrence_count': payload.get('occurrence_count')}))
            for child_id in topology_node.children:
                edges.append(_graph_edge(growth_id, f'growth:{child_id}', 'branches_to', '自动生长'))

    for unit in parent_units:
        unit_id = f'unit:{unit.unit_id}'
        nodes.append(_graph_node(unit_id, 'parent_unit', f'父块 · {unit.locator.anchor or unit.unit_id}', 4,
                                 layer='middle', status='active',
                                 detail={'unit_id': unit.unit_id, 'document_id': unit.document_id,
                                         'locator': unit.locator.model_dump(mode='json')},
                                 metrics={'child_count': sum(child.parent_unit_id == unit.unit_id for child in child_units)}))
    for unit in child_units:
        unit_id = f'unit:{unit.unit_id}'
        evidence_ids = list(store.evidence_by_unit.get(unit.unit_id, []))
        nodes.append(_graph_node(unit_id, 'knowledge_unit',
                                 unit.locator.anchor or _short(store.unit_text.get(unit.unit_id), 48) or unit.unit_id,
                                 4, layer='middle', status='published' if store.is_published(unit.document_id) else 'unpublished',
                                 query_hit=unit.unit_id in hit_ids,
                                 detail={'unit_id': unit.unit_id, 'document_id': unit.document_id,
                                         'unit_type': unit.unit_type, 'concept_path': unit.concept_path,
                                         'locator': unit.locator.model_dump(mode='json'),
                                         'text_excerpt': _short(store.unit_text.get(unit.unit_id), 240)},
                                 metrics={'evidence_count': len(evidence_ids),
                                          'vector_dimensions': brain.query_index.vector_dimensions(unit.unit_id)}))
        if unit.parent_unit_id and unit.parent_unit_id in parent_ids:
            edges.append(_graph_edge(f'unit:{unit.parent_unit_id}', unit_id, 'parent_child', '父子分块'))
        if unit.unit_id in concept_by_unit:
            edges.append(_graph_edge(concept_by_unit[unit.unit_id], unit_id, 'anchors', '概念锚定'))
        trace_id = store.trace_by_unit.get(unit.unit_id)
        if trace_id and trace_id in store.traces:
            trace = store.traces[trace_id]
            graph_trace_id = f'trace:{trace_id}'
            nodes.append(_graph_node(graph_trace_id, 'vector_trace', '向量留痕', 5, layer='middle', status='active',
                                     query_hit=unit.unit_id in hit_ids,
                                     detail={'trace_id': trace_id, 'aggregate_key': trace.aggregate_key,
                                             'source_ref': trace.source_ref, 'created_at': trace.created_at},
                                     metrics={'vector_dimensions': store.trace_vector_dimensions.get(trace_id, len(trace.vector))}))
            edges.append(_graph_edge(unit_id, graph_trace_id, 'vectorized_as', '向量空间留痕'))

    tree_clusters = [cluster for cluster in workbench.list_clusters() if cluster.tree_id == tree_id]
    for cluster in tree_clusters:
        cluster_id = f'cluster:{cluster.cluster_id}'
        nodes.append(_graph_node(cluster_id, 'cluster', f'知识星团 · {cluster.cluster_id}', 6, layer='middle',
                                 status=cluster.status,
                                 query_hit=bool(hit_ids.intersection(cluster.member_unit_ids)),
                                 detail={'cluster_id': cluster.cluster_id,
                                                               'embedding_space_id': cluster.embedding_space_id,
                                                               'index_version': cluster.index_version,
                                                               'dispersion': cluster.dispersion,
                                                               'corrections': cluster.corrections},
                                 metrics={'member_count': len(cluster.member_unit_ids),
                                          'independent_source_count': cluster.independent_source_count,
                                          'occurrence_count': cluster.occurrence_count,
                                          'vector_dimensions': len(cluster.centroid),
                                          'generation': cluster.generation}))
        for member_id in cluster.member_unit_ids:
            trace_id = store.trace_by_unit.get(member_id)
            if trace_id:
                edges.append(_graph_edge(f'trace:{trace_id}', cluster_id, 'clustered_into', '相似聚合'))
        for event_id in cluster.threshold_events:
            for growth_id in growth_by_event.get(event_id, []):
                edges.append(_graph_edge(cluster_id, growth_id, 'grew_into', '阈值触发'))

    links = [link for link in workbench.list_cross_brain_links()
             if link.source_ref.startswith('knowledge://cluster/') and
             any(link.source_ref.endswith(cluster.cluster_id) for cluster in tree_clusters)]
    for link in links:
        anchor_id = f'anchor:{link.link_id}'
        source_cluster = link.source_ref.rsplit('/', 1)[-1]
        nodes.append(_graph_node(anchor_id, 'cross_anchor',
                                 f'{link.target_brain} · {link.anchor_value}', 8, layer='outer', status=link.status,
                                 detail={'link_id': link.link_id, 'target_brain': link.target_brain,
                                         'target_ref': link.target_ref, 'anchor_type': link.anchor_type,
                                         'basis': link.basis}, metrics={'evidence_count': len(link.evidence_ids)}))
        edges.append(_graph_edge(f'cluster:{source_cluster}', anchor_id, 'cross_brain_candidate', '公共锚点'))

    output_id = f'{tree_id}:generation:{generation_number or 0}'
    nodes.append(_graph_node(output_id, 'generation',
                             f'智慧树生长版本 G{generation_number}' if generation_number else '智慧树基础结构',
                             8, layer='version', status='active',
                             detail={'tree_id': tree_id,
                                     'cause': dict(topology_generation.cause) if topology_generation else {},
                                     'created_at': topology_generation.created_at if topology_generation else None},
                             metrics={'generation': generation_number, 'history_count': len(history)}))
    for growth_id in growth_ids:
        edges.append(_graph_edge(growth_id, output_id, 'materialized_in', '固化版本'))
    if not growth_ids:
        for cluster in tree_clusters:
            edges.append(_graph_edge(f'cluster:{cluster.cluster_id}', output_id, 'observed_in', '当前版本'))

    highlighted_graph_nodes = {node['id'] for node in nodes if node.get('query_hit')}
    for edge in edges:
        edge['query_hit'] = edge['source'] in highlighted_graph_nodes and edge['target'] in highlighted_graph_nodes

    if node_types:
        allowed = set(node_types)
        nodes = [node for node in nodes if node['type'] in allowed]
    nodes.sort(key=lambda item: (item['rank'], item['type'], item['id']))
    if not node_types:
        # A bounded page must still expose every architectural layer. Interleave
        # real nodes by type instead of returning all units before later layers.
        groups = defaultdict(list)
        for node in nodes:
            groups[node['type']].append(node)
        ordered_types = sorted(groups, key=lambda item: (groups[item][0]['rank'], item))
        interleaved = []
        index = 0
        while len(interleaved) < len(nodes):
            for node_type in ordered_types:
                if index < len(groups[node_type]):
                    interleaved.append(groups[node_type][index])
            index += 1
        nodes = interleaved
    total = len(nodes)
    page = nodes[cursor:cursor + limit]
    visible = {node['id'] for node in page}
    page_edges = [edge for edge in edges if edge['source'] in visible and edge['target'] in visible]
    return {
        'schema_version': 3,
        'tree_id': tree_id,
        'generation': generation_number,
        'generation_history': [item.snapshot() for item in history],
        'runtime': runtime,
        'nodes': page,
        'edges': page_edges,
        'page': {
            'cursor': cursor,
            'limit': limit,
            'total': total,
            'next_cursor': cursor + limit if cursor + limit < total else None,
        },
        'counts': dict(Counter(node['type'] for node in nodes)),
        'query_overlay': {
            'node_ids': sorted(hit_ids),
            'edge_ids': sorted(edge['id'] for edge in edges if edge.get('query_hit')),
            'matched_count': sum(bool(node.get('query_hit')) for node in nodes),
        },
        'semantics': {
            'inner': '概念路径与中通内链',
            'middle': '父子分块、周存知识单元、向量留痕、知识星团与自动分枝',
            'outer': '原文定位、证据引用与跨脑公共锚点',
            'weknora': '仅嵌入知识脑的解析、父子分块、混合检索、向量索引与重排运行层',
        },
    }


def workspace_topology(workspace):
    """Freeze one consistent, UI-safe view while the in-memory workspace is hot."""
    clusters = []
    for item in workspace.knowledge.list_clusters():
        value = item.model_dump(mode='json', exclude={'member_unit_ids', 'evidence_ids', 'centroid'})
        value.update(member_count=len(item.member_unit_ids), evidence_count=len(item.evidence_ids),
                     vector_dimensions=len(item.centroid))
        clusters.append(value)
    links = []
    for item in workspace.knowledge.list_cross_brain_links():
        value = item.model_dump(mode='json', exclude={'evidence_ids'})
        value['evidence_count'] = len(item.evidence_ids)
        links.append(value)
    temporal_clusters = []
    for item in workspace.knowledge.list_temporal_clusters():
        value = item.model_dump(mode='json', exclude={'member_unit_ids', 'anchor_ids', 'document_ids'})
        value.update(
            member_count=len(item.member_unit_ids),
            anchor_count=len(item.anchor_ids),
            document_count=len(item.document_ids),
        )
        temporal_clusters.append(value)
    memory_clusters = []
    for index in workspace.memory.store.clusters.values():
        for item in index.clusters.values():
            value = item.model_dump(mode='json', exclude={'prototype_vector', 'centroid', 'member_trace_ids'})
            member_unit_ids = sorted({
                workspace.memory.store.occurrence_unit_id.get(
                    workspace.memory.store.occurrence_by_trace.get(trace_id, ''), ''
                )
                for trace_id in item.member_trace_ids
            } - {''})
            value.update(member_count=len(item.member_trace_ids), member_unit_ids=member_unit_ids,
                         vector_dimensions=len(item.centroid))
            memory_clusters.append(value)
    return {
        'knowledge_topology': knowledge_topology(workspace.knowledge),
        'knowledge_clusters': clusters,
        'cross_brain_links': links,
        'knowledge_temporal_clusters': temporal_clusters,
        'knowledge_temporal_index': dict(workspace.knowledge.brain.temporal_index_stats),
        'memory_units': [item.model_dump(mode='json') for item in workspace.memory.store.units.values()],
        'memory_clusters': memory_clusters,
        # Filled by Ingestion.topology from the durable record timestamp.  It
        # lets the unified Agent reuse its reverse anchor index until a real
        # import/review changes the topology projection.
        '_topology_version': None,
    }


def paged_topology(value, cluster_page=1, link_page=1, page_size=20):
    result = dict(value)
    clusters = value.get('knowledge_clusters', [])
    links = value.get('cross_brain_links', [])
    temporal_clusters = value.get('knowledge_temporal_clusters', [])
    cluster_start = (cluster_page - 1) * page_size
    link_start = (link_page - 1) * page_size
    result.update(
        knowledge_cluster_total=len(clusters),
        cross_brain_link_total=len(links),
        knowledge_clusters=clusters[cluster_start:cluster_start + page_size],
        cross_brain_links=links[link_start:link_start + page_size],
        knowledge_temporal_cluster_total=len(temporal_clusters),
        page_size=page_size,
    )
    return result


def rebuilding_topology(store, owner):
    """Truthful fast response while an older installation builds its first snapshot."""
    assets = [
        asset for asset in store.list('asset', owner)
        if asset.get('brain') == 'knowledge' and asset.get('document_id')
        and asset.get('status') in {'ready', 'partial', 'pending_review', 'rejected'}
    ]
    documents = [{
        'document_id': asset['document_id'],
        'title': asset.get('title') or asset.get('filename'),
        'status': asset.get('publication_status') or asset.get('status'),
        'unit_count': int(asset.get('indexed_units') or 0),
    } for asset in assets]
    unit_count = sum(item['unit_count'] for item in documents)
    has_assets = bool(documents)
    return {
        'knowledge_topology': {
            'status': 'rebuilding' if has_assets else 'ready', 'schema_version': 3,
            'trees': [],
            'durable_documents': documents,
            'runtime': {'status': 'rebuilding' if has_assets else 'ready'},
            'payload_counts': {},
            'retrieval_mode': 'hybrid_then_rerank',
            'notes': ([
                f'已从持久化记录确认 {len(documents)} 份知识文档、{unit_count} 个已索引知识单元；完整概念路径与自动分枝正在后台重建。',
                '此处不伪造树节点；重建完成后页面会自动切换到完整智慧树快照。',
            ] if has_assets else ['尚未导入已索引的知识资料。']),
        },
        'knowledge_clusters': [],
        'cross_brain_links': [],
        'knowledge_temporal_clusters': [],
        'knowledge_temporal_index': {},
        'memory_units': [],
        'memory_clusters': [],
    }
