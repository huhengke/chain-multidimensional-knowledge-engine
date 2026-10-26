from brains.knowledge import BuiltinKnowledgeRuntime, KnowledgeBrain, KnowledgeInput, KnowledgeSource
from brains.protocol import BrainQuery, ResultStatus


class CountingRuntime(BuiltinKnowledgeRuntime):
    def __init__(self):
        super().__init__()
        self.query_embed_calls = 0
        self._ingest_phase = True

    def embed(self, text: str) -> list[str]:
        if not self._ingest_phase:
            self.query_embed_calls += 1
        return super().embed(text)


def _input(content: str, raw_id: str = "fast-retrieval") -> KnowledgeInput:
    return KnowledgeInput(
        raw_id=raw_id,
        source=KnowledgeSource(source_id="fast-source", kind="text"),
        title="设备手册",
        content=content,
    )


def _query(text: str) -> BrainQuery:
    return BrainQuery(
        request_id="fast-request",
        trace_id="fast-trace",
        day_key="2026-10-18",
        raw_query=text,
        profile_id="alice",
    )


def test_text_hit_skips_query_embedding_and_supports_text_only_unit():
    runtime = CountingRuntime()
    brain = KnowledgeBrain(runtime=runtime)
    document = brain.ingest(_input("# 温度监控\n设备运行温度应保持在合理区间。"))
    brain.publish(document.document_id)
    runtime._ingest_phase = False

    unit = next(unit for unit in brain.store.document_units(document.document_id) if unit.chunk_level == 1)
    brain.store.unit_vector[unit.unit_id] = []
    brain.prepare_query_index()

    result = brain.query(_query("温度监控"))

    assert result.status is ResultStatus.ok
    assert result.claims
    assert runtime.query_embed_calls == 0
    assert brain.last_retrieval_stats["mode"] in {"lexical", "boundary"}
    assert brain.last_retrieval_stats["embedding_skipped"] is True
    assert brain.query_index_stats["text_unit_count"] >= 1
    assert brain.query_index_stats["vector_unit_count"] < brain.query_index_stats["text_unit_count"]


def test_semantic_fallback_embeds_once_then_uses_query_cache():
    runtime = CountingRuntime()
    brain = KnowledgeBrain(runtime=runtime)
    document = brain.ingest(_input("# 温度监控\n设备运行温度应保持在合理区间。"))
    brain.publish(document.document_id)
    runtime._ingest_phase = False

    query = _query("完全不同的语义问题")
    brain.query(query)
    first_count = runtime.query_embed_calls
    brain.query(query)

    assert first_count == 1
    assert runtime.query_embed_calls == first_count
    assert brain.last_retrieval_stats["embedding_used"] is True
    assert brain.last_retrieval_stats["embedding_cache_hit"] is True


def test_native_hits_are_primary_and_skip_query_embedding():
    runtime = CountingRuntime()
    brain = KnowledgeBrain(runtime=runtime)
    document = brain.ingest(_input("# 温度监控\n设备运行温度应保持在合理区间。"))
    brain.publish(document.document_id)
    unit = next(unit for unit in brain.store.document_units(document.document_id) if unit.chunk_level == 1)
    runtime._ingest_phase = False
    runtime.rerank_mode = "off"
    calls = []
    runtime.native_retrieve = lambda query, *, tree_ids=None, limit=40: calls.append((query, tree_ids, limit)) or {
        "hits": [{"id": unit.unit_id, "score": 0.91}],
        "stats": {"search_ms": 2.0, "chunk_hits": 1, "mapped_units": 1},
    }

    result = brain.query(_query("温度监控"))

    assert result.status is ResultStatus.ok and result.claims
    assert calls and calls[0][0] == "温度监控"
    assert brain.last_retrieval_stats["mode"] == "native"
    assert brain.last_retrieval_stats["native_status"] == "ready"
    assert brain.last_retrieval_stats["embedding_used"] is False
    assert brain.last_retrieval_stats["embedding_skipped"] is True
    assert runtime.query_embed_calls == 0


def test_native_hits_filter_unpublished_units_before_adaptive_rerank():
    runtime = CountingRuntime()
    brain = KnowledgeBrain(runtime=runtime)
    first = brain.ingest(_input("# 设备甲\n温度正常。", raw_id="native-a"))
    second = brain.ingest(_input("# 设备乙\n压力正常。", raw_id="native-b"))
    draft = brain.ingest(_input("# 草稿\n温度异常。", raw_id="native-draft"))
    brain.publish(first.document_id)
    brain.publish(second.document_id)
    brain.store.publications[draft.document_id].status = "pending_review"
    unit_a = next(unit for unit in brain.store.document_units(first.document_id) if unit.chunk_level == 1)
    unit_b = next(unit for unit in brain.store.document_units(second.document_id) if unit.chunk_level == 1)
    unit_draft = next(unit for unit in brain.store.document_units(draft.document_id) if unit.chunk_level == 1)
    runtime._ingest_phase = False
    runtime.rerank_mode = "always"
    rerank_calls = []

    def rerank(query, candidates):
        rerank_calls.append([unit_id for unit_id, _text in candidates])
        return list(reversed(candidates))

    runtime.rerank = rerank
    runtime.native_retrieve = lambda query, *, tree_ids=None, limit=40: {
        "hits": [{"id": unit_draft.unit_id, "score": 0.99},
                 {"id": unit_a.unit_id, "score": 0.9},
                 {"id": unit_b.unit_id, "score": 0.8}],
        "stats": {"search_ms": 1.0, "chunk_hits": 3, "mapped_units": 3},
    }

    result = brain.query(_query("温度压力"))

    assert result.status is ResultStatus.ok and result.claims
    assert rerank_calls == [[unit_a.unit_id, unit_b.unit_id]]
    assert unit_draft.unit_id not in brain._last_retrieval_scores
    assert list(brain._last_retrieval_scores) == [unit_b.unit_id, unit_a.unit_id]
    assert brain.last_retrieval_stats["native_candidate_count"] == 3
    assert brain.last_retrieval_stats["native_eligible_count"] == 2
    assert brain.last_retrieval_stats["rerank_candidates"] == 2


def test_match_tokens_ignore_layout_punctuation_and_spaces():
    from brains.knowledge.src.brain import _match_tokens

    assert _match_tokens("妇人中风右半身不遂") == _match_tokens("妇人中风， 右 半身不遂")


def test_adaptive_head_exact_second_unit_skips_rerank():
    runtime = CountingRuntime()
    brain = KnowledgeBrain(runtime=runtime)
    generic = brain.ingest(_input("# 一般\n设备运行温度应保持在合理区间。", raw_id="adaptive-generic"))
    exact = brain.ingest(_input("# 监控\n温度监控系统会记录温度。", raw_id="adaptive-exact"))
    brain.publish(generic.document_id)
    brain.publish(exact.document_id)
    first = next(unit for unit in brain.store.document_units(generic.document_id) if unit.chunk_level == 1)
    second = next(unit for unit in brain.store.document_units(exact.document_id) if unit.chunk_level == 1)
    runtime._ingest_phase = False
    runtime.rerank_mode = "adaptive"
    calls = []
    runtime.rerank = lambda query, candidates: calls.append(candidates) or list(candidates)
    runtime.native_retrieve = lambda query, *, tree_ids=None, limit=40: {
        "hits": [{"id": first.unit_id, "score": 0.9},
                 {"id": second.unit_id, "score": 0.89}],
        "stats": {"search_ms": 1.0, "chunk_hits": 2, "mapped_units": 2},
    }

    brain.query(_query("温度监控"))

    assert calls == []
    assert brain.last_retrieval_stats["rerank_candidates"] == 0


def test_native_equal_score_units_prefer_the_lexical_match():
    runtime = CountingRuntime()
    brain = KnowledgeBrain(runtime=runtime)
    heated = brain.ingest(_input("# 设备甲\n温度正常。", raw_id="native-temp"))
    pressed = brain.ingest(_input("# 设备乙\n压力正常。", raw_id="native-pressure"))
    brain.publish(heated.document_id)
    brain.publish(pressed.document_id)
    temperature = next(unit for unit in brain.store.document_units(heated.document_id) if unit.chunk_level == 1)
    pressure = next(unit for unit in brain.store.document_units(pressed.document_id) if unit.chunk_level == 1)
    runtime._ingest_phase = False
    runtime.rerank_mode = "off"
    runtime.native_retrieve = lambda query, *, tree_ids=None, limit=40: {
        "hits": [{"id": temperature.unit_id, "score": 0.9},
                 {"id": pressure.unit_id, "score": 0.9}],
        "stats": {"search_ms": 1.0, "chunk_hits": 2, "mapped_units": 2},
    }

    brain.query(_query("压力"))

    assert next(iter(brain._last_retrieval_scores)) == pressure.unit_id


def test_native_exact_phrase_leads_even_from_a_lower_scored_chunk():
    runtime = CountingRuntime()
    brain = KnowledgeBrain(runtime=runtime)
    generic = brain.ingest(_input("# 设备甲\n设备运行温度应保持在合理区间。", raw_id="native-generic"))
    exact = brain.ingest(_input("# 设备乙\n压力监控系统会持续记录压力。", raw_id="native-exact"))
    brain.publish(generic.document_id)
    brain.publish(exact.document_id)
    generic_unit = next(unit for unit in brain.store.document_units(generic.document_id) if unit.chunk_level == 1)
    exact_unit = next(unit for unit in brain.store.document_units(exact.document_id) if unit.chunk_level == 1)
    runtime._ingest_phase = False
    runtime.rerank_mode = "adaptive"
    rerank_calls = []
    runtime.rerank = lambda query, candidates: rerank_calls.append(candidates) or list(candidates)
    runtime.native_retrieve = lambda query, *, tree_ids=None, limit=40: {
        "hits": [{"id": generic_unit.unit_id, "score": 0.93},
                 {"id": exact_unit.unit_id, "score": 0.81}],
        "stats": {"search_ms": 1.0, "chunk_hits": 2, "mapped_units": 2},
    }

    brain.query(_query("压力监控"))

    assert next(iter(brain._last_retrieval_scores)) == exact_unit.unit_id
    assert rerank_calls == []
    assert brain.last_retrieval_stats["rerank_candidates"] == 0


def test_corpus_expansion_borrows_corpus_terms_only_when_the_question_has_none():
    brain = KnowledgeBrain(runtime=BuiltinKnowledgeRuntime())
    for index, content in enumerate([
        "咳嗽痰多者慎用寒凉之品，久咳不愈当温肺化饮。",
        "咳嗽遇风寒则加重，宜温肺化饮，忌生冷。",
        "仓库月度盘点由值班员负责，登记出库数量。",
    ]):
        brain.publish(brain.ingest(_input(content, raw_id=f"expansion-{index}")).document_id)
    brain.prepare_query_index()
    max_units = max(256, 20 * 32)

    # The question already uses a bigram the corpus stores, so the plain native
    # route stays untouched.
    assert brain._corpus_expansion("咳嗽痰多，怎么办", max_units=max_units) == []

    # A colloquial phrasing whose bigrams exist nowhere in the corpus is widened
    # with the terms the corpus itself attaches to the shared characters, and
    # only with terms corroborated by more than one unit.
    expansion = brain._corpus_expansion("咳个不停，怎么办", max_units=max_units)
    assert "咳嗽" in expansion
    assert not {"痰多", "久咳", "盘点"} & set(expansion)
    assert not {"咳个", "个不", "不停", "怎么", "么办"} & set(expansion)
