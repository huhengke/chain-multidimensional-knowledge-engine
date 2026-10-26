"""M4 知识认知脑（KnowledgeBrain），实现 ``brains.protocol.Brain``。

- 五层导入管道由 ``KnowledgePipeline`` 编排（parse/segment/annotate/gate/publish）；
- 检索：向量/词项只做**候选召回**，本体过滤与权限判断单独生效；
- 结论只来自**已发布**版本；未决门禁（``pending_review``）只作 warnings/待核对线索；
- 冲突**并列呈现**双方来源/条件/发布日期，禁止静默裁决；
- 运行时由宿主注入；生产平台使用内嵌 WeKnora，隔离测试可使用 builtin。
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from decimal import Decimal
from typing import Callable

from brains.knowledge.src.contract import CanonicalDocument, KnowledgeReasoningResult, KnowledgeRoute
from brains.knowledge.src.ontology import OntologyStore, domain_risk
from brains.knowledge.src.pipeline import (
    DocumentPublication,
    GateRecord,
    KnowledgePipeline,
    KnowledgeStore,
)
from brains.knowledge.src.reasoning import ProfessionalReasoner, ReasoningRule
from brains.knowledge.src.query_index import KnowledgeQueryIndex
from brains.knowledge.src.runtime_adapter import (
    BuiltinKnowledgeRuntime,
    KnowledgeInput,
    KnowledgeRuntime,
    RuntimeMetadata,
    quantize,
    tokenize,
)
from brains.knowledge.src.temporal import KnowledgeTemporalIndex
from brains.protocol import (
    BrainName,
    BrainQuery,
    BrainResult,
    Claim,
    EvidenceRef,
    ResultStatus,
)
from topology.src.ruler import Ruler

_NON_MATCHING_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")
_HAN_RE = re.compile(r"[\u4e00-\u9fff]")
_STRONG_COVERAGE = Decimal("0.7")


def _match_tokens(text: str) -> set[str]:
    """Bigram/Latin tokens after removing punctuation and layout whitespace."""
    return {token for token in tokenize(_NON_MATCHING_RE.sub("", text or "")) if len(token) >= 2}


def _query_coverage(q_tokens: set[str], text: str) -> Decimal:
    if not q_tokens:
        return Decimal(0)
    return Decimal(len(q_tokens & _match_tokens(text))) / Decimal(len(q_tokens))


class KnowledgeBrain:
    """知识脑。可直接注入控制面 ``brains: dict[BrainName, Brain]``。"""

    name: BrainName = BrainName.knowledge

    def __init__(
        self,
        runtime: KnowledgeRuntime | None = None,
        *,
        ruler: Ruler | None = None,
        tolerance: str = "eps=0.05",
        growth_threshold: int = 3,
        ontology: OntologyStore | None = None,
    ) -> None:
        self.runtime: KnowledgeRuntime = runtime or BuiltinKnowledgeRuntime()
        self.ruler = ruler or Ruler()
        self.tolerance = tolerance
        self.growth_threshold = growth_threshold
        self.ontology = ontology or OntologyStore()
        self.store = KnowledgeStore()
        self.pipeline = KnowledgePipeline(
            self.runtime,
            self.store,
            self.ontology,
            self.ruler,
            tolerance=tolerance,
            growth_threshold=growth_threshold,
        )
        self.reasoner: ProfessionalReasoner = self.pipeline.reasoner
        self.last_route: KnowledgeRoute | None = None
        self._last_retrieval_scores: dict[str, dict[str, Decimal]] = {}
        self.last_retrieval_stats: dict[str, object] = {}
        self._query_embedding_cache: OrderedDict[tuple[str, str], list[str]] = OrderedDict()
        self._query_embedding_cache_limit = 256
        self.query_index = KnowledgeQueryIndex()
        self.query_index_stats: dict[str, int] = {}
        # A query workspace is immutable while it is active.  Import/review
        # code explicitly invalidates this flag before changing that workspace;
        # query execution must never derive freshness by scanning every unit.
        self.query_index_ready = False
        self.temporal_index: KnowledgeTemporalIndex = self.store.temporal_index
        self.temporal_index_stats: dict[str, int] = {}

    def prepare_query_index(self) -> dict[str, int]:
        """Build the rebuildable forest/ring/cluster projection off the hot path."""
        self.query_index_stats = self.query_index.rebuild(self.store)
        generation_by_tree = {}
        for tree_id in self.store.clusters:
            history = self.store.generations.history(tree_id)
            if history:
                generation_by_tree[tree_id] = self.store.generations.current(tree_id).generation
        self.temporal_index_stats = self.temporal_index.rebuild(
            units=(unit for unit in self.store.units.values() if unit.chunk_level == 1),
            unit_tree=self.store.unit_tree,
            anchors_by_unit=self.store.unit_content_time,
            ingested_at_by_unit=self.store.unit_ingested_at,
            observed_at_by_unit=self.store.unit_observed_at,
            created_at=self.ruler.day_key(),
            generation_by_tree=generation_by_tree,
        )
        self.query_index_ready = True
        return dict(self.query_index_stats)

    def invalidate_query_index(self) -> None:
        """Mark a mutable workspace dirty; the next controlled preparation rebuilds it."""
        self.query_index_ready = False

    def ensure_query_index(self) -> dict[str, int]:
        """Return the active projection without an O(N) freshness scan.

        Import and artifact activation are the only writers of a workspace.
        Older in-memory callers that have not prepared an index still retain a
        safe one-time fallback, but normal queries only read this state.
        """
        if not self.query_index_ready:
            return self.prepare_query_index()
        return dict(self.query_index_stats)

    def compact_query_payload(self) -> None:
        """Release duplicate Decimal-string vectors after a read index is ready.

        The active query matrix is held by ``KnowledgeQueryIndex`` and persisted
        as safe NPY artifacts.  Keeping per-unit and per-trace string copies in
        a stable workspace multiplied memory without improving retrieval.
        """
        self.ensure_query_index()
        dimensions = {
            trace_id: self.store.trace_vector_dimensions.get(trace_id, len(trace.vector))
            for trace_id, trace in self.store.traces.items()
        }
        self.store.trace_vector_dimensions = dimensions
        self.store.traces = {
            trace_id: trace.model_copy(update={"vector": ["0"]})
            for trace_id, trace in self.store.traces.items()
        }
        self.store.unit_vector.clear()

    # ---- 运行时元数据（如实标注）-----------------------------------------
    @property
    def runtime_meta(self) -> RuntimeMetadata:
        return self.runtime.metadata

    @property
    def runtime_warning(self) -> str:
        meta = self.runtime.metadata
        if meta.runtime != "builtin":
            return (
                f"知识运行时：{meta.runtime}（上游状态 {meta.upstream_status}；"
                f"健康状态 {meta.health_status}）"
            )
        return (
            f"知识运行时：{meta.runtime}（上游 {meta.upstream} 尚未接入，"
            f"状态 {meta.upstream_status}）"
        )

    # ---- 导入 / 发布 / 复核 ----------------------------------------------
    def ingest(
        self,
        raw: KnowledgeInput,
        *,
        stage_callback: Callable[[str], None] | None = None,
    ) -> CanonicalDocument:
        """走完五层管道，返回 ``CanonicalDocument``。"""
        self.invalidate_query_index()
        return self.pipeline.run(raw, stage_callback=stage_callback)

    def publish(self, document_id: str) -> DocumentPublication:
        return self.pipeline.publish(document_id)

    def review(
        self,
        document_id: str,
        *,
        action: str,
        reviewer: str | None = None,
        reason: str | None = None,
    ) -> DocumentPublication:
        publication = self.pipeline.review(
            document_id, action=action, reviewer=reviewer, reason=reason
        )
        document = self.store.documents.get(document_id)
        if document is not None:
            document.document_status = publication.status
        return publication

    def add_rule(self, rule: ReasoningRule) -> ReasoningRule:
        return self.reasoner.add_rule(rule)

    def pending_gates(self, document_id: str) -> list[GateRecord]:
        return [
            self.store.gates[gid]
            for gid in self.store.gate_by_document.get(document_id, [])
            if self.store.gates[gid].status == "pending_review"
        ]

    # ---- 检索 -------------------------------------------------------------
    def _allowed_scopes(self, query: BrainQuery) -> set[str]:
        if query.profile_id is None:
            return {"public"}
        return {"public", "owner", query.profile_id}

    def _rerank_plan(self, query: BrainQuery, candidates: list[dict]) -> tuple[list[dict], str]:
        """Choose a bounded cross-encoder pool; adaptive is the default.

        Native RRF already resolves exact term hits well.  Paying a 2s+
        cross-encoder for every query was the dominant interactive cost, so
        only an ambiguous head is reranked and the pool is capped at five.
        """
        mode = getattr(self.runtime, "rerank_mode", "off")
        if mode == "off" or len(candidates) < 2:
            return [], mode
        cap = min(5, len(candidates))
        if mode == "always":
            return candidates[:cap], mode
        q_tokens = _match_tokens(query.raw_query)
        if not q_tokens:
            return [], mode
        top = candidates[0]
        # RRF often puts the exact unit second when two chunks are nearly
        # tied.  Judge the head, not only rank 1, so a strong lexical match
        # in the first three candidates avoids a 1.3s cross-encoder pass.
        coverage = max(
            _query_coverage(q_tokens, candidate["text"])
            for candidate in candidates[:3]
        )
        top_score = Decimal(str(top["score"]))
        second_score = Decimal(str(candidates[1]["score"]))
        gap = top_score / second_score if second_score > 0 else Decimal(10)
        if coverage >= _STRONG_COVERAGE or (coverage >= Decimal("0.5") and gap >= Decimal("1.35")):
            return [], mode
        return candidates[:cap], mode

    def _corpus_expansion(self, raw_query: str, *, max_units: int) -> list[str]:
        """Corpus terms for a question that does not speak the corpus's language.

        Seeds are the units a single character of the question already touches;
        the returned terms are the bigrams corroborated by several of those
        units, ranked by how many seed units share them.  Nothing is listed by
        hand: the mapping is recomputed from the live posting index, so a new
        corpus changes the expansion with it.  Returns nothing as soon as one
        question bigram already has a selective posting, because then the plain
        native route is the right one and must stay untouched.
        """
        tokens = set(tokenize(raw_query))
        counts = self.query_index.posting_count
        if any(len(token) >= 2 and 0 < counts(token) <= max_units for token in tokens):
            return []
        seeds: set[str] = set()
        for token in sorted(item for item in tokens if len(item) == 1):
            if 0 < counts(token) <= max_units:
                seeds |= self.query_index.units_for(token)
        if not seeds:
            return []
        shared: dict[str, set[str]] = {}
        for unit_id in sorted(seeds)[:400]:
            text = self.store.unit_text.get(unit_id, "")
            if not text:
                continue
            for term in _match_tokens(text):
                if term in tokens or not _HAN_RE.search(term):
                    continue
                shared.setdefault(term, set()).add(unit_id)
        ranked = sorted(
            ((-len(units), term) for term, units in shared.items()
             if len(units) > 1 and 0 < counts(term) <= max_units),
        )
        return [term for _, term in ranked[:6]]

    def _native_matches(self, query: BrainQuery, hits: list[dict], stats: dict,
                        candidate_limit: int) -> list[tuple[str, Decimal, bool]] | None:
        """Permission/publication-filter native hits before any provider call."""
        q_tokens = _match_tokens(query.raw_query)
        allowed = self._allowed_scopes(query)
        scores: dict[str, dict[str, Decimal]] = {}
        candidates: list[dict] = []
        for hit in hits:
            unit_id = hit.get("id")
            unit = self.store.units.get(unit_id)
            if unit is None:
                continue
            evidence_ids = self.store.evidence_by_unit.get(unit_id, [])
            if not evidence_ids or not self.store.is_published(unit.document_id):
                continue
            if not any(set(self.store.evidence[eid].permission_scope) & allowed for eid in evidence_ids):
                continue
            text = self.store.unit_text.get(unit_id, "")
            if not text:
                continue
            lexical = self._overlap(q_tokens, text)
            coverage = _query_coverage(q_tokens, text)
            score = Decimal(str(hit.get("score") or 0))
            candidates.append({"id": unit_id, "text": text, "score": score, "coverage": coverage})
            scores[unit_id] = {"lexical": lexical, "semantic": Decimal(0), "fusion": score, "rerank": Decimal(0)}
        stats["native_eligible_count"] = len(candidates)
        if not candidates:
            return None
        # Native RRF scores a whole chunk, so every unit expanded from it
        # shares one score.  A generic chunk with many units can otherwise
        # bury the unit that actually contains the query.  Strong phrase
        # coverage leads; within the same band the native score still wins.
        candidates.sort(key=lambda item: (
            -int(item["coverage"] >= _STRONG_COVERAGE),
            -item["score"],
            -item["coverage"],
            -scores[item["id"]]["lexical"],
        ))
        pool, mode = self._rerank_plan(query, candidates)
        stats["rerank_mode"] = mode
        stats["rerank_candidates"] = len(pool)
        order = [item["id"] for item in candidates]
        if pool:
            started = time.perf_counter()
            ranked = []
            try:
                ranked = self.runtime.rerank(
                    query.raw_query, [(item["id"], item["text"]) for item in pool])
            except Exception as exc:  # rerank is an optional quality step
                stats["rerank_error"] = type(exc).__name__
            stats["rerank_ms"] = round((time.perf_counter() - started) * 1000, 3)
            if ranked:
                reranked = []
                for unit_id, _text in ranked:
                    if unit_id in scores and unit_id not in reranked:
                        reranked.append(unit_id)
                reranked_set = set(reranked)
                order = reranked + [unit_id for unit_id in order if unit_id not in reranked_set]
        order = order[:candidate_limit]
        total = max(1, len(order))
        self._last_retrieval_scores = {}
        matches = []
        for rank, unit_id in enumerate(order):
            scores[unit_id]["rerank"] = Decimal(total - rank) / Decimal(total)
            self._last_retrieval_scores[unit_id] = scores[unit_id]
            matches.append((unit_id, scores[unit_id]["rerank"], True))
        return matches

    def _embed_query(self, text: str) -> tuple[list[str], float, bool]:
        """Embed a query lazily and reuse recent queries in the same model space."""
        metadata = self.runtime.metadata
        key = (metadata.runtime_version or metadata.runtime, text)
        cached = self._query_embedding_cache.get(key)
        if cached is not None:
            self._query_embedding_cache.move_to_end(key)
            return list(cached), 0.0, True
        started = time.perf_counter()
        vector = list(self.runtime.embed(text))
        self._query_embedding_cache[key] = list(vector)
        self._query_embedding_cache.move_to_end(key)
        while len(self._query_embedding_cache) > self._query_embedding_cache_limit:
            self._query_embedding_cache.popitem(last=False)
        return vector, round((time.perf_counter() - started) * 1000, 3), False

    def _retrieve(self, query: BrainQuery, *, candidate_limit: int = 20):
        """分层候选召回（树/日圈/星团→周存）→ 权限与发布过滤。

        Text/time/boundary postings are tried before query embedding.  A
        semantic call is made only when the bounded lexical route is too broad
        or empty; its candidates still go through WeKnora fusion and rerank.
        The query index keeps the remaining work bounded and never refills an
        empty explicit time intersection from the whole forest.
        """
        started = time.perf_counter()
        stats: dict[str, object] = {
            "mode": "unknown",
            "embedding_used": False,
            "embedding_skipped": False,
            "embedding_cache_hit": False,
            "embedding_ms": 0.0,
            "lexical_candidate_count": 0,
            "candidate_count": 0,
        }

        def finish() -> None:
            stats["total_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.last_retrieval_stats = dict(stats)

        q_tokens = set(tokenize(query.raw_query))
        annotation = self.ontology.annotate(query.raw_query)
        query_domain = annotation.domain
        allowed = self._allowed_scopes(query)
        index_started = time.perf_counter()
        self.query_index_stats = self.ensure_query_index()
        stats["index_ms"] = round((time.perf_counter() - index_started) * 1000, 3)

        tree_ids = None
        if query_domain != "general":
            # The ontology is a ranking hint, not an access boundary.  Keep
            # the general tree in the resonance lane so a medical question
            # can still cite a general safety/definition document; the final
            # publication and permission gates remain authoritative below.
            tree_ids = {f"knowledge.{query_domain}", "knowledge.general"}
        time_scope = query.time_scope
        forced_unit_ids = query.constraints.get("candidate_unit_ids") if query.constraints else None
        if forced_unit_ids is not None and not forced_unit_ids:
            self._last_retrieval_scores = {}
            stats.update({"mode": "empty-boundary", "embedding_skipped": True, "boundary": True})
            finish()
            return annotation, []

        start = time_scope.start if time_scope else None
        end = time_scope.end if time_scope else None
        # WeKnora's native FTS5 + sqlite-vec + RRF index is the production
        # retrieval path.  Explicit time/cross-brain boundaries stay on the
        # canonical posting index because the native projection has no
        # temporal filters; an unavailable or stale native index falls back.
        if forced_unit_ids is None and start is None and end is None and hasattr(self.runtime, "native_retrieve"):
            native_started = time.perf_counter()
            expansion = self._corpus_expansion(query.raw_query, max_units=max(256, candidate_limit * 32))
            if expansion:
                stats["corpus_expansion"] = expansion
            native = self.runtime.native_retrieve(
                " ".join([query.raw_query, *expansion]), tree_ids=tree_ids, limit=max(candidate_limit * 2, 24))
            stats["native_ms"] = round((time.perf_counter() - native_started) * 1000, 3)
            if native is None:
                stats["native_status"] = "unavailable"
            else:
                stats.update(native.get("stats") or {})
                stats["native_status"] = "ready"
                native_hits = native.get("hits") or []
                stats["native_candidate_count"] = len(native_hits)
                native_matches = self._native_matches(query, native_hits, stats, candidate_limit)
                if native_matches is not None:
                    stats["mode"] = "native"
                    stats["embedding_used"] = False
                    stats["embedding_skipped"] = True
                    stats["candidate_count"] = len(native_matches)
                    finish()
                    return annotation, native_matches
                stats["native_status"] = "no-eligible-hits"
            stats["native_fallback"] = stats["native_status"]
            self._last_retrieval_scores = {}

        max_units = max(256, candidate_limit * 32)
        lexical_started = time.perf_counter()
        lexical_ids = self.query_index.candidates(
            [], query.raw_query, tree_ids=tree_ids, start=start, end=end,
            forced_unit_ids=forced_unit_ids, max_units=max_units,
        )
        stats["lexical_ms"] = round((time.perf_counter() - lexical_started) * 1000, 3)
        stats["lexical_candidate_count"] = len(lexical_ids)
        specific_lexical_hits = sum(
            1
            for token in q_tokens
            if len(token) >= 2
            and 0 < self.query_index.posting_count(token) <= max_units
        )
        stats["specific_lexical_hits"] = specific_lexical_hits

        query_vector: list[str] = []
        lexical_fast_path = bool(lexical_ids) and (
            specific_lexical_hits > 0
            and len(lexical_ids) <= max(candidate_limit * 8, 64)
        )
        if forced_unit_ids is not None or lexical_fast_path:
            # A temporal/cross-brain boundary or a text posting hit is already
            # a bounded route.  Do not pay for query embedding or a provider
            # rerank just to rediscover the same exact terms.
            candidate_ids = lexical_ids
            stats["mode"] = "boundary" if forced_unit_ids is not None else "lexical"
            stats["embedding_skipped"] = True
            stats["boundary"] = forced_unit_ids is not None or bool(start or end)
        else:
            query_vector, embedding_ms, cache_hit = self._embed_query(query.raw_query)
            stats.update({
                "mode": "semantic",
                "embedding_used": True,
                "embedding_ms": embedding_ms,
                "embedding_cache_hit": cache_hit,
            })
            candidate_started = time.perf_counter()
            candidate_ids = self.query_index.candidates(
                query_vector, query.raw_query, tree_ids=tree_ids, start=start, end=end,
                forced_unit_ids=forced_unit_ids, max_units=max_units,
            )
            stats["semantic_candidate_ms"] = round((time.perf_counter() - candidate_started) * 1000, 3)

        # An empty bounded index is a real no-hit.  Never refill it from every
        # unit: doing that defeats the time/star-cluster boundary and was the
        # source of the old full-corpus scan.
        stats["candidate_count"] = len(candidate_ids)

        if hasattr(self.runtime, 'retrieve'):
            # Filter locally before sending any content to the model provider.
            candidates = []
            self._last_retrieval_scores = {}
            for unit_id in candidate_ids:
                unit = self.store.units.get(unit_id)
                if unit is None:
                    continue
                evidence_ids = self.store.evidence_by_unit.get(unit_id, [])
                if not evidence_ids or not self.store.is_published(unit.document_id):
                    continue
                if not any(set(self.store.evidence[eid].permission_scope) & allowed for eid in evidence_ids):
                    continue
                text = self.store.unit_text.get(unit_id, '')
                lexical = self._overlap(q_tokens, text)
                semantic = self._cosine(query_vector, self.query_index.vector_for(unit_id) or self.store.unit_vector.get(unit_id, []))
                if lexical <= 0 and semantic < Decimal('0.3'):
                    continue
                candidates.append({'id':unit_id,'text':text,'lexical':str(lexical),'semantic':str(max(Decimal(0),semantic))})
                self._last_retrieval_scores[unit_id] = {'lexical':lexical,'semantic':semantic,'fusion':Decimal(0),'rerank':Decimal(0)}
            if not candidates:
                finish()
                return annotation, []
            runtime_started = time.perf_counter()
            if query_vector:
                ranked = self.runtime.retrieve(query.raw_query, candidates, candidate_limit=candidate_limit)
                stats["fusion_and_rerank_ms"] = round((time.perf_counter() - runtime_started) * 1000, 3)
            else:
                # Lexical/time fast path: WeKnora is still the source of the
                # stored units and evidence, but no semantic provider call is
                # necessary after an exact bounded posting hit.
                ranked_candidates = sorted(
                    candidates,
                    key=lambda item: (-Decimal(item["lexical"]), item["id"]),
                )[:candidate_limit]
                ranked = [
                    {"id": item["id"], "score": item["lexical"], "rank": rank}
                    for rank, item in enumerate(ranked_candidates)
                ]
                stats["local_rank_ms"] = round((time.perf_counter() - runtime_started) * 1000, 3)
            matches=[]
            for item in ranked:
                scores=self._last_retrieval_scores[item['id']]
                scores['fusion']=Decimal(item['score'])
                scores['rerank']=Decimal(len(ranked)-item['rank'])/Decimal(len(ranked))
                matches.append((item['id'],scores['rerank'],True))
            finish()
            return annotation, matches

        candidates: list[tuple[str, str]] = []
        for unit_id in candidate_ids:
            unit = self.store.units.get(unit_id)
            if unit is None:
                continue
            if not self.store.evidence_by_unit.get(unit_id):
                continue
            candidates.append((unit_id, self.store.unit_text.get(unit_id, "")))
        if query_vector:
            rerank_started = time.perf_counter()
            ranked = self.runtime.rerank(query.raw_query, candidates)
            stats["rerank_ms"] = round((time.perf_counter() - rerank_started) * 1000, 3)
        else:
            ranked = sorted(
                candidates,
                key=lambda item: (-self._overlap(q_tokens, item[1]), item[0]),
            )
            stats["local_rank_ms"] = 0.0
        rank_order = {candidate_id: position for position, (candidate_id, _) in enumerate(ranked)}
        total_ranked = max(1, len(ranked))
        self._last_retrieval_scores = {}

        matches = []
        for unit_id in candidate_ids:
            unit = self.store.units.get(unit_id)
            if unit is None:
                continue
            if not self.store.evidence_by_unit.get(unit_id):
                continue
            lexical = self._overlap(q_tokens, self.store.unit_text.get(unit_id, ""))
            semantic = self._cosine(query_vector, self.query_index.vector_for(unit_id) or self.store.unit_vector.get(unit_id, []))
            fusion = (lexical + semantic) / Decimal(2)
            rerank = Decimal(total_ranked - rank_order.get(unit_id, total_ranked)) / Decimal(total_ranked)
            self._last_retrieval_scores[unit_id] = {
                "lexical": lexical,
                "semantic": semantic,
                "fusion": fusion,
                "rerank": rerank,
            }
            score = fusion
            if query_domain != "general" and self.store.unit_tree.get(unit_id) == f"knowledge.{query_domain}":
                score += Decimal("0.5")
            concept_hit = False
            for label in unit.concept_path:
                if label and label in query.raw_query:
                    score += Decimal("0.5")
                    concept_hit = True
            # 内建哈希向量会有桶碰撞；没有词项、本体或概念锚点时不得把偶然
            # 向量重合升级为可引用候选。
            domain_hit = query_domain != "general" and self.store.unit_tree.get(unit_id) == f"knowledge.{query_domain}"
            if lexical == 0 and not domain_hit and not concept_hit:
                continue
            if score <= 0:
                continue
            evidence_ids = self.store.evidence_by_unit.get(unit_id, [])
            permitted = any(
                set(self.store.evidence[eid].permission_scope) & allowed for eid in evidence_ids
            )
            matches.append((unit_id, score, permitted))

        matches.sort(key=lambda item: (-item[1], item[0]))
        finish()
        return annotation, matches

    @staticmethod
    def _cosine(query_vector: list[str], unit_vector: list[str]) -> Decimal:
        if not query_vector or not unit_vector or len(query_vector) != len(unit_vector):
            return Decimal(0)
        left = [Decimal(item) for item in query_vector]
        right = [Decimal(item) for item in unit_vector]
        numerator = sum((a * b for a, b in zip(left, right)), Decimal(0))
        left_norm = sum((a * a for a in left), Decimal(0)).sqrt()
        right_norm = sum((b * b for b in right), Decimal(0)).sqrt()
        if left_norm == 0 or right_norm == 0:
            return Decimal(0)
        return numerator / (left_norm * right_norm)

    @staticmethod
    def _overlap(q_tokens: set[str], text: str) -> Decimal:
        if not q_tokens:
            return Decimal(0)
        t_tokens = _match_tokens(text)
        if not t_tokens:
            return Decimal(0)
        return Decimal(len(q_tokens & t_tokens)) / Decimal(len(q_tokens | t_tokens))

    def query(self, query: BrainQuery) -> BrainResult:
        warnings: list[str] = [self.runtime_warning]
        claims: list[Claim] = []
        evidence: list[EvidenceRef] = []
        annotation, matches = self._retrieve(query)

        selected: list[str] = []
        for unit_id, _score, permitted in matches:
            unit = self.store.units[unit_id]
            if not self.store.is_published(unit.document_id):
                warnings.append(
                    f"命中知识单元 {unit_id} 属未发布（门禁 pending_review），仅作待核对线索，不作为结论。"
                )
                evidence.append(self._evidence_ref(unit_id))
                continue
            if not permitted:
                warnings.append(f"命中知识单元 {unit_id} 权限不足，仅作待核对线索。")
                evidence.append(self._evidence_ref(unit_id))
                continue
            if unit_id not in selected:
                selected.append(unit_id)

        # 冲突并列：把冲突对端也纳入，绝不静默取其一
        conflict_notes, extra = self._resolve_conflicts(selected)
        warnings.extend(conflict_notes)
        for unit_id in extra:
            unit = self.store.units[unit_id]
            permitted = any(set(self.store.evidence[eid].permission_scope) & self._allowed_scopes(query)
                            for eid in self.store.evidence_by_unit.get(unit_id, []))
            if not self.store.is_published(unit.document_id) or not permitted:
                warnings.append(f"冲突对端 {unit_id} 未发布或权限不足，不加入结论。")
                continue
            if unit_id not in selected:
                selected.append(unit_id)

        for unit_id in selected:
            unit = self.store.units[unit_id]
            evidence_id = self.store.evidence_by_unit[unit_id][0]
            claims.append(
                Claim(
                    field=f"knowledge.{'.'.join(unit.concept_path) or 'unit'}",
                    value=self.store.unit_text.get(unit_id, ""),
                    source=BrainName.knowledge,
                    unit=unit_id,
                    evidence_id=evidence_id,
                )
            )
            evidence.append(self._evidence_ref(unit_id))

        self.last_route = KnowledgeRoute(
            route_id="kr-" + _digest(query.trace_id, query.raw_query),
            query=query.raw_query,
            candidates=[uid for uid, _s, _p in matches],
            selected=list(selected),
            retrieval_version=f"{self.runtime.metadata.runtime}:hybrid-v1",
            lexical_score={uid: quantize(scores["lexical"]) for uid, scores in self._last_retrieval_scores.items()},
            semantic_score={uid: quantize(scores["semantic"]) for uid, scores in self._last_retrieval_scores.items()},
            fusion_score={uid: quantize(scores["fusion"]) for uid, scores in self._last_retrieval_scores.items()},
            rerank_score={uid: quantize(scores["rerank"]) for uid, scores in self._last_retrieval_scores.items()},
        )

        status = ResultStatus.ok if claims else ResultStatus.no_match
        if status is ResultStatus.no_match:
            warnings.append("无已发布可引用证据，不提供知识结论。")
            return BrainResult(
                brain=self.name,
                request_id=query.request_id,
                trace_id=query.trace_id,
                day_key=query.day_key,
                status=status,
                claims=[],
                evidence=evidence,
                warnings=warnings,
            )

        return BrainResult(
            brain=self.name,
            request_id=query.request_id,
            trace_id=query.trace_id,
            day_key=query.day_key,
            status=status,
            claims=claims,
            evidence=evidence,
            warnings=warnings,
        )

    def _resolve_conflicts(self, selected: list[str]):
        selected_evidence = {
            self.store.evidence_by_unit[uid][0]: uid for uid in selected
        }
        notes: list[str] = []
        extra: list[str] = []
        for conflict in self.store.conflicts:
            if not (set(conflict.evidence_ids) & set(selected_evidence)):
                continue
            notes.append(
                f"冲突并列（{conflict.conflict_id}）：{conflict.note} "
                f"来源={conflict.sources}；条件={conflict.conditions}；版本={conflict.published_dates}。"
            )
            for evidence_id in conflict.evidence_ids:
                evidence = self.store.evidence.get(evidence_id)
                if evidence is not None and evidence.unit_id not in selected:
                    extra.append(evidence.unit_id)
        return notes, extra

    def _evidence_ref(self, unit_id: str) -> EvidenceRef:
        unit = self.store.units[unit_id]
        evidence = self.store.evidence[self.store.evidence_by_unit[unit_id][0]]
        locator = evidence.locator or {}
        ref = (
            f"knowledge://document/{unit.document_id}/unit/{unit_id}"
            f"?page={locator.get('page')}&anchor={locator.get('anchor')}"
            f"&timecode={locator.get('timecode')}"
        )
        detail = (
            f"source={evidence.source_id}; time_status={evidence.time_status}; "
            f"concept_path={'/'.join(unit.concept_path)}"
        )
        anchors = self.store.unit_content_time.get(unit_id, [])
        if anchors:
            detail += "; content_time=" + ",".join(f"{anchor.start}/{anchor.end}" for anchor in anchors[:4])
        if unit_id in self.store.unit_ingested_at:
            detail += "; ingested_at=" + self.store.unit_ingested_at[unit_id]
        return EvidenceRef(
            evidence_id=evidence.evidence_id,
            source=BrainName.knowledge,
            ref=ref,
            detail=detail,
        )

    # ---- 专业推理 ---------------------------------------------------------
    def reason(self, query: BrainQuery, *, rules: list[ReasoningRule] | None = None) -> KnowledgeReasoningResult:
        annotation, matches = self._retrieve(query)
        evidence_ids: list[str] = []
        for unit_id, _score, permitted in matches:
            unit = self.store.units[unit_id]
            if not self.store.is_published(unit.document_id) or not permitted:
                continue
            evidence_ids.append(self.store.evidence_by_unit[unit_id][0])
        result = self.reasoner.reason(
            "krr-" + _digest(query.trace_id, query.raw_query),
            query.raw_query,
            domain=annotation.domain,
            evidence_ids=evidence_ids,
            rules=rules,
            domain_risk_level=domain_risk(annotation.domain),
        )
        result.runtime_version = self.runtime.metadata.runtime_version or self.runtime.metadata.runtime
        result.review_status = "published_evidence_only"
        return result


def _digest(*parts: str) -> str:
    import hashlib

    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


__all__ = ["KnowledgeBrain"]
