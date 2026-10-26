import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceInput(Input):
    name: str = Field(min_length=1, max_length=200)
    brain: Literal["knowledge", "memory"]
    kind: Literal["file", "text", "camera", "rtsp"]
    device_index: int = Field(default=0, ge=0, le=32)
    rtsp_url: str = Field(default="", max_length=2048, repr=False)
    sample_interval: float = Field(default=5, ge=1, le=60)


class ImportInput(Input):
    source_id: str
    title: str = Field(min_length=1, max_length=250)
    content: str = Field(min_length=1, max_length=2_000_000)
    timestamp: str | None = None
    place: str | None = Field(default=None, max_length=200)
    person: str | None = Field(default=None, max_length=200)


class ModelInput(Input):
    name: str = Field(min_length=1, max_length=100)
    brain: Literal["orchestrator", "knowledge", "memory", "structured"]
    purpose: Literal["chat", "vision", "embedding", "rerank", "asr"] = "chat"
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default="", max_length=4096, repr=False)
    enabled: bool = False
    # Chat rows that share a deployment slug form one switchable vendor
    # family (for example codex or deepseek); one row may be the default per brain.
    deployment: str = Field(default="", max_length=50)
    deployment_name: str = Field(default="", max_length=100)
    is_default: bool = False
    supports_images: bool = False
    # OpenAI-compatible chat endpoints can expose token deltas over SSE.
    # Keeping this explicit lets deployments that only support JSON responses
    # opt out while enabling TTFT/generation timing for production providers.
    supports_stream: bool = True
    timeout: int = Field(default=90, ge=5, le=300)

    @field_validator("deployment")
    @classmethod
    def validate_deployment(cls, value):
        value = (value or "").strip().lower()
        if value and not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
            raise ValueError("部署标识只能包含小写字母、数字、下划线和连字符")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("模型地址必须是无凭据和查询参数的 HTTP(S) 地址")
        return value.rstrip("/")


class QueryInput(Input):
    query: str = Field(min_length=1, max_length=16000)
    start: str | None = None
    end: str | None = None
    # 可选的双时间轴显式约束；自然语言中的“去年/昨天/最近”仍由知识脑自动识别。
    content_start: str | None = None
    content_end: str | None = None
    ingest_start: str | None = None
    ingest_end: str | None = None
    observed_start: str | None = None
    observed_end: str | None = None
    place: str | None = None
    person: str | None = None

class EvaluationInput(Input):
    query: str = Field(min_length=1, max_length=16000)
    expected_asset_ids: list[str] = Field(min_length=1, max_length=100)
    k: int = Field(default=10, ge=1, le=100)


class ClassificationInput(Input):
    domain: Literal['medical', 'equipment', 'finance', 'legal', 'quality', 'safety', 'humanities', 'general'] | None = None
    reason: str = Field(min_length=2, max_length=500)

    @field_validator('reason')
    @classmethod
    def nonempty_reason(cls, value):
        if len(value.strip()) < 2:
            raise ValueError('请填写分类纠偏依据')
        return value.strip()


class SessionInput(Input):
    brain: Literal["unified", "knowledge", "memory"]
    title: str = Field(default="新会话", min_length=1, max_length=200)


class SessionTitleInput(Input):
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("会话名称不能为空")
        return value


class SessionPinInput(Input):
    pinned: bool


class SessionArchiveInput(Input):
    archived: bool


class MessageFeedbackInput(Input):
    feedback: Literal["up", "down"] | None = None


class MessageInput(QueryInput):
    model_id: str = Field(min_length=1)


class UnifiedMessageInput(QueryInput):
    route_mode: Literal["auto", "scoped"] = "auto"
    requested_brains: list[Literal["structured", "knowledge", "memory"]] = Field(default_factory=list, max_length=3)
    # Empty selects the installation default (Codex rows when configured).
    model_deployment: str = Field(default="", max_length=50)
    # Fast keeps the same retrieval and evidence but uses a compact prompt and a
    # capped completion; standard keeps the full two-lane evidence report.
    answer_mode: Literal["standard", "fast"] = "standard"

    @field_validator("requested_brains")
    @classmethod
    def unique_brains(cls, value):
        result = []
        for brain in value:
            if brain not in result:
                result.append(brain)
        return result


class SettingsInput(Input):
    upload_limit_mb: int = Field(default=50, ge=1, le=100)
    session_hours: int = Field(default=8, ge=1, le=24)
    stream_frame_limit: int = Field(default=300, ge=10, le=3000)


class ReviewInput(Input):
    action: Literal["approve", "reject", "rollback"]
    reason: str = Field(min_length=2, max_length=2000)
    expected_status: Literal['pending_review', 'published', 'rejected'] | None = None

    @field_validator('reason', mode='before')
    @classmethod
    def clean_reason(cls, value):
        return value.strip() if isinstance(value, str) else value
