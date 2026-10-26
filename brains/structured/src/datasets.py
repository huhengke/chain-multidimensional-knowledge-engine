"""现有 AI 数据引擎的数据集 / 组织声明（冻结常量）。

来源：现有引擎 ``schemas/query-contract.json`` 与 ``organization-bridge.json``。
本模块只做**声明内置**，供适配器把自然语言问题映射到 ``dataset`` + ``metrics``；
逻辑条件的层级归一由现有引擎负责，本模块**不实现任何过滤逻辑**。

硬约束（failure_policy）：
    reject unresolved conditions; never remove a requested filter
"""

from __future__ import annotations

from typing import Any

# 现有引擎的失败策略：拒绝未解析的条件，绝不删除用户显式要求的过滤条件。
FAILURE_POLICY = "reject unresolved conditions; never remove a requested filter"

#: 数据集声明（逐字保真）。结构：
#:   label / aliases / kind / time_granularity / metrics(code -> 中文标签)
#:   order_key（可选，明细类数据集的订单主键）
DATASETS: dict[str, dict[str, Any]] = {
    "oil_master": {
        "label": "油品基础信息",
        "aliases": ["油品主数据", "油品信息"],
        "kind": "dimension",
        "time_granularity": "none",
        "metrics": {},
    },
    "non_oil_sales": {
        "label": "非油品销售明细",
        "aliases": ["非油品", "非油品销售", "线上商城", "商城销售"],
        "kind": "fact",
        "time_granularity": "day",
        "order_key": "shopping_order_id",
        "metrics": {
            "total_amount": "订单总额",
            "cost_amount": "成本金额",
            "freight_amount": "运费",
            "coupon_amount": "优惠券金额",
            "points_amount": "积分抵扣金额",
            "discount_amount": "折扣金额",
            "pay_amount": "支付金额",
            "freight_discount_amount": "运费折扣",
            "sale_amount": "销售金额",
            "sale_tax": "销售税额",
            "freight_pay_amount": "实付运费",
            "freight_coupon_amount": "运费优惠券金额",
            "freight_points_amount": "运费积分金额",
            "real_amount": "实收金额",
            "convert_amount": "转换金额",
        },
    },
    "oil_sales_plan": {
        "label": "油品销售计划",
        "aliases": ["站点销售计划", "销售计划"],
        "kind": "fact",
        "time_granularity": "month",
        "metrics": {"monsalesplan": "月销售计划"},
    },
    "oil_sales_daily": {
        "label": "油品销售日报",
        "aliases": ["站级销售日报", "日销售"],
        "kind": "fact",
        "time_granularity": "day",
        "metrics": {
            "salesvol": "销售吨数",
            "qty": "销售升数",
            "amount": "销售金额",
        },
    },
    "oil_monthly_sales": {
        "label": "油品销售月报",
        "aliases": ["油品销售", "月销售"],
        "kind": "fact",
        "time_granularity": "month",
        "metrics": {
            "monthlysales": "销售吨数",
            "monthlyqty": "销售升数",
            "monthlyamount": "销售金额",
        },
    },
}

#: 统一请求体允许出现的字段白名单（UnifiedQueryRequest，extra 会被拒绝）。
ALLOWED_REQUEST_FIELDS: tuple[str, ...] = (
    "intent",
    "period",
    "location",
    "product",
    "grade",
    "group_by",
    "products",
    "comparisons",
    "sort",
    "metrics",
    "dataset",
    "datasets",
    "dataset_scope",
    "conditions",
    "filters",
    "base_filters",
    "detail",
)

#: 组织映射（organization-bridge.json，逐字保真）：数据集 -> 层级 -> code/name 列。
#: 仅作层级提示使用；适配器只透传，不据此构造过滤条件。
#: ``resolution``：只在所选数据集内解析请求的组织名称/编码。
ORGANIZATION_BRIDGE_RESOLUTION = (
    "resolve requested organization names or codes against the selected dataset only"
)

ORGANIZATION_BRIDGE: dict[str, list[dict[str, str]]] = {
    "non_oil_sales": [
        {"level": "province", "code": "store_province_org_ou_code", "name": "store_province_org_name"},
        {"level": "city", "code": "store_city_org_ou_code", "name": "store_city_org_name"},
        {"level": "branch", "code": "store_branch_org_ou_code", "name": "store_branch_org_name"},
        {"level": "area", "code": "store_area_org_ou_code", "name": "store_area_org_name"},
        {"level": "station", "code": "store_ou_code", "name": "store_name"},
    ],
    "oil_sales_plan": [
        {"level": "organization", "code": "unitcode", "name": "unitname"},
    ],
    "oil_sales_daily": [
        {"level": "branch", "code": "bigareacode", "name": "bigareaname"},
        {"level": "county_company", "code": "branchcode", "name": "branchname"},
        {"level": "area", "code": "areacode", "name": "areaname"},
        {"level": "station", "code": "oucode", "name": "shortname"},
    ],
    "oil_monthly_sales": [
        {"level": "branch", "code": "bigareacode", "name": "bigareaname"},
        {"level": "county_company", "code": "branchcode", "name": "branchname"},
        {"level": "area", "code": "areacode", "name": "areaname"},
        {"level": "station", "code": "oucode", "name": "shortname"},
    ],
}


def metric_label(dataset: str, metric: str) -> str:
    """返回指标的中文标签；未声明时回退为指标代码本身。"""
    metrics = DATASETS.get(dataset, {}).get("metrics", {})
    return metrics.get(metric, metric)


def dataset_label(dataset: str) -> str:
    """返回数据集中文标签；未声明时回退为数据集 id。"""
    return DATASETS.get(dataset, {}).get("label", dataset)


def level_columns(dataset: str, level: str) -> dict[str, str]:
    """返回某数据集某组织层级的 code/name 列提示；不存在时返回空字典。"""
    for entry in ORGANIZATION_BRIDGE.get(dataset, []):
        if entry.get("level") == level:
            return {"code": entry.get("code", ""), "name": entry.get("name", "")}
    return {}


def level_names(dataset: str) -> list[str]:
    """返回某数据集声明的组织层级名列表（不存在时为空）。"""
    return [entry["level"] for entry in ORGANIZATION_BRIDGE.get(dataset, []) if entry.get("level")]