"""M5 结构化数据推理脑适配器（真实接通现有 FastAPI 引擎）。

``StructuredBrain`` 严格实现 ``brains.protocol.Brain`` 协议：
    name = BrainName.structured
    query(BrainQuery) -> BrainResult

它只做两件事：把 ``BrainQuery`` 翻译为引擎请求 → 把响应映射为 ``BrainResult``。
引擎不可达时不伪造数值，返回 ``error``（code=``ENGINE_UNAVAILABLE``）。
"""

from __future__ import annotations

from brains.protocol import (
    BrainError,
    BrainName,
    BrainQuery,
    BrainResult,
    ResultStatus,
)

from .client import EngineClient, EngineUnavailableError
from .mapping import build_request, to_result

CODE_ENGINE_UNAVAILABLE = "ENGINE_UNAVAILABLE"


class StructuredBrain:
    """结构化脑适配器。可直接注入控制面 ``brains: dict[BrainName, Brain]``。"""

    name: BrainName = BrainName.structured

    def __init__(self, client: EngineClient, *, default_dataset: str = "oil_monthly_sales") -> None:
        self.client = client
        self.default_dataset = default_dataset

    def query(self, query: BrainQuery) -> BrainResult:
        payload = build_request(query, default_dataset=self.default_dataset)
        try:
            response = self.client.query(payload, trace_id=query.trace_id)
        except EngineUnavailableError as exc:
            return BrainResult(
                brain=self.name,
                request_id=query.request_id,
                trace_id=query.trace_id,
                day_key=query.day_key,
                status=ResultStatus.error,
                claims=[],
                evidence=[],
                warnings=[],
                error=BrainError(code=CODE_ENGINE_UNAVAILABLE, message=str(exc)),
            )
        return to_result(query, response)