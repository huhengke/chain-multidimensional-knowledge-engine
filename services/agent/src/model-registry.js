import { config } from './config.js';
async function models() {
  const response = await fetch(config.platformBaseUrl + '/api/internal/structured-models', {
    headers: { 'X-Service-Token': config.serviceToken }, signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw Object.assign(new Error('无法读取本项目模型配置'), {status:503, code:'MODEL_CONFIG_UNAVAILABLE'});
  return (await response.json()).items;
}
function publicModel(item) {
  const {api_key, ...safe} = item;
  return {...safe, available: item.enabled, active: item.enabled, provider:'openai-compatible', supports_images:!!item.supports_images};
}
export async function listAgentModels() {
  const items = await models();
  return {active: items.find(x=>x.enabled)?.id || '', items:items.map(publicModel)};
}
export async function resolveAgentModel(id) {
  const items = await models();
  const model = items.find(x=>x.id === id && x.enabled) || (!id && items.find(x=>x.enabled));
  if (!model) throw Object.assign(new Error('请在模型配置中添加并启用结构化脑 Agent 对话模型'), {status:422,code:'MODEL_NOT_CONFIGURED'});
  return {...publicModel(model), environment:{OPENAI_API_KEY:model.api_key}, base_url:model.base_url};
}
