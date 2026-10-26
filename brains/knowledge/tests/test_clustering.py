import random
from brains.knowledge.src.clustering import KnowledgeClusterIndex
from topology.src.star_cluster import StarClusterIndex
from topology.src.trace import TraceRecord


def test_optimized_knowledge_clustering_matches_original_exactly():
    rng=random.Random(42)
    # Include repeats, exact epsilon boundaries, nearest candidates, ties and
    # full real-model dimensions; only knowledge uses the optimized index.
    vectors=[['0','0'],['0.05','0'],['0.025','0'],['0.0500000000000000000001','0'],['0','0']]
    vectors += [[str(rng.randint(-100,100)/1000) for _ in range(512)] for _ in range(35)]
    vectors += [vectors[-1], vectors[-5]]
    original=StarClusterIndex('knowledge.general')
    optimized=KnowledgeClusterIndex('knowledge.general')
    for i,vector in enumerate(vectors):
        trace=TraceRecord(trace_id=f't{i}',aggregate_key='knowledge://general',vector=vector,source_ref=f'knowledge://unit/{i}',created_at='2026-10-13T00:00:00Z')
        assert optimized.assign(trace).model_dump()==original.assign(trace).model_dump()
    assert {k:v.model_dump() for k,v in optimized.clusters.items()}=={k:v.model_dump() for k,v in original.clusters.items()}
