from datetime import date

from brains.knowledge.src.brain import KnowledgeBrain
from brains.knowledge.src.temporal import extract_content_time, infer_query_plan
from brains.knowledge.src.runtime_adapter import KnowledgeInput
from brains.knowledge.src.contract import KnowledgeSource
from brains.knowledge.src.workbench import KnowledgeWorkbenchService, SearchRequest, TextImportRequest
from governance.src import Principal


OWNER = Principal(subject="owner", scopes=["knowledge:publish"])


def test_content_time_extracts_absolute_and_relative_without_manual_gate():
    raw = KnowledgeInput(
        raw_id="raw-1",
        source=KnowledgeSource(source_id="source-1", kind="text"),
        title="案例汇编",
        content="去年发生了一个变化，2024年12月31日完成，2024年至2025年持续。",
        metadata={},
    )
    anchors = extract_content_time(
        raw=raw,
        unit_id="unit-1",
        text=raw.content or "",
        title=raw.title,
        heading_path=[],
        locator={"page": 3},
        ingested_at="2026-10-15T00:00:00+08:00",
    )
    assert {(item.start, item.end) for item in anchors} == {
        ("2025-01-01", "2025-12-31"),
        ("2024-12-31", "2024-12-31"),
        ("2024-01-01", "2025-12-31"),
    }
    assert all(item.source_locator["page"] == 3 for item in anchors)


def test_query_plan_separates_content_and_ingest_time():
    plan = infer_query_plan("昨天听别人说去年发生了什么", reference=date(2026, 10, 16))
    assert plan.mode == "dual"
    assert (plan.content_start, plan.content_end) == ("2025-01-01", "2025-12-31")
    assert (plan.ingest_start, plan.ingest_end) == ("2026-10-15", "2026-10-15")
    assert (plan.observed_start, plan.observed_end) == ("2026-10-15", "2026-10-15")


def test_dual_time_search_intersects_content_and_ingest_axes():
    service = KnowledgeWorkbenchService(KnowledgeBrain())
    first = service.create_text_import(
        TextImportRequest(
            request_id="r-1", trace_id="t-1", title="昨天导入的去年资料",
            source_id="source-1", content="去年发生了 A 事件。",
            metadata={"ingested_at": "2026-10-15T10:00:00+08:00"},
        ), OWNER,
    )
    second = service.create_text_import(
        TextImportRequest(
            request_id="r-2", trace_id="t-2", title="更早导入的去年资料",
            source_id="source-2", content="去年发生了 B 事件。",
            metadata={"ingested_at": "2026-10-10T10:00:00+08:00"},
        ), OWNER,
    )
    service.start_import(first.job_id, OWNER)
    service.start_import(second.job_id, OWNER)
    response = service.search(SearchRequest(
        request_id="r-q", trace_id="t-q", day_key="2026-10-16", query="昨天听别人说去年发生了什么",
    ))
    selected = response["route"]["selected"]
    first_units = {unit.unit_id for unit in service.brain.store.document_units(first.document_id) if unit.chunk_level == 1}
    second_units = {unit.unit_id for unit in service.brain.store.document_units(second.document_id) if unit.chunk_level == 1}
    assert set(selected) & first_units
    assert not (set(selected) & second_units)
    assert response["temporal"]["mode"] == "dual"
    assert response["temporal"]["intersection_candidate_count"] == len(first_units)
