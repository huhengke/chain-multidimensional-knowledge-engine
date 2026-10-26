"""Model discovery validates the selected ID, separately from inference readiness."""
import httpx
import math
from pydantic import Field
from platform_server.contracts import Input, ModelInput


class ModelDiscoveryInput(Input):
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(default='', max_length=4096, repr=False)
    model_id: str | None = None


def model_base(value):
    return ModelInput.validate_url(value)


async def discover_models(conn, base_url, key):
    try:
        result = await conn.get(model_base(base_url) + '/models', headers={'Authorization': 'Bearer ' + key} if key else {})
    except httpx.HTTPError:
        return {'ok': False, 'model_ids': [], 'code': 'MODEL_CONNECTION_FAILED', 'message': '无法连接模型列表接口，请检查地址、网络和超时设置'}
    if result.status_code != 200:
        return {'ok': False, 'model_ids': [], 'code': 'MODEL_CATALOG_UNAVAILABLE', 'message': f'模型列表接口返回 HTTP {result.status_code}；请检查地址和凭据。若提供方不支持列表，请手动填写准确的模型 ID 并验证推理。'}
    try:
        data = result.json()['data']
        if not isinstance(data, list):
            raise ValueError('invalid catalog')
        ids = sorted({item['id'] for item in data if isinstance(item, dict) and isinstance(item.get('id'), str) and item['id'].strip()})
    except (ValueError, KeyError, TypeError):
        return {'ok': False, 'model_ids': [], 'code': 'MODEL_CATALOG_INVALID', 'message': '接口返回了 HTTP 200，但不是有效的模型列表，请检查 API Base URL'}
    return {'ok': True, 'model_ids': ids, 'message': f'已读取 {len(ids)} 个模型，请选择准确的模型 ID'}


def inference_error(status, model, payload):
    error = payload.get('error', {}) if isinstance(payload, dict) else {}
    kind = str(error.get('code') or error.get('type') or '') if isinstance(error, dict) else ''
    if kind in {'model_not_found', 'invalid_model', 'unknown_model'}:
        return f'模型“{model}”不存在或当前账号无权使用（HTTP {status}），请从可用模型列表重新选择。'
    if status == 404:
        return '推理接口返回 HTTP 404，请检查 API Base URL 是否支持 /chat/completions，以及模型 ID 是否正确。'
    if status in {401, 403}:
        return f'模型鉴权失败（HTTP {status}），请检查 API Key 和模型访问权限。'
    if status == 429:
        return '模型服务限流或额度不足（HTTP 429）；本次检索证据已保留，请稍后重试或检查提供方额度。'
    if status in {502, 503, 504}:
        return f'模型提供方网关或上游服务暂不可用（HTTP {status}）；检索证据已保留，请稍后重试或检查提供方状态。'
    return f'模型推理失败 HTTP {status}，请检查提供方状态和模型能力。'


def validate_inference_probe(purpose, status, model, payload):
    if status != 200:
        return False, inference_error(status, model, payload) if purpose == 'chat' else f'{purpose} 实际推理返回 HTTP {status}，请检查模型用途、协议和凭据'
    if not isinstance(payload, dict):
        return False, '推理接口返回结构无效'
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if purpose == 'embedding':
        rows = payload.get('data')
        vector = rows[0].get('embedding') if isinstance(rows, list) and len(rows)==1 and isinstance(rows[0], dict) else None
        ok = isinstance(vector, list) and len(vector)>1 and all(finite(x) for x in vector) and any(x != 0 for x in vector)
        return bool(ok), f'真实向量推理成功，维度 {len(vector)}；请继续用资料导入和检索验收。' if ok else '接口未返回有效的非零向量；请检查 embedding 模型用途'
    if purpose == 'rerank':
        rows = payload.get('results')
        ok = isinstance(rows, list) and len(rows)==2 and all(isinstance(r, dict) and type(r.get('index')) is int and finite(r.get('relevance_score')) for r in rows)
        ok = ok and {r['index'] for r in rows} == {0, 1}
        return bool(ok), '真实重排推理成功；请继续用知识检索核对相关性。' if ok else '重排结果缺少有效分数或候选编号'
    rows = payload.get('choices')
    ok = bool(isinstance(rows, list) and rows and isinstance(rows[0], dict) and isinstance(rows[0].get('message'), dict) and rows[0]['message'].get('content'))
    return ok, '真实对话推理验证成功；Agent 工具效果请在工作台继续核对。' if ok else '接口返回 HTTP 200，但没有有效回答，请检查推理协议'
