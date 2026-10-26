"""M4 知识脑验收测试（对应验收 14、15、16、17、18、27 雏形等）。

覆盖：六件套契约逐字段一致、五层管道端到端、未定时间层、证据可展开、
冲突不静默、门禁生效、无命中、未配置模态不可用、专业推理、内嵌合规。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from brains.knowledge import (
    BuiltinKnowledgeRuntime,
    CanonicalDocument,
    KnowledgeBrain,
    KnowledgeInput,
    KnowledgeReasoningResult,
    KnowledgeSource,
    OntologyStore,
    ReasoningRule,
)
from brains.knowledge.src.contract import SIX_PIECE_MODELS
from brains.protocol import BrainQuery, ResultStatus
from topology.src.trace import contains_date

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "docs" / "contracts" / "knowledge-evidence.schema.json"
WEKNORA_DIR = ROOT / "third_party" / "weknora"

MANUAL = (
    "# 设备维护手册\n"
    "## 温度控制\n"
    "设备运行温度应保持在合理区间。\n"
    "本段说明温度监控要点。\n"
    "## 润滑规范\n"
    "每月对运动部件进行润滑保养。\n"
)


def _input(
    content: str | None,
    *,
    raw_id: str = "r1",
    source_id: str = "src-1",
    source_kind: str = "note",
    media_kind: str = "text",
    title: str = "示例文档",
    version: str | None = None,
    published_at: str | None = None,
    effective_at: str | None = None,
    revised_at: str | None = None,
    retrieved_at: str | None = None,
    permission_scope: list[str] | None = None,
    metadata: dict | None = None,
    risk_domain: str | None = None,
) -> KnowledgeInput:
    return KnowledgeInput(
        raw_id=raw_id,
        source=KnowledgeSource(
            source_id=source_id, kind=source_kind, uri=f"file://{raw_id}/{raw_id}.md"
        ),
        content=content,
        media_kind=media_kind,
        title=title,
        version=version,
        published_at=published_at,
        effective_at=effective_at,
        revised_at=revised_at,
        retrieved_at=retrieved_at,
        permission_scope=permission_scope or ["public"],
        metadata=metadata or {},
        risk_domain=risk_domain,
    )


def _query(raw: str, *, day: str = "2026-10-11", profile_id: str = "alice") -> BrainQuery:
    return BrainQuery(
        request_id="req-1",
        trace_id="tr-1",
        day_key=day,
        raw_query=raw,
        profile_id=profile_id,
    )


# ---- 01 六件套字段与 schema 逐字段一致 ---------------------------------
def test_01_six_piece_matches_schema_exactly():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    defs = schema["$defs"]
    for name, model in SIX_PIECE_MODELS.items():
        spec = defs[name]
        assert model.model_config.get("extra") == "forbid", name
        assert set(model.model_fields.keys()) == set(spec["properties"].keys()), name
        model_required = {n for n, f in model.model_fields.items() if f.is_required()}
        assert model_required == set(spec.get("required", [])), name


# ---- 02 五层管道端到端 -------------------------------------------------
def test_02_pipeline_end_to_end():
    brain = KnowledgeBrain(growth_threshold=3)
    document = brain.ingest(_input(MANUAL, raw_id="manual", title="设备维护手册"))
    assert isinstance(document, CanonicalDocument)
    assert document.document_id.startswith("kd-")

    brain.publish(document.document_id)
    units = brain.store.document_units(document.document_id)
    assert len(units) >= 2  # 多标题层级 -> 多个知识单元
    assert any(u.locator.anchor for u in units)  # anchor locator
    assert any(u.locator.page is not None for u in units)  # page locator

    # 六件套之证据产出
    assert brain.store.evidence_by_document[document.document_id]
    # 概念分枝写入 M1 拓扑（前缀 knowledge.）
    assert any(node.tree_id.startswith("knowledge.") for node in brain.store.nodes.values())
    # 留痕层有 TraceRecord，且 aggregate_key 不含日期
    assert brain.store.traces
    for trace in brain.store.traces.values():
        assert "://" in trace.aggregate_key
        assert not contains_date(trace.aggregate_key)
    # 生成不可变 generation 已提交
    assert brain.store.generations.history("knowledge.equipment")


# ---- 03 未定时间层：不伪造时序 -----------------------------------------
def test_03_undetermined_time_layer():
    brain = KnowledgeBrain()
    document = brain.ingest(
        _input("# 无日期资料\n某设备的维护要点与注意事项。", raw_id="undated")
    )
    brain.publish(document.document_id)
    evidence_ids = brain.store.evidence_by_document[document.document_id]
    assert evidence_ids
    for evidence_id in evidence_ids:
        evidence = brain.store.evidence[evidence_id]
        assert evidence.time_status == "undetermined"
        # 不伪造版本/时间
        assert evidence.content_version is None
    unit = brain.store.document_units(document.document_id)[0]
    assert unit.version is None
    assert brain.store.document_published[document.document_id] is None


# ---- 04 证据可展开到原文定位 -------------------------------------------
def test_04_evidence_expandable_to_locator():
    brain = KnowledgeBrain()
    document = brain.ingest(_input(MANUAL, raw_id="manual-4"))
    brain.publish(document.document_id)

    result = brain.query(_query("温度监控要点"))
    assert result.status is ResultStatus.ok
    assert result.claims
    for claim in result.claims:
        evidence = brain.store.evidence[claim.evidence_id]
        assert evidence.document_id == document.document_id
        assert evidence.unit_id == claim.unit
        assert set(evidence.locator.keys()) == {"page", "anchor", "timecode"}
        ref = next(r for r in result.evidence if r.evidence_id == claim.evidence_id)
        assert document.document_id in ref.ref
        assert claim.unit in ref.ref
        assert "page=" in ref.ref and "anchor=" in ref.ref


# ---- 05 冲突不静默：并列保留 -------------------------------------------
def test_05_conflict_is_not_silently_resolved():
    brain = KnowledgeBrain()
    doc_a = brain.ingest(
        _input(
            "# 设备参数\n温度上限为 80℃。",
            raw_id="a",
            source_id="src-A",
            published_at="2026-01-01",
        )
    )
    brain.publish(doc_a.document_id)
    doc_b = brain.ingest(
        _input(
            "# 设备参数\n温度上限为 120℃。",
            raw_id="b",
            source_id="src-B",
            published_at="2026-02-01",
        )
    )
    brain.publish(doc_b.document_id)

    assert brain.store.conflicts, "应记录冲突"
    conflict = brain.store.conflicts[0]
    assert len(conflict.evidence_ids) == 2
    assert set(conflict.sources) == {"src-A", "src-B"}
    assert len(conflict.conditions) == 2
    assert conflict.published_dates == ["2026-01-01", "2026-02-01"]

    result = brain.query(_query("温度上限"))
    assert result.status is ResultStatus.ok
    values = {claim.value for claim in result.claims}
    assert any("80" in v for v in values) and any("120" in v for v in values)
    assert len({c.evidence_id for c in result.claims}) >= 2
    assert any("冲突并列" in w for w in result.warnings)
    brain.review(doc_b.document_id, action="rollback", reviewer="alice")
    after = brain.query(_query("温度上限"))
    assert any("80" in claim.value for claim in after.claims)
    assert all("120" not in claim.value for claim in after.claims)


# ---- 06 门禁生效 + approve/rollback ------------------------------------
def test_06_gate_blocks_until_approved():
    brain = KnowledgeBrain()
    document = brain.ingest(
        _input(
            "# 合同条款\n乙方若逾期交货，应当承担违约赔偿责任。",
            raw_id="legal",
            source_id="src-legal",
        )
    )
    publication = brain.publish(document.document_id)
    assert publication.status == "pending_review"
    assert brain.pending_gates(document.document_id)

    query = _query("合同 违约 责任")
    blocked = brain.query(query)
    assert blocked.claims == []  # 未发布不得作为结论
    assert any("pending_review" in w for w in blocked.warnings)

    approved = brain.review(document.document_id, action="approve", reviewer="alice")
    assert approved.status == "published"
    assert brain.query(query).claims

    rolled = brain.review(document.document_id, action="rollback")
    assert rolled.status == "pending_review"
    assert brain.query(query).claims == []


# ---- 07 无命中 ---------------------------------------------------------
def test_07_no_match_returns_empty_claims():
    brain = KnowledgeBrain()
    document = brain.ingest(_input(MANUAL, raw_id="manual-7"))
    brain.publish(document.document_id)
    result = brain.query(_query("量子纠缠 拓扑绝缘体 冷原子"))
    assert result.status is ResultStatus.no_match
    assert result.claims == []


# ---- 08 未配置模态：显式 unavailable 且不伪造 --------------------------
def test_08_unconfigured_modalities_are_unavailable():
    brain = KnowledgeBrain()
    runtime = BuiltinKnowledgeRuntime()

    image_parsed = runtime.parse(_input(None, media_kind="image", raw_id="img"))
    assert image_parsed.blocks == []
    assert any(
        s.modality == "ocr" and s.status == "unavailable" for s in image_parsed.unavailable
    )

    audio_parsed = runtime.parse(_input(None, media_kind="audio", raw_id="aud"))
    assert audio_parsed.blocks == []
    assert any(
        s.modality == "asr_transcription" and s.status == "unavailable"
        for s in audio_parsed.unavailable
    )

    document = brain.ingest(_input(None, media_kind="image", raw_id="img-doc"))
    assert brain.store.document_units(document.document_id) == []
    assert brain.store.evidence_by_document.get(document.document_id, []) == []


# ---- 09 专业推理 -------------------------------------------------------
def test_09_professional_reasoning_returns_bounded_result():
    brain = KnowledgeBrain()
    document = brain.ingest(
        _input(
            "# 检修规程\n设备检修前若未断电，必须检查接地保护。",
            raw_id="rule-doc",
            source_id="src-rule",
        )
    )
    brain.publish(document.document_id)
    brain.add_rule(
        ReasoningRule(
            rule_id="r-1",
            domain="equipment",
            definition="设备检修作业安全规则",
            conditions=["检修前已断电", "接地保护有效"],
            conclusion="可在完成断电与接地保护后开展检修",
            exceptions=["带电作业必须先办理审批"],
            missing_facts=["检修作业票"],
            version="v2",
            risk="medium",
        )
    )
    result = brain.reason(_query("设备检修 是否可开展"))
    assert isinstance(result, KnowledgeReasoningResult)
    assert result.result_id
    assert result.conclusion
    assert result.applicable_conditions
    assert result.counter_examples
    assert result.missing_facts
    assert result.evidence_ids
    assert result.domain_risk in ("low", "medium", "high")


# ---- 10 内嵌合规 + 运行时如实标注 --------------------------------------
def test_10_weknora_embedding_compliance_and_honest_runtime():
    commit_file = WEKNORA_DIR / "UPSTREAM_COMMIT"
    notices = WEKNORA_DIR / "THIRD_PARTY_NOTICES.md"
    patches_readme = WEKNORA_DIR / "patches" / "README.md"
    assert commit_file.exists()
    assert notices.exists()
    assert patches_readme.exists()

    commit_text = commit_file.read_text(encoding="utf-8")
    assert "https://github.com/Tencent/WeKnora" in commit_text
    assert "status=LOCKED" in commit_text
    assert "locked_commit=97796c637aa53713edc846bd0a39470d687f0fdf" in commit_text
    import hashlib
    digest = hashlib.sha256((WEKNORA_DIR / 'derived/LICENSE').read_bytes()).hexdigest()
    assert f'license_sha256={digest}' in commit_text
    assert "许可证" in notices.read_text(encoding="utf-8")

    brain = KnowledgeBrain()
    meta = brain.runtime_meta
    assert meta.runtime == "builtin"
    assert meta.upstream == "https://github.com/Tencent/WeKnora"
    assert meta.upstream_status == "PENDING_LOCK"
    # 结果如实标注运行时，不暗示已接入 WeKnora
    result = brain.query(_query("任意查询"))
    assert any("builtin" in w for w in result.warnings)
