"""统一脑契约 <-> 现有引擎请求/响应 的映射层。

两条方向：

- ``build_request``：``BrainQuery`` -> ``UnifiedQueryRequest``（只透传白名单字段）。
- ``to_result``：``UnifiedQueryResponse`` -> ``BrainResult``。

硬约束（逐条落地）：
* 指标值恒为 **Decimal 定点数字符串**，任何情况下都不转 float；
* 无命中（``available=False`` / ``record_count==0`` / ``aggregations`` 为空）
  一律映射为 ``no_match`` 且 ``claims == []``，**绝不返回数值 0**；
* ``unsupported_conditions`` / ``precision_gap`` 一律映射为 ``error``，
  不伪装成 no_match，message 进 ``error.message`` 并保留到 ``warnings``。
"""

from __future__ import annotations

from typing import Any

from brains.protocol import (
    BrainError,
    BrainName,
    BrainQuery,
    BrainResult,
    Claim,
    EvidenceRef,
    ResultStatus,
)

from .datasets import ALLOWED_REQUEST_FIELDS, DATASETS, dataset_label, metric_label

# 显式错误码
CODE_UNSUPPORTED_CONDITIONS = "UNSUPPORTED_CONDITIONS"
CODE_PRECISION_GAP = "PRECISION_GAP"


# --------------------------------------------------------------------------- #
# 自然语言 -> 数据集
# --------------------------------------------------------------------------- #
def resolve_dataset(text: str, *, default: str) -> str:
    """按 aliases / label 关键词命中数据集；命中多个取**最长**别名；无命中用 default。"""
    best_len = 0
    best = default
    for dataset_id, meta in DATASETS.items():
        keywords = list(meta.get("aliases", []))
        label = meta.get("label")
        if label:
            keywords.append(label)
        for keyword in keywords:
            if keyword and keyword in text and len(keyword) > best_len:
                best_len = len(keyword)
                best = dataset_id
    return best


# --------------------------------------------------------------------------- #
# BrainQuery -> UnifiedQueryRequest
# --------------------------------------------------------------------------- #
def build_request(query: BrainQuery, *, default_dataset: str) -> dict[str, Any]:
    """构造现有引擎的 ``UnifiedQueryRequest`` 载荷（仅白名单字段）。

    优先级：``constraints`` 中的显式字段 > ``time_scope`` 推导的 period；
    ``time_scope`` 为 ``None`` 时不注入 period（交由引擎默认）；未知键忽略。
    """
    constraints: dict[str, Any] = dict(query.constraints or {})

    payload: dict[str, Any] = {}

    # dataset：显式优先，否则按 raw_query 解析，否则 default_dataset
    explicit_dataset = constraints.get("dataset")
    if isinstance(explicit_dataset, str) and explicit_dataset:
        payload["dataset"] = explicit_dataset
    else:
        payload["dataset"] = resolve_dataset(query.raw_query, default=default_dataset)

    # 其余白名单字段：仅在 constraints 显式提供时透传
    for key in ALLOWED_REQUEST_FIELDS:
        if key in ("dataset", "period"):
            continue
        value = constraints.get(key)
        if value is not None:
            payload[key] = value

    # period：constraints 显式 > time_scope；两者皆无则不注入
    explicit_period = constraints.get("period")
    if explicit_period is not None:
        payload["period"] = explicit_period
    elif query.time_scope is not None:
        payload["period"] = {"start": query.time_scope.start, "end": query.time_scope.end}

    return payload


# --------------------------------------------------------------------------- #
# UnifiedQueryResponse -> BrainResult
# --------------------------------------------------------------------------- #
def _as_warnings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _ensure_warning(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _decimal_str(value: Any) -> str:
    """保真 Decimal 定点数字符串：字符串原样保留，其余仅做 ``str``（绝不经过 float）。"""
    if isinstance(value, str):
        return value
    return str(value)


def _hard_failure(block: dict[str, Any]) -> BrainError | None:
    """检测引擎返回的硬失败信号（不支持条件 / 精度缺口）。"""
    if not isinstance(block, dict):
        return None
    if block.get("unsupported_conditions") is True:
        message = block.get("unsupported_message") or "引擎报告条件不支持（unsupported_conditions）"
        return BrainError(code=CODE_UNSUPPORTED_CONDITIONS, message=str(message))
    if block.get("precision_gap") is True:
        message = block.get("precision_gap_message") or "引擎报告精度缺口（precision_gap）"
        return BrainError(code=CODE_PRECISION_GAP, message=str(message))
    return None


def _has_data(block: dict[str, Any]) -> bool:
    """判定单个 actual 块是否命中可用数值（无命中绝不返回 0）。"""
    if not isinstance(block, dict):
        return False
    available = block.get("available") is True
    record_count = block.get("record_count")
    aggregations = block.get("aggregations")
    has_records = isinstance(record_count, int) and record_count > 0
    has_aggs = isinstance(aggregations, dict) and len(aggregations) > 0
    return available and has_records and has_aggs


def _single_claims(
    block: dict[str, Any], dataset: str, *, field_prefix: str, evidence_prefix: str
) -> list[Claim]:
    aggregations = block.get("aggregations") or {}
    claims: list[Claim] = []
    for metric, value in aggregations.items():
        label = metric_label(dataset, metric)
        claims.append(
            Claim(
                field=f"{field_prefix}{label}",
                value=_decimal_str(value),
                source=BrainName.structured,
                unit=None,
                evidence_id=f"{evidence_prefix}{metric}",
            )
        )
    return claims


def _evidence_for(trace_id: str, dataset: str, block: dict[str, Any]) -> EvidenceRef:
    record_count = block.get("record_count")
    detail = f"{dataset_label(dataset)}；record_count={record_count}"
    return EvidenceRef(
        evidence_id=f"ev-structured-{dataset}",
        source=BrainName.structured,
        ref=f"engine://{trace_id}/{dataset}",
        detail=detail,
    )


def _error_result(
    query: BrainQuery,
    trace_id: str,
    warnings: list[str],
    error: BrainError,
) -> BrainResult:
    _ensure_warning(warnings, error.message)
    return BrainResult(
        brain=BrainName.structured,
        request_id=query.request_id,
        trace_id=trace_id,
        day_key=query.day_key,
        status=ResultStatus.error,
        claims=[],
        evidence=[],
        warnings=warnings,
        error=error,
    )


def _no_match_result(
    query: BrainQuery,
    trace_id: str,
    warnings: list[str],
    evidence: list[EvidenceRef],
) -> BrainResult:
    return BrainResult(
        brain=BrainName.structured,
        request_id=query.request_id,
        trace_id=trace_id,
        day_key=query.day_key,
        status=ResultStatus.no_match,
        claims=[],
        evidence=evidence,
        warnings=warnings,
        error=None,
    )


def _ok_result(
    query: BrainQuery,
    trace_id: str,
    warnings: list[str],
    claims: list[Claim],
    evidence: list[EvidenceRef],
) -> BrainResult:
    return BrainResult(
        brain=BrainName.structured,
        request_id=query.request_id,
        trace_id=trace_id,
        day_key=query.day_key,
        status=ResultStatus.ok,
        claims=claims,
        evidence=evidence,
        warnings=warnings,
        error=None,
    )


def _constraint_dataset(query: BrainQuery) -> str | None:
    value = (query.constraints or {}).get("dataset")
    return value if isinstance(value, str) and value else None


def to_result(query: BrainQuery, response: dict[str, Any]) -> BrainResult:
    """把现有引擎响应映射为 ``BrainResult``（保真 trace_id / warnings / Decimal）。"""
    response = response or {}
    warnings = _as_warnings(response.get("warnings"))
    trace_id = str(response.get("trace_id") or query.trace_id)

    actual = response.get("actual")
    if not isinstance(actual, dict) or not actual:
        return _no_match_result(query, trace_id, warnings, evidence=[])

    # ---- 批量模式：datasets 非空 -----------------------------------------
    datasets = actual.get("datasets")
    if isinstance(datasets, dict) and datasets:
        return _batch_result(query, trace_id, warnings, actual, datasets)

    # ---- 单数据集模式 ----------------------------------------------------
    return _single_result(query, trace_id, warnings, actual)


def _single_result(
    query: BrainQuery,
    trace_id: str,
    warnings: list[str],
    actual: dict[str, Any],
) -> BrainResult:
    dataset = str(actual.get("dataset") or _constraint_dataset(query) or "unknown")

    hard = _hard_failure(actual)
    if hard is not None:
        return _error_result(query, trace_id, warnings, hard)

    evidence = [_evidence_for(trace_id, dataset, actual)]
    if not _has_data(actual):
        return _no_match_result(query, trace_id, warnings, evidence)

    claims = _single_claims(
        actual,
        dataset,
        field_prefix="",
        evidence_prefix="ev-structured-",
    )
    if not claims:
        return _no_match_result(query, trace_id, warnings, evidence)
    return _ok_result(query, trace_id, warnings, claims, evidence)


def _batch_result(
    query: BrainQuery,
    trace_id: str,
    warnings: list[str],
    actual: dict[str, Any],
    datasets: dict[str, Any],
) -> BrainResult:
    # 批量容器自身也可携带硬失败信号
    container_hard = _hard_failure(actual)
    if container_hard is not None:
        return _error_result(query, trace_id, warnings, container_hard)

    all_claims: list[Claim] = []
    all_evidence: list[EvidenceRef] = []
    hard_errors: list[BrainError] = []
    any_data = False

    for dataset_id, entry in datasets.items():
        entry = entry if isinstance(entry, dict) else {}
        current = entry.get("current")
        current = current if isinstance(current, dict) else {}
        dataset = str(entry.get("dataset") or dataset_id)

        hard = _hard_failure(entry) or _hard_failure(current)
        if hard is not None:
            hard_errors.append(hard)
            continue

        all_evidence.append(_evidence_for(trace_id, dataset, current))
        if not _has_data(current):
            continue

        claims = _single_claims(
            current,
            dataset,
            field_prefix=f"{dataset}.",
            evidence_prefix=f"ev-structured-{dataset}-",
        )
        if claims:
            any_data = True
            all_claims.extend(claims)

    if hard_errors:
        message = "；".join(f"[{err.code}] {err.message}" for err in hard_errors)
        return _error_result(
            query,
            trace_id,
            warnings,
            BrainError(code=hard_errors[0].code, message=message),
        )

    if not any_data:
        return _no_match_result(query, trace_id, warnings, all_evidence)
    return _ok_result(query, trace_id, warnings, all_claims, all_evidence)