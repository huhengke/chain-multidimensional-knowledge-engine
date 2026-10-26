// Classify provider failures without displaying raw bodies, headers or API keys.
export async function modelRequestError(response, model) {
  const payload=await response.json().catch(()=>({}));
  const kind=payload?.error?.code || payload?.error?.type;
  const status=response.status;
  let message=`模型请求失败 HTTP ${status}，请检查提供方状态及模型能力。`;
  let code='MODEL_REQUEST_FAILED';
  if(['model_not_found','invalid_model','unknown_model'].includes(kind)) {
    code='MODEL_NOT_FOUND';
    message=`模型“${model}”不存在或当前账号无权使用（HTTP ${status}）。请到模型配置读取可用模型，选择实际模型 ID，不能填写厂商名称。`;
  } else if(status===404) {
    code='MODEL_ENDPOINT_NOT_FOUND';
    message='模型推理接口返回 HTTP 404。请检查 API Base URL 是否支持 /chat/completions，以及模型 ID 是否正确。';
  } else if(status===401||status===403) {
    code='MODEL_AUTH_FAILED';
    message=`模型鉴权失败（HTTP ${status}），请检查 API Key 和模型访问权限。`;
  }
  return Object.assign(new Error(message),{code});
}
