"""知识运行时契约、受管 WeKnora 桥接器及独立的开发用运行时。

生产管理平台由 platform_server.knowledge_runtime.EmbeddedRuntime 调用本项目
已锁定、已编译的 WeKnora 派生源码。BuiltinKnowledgeRuntime 仅用于核心开发测试，
不作为受管运行时失败后的回退。统一证据中的向量与打分使用定点字符串。
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from brains.knowledge.src.contract import KnowledgeSource

# 真实上游仓库；当前源码锁定与许可证见 third_party/weknora。
UPSTREAM_REPO_URL = "https://github.com/Tencent/WeKnora"
UPSTREAM_STATUS_PENDING_LOCK = "PENDING_LOCK"
BRIDGE_PROTOCOL_VERSION = "weknora-bridge/v1"
EMBEDDED_RUNTIME_PROFILE = "weknora-embedded-v1"

QUANT = Decimal("0.000001")
VECTOR_DIM = 32

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")
_CJK_SEQ_RE = re.compile(r"[\u4e00-\u9fff]+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_PAGE_MARKER_RE = re.compile(r"^\s*\[\[\s*page\s*=\s*(\d+)\s*\]\]\s*$", re.IGNORECASE)


def quantize(value: Decimal) -> str:
    """规范化定点字符串（6 位去尾零，永不科学计数法）。"""
    return format(value.quantize(QUANT, rounding=ROUND_HALF_UP).normalize(), "f")


def to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - 统一转换为显式错误
        raise ValueError(f"非定点数：{value!r}") from exc


def tokenize(text: str) -> list[str]:
    """确定性分词：拉丁词 + CJK 单字 + CJK 二元组。"""
    text = text or ""
    tokens = [t.lower() for t in _TOKEN_RE.findall(text)]
    for seq in _CJK_SEQ_RE.findall(text):
        tokens.extend(seq[i : i + 2] for i in range(len(seq) - 1))
    return tokens


def hash_vector(tokens: list[str], dim: int = VECTOR_DIM) -> list[str]:
    """把 token 序列哈希到固定维频次向量（归一化为定点字符串）。空输入返回 []。"""
    counts = [Decimal(0)] * dim
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        counts[int(digest[:8], 16) % dim] += 1
    total = sum(counts, Decimal(0))
    if total == 0:
        return []
    return [quantize(c / total) for c in counts]


def slugify(text: str) -> str:
    """确定性 slug：拉丁小写保留，其余折叠为 ``-``。"""
    text = (text or "").strip().lower()
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text).strip("-")
    return slug or "item"


# --------------------------------------------------------------------------- #
# 运行时数据结构
# --------------------------------------------------------------------------- #
class KnowledgeInput(BaseModel):
    """接入知识运行时的原始材料。

    ``raw_id`` 由调用方给出，用于稳定派生 ``document_id``。
    """

    model_config = ConfigDict(extra="forbid")

    raw_id: str = Field(min_length=1)
    source: KnowledgeSource
    content: str | None = None
    media_kind: str = "text"  # text | markdown | table | image | audio | video
    title: str | None = None
    lang: str | None = None
    version: str | None = None
    published_at: str | None = None
    effective_at: str | None = None
    revised_at: str | None = None
    retrieved_at: str | None = None
    parse_version: str = "knowledge-parse-v1"
    permission_scope: list[str] = Field(default_factory=lambda: ["public"])
    risk_domain: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModalityStatus(BaseModel):
    """模态可用性如实声明：``available`` / ``unavailable``。"""

    model_config = ConfigDict(extra="forbid")

    modality: str = Field(min_length=1)
    status: str
    reason: str | None = None


class ParsedBlock(BaseModel):
    """解析块：标题 / 段落 / 表格，附页码与锚点。"""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    text: str
    block_type: str = "paragraph"  # heading | paragraph | table
    heading_level: int = 0
    heading_path: list[str] = Field(default_factory=list)
    page: int | None = None
    anchor: str | None = None
    timecode: str | None = None


class ParsedContent(BaseModel):
    """解析结果：块序列 + 未配置模态的显式不可用声明。"""

    model_config = ConfigDict(extra="forbid")

    blocks: list[ParsedBlock] = Field(default_factory=list)
    unavailable: list[ModalityStatus] = Field(default_factory=list)
    parse_version: str = "knowledge-parse-v1"
    lang: str | None = None


class Segment(BaseModel):
    """切分出的可引用知识单元候选（避免任意截断）。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    unit_type: str = "section"  # section | preamble | table | transcript
    heading_path: list[str] = Field(default_factory=list)
    page: int | None = None
    anchor: str | None = None
    timecode: str | None = None
    confidence: str = "1"
    parent_text: str | None = None
    parent_index: int = -1


class RuntimeMetadata(BaseModel):
    """运行时身份元数据——如实标注，不暗示已接入 WeKnora。"""

    model_config = ConfigDict(extra="forbid")

    runtime: str
    upstream: str
    upstream_status: str
    capabilities: list[str] = Field(default_factory=list)
    unavailable_capabilities: list[str] = Field(default_factory=list)
    runtime_version: str | None = None
    protocol_version: str | None = None
    health_status: str = "ready"


class RuntimeUnavailableError(RuntimeError):
    """受管运行时不可用且绝不回退到内建运行时。"""

    def __init__(self, code: str, message: str, *, operation: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.operation = operation

    def as_error(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "operation": self.operation}}


# --------------------------------------------------------------------------- #
# 集成缝协议
# --------------------------------------------------------------------------- #
@runtime_checkable
class KnowledgeRuntime(Protocol):
    """知识运行时集成缝。

    五层管道只依赖这组方法；未来替换为 WeKnora 派生的运行时即可，
    六件套证据契约与管道编排保持不变。
    """

    @property
    def metadata(self) -> RuntimeMetadata:  # pragma: no cover - 协议声明
        ...

    def parse(self, raw: KnowledgeInput) -> ParsedContent:  # pragma: no cover
        ...

    def segment(self, parsed: ParsedContent) -> list[Segment]:  # pragma: no cover
        ...

    def embed(self, text: str) -> list[str]:  # pragma: no cover
        ...

    def rerank(self, query: str, candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
        ...  # pragma: no cover

    def concept_graph(self, segments: list[Segment]) -> dict[str, list[str]]:  # pragma: no cover
        ...


class ManagedWeKnoraRuntime:
    """已锁定 WeKnora 派生 bridge 的受管适配器。

    当前仓库仍为 ``PENDING_LOCK``，因此任何操作都会明确报
    ``RUNTIME_UNAVAILABLE``；绝不静默改用 ``BuiltinKnowledgeRuntime``。
    锁定后可通过同一份 stdio JSON-RPC 契约接入派生 Go bridge。
    """

    runtime_id = EMBEDDED_RUNTIME_PROFILE

    def __init__(
        self,
        bridge_command: list[str] | None = None,
        *,
        timeout_seconds: float = 20.0,
        lock_file: str | Path | None = None,
    ) -> None:
        root = Path(__file__).resolve().parents[3]
        self.lock_file = Path(lock_file) if lock_file else root / "third_party" / "weknora" / "UPSTREAM_COMMIT"
        self.bridge_command = list(bridge_command or [])
        self.timeout_seconds = timeout_seconds
        self._meta = RuntimeMetadata(
            runtime=EMBEDDED_RUNTIME_PROFILE,
            upstream=UPSTREAM_REPO_URL,
            upstream_status=self._upstream_status(),
            capabilities=[
                "parse.managed",
                "segment.parent_child",
                "retrieve.hybrid",
                "rerank.managed",
                "graph.candidates",
                "evaluate.fixed_set",
            ],
            unavailable_capabilities=[] if self._is_locked() else ["bridge", "upstream_source"],
            runtime_version=None,
            protocol_version=BRIDGE_PROTOCOL_VERSION,
            health_status="ready" if self._bridge_configured_and_locked() else "unavailable",
        )

    @property
    def metadata(self) -> RuntimeMetadata:
        return self._meta.model_copy()

    def health(self, *, request_id: str = "runtime-health", trace_id: str = "runtime-health") -> dict[str, Any]:
        return self._invoke("health", {}, request_id=request_id, trace_id=trace_id)

    def parse(self, raw: KnowledgeInput) -> ParsedContent:
        data = self._invoke("parse", {"input": raw.model_dump(mode="json")})
        return ParsedContent.model_validate(data.get("parsed", data))

    def segment(self, parsed: ParsedContent) -> list[Segment]:
        data = self._invoke("segment", {"parsed": parsed.model_dump(mode="json")})
        return [Segment.model_validate(item) for item in data.get("segments", [])]

    def embed(self, text: str) -> list[str]:
        data = self._invoke("index", {"text": text, "mode": "embed_only"})
        vector = data.get("vector", [])
        if not isinstance(vector, list) or not all(isinstance(item, str) for item in vector):
            raise RuntimeUnavailableError("INDEX_FAILED", "bridge index 响应缺少定点向量", operation="index")
        return vector

    def rerank(self, query: str, candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
        data = self._invoke(
            "rerank",
            {"query": query, "candidates": [{"id": cid, "text": text} for cid, text in candidates]},
        )
        ranked = data.get("candidates", [])
        output: list[tuple[str, str]] = []
        by_id = dict(candidates)
        for item in ranked:
            candidate_id = item.get("id") if isinstance(item, dict) else None
            if candidate_id in by_id:
                output.append((candidate_id, by_id[candidate_id]))
        if len(output) != len(candidates):
            raise RuntimeUnavailableError("RERANK_FAILED", "bridge rerank 响应与候选集合不一致", operation="rerank")
        return output

    def concept_graph(self, segments: list[Segment]) -> dict[str, list[str]]:
        data = self._invoke("graph", {"segments": [item.model_dump(mode="json") for item in segments]})
        graph = data.get("graph", {})
        if not isinstance(graph, dict):
            raise RuntimeUnavailableError("GRAPH_FAILED", "bridge graph 响应无效", operation="graph")
        return {str(key): [str(value) for value in values] for key, values in graph.items() if isinstance(values, list)}

    def _is_locked(self) -> bool:
        if not self.lock_file.exists():
            return False
        text = self.lock_file.read_text(encoding="utf-8")
        return bool(re.search(r"(?m)^\s*(?:STATUS|status)\s*[:=]\s*LOCKED\s*$", text)) and bool(re.search(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", text))

    def _upstream_status(self) -> str:
        if not self.lock_file.exists():
            return "LOCK_BLOCKED"
        text = self.lock_file.read_text(encoding="utf-8")
        match = re.search(r"(?:STATUS|status)\s*[:=]\s*([A-Z_]+)", text)
        if match:
            return match.group(1)
        return "LOCKED" if self._is_locked() else UPSTREAM_STATUS_PENDING_LOCK

    def _bridge_configured_and_locked(self) -> bool:
        return self._is_locked() and bool(self.bridge_command)

    def _invoke(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        request_id: str = "runtime-request",
        trace_id: str = "runtime-trace",
    ) -> dict[str, Any]:
        if not self._is_locked():
            raise RuntimeUnavailableError(
                "RUNTIME_UNAVAILABLE",
                "weknora-embedded-v1 未启用：上游源码尚未完成真实锁定",
                operation=operation,
            )
        if not self.bridge_command:
            raise RuntimeUnavailableError(
                "RUNTIME_UNAVAILABLE",
                "weknora-embedded-v1 已锁定但未配置受管 bridge 命令",
                operation=operation,
            )
        request = {
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "request_id": request_id,
            "trace_id": trace_id,
            "runtime_version": EMBEDDED_RUNTIME_PROFILE,
            "operation": operation,
            "payload": payload,
        }
        try:
            completed = subprocess.run(
                self.bridge_command,
                input=json.dumps(request, ensure_ascii=True) + "\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeUnavailableError("RUNTIME_TIMEOUT", "受管 bridge 调用超时", operation=operation) from exc
        except OSError as exc:
            raise RuntimeUnavailableError("RUNTIME_UNAVAILABLE", f"无法启动受管 bridge：{exc}", operation=operation) from exc
        if completed.returncode != 0:
            raise RuntimeUnavailableError("RUNTIME_EXITED", "受管 bridge 非正常退出", operation=operation)
        try:
            response = json.loads(completed.stdout.strip())
        except json.JSONDecodeError as exc:
            raise RuntimeUnavailableError("RUNTIME_PROTOCOL_INVALID", "受管 bridge 返回非 JSON 响应", operation=operation) from exc
        if not isinstance(response, dict) or response.get("protocol_version") != BRIDGE_PROTOCOL_VERSION:
            raise RuntimeUnavailableError("RUNTIME_PROTOCOL_MISMATCH", "受管 bridge 协议版本不匹配", operation=operation)
        if response.get("request_id") != request_id or response.get("operation") != operation:
            raise RuntimeUnavailableError("RUNTIME_PROTOCOL_INVALID", "受管 bridge 响应关联字段不匹配", operation=operation)
        if response.get("error"):
            error = response["error"]
            code = error.get("code", "RUNTIME_OPERATION_FAILED") if isinstance(error, dict) else "RUNTIME_OPERATION_FAILED"
            message = error.get("message", "受管 bridge 操作失败") if isinstance(error, dict) else "受管 bridge 操作失败"
            raise RuntimeUnavailableError(code, message, operation=operation)
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeUnavailableError("RUNTIME_PROTOCOL_INVALID", "受管 bridge 缺少对象 result", operation=operation)
        return result


# --------------------------------------------------------------------------- #
# 内建运行时（标准库确定性实现）
# --------------------------------------------------------------------------- #
class BuiltinKnowledgeRuntime:
    """内建知识运行时。

    - ``runtime_id = "builtin"``，``upstream_status = "PENDING_LOCK"``；
    - 未配置 OCR / 转写引擎时**显式 unavailable**，不伪造内容；
    - 解析 / 切分 / 向量 / 重排 / 概念图谱均为确定性标准库实现。
    """

    runtime_id = "builtin"

    def __init__(self) -> None:
        self._meta = RuntimeMetadata(
            runtime="builtin",
            upstream=UPSTREAM_REPO_URL,
            upstream_status=UPSTREAM_STATUS_PENDING_LOCK,
            capabilities=[
                "parse.text",
                "parse.table",
                "parse.heading",
                "segment.heading_semantic",
                "retrieve.hybrid",
                "rerank.lexical",
                "graph.concept_cooccurrence",
            ],
            unavailable_capabilities=["ocr", "asr_transcription", "vision", "speech"],
        )

    @property
    def metadata(self) -> RuntimeMetadata:
        return self._meta.model_copy()

    # ---- ① parse ---------------------------------------------------------
    def parse(self, raw: KnowledgeInput) -> ParsedContent:
        kind = (raw.media_kind or "text").lower()
        unavailable: list[ModalityStatus] = []
        blocks: list[ParsedBlock] = []

        if kind == "image":
            # 无 OCR 引擎 -> 显式不可用，绝不伪造图片文字。
            unavailable.append(
                ModalityStatus(modality="ocr", status="unavailable", reason="未配置 OCR 引擎")
            )
            return ParsedContent(
                blocks=[],
                unavailable=unavailable,
                parse_version=raw.parse_version,
                lang=raw.lang,
            )

        if kind in ("audio", "video"):
            unavailable.append(
                ModalityStatus(
                    modality="asr_transcription",
                    status="unavailable",
                    reason="未配置音视频转写引擎",
                )
            )
            # 若调用方提供了**显式文本稿**（非生成），仍按文本解析；否则无内容。
            if not (raw.content or "").strip():
                return ParsedContent(
                    blocks=[],
                    unavailable=unavailable,
                    parse_version=raw.parse_version,
                    lang=raw.lang,
                )

        if kind in {"pdf", "word", "powerpoint", "excel", "office", "binary"} and not (raw.content or "").strip():
            unavailable.append(
                ModalityStatus(
                    modality="document_parser",
                    status="unavailable",
                    reason=f"未配置 {kind} 解析器",
                )
            )
            return ParsedContent(
                blocks=[],
                unavailable=unavailable,
                parse_version=raw.parse_version,
                lang=raw.lang,
            )

        blocks = self._split_blocks(raw.content or "")
        return ParsedContent(
            blocks=blocks,
            unavailable=unavailable,
            parse_version=raw.parse_version,
            lang=raw.lang,
        )

    def _split_blocks(self, text: str) -> list[ParsedBlock]:
        # 先把 form feed 显式翻页，保证页码真实（不伪造）。
        raw_pages = text.replace("\r\n", "\n").replace("\r", "\n").split("\f")
        blocks: list[ParsedBlock] = []
        heading_path: list[str] = []
        page = 1
        for page_raw in raw_pages:
            lines = page_raw.split("\n")
            i = 0
            while i < len(lines):
                line = lines[i]
                marker = _PAGE_MARKER_RE.match(line)
                if marker:
                    page = int(marker.group(1))
                    i += 1
                    continue
                if not line.strip():
                    i += 1
                    continue
                heading = _HEADING_RE.match(line.strip())
                if heading:
                    level = len(heading.group(1))
                    title = heading.group(2).strip()
                    heading_path = heading_path[: level - 1]
                    heading_path.append(title)
                    blocks.append(
                        ParsedBlock(
                            index=len(blocks),
                            text=title,
                            block_type="heading",
                            heading_level=level,
                            heading_path=list(heading_path),
                            page=page,
                            anchor=f"h{len(blocks)}-{slugify(title)}",
                        )
                    )
                    i += 1
                    continue
                if "|" in line:
                    table_lines = []
                    while i < len(lines) and "|" in lines[i] and lines[i].strip():
                        table_lines.append(lines[i].strip())
                        i += 1
                    blocks.append(
                        ParsedBlock(
                            index=len(blocks),
                            text="\n".join(table_lines),
                            block_type="table",
                            heading_path=list(heading_path),
                            page=page,
                            anchor=f"t{len(blocks)}",
                        )
                    )
                    continue
                para_lines = []
                while (
                    i < len(lines)
                    and lines[i].strip()
                    and "|" not in lines[i]
                    and not _HEADING_RE.match(lines[i].strip())
                    and not _PAGE_MARKER_RE.match(lines[i])
                ):
                    para_lines.append(lines[i].strip())
                    i += 1
                blocks.append(
                    ParsedBlock(
                        index=len(blocks),
                        text="\n".join(para_lines),
                        block_type="paragraph",
                        heading_path=list(heading_path),
                        page=page,
                        anchor=f"p{len(blocks)}",
                    )
                )
            page += 1
        return blocks

    # ---- ② segment -------------------------------------------------------
    def segment(self, parsed: ParsedContent) -> list[Segment]:
        segments: list[Segment] = []
        current: dict[str, Any] | None = None

        def flush() -> None:
            nonlocal current
            if current is not None and current["parts"]:
                segments.append(
                    Segment(
                        text="\n".join(current["parts"]),
                        unit_type=current["unit_type"],
                        heading_path=list(current["heading_path"]),
                        page=current["page"],
                        anchor=current["anchor"],
                        timecode=current["timecode"],
                    )
                )
            current = None

        for block in parsed.blocks:
            if block.block_type == "heading":
                flush()
                current = {
                    "parts": [block.text],
                    "unit_type": "section",
                    "heading_path": list(block.heading_path),
                    "page": block.page,
                    "anchor": block.anchor,
                    "timecode": block.timecode,
                }
            elif block.block_type == "table":
                flush()
                segments.append(
                    Segment(
                        text=block.text,
                        unit_type="table",
                        heading_path=list(block.heading_path),
                        page=block.page,
                        anchor=block.anchor,
                        timecode=block.timecode,
                    )
                )
            else:  # paragraph
                if current is None:
                    current = {
                        "parts": [],
                        "unit_type": "preamble",
                        "heading_path": list(block.heading_path),
                        "page": block.page,
                        "anchor": block.anchor,
                        "timecode": block.timecode,
                    }
                current["parts"].append(block.text)
                if current["page"] is None:
                    current["page"] = block.page
                if current["timecode"] is None:
                    current["timecode"] = block.timecode
        flush()
        return segments

    # ---- ③ 向量（仅候选召回）---------------------------------------------
    def embed(self, text: str) -> list[str]:
        return hash_vector(tokenize(text))

    # ---- ④ 重排（确定性词项重合度）---------------------------------------
    def rerank(self, query: str, candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """按查询与候选文本的 token 重合度确定性重排。

        ``candidates`` 为 ``(id, text)`` 列表；返回同样结构、按分数降序（并列按 id 升序）。
        """
        q_tokens = set(tokenize(query))
        scored: list[tuple[Decimal, str, str]] = []
        for cid, ctext in candidates:
            c_tokens = set(tokenize(ctext))
            if not q_tokens or not c_tokens:
                score = Decimal(0)
            else:
                inter = len(q_tokens & c_tokens)
                union = len(q_tokens | c_tokens)
                score = Decimal(inter) / Decimal(union)
            scored.append((score, cid, ctext))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [(cid, ctext) for _, cid, ctext in scored]

    # ---- ⑤ 概念图谱（同段共现）-------------------------------------------
    def concept_graph(self, segments: list[Segment]) -> dict[str, list[str]]:
        """由同段共现构造概念关联图（确定性无向邻接表）。"""
        graph: dict[str, set[str]] = {}
        for seg in segments:
            terms = sorted({slugify(h) for h in seg.heading_path if h})
            for i, a in enumerate(terms):
                for b in terms[i + 1 :]:
                    graph.setdefault(a, set()).add(b)
                    graph.setdefault(b, set()).add(a)
        return {key: sorted(values) for key, values in graph.items()}


__all__ = [
    "UPSTREAM_REPO_URL",
    "UPSTREAM_STATUS_PENDING_LOCK",
    "BRIDGE_PROTOCOL_VERSION",
    "EMBEDDED_RUNTIME_PROFILE",
    "KnowledgeInput",
    "ModalityStatus",
    "ParsedBlock",
    "ParsedContent",
    "Segment",
    "RuntimeMetadata",
    "RuntimeUnavailableError",
    "KnowledgeRuntime",
    "ManagedWeKnoraRuntime",
    "BuiltinKnowledgeRuntime",
    "tokenize",
    "hash_vector",
    "slugify",
    "quantize",
]
