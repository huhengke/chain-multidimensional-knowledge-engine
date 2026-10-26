"""M8 契约一致性 / 契约自检测试。

- 每份 ``docs/contracts/*.schema.json`` 通过 JSON Schema 元校验；
- Schema 与 pydantic 模型的字段名/必填集合**逐项一致**（漂移即失败）；
- 代表性实例可被对应契约校验通过。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from brains.protocol import (
    BrainError,
    BrainQuery,
    BrainResult,
    Claim,
    Constraints,
    EvidenceRef,
    TimeScope,
)
from profile import RuntimeProfile
from service import CognitiveRequest
from brains.knowledge.src.workbench import (
    CrossBrainLink,
    EvaluationCase,
    EvaluationRun,
    GraphProposal,
    ImportStep,
    KnowledgeClusterProjection,
    KnowledgeImportJob,
    KnowledgeIndexVersion,
)
from topology.src.node import TopologyNode
from topology.src.ring import RingRef
from topology.src.star_cluster import StarCluster
from topology.src.trace import OccurrenceRef, TraceRecord

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "docs" / "contracts"
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

DAY = "2026-10-11"

# 文件 -> {定义名: pydantic 模型}
CONSISTENCY: dict[str, dict[str, type]] = {
    "cognitive-request.schema.json": {
        "CognitiveRequest": CognitiveRequest,
        "TimeScope": TimeScope,
        "Constraints": Constraints,
    },
    "topology-node.schema.json": {
        "RingRef": RingRef,
        "TopologyNode": TopologyNode,
        "OccurrenceRef": OccurrenceRef,
    },
    "trace-record.schema.json": {
        "TraceRecord": TraceRecord,
        "StarCluster": StarCluster,
    },
    "brain-contract.schema.json": {
        "BrainQuery": BrainQuery,
        "BrainResult": BrainResult,
        "Claim": Claim,
        "EvidenceRef": EvidenceRef,
        "BrainError": BrainError,
        "TimeScope": TimeScope,
    },
    "runtime-profile.schema.json": {
        "RuntimeProfile": RuntimeProfile,
    },
    "knowledge-workbench.schema.json": {
        "ImportStep": ImportStep,
        "KnowledgeImportJob": KnowledgeImportJob,
        "KnowledgeIndexVersion": KnowledgeIndexVersion,
        "KnowledgeClusterProjection": KnowledgeClusterProjection,
        "CrossBrainLink": CrossBrainLink,
        "GraphProposal": GraphProposal,
        "EvaluationCase": EvaluationCase,
        "EvaluationRun": EvaluationRun,
    },
}


def _load(name: str) -> dict:
    return json.loads((CONTRACTS_DIR / name).read_text(encoding="utf-8"))


ALL_SCHEMAS = sorted(p.name for p in CONTRACTS_DIR.glob("*.schema.json"))


def test_all_contracts_present():
    assert ALL_SCHEMAS == [
        "brain-contract.schema.json",
        "cognitive-request.schema.json",
        "knowledge-evidence.schema.json",
        "knowledge-workbench.schema.json",
        "runtime-profile.schema.json",
        "topology-node.schema.json",
        "trace-record.schema.json",
    ]


@pytest.mark.parametrize("name", ALL_SCHEMAS)
def test_schema_is_valid_json_schema(name):
    Draft202012Validator.check_schema(_load(name))


@pytest.mark.parametrize("name,models", list(CONSISTENCY.items()))
def test_schema_matches_pydantic_models(name, models):
    schema = _load(name)
    defs = schema["$defs"]
    for def_name, model in models.items():
        assert def_name in defs, f"{name} 缺少定义 {def_name}"
        generated = model.model_json_schema()
        assert set(defs[def_name].get("properties", {})) == set(
            generated.get("properties", {})
        ), f"{name}#{def_name} 字段名与模型不一致"
        assert set(defs[def_name].get("required", [])) == set(
            generated.get("required", [])
        ), f"{name}#{def_name} 必填集合与模型不一致"


# ---- 代表性实例校验 ----------------------------------------------------
def _wrapper(file_name: str, def_name: str) -> dict:
    return {
        "$schema": SCHEMA_DIALECT,
        "$defs": _load(file_name)["$defs"],
        "$ref": f"#/$defs/{def_name}",
    }


INSTANCES = [
    (
        CognitiveRequest(
            request_id="r-1",
            trace_id="t-1",
            raw_query="上周华东区汽油销量环比如何",
            day_key=DAY,
            time_scope=TimeScope(
                start="2026-10-01", end="2026-10-11", granularity="day", confidence="exact"
            ),
        ),
        "cognitive-request.schema.json",
        "CognitiveRequest",
    ),
    (RingRef(tree_id="memory.voice", day_key=DAY, ring_index=182), "topology-node.schema.json", "RingRef"),
    (
        TopologyNode(
            node_id="structured.oil_sales_daily/2026-10-11/area/east",
            tree_id="structured.oil_sales_daily",
            day_key=DAY,
            ring_index=182,
            layer="inner",
            payload_ref="store://middle_store/deadbeef",
        ),
        "topology-node.schema.json",
        "TopologyNode",
    ),
    (
        OccurrenceRef(
            occurrence_id="occ-1",
            tree_id="memory.voice",
            day_key=DAY,
            node_id="memory.voice/2026-10-11/cluster/sc-1",
            evidence_ref="ev-3",
        ),
        "topology-node.schema.json",
        "OccurrenceRef",
    ),
    (
        TraceRecord(
            trace_id="tr-1",
            aggregate_key="feature://acoustic-signature/9f2c1d",
            vector=["0.10", "0.20"],
            source_ref="memory://media/1",
            created_at="2026-10-11T00:00:00Z",
        ),
        "trace-record.schema.json",
        "TraceRecord",
    ),
    (
        StarCluster(
            cluster_id="sc-1",
            tree_id="memory.voice",
            prototype_vector=["0.10", "0.20"],
            tolerance="eps=0.05",
            occurrence_count=38,
            centroid=["0.105", "0.195"],
            dispersion="0.03",
            algo_version="v1",
            rule_version="v1",
        ),
        "trace-record.schema.json",
        "StarCluster",
    ),
    (
        BrainQuery(request_id="r-1", trace_id="t-1", day_key=DAY, raw_query="销量"),
        "brain-contract.schema.json",
        "BrainQuery",
    ),
    (
        BrainResult(
            brain="structured",
            request_id="r-1",
            trace_id="t-1",
            day_key=DAY,
            status="no_match",
            claims=[],
            error=BrainError(code="BRAIN_NOT_CONNECTED", message="结构化脑（M5）尚未接入"),
        ),
        "brain-contract.schema.json",
        "BrainResult",
    ),
    (
        RuntimeProfile(
            profile_id="legacy-local-v1",
            status="active",
            engine="legacy",
            generation_pins={"oil_sales_daily": 7},
        ),
        "runtime-profile.schema.json",
        "RuntimeProfile",
    ),
]


@pytest.mark.parametrize("model,file_name,def_name", INSTANCES)
def test_instance_validates_against_contract(model, file_name, def_name):
    instance = model.model_dump(mode="json")
    wrapper = _wrapper(file_name, def_name)
    Draft202012Validator.check_schema(wrapper)
    Draft202012Validator(wrapper).validate(instance)


def test_knowledge_evidence_undetermined_time_layer():
    schema = _load("knowledge-evidence.schema.json")
    evidence = schema["$defs"]["KnowledgeEvidence"]
    assert evidence["properties"]["time_status"]["enum"] == ["known", "undetermined"]
    # 六件套齐全
    assert set(schema["$defs"]) == {
        "KnowledgeSource",
        "CanonicalDocument",
        "KnowledgeUnit",
        "KnowledgeEvidence",
        "KnowledgeRoute",
        "KnowledgeReasoningResult",
    }
