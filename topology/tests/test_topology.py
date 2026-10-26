"""M1 验收测试（对应验收矩阵 01–05）与三条硬不变式中的「不留日期」。"""

from __future__ import annotations

import re

import pytest

from topology.src.growth import GenerationStore, GrowthEngine, TopologyGeneration
from topology.src.node import Layer, TopologyNode
from topology.src.payload import PayloadStore
from topology.src.ring import RingRef
from topology.src.ruler import Ruler
from topology.src.star_cluster import StarClusterIndex
from topology.src.trace import (
    OccurrenceRef,
    TraceRecord,
    assert_no_date_in_aggregate_key,
    contains_date,
)

DAY = "2026-10-11"
TS = "2026-10-11T09:30:00+08:00"


# ---- 01 同尺一致 -------------------------------------------------------
def test_01_same_day_key_across_three_trees():
    ruler = Ruler()
    day_key = ruler.day_key(TS)
    assert day_key == DAY
    trees = ["memory.voice", "knowledge.domain", "structured.oil_sales_daily"]
    keys = {ruler.day_key(TS) for _ in trees}
    assert keys == {DAY}
    # 同一 day_key 在三棵树内分别为其独立主干的首圈
    assert [ruler.ring_index(t, day_key) for t in trees] == [0, 0, 0]


def test_day_boundary_asia_shanghai():
    ruler = Ruler()
    # UTC 2026-10-10T16:30:00Z == Asia/Shanghai 2026-10-11 00:30
    assert ruler.day_key("2026-10-10T16:30:00+00:00") == "2026-10-11"
    assert ruler.day_key("2026-10-10T15:30:00+00:00") == "2026-10-10"


# ---- 02 每天一圈 -------------------------------------------------------
def test_02_ring_index_unique_and_monotonic():
    ruler = Ruler()
    tree = "structured.oil_sales_daily"
    idx1 = ruler.ring_index(tree, "2026-10-09")
    idx2 = ruler.ring_index(tree, "2026-10-10")
    idx3 = ruler.ring_index(tree, "2026-10-11")
    assert (idx1, idx2, idx3) == (0, 1, 2)
    assert idx3 > idx2 > idx1
    # 幂等
    assert ruler.ring_index(tree, "2026-10-11") == idx3
    # 不允许插入更早日期
    with pytest.raises(ValueError):
        ruler.ring_index(tree, "2026-10-08")

    ref = RingRef(tree_id=tree, day_key=DAY, ring_index=idx3)
    assert ref.ring_index == 2


# ---- 03 三层载荷 -------------------------------------------------------
def test_03_three_layer_payload_separation():
    store = PayloadStore()
    tree = "memory.voice"
    inner_ref = store.put("inner", {"anchor": "place:home"})
    middle_ref = store.put("middle", {"summary": "scene"})
    outer_ref = store.put("outer", {"media": "s3://raw/clip-1"})

    for layer, ref, expected_store in [
        (Layer.inner, inner_ref, "inner_chain"),
        (Layer.middle, middle_ref, "middle_store"),
        (Layer.outer, outer_ref, "outer_chain"),
    ]:
        node = TopologyNode(
            node_id=TopologyNode.build_id(tree, DAY, layer.value),
            tree_id=tree,
            day_key=DAY,
            ring_index=0,
            layer=layer,
            payload_ref=ref,
        )
        assert node.store == expected_store
        # 身份与物理载荷分离
        assert node.node_id != node.payload_ref
        assert store.get(node.payload_ref) is not None

    # 载荷内容变化 -> payload_ref 变化，但节点身份不变
    ref_a = store.put("inner", {"anchor": "place:home"})
    ref_b = store.put("inner", {"anchor": "place:office"})
    assert ref_a == inner_ref and ref_b != inner_ref


# ---- 04 留痕不变式：aggregate_key 不含日期 ------------------------------
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2}|(?<!\d)\d{8}(?!\d)")


def test_04_aggregate_key_must_not_contain_date():
    trace = TraceRecord(
        trace_id="tr-1",
        aggregate_key="feature://acoustic-signature/9f2c1d",
        vector=["0.10", "0.20", "0.30"],
        source_ref="memory://media/1",
        created_at="2026-10-11T00:00:00Z",
    )
    # 正则断言：聚合身份不含日期
    assert DATE_RE.search(trace.aggregate_key) is None
    assert not contains_date(trace.aggregate_key)

    for bad in [
        "feature://acoustic-signature/2026-10-11",
        "feature://acoustic-signature/2026/10/11",
        "feature://acoustic-signature/20261011",
    ]:
        with pytest.raises(ValueError):
            assert_no_date_in_aggregate_key(bad)
        with pytest.raises(ValueError):
            TraceRecord(
                trace_id="tr-x",
                aggregate_key=bad,
                vector=["0.1"],
                source_ref="memory://m",
                created_at="now",
            )

    # 日期只能经 OccurrenceRef 表达
    occ = OccurrenceRef(
        occurrence_id="occ-1",
        tree_id="memory.voice",
        day_key=DAY,
        node_id="memory.voice/2026-10-11/cluster/sc-1",
        evidence_ref="ev-3",
    )
    assert occ.day_key == DAY
    assert "day_key" not in TraceRecord.model_fields


# ---- 03/05 星团与生长 --------------------------------------------------
def _trace(i: int, vector: list[str], key: str = "feature://acoustic-signature/9f2c1d") -> TraceRecord:
    return TraceRecord(
        trace_id=f"tr-{i}",
        aggregate_key=key,
        vector=vector,
        source_ref=f"memory://media/{i}",
        created_at="2026-10-11T00:00:00Z",
    )


def test_star_cluster_merges_within_epsilon_and_updates_centroid():
    index = StarClusterIndex("memory.voice", tolerance="eps=0.05")
    c = index.assign(_trace(1, ["0.10", "0.20"]))
    assert c.occurrence_count == 1 and c.dispersion == "0"

    # 邻近向量并入同一星团
    index.assign(_trace(2, ["0.11", "0.19"]))
    c = index.get(c.cluster_id)
    assert c.occurrence_count == 2
    assert len(c.member_trace_ids) == 2
    assert c.centroid == ["0.105", "0.195"]

    # 远向量建立新星团
    far = index.assign(_trace(3, ["0.90", "0.90"]))
    assert far.cluster_id != c.cluster_id
    assert len(index) == 2


def test_05_generation_immutable_and_rollback():
    store = PayloadStore()
    ruler = Ruler()
    tree = "memory.voice"
    ring_index = ruler.ring_index(tree, DAY)

    index = StarClusterIndex(tree, tolerance="eps=0.05")
    cluster = index.assign(_trace(1, ["0.10", "0.20"]))
    for i in range(2, 6):
        index.assign(_trace(i, ["0.11", "0.19"]))
    assert cluster.occurrence_count == 5

    gen1 = TopologyGeneration(generation=1, tree_id=tree, nodes={})
    engine = GrowthEngine(growth_threshold=3, growth_dimensions=["time", "place", "person"])
    assert engine.triggers(cluster)

    gen2 = engine.grow(gen1, [cluster], store, DAY, ring_index)
    assert gen2.generation == 2
    assert gen1.generation == 1  # 旧代未被就地修改
    assert len(gen2) > len(gen1)
    root_id = TopologyNode.build_id(tree, DAY, "cluster", cluster.cluster_id)
    assert len(gen2.node(root_id).children) == 3
    assert cluster.growth_events  # 生长事件被记录

    # 不可变：节点集合与属性均不可改
    with pytest.raises(TypeError):
        gen2.nodes["hack"] = gen1  # type: ignore[index]
    with pytest.raises(AttributeError):
        gen2.generation = 99  # type: ignore[misc]

    # 旧代仍可读、可回滚
    gs = GenerationStore()
    gs.commit(gen1)
    gs.commit(gen2)
    assert gs.current(tree).generation == 2
    assert gs.get(tree, 1) is gen1
    rolled = gs.rollback(tree, 1)
    assert rolled is gen1
    assert gs.current(tree) is gen1
    assert gs.get(tree, 2) is gen2  # 历史代依然保留、可读


def test_growth_no_trigger_returns_same_generation():
    store = PayloadStore()
    ruler = Ruler()
    tree = "memory.voice"
    ring_index = ruler.ring_index(tree, DAY)
    index = StarClusterIndex(tree, tolerance="eps=0.05")
    cluster = index.assign(_trace(1, ["0.10", "0.20"]))  # 仅 1 次

    gen1 = TopologyGeneration(generation=1, tree_id=tree, nodes={})
    engine = GrowthEngine(growth_threshold=3)
    gen2 = engine.grow(gen1, [cluster], store, DAY, ring_index)
    assert gen2 is gen1