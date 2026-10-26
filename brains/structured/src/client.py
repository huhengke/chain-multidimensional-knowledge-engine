"""引擎客户端：把统一请求 POST 到现有 FastAPI 引擎。

- ``EngineClient``：协议边界，便于测试注入。
- ``HttpEngineClient``：基于标准库 ``urllib.request`` 的真实实现（不引入 httpx）。
  HTTP 不可达 / 超时 / 非 2xx 一律抛 ``EngineUnavailableError``，
  由上层映射为显式 ``error``，**绝不伪造数值**。
- ``transport``：可注入的传输函数，测试时用它替代真实网络。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol, runtime_checkable

QUERY_PATH = "/api/v1/engine/query"


class EngineUnavailableError(RuntimeError):
    """现有引擎不可达 / 超时 / 返回非 2xx 时抛出。"""


#: 传输函数签名：``transport(url, payload, headers, timeout) -> (status, decoded_body)``。
#: ``decoded_body`` 必须是已解析的 JSON ``dict``。
Transport = Callable[[str, dict[str, Any], dict[str, str], float], "tuple[int, dict]"]


@runtime_checkable
class EngineClient(Protocol):
    """引擎客户端协议。"""

    def query(self, payload: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
        ...


def _urllib_transport(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
) -> tuple[int, dict]:
    """默认传输：标准库 urllib 发送 JSON 并解析响应。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:  # 非 2xx 仍带响应体
        body = exc.read().decode("utf-8") if exc.fp is not None else ""
        decoded: dict[str, Any] = {}
        if body:
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    decoded = parsed
            except json.JSONDecodeError:
                decoded = {}
        return exc.code, decoded
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EngineUnavailableError(f"引擎不可达：{exc}") from exc


class HttpEngineClient:
    """基于 ``urllib.request`` 的引擎客户端。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 10.0,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self._transport: Transport = transport or _urllib_transport

    def query(self, payload: dict[str, Any], *, trace_id: str) -> dict[str, Any]:
        url = f"{self.base_url}{QUERY_PATH}"
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "X-Trace-Id": trace_id,
        }
        try:
            status, body = self._transport(url, payload, headers, self.timeout)
        except EngineUnavailableError:
            raise
        except Exception as exc:  # 传输层任何异常都视为不可用
            raise EngineUnavailableError(f"引擎请求失败：{exc}") from exc

        if not isinstance(status, int):
            raise EngineUnavailableError(f"引擎返回非法状态码：{status!r}")
        if status < 200 or status >= 300:
            message = ""
            if isinstance(body, dict):
                message = str(body.get("detail") or body.get("message") or "")
            suffix = f"：{message}" if message else ""
            raise EngineUnavailableError(f"引擎返回 HTTP {status}{suffix}")
        if not isinstance(body, dict):
            raise EngineUnavailableError("引擎响应不是 JSON 对象")
        return body