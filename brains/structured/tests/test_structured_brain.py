"""M5 结构化脑适配器测试。

全部使用**注入式 transport**，不发真实网络请求。
覆盖：ok / no_match(3 态) / unsupported / precision_gap / 引擎不可达 /
请求构造(period) / dataset 解析 / 批量模式。
"""

from __future__ import annotations

from typing import Any

import pytest

from brains.protocol import BrainName, BrainQuery, ResultStatus
from brains.structured import (
    HttpEngineClient,
    StructuredBrain,
    resolve_dataset,
)
from brains.structured.src.brain import CODE_ENGINE_UNAVAILABLE
from brains.structured.src.mapping import CODE_PRECISION_GAP, CODE_UNSUPPORTED_CONDITIONS

DAY = "2026-10-11"


class FakeTransport:
    """记录请求并返回预置响应 / 抛异常。"""

    def __init__(self, response: dict | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return 200, self.response


def make_query(**overrides) -> BrainQuery:
    base: dict[str, Any] = dict(
        request_id="req-1",
        trace_id="tr-1",
        day_key=DAY,
        raw_query="本月油品销售",
    )
    base.update(overrides)
    return BrainQuery(**base)


def make_brain(transport: FakeTransport, **kwargs) -> StructuredBrain:
    client = HttpEngineClient(base_url="http://engine.test:8000", transport=transport)
    return StructuredBrain(client, **kwargs)


def single_ok_response(**actual_overrides) -> dict:
    actual: dict[str, Any] = {
        "dataset": "oil_monthly_sales",
        "available": True,
        "nodes": [],
        "record_count": 3,
        "aggregations": {"monthlysales": "12345.67", "monthlyqty": "999.00"},
    }
    actual.update(actual_overrides)
    return {
        "trace_id": "tr-server-1",
        "query_plan": {"dataset": "oil_monthly_sales"},
        "actual": actual,
        "plan": None,
        "warnings": ["引擎提示：口径已归一"],
        "timing_ms": {"total": 12},
    }


# --------------------------------------------------------------------------- #
# 1. ok 路径
# --------------------------------------------------------------------------- #
def test_ok_path_decimal_fidelity_and_evidence():
    transport = FakeTransport(single_ok_response())
    brain = make_brain(transport)

    result = brain.query(make_query())

    assert result.status is ResultStatus.ok
    assert result.brain is BrainName.structured
    assert result.trace_id == "tr-server-1"  # trace_id 用响应里的

    by_field = {c.field: c for c in result.claims}
    assert by_field["销售吨数"].value == "12345.67"
    assert isinstance(by_field["销售吨数"].value, str)
    assert by_field["销售升数"].value == "999.00"
    assert all(c.value != "0" for c in result.claims)

    # claim 的 evidence_id 采用 ev-structured-<metric> 形式
    assert by_field["销售吨数"].evidence_id == "ev-structured-monthlysales"
    assert all(c.evidence_id for c in result.claims)

    ev_ids = {e.evidence_id for e in result.evidence}
    assert "ev-structured-oil_monthly_sales" in ev_ids
    ref = next(e for e in result.evidence if e.evidence_id == "ev-structured-oil_monthly_sales")
    assert ref.source is BrainName.structured
    assert ref.ref == "engine://tr-server-1/oil_monthly_sales"
    assert result.warnings == ["引擎提示：口径已归一"]

    # 请求头带 X-Trace-Id
    assert transport.calls[0]["headers"]["X-Trace-Id"] == "tr-1"


# --------------------------------------------------------------------------- #
# 2. no_match（三种来源：available=false / record_count=0 / aggregations={}）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "overrides",
    [
        {"available": False, "aggregations": {}},
        {"available": True, "record_count": 0},
        {"available": True, "record_count": 5, "aggregations": {}},
    ],
)
def test_no_match_never_returns_zero(overrides):
    transport = FakeTransport(single_ok_response(**overrides))
    brain = make_brain(transport)

    result = brain.query(make_query())

    assert result.status is ResultStatus.no_match
    assert result.claims == []
    assert all(c.value != "0" for c in result.claims)
    assert result.error is None


# --------------------------------------------------------------------------- #
# 3. unsupported_conditions -> error
# --------------------------------------------------------------------------- #
def test_unsupported_conditions_maps_to_error():
    message = "不支持的条件：同时使用 yoy 与 mom"
    transport = FakeTransport(
        single_ok_response(
            available=True,
            record_count=3,
            aggregations={"monthlysales": "100.00"},
            unsupported_conditions=True,
            unsupported_message=message,
        )
    )
    brain = make_brain(transport)

    result = brain.query(make_query())

    assert result.status is ResultStatus.error
    assert result.claims == []
    assert result.error is not None
    assert result.error.code == CODE_UNSUPPORTED_CONDITIONS
    assert message in result.error.message
    assert message in result.warnings
    assert "引擎提示：口径已归一" in result.warnings  # 原样保留


# --------------------------------------------------------------------------- #
# 4. precision_gap -> error
# --------------------------------------------------------------------------- #
def test_precision_gap_maps_to_error():
    message = "环比精度不足，无法给出可比结果"
    transport = FakeTransport(
        single_ok_response(
            available=True,
            record_count=3,
            aggregations={"monthlysales": "100.00"},
            precision_gap=True,
            precision_gap_message=message,
        )
    )
    brain = make_brain(transport)

    result = brain.query(make_query())

    assert result.status is ResultStatus.error
    assert result.claims == []
    assert result.error is not None
    assert result.error.code == CODE_PRECISION_GAP
    assert message in result.error.message


# --------------------------------------------------------------------------- #
# 5. HTTP 不可达 -> error(ENGINE_UNAVAILABLE)，不伪造数值
# --------------------------------------------------------------------------- #
def test_engine_unavailable_maps_to_error():
    transport = FakeTransport(error=ConnectionError("connection refused"))
    brain = make_brain(transport)

    result = brain.query(make_query())

    assert result.status is ResultStatus.error
    assert result.claims == []
    assert result.error is not None
    assert result.error.code == CODE_ENGINE_UNAVAILABLE
    assert result.trace_id == "tr-1"


def test_non_2xx_maps_to_engine_unavailable():
    def transport(url, payload, headers, timeout):
        return 500, {"detail": "boom"}

    brain = make_brain(transport)

    result = brain.query(make_query())

    assert result.status is ResultStatus.error
    assert result.error is not None
    assert result.error.code == CODE_ENGINE_UNAVAILABLE
    assert "500" in result.error.message


# --------------------------------------------------------------------------- #
# 6. 请求构造：period
# --------------------------------------------------------------------------- #
def test_build_request_with_time_scope_injects_period():
    from brains.protocol import Confidence, Granularity, TimeScope

    transport = FakeTransport(single_ok_response())
    brain = make_brain(transport)
    query = make_query(
        time_scope=TimeScope(
            start="2026-10-01", end="2026-10-31", granularity=Granularity.month, confidence=Confidence.exact
        )
    )

    brain.query(query)

    payload = transport.calls[0]["payload"]
    assert payload["period"] == {"start": "2026-10-01", "end": "2026-10-31"}
    assert payload["dataset"] == "oil_monthly_sales"


def test_build_request_without_time_scope_omits_period():
    transport = FakeTransport(single_ok_response())
    brain = make_brain(transport)

    brain.query(make_query())

    payload = transport.calls[0]["payload"]
    assert "period" not in payload


def test_build_request_passes_explicit_constraints_and_ignores_unknown():
    transport = FakeTransport(single_ok_response())
    brain = make_brain(transport)
    query = make_query(
        constraints={
            "metrics": ["monthlysales"],
            "group_by": ["day"],
            "location": "华东区",
            "不存在的键": "应被忽略",
        }
    )

    brain.query(query)

    payload = transport.calls[0]["payload"]
    assert payload["metrics"] == ["monthlysales"]
    assert payload["group_by"] == ["day"]
    assert payload["location"] == "华东区"
    assert "不存在的键" not in payload


# --------------------------------------------------------------------------- #
# 7. dataset 解析
# --------------------------------------------------------------------------- #
def test_resolve_dataset_chinese_alias_longest_wins():
    assert resolve_dataset("本月油品销售多少", default="x") == "oil_monthly_sales"
    # 「非油品销售」同时命中 non_oil_sales 别名(5 字)与 oil_monthly "油品销售"(4 字)，取最长
    assert resolve_dataset("非油品销售明细", default="x") == "non_oil_sales"
    assert resolve_dataset("站点销售计划完成率", default="x") == "oil_sales_plan"


def test_resolve_dataset_falls_back_to_default():
    assert resolve_dataset("完全无关的一句话", default="oil_monthly_sales") == "oil_monthly_sales"


# --------------------------------------------------------------------------- #
# 8. 批量模式
# --------------------------------------------------------------------------- #
def batch_response() -> dict:
    return {
        "trace_id": "tr-batch-1",
        "warnings": ["批量提示：两数据集口径不同"],
        "actual": {
            "datasets": {
                "oil_sales_daily": {
                    "dataset": "oil_sales_daily",
                    "label": "油品销售日报",
                    "metric_labels": {"salesvol": "销售吨数"},
                    "periods": {},
                    "current": {
                        "dataset": "oil_sales_daily",
                        "available": True,
                        "record_count": 2,
                        "aggregations": {"salesvol": "88.50"},
                    },
                    "comparisons": {},
                },
                "oil_sales_plan": {
                    "dataset": "oil_sales_plan",
                    "label": "油品销售计划",
                    "metric_labels": {"monsalesplan": "月销售计划"},
                    "periods": {},
                    "current": {
                        "dataset": "oil_sales_plan",
                        "available": False,
                        "record_count": 0,
                        "aggregations": {},
                    },
                    "comparisons": {},
                },
            },
            "totals": {},
            "dataset_count": 2,
            "available": True,
        },
    }


def test_batch_mode_independent_status_and_no_zero():
    transport = FakeTransport(batch_response())
    brain = make_brain(transport)
    query = make_query(
        raw_query="日报与计划对比",
        constraints={"datasets": ["oil_sales_daily", "oil_sales_plan"]},
    )

    result = brain.query(query)

    assert result.status is ResultStatus.ok
    assert result.trace_id == "tr-batch-1"
    assert result.warnings == ["批量提示：两数据集口径不同"]

    # 有数据的数据集出 claim，且带数据集前缀避免同名冲突
    daily_claims = [c for c in result.claims if c.field.startswith("oil_sales_daily.")]
    assert len(daily_claims) == 1
    assert daily_claims[0].field == "oil_sales_daily.销售吨数"
    assert daily_claims[0].value == "88.50"

    # 无数据的数据集：不出 claim，更不出 0
    assert not any(c.field.startswith("oil_sales_plan.") for c in result.claims)
    assert all(c.value != "0" for c in result.claims)

    # 每个被查询的数据集都有一条 evidence
    ev_ids = {e.evidence_id for e in result.evidence}
    assert "ev-structured-oil_sales_daily" in ev_ids
    assert "ev-structured-oil_sales_plan" in ev_ids


def test_batch_mode_all_empty_is_no_match():
    response = batch_response()
    response["actual"]["datasets"]["oil_sales_daily"]["current"] = {
        "dataset": "oil_sales_daily",
        "available": True,
        "record_count": 0,
        "aggregations": {},
    }
    brain = make_brain(FakeTransport(response))

    result = brain.query(
        make_query(constraints={"datasets": ["oil_sales_daily", "oil_sales_plan"]})
    )

    assert result.status is ResultStatus.no_match
    assert result.claims == []


def test_batch_mode_hard_failure_is_error():
    response = batch_response()
    response["actual"]["datasets"]["oil_sales_plan"]["current"]["unsupported_conditions"] = True
    response["actual"]["datasets"]["oil_sales_plan"]["current"][
        "unsupported_message"
    ] = "不支持的计划口径"
    brain = make_brain(FakeTransport(response))

    result = brain.query(
        make_query(constraints={"datasets": ["oil_sales_daily", "oil_sales_plan"]})
    )

    assert result.status is ResultStatus.error
    assert result.claims == []
    assert result.error is not None
    assert "不支持的计划口径" in result.error.message


# --------------------------------------------------------------------------- #
# 协议兼容：可被控制面直接注入
# --------------------------------------------------------------------------- #
def test_brain_satisfies_protocol_and_injectable():
    from brains.protocol import Brain

    brain = make_brain(FakeTransport(single_ok_response()))
    assert brain.name is BrainName.structured
    assert isinstance(brain, Brain)