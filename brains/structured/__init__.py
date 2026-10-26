"""M5 结构化数据推理脑适配器。

对外导出：``StructuredBrain``（实现 ``brains.protocol.Brain``）、引擎客户端与映射工具。
"""

from .src import (
    ALLOWED_REQUEST_FIELDS,
    CODE_ENGINE_UNAVAILABLE,
    DATASETS,
    FAILURE_POLICY,
    ORGANIZATION_BRIDGE,
    ORGANIZATION_BRIDGE_RESOLUTION,
    EngineClient,
    EngineUnavailableError,
    HttpEngineClient,
    StructuredBrain,
    Transport,
    build_request,
    dataset_label,
    level_columns,
    level_names,
    metric_label,
    resolve_dataset,
    to_result,
)

__all__ = [
    "StructuredBrain",
    "CODE_ENGINE_UNAVAILABLE",
    "EngineClient",
    "HttpEngineClient",
    "EngineUnavailableError",
    "Transport",
    "build_request",
    "resolve_dataset",
    "to_result",
    "DATASETS",
    "ORGANIZATION_BRIDGE",
    "ORGANIZATION_BRIDGE_RESOLUTION",
    "ALLOWED_REQUEST_FIELDS",
    "FAILURE_POLICY",
    "dataset_label",
    "metric_label",
    "level_columns",
    "level_names",
]