"""Actual model-backed caption/transcription. Stored output is replayed without billing again."""
import base64
import io

import httpx
from PIL import Image

from brains.memory.src.extract import Feature, hash_vector, tokenize


def extract_media(store, asset, data, *, transport=None):
    mime = asset["mime"]
    purpose = "vision" if mime.startswith("image/") else "asr" if mime.startswith("audio/") else None
    if purpose is None:
        return None
    candidates = [m for m in store.list("model", "system") if m["brain"] == asset["brain"] and m["purpose"] == purpose and m["enabled"]]
    if not candidates:
        return None
    model = candidates[-1]
    headers = {"Authorization": "Bearer " + store.reveal(model["secret_ref"])} if model.get("secret_ref") else {}
    try:
        with httpx.Client(timeout=model["timeout"], follow_redirects=False, transport=transport) as client:
            if purpose == "vision":
                with Image.open(io.BytesIO(data)) as image:
                    image.thumbnail((1600, 1600))
                    output = io.BytesIO()
                    image.convert("RGB").save(output, "JPEG", quality=85)
                prompt = "提取图片中可见的文字及场景观察。保留不确定信息，不识别个人身份，不推测拍摄时间地点。只描述可见内容。"
                response = client.post(model["base_url"] + "/chat/completions", headers=headers, json={
                    "model": model["model"], "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")}},
                    ]}], "max_tokens": 2000})
                response.raise_for_status()
                text = response.json()["choices"][0]["message"]["content"]
            else:
                response = client.post(model["base_url"] + "/audio/transcriptions", headers=headers,
                    data={"model": model["model"]}, files={"file": (asset["filename"], data, mime)})
                response.raise_for_status()
                text = response.json()["text"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("模型未返回有效提取文本")
    except Exception as exc:
        # Provider error bodies can include credentials and complete media URLs.
        raise ValueError("媒体模型调用失败，请检查对应视觉/转写配置；原始文件已保留") from exc
    return {"text": text, "model": model["model"], "model_config_id": model["id"],
            "model_config_version": model["updated_at"], "purpose": purpose, "status": "inferred"}


class StoredMediaExtractor:
    """Text features derived from a persisted model observation, never a claimed visual embedding."""
    def __init__(self, modality):
        self.modality = modality

    def extract(self, item):
        if not item.metadata.get("model_extraction"):
            return Feature(name=self.modality + ".model_description", tree_id="memory.visual" if self.modality == "keyframe" else "memory.voice", kind="text", status="unavailable", reason="此资料未产生模型提取结果", model_version="n/a")
        extraction = item.metadata["model_extraction"]
        text = extraction["text"]
        return Feature(name=self.modality + ".model_description", tree_id="memory.voice" if self.modality == "asr" else "memory.visual",
            kind="text", status="available", vector=hash_vector(tokenize(text)),
            detail={"tokens": tokenize(text), "observation": text, "representation": "text_term_frequency", "model_status": "inferred"},
            confidence="0", model_version=extraction["model"] + ":" + extraction["model_config_version"])
