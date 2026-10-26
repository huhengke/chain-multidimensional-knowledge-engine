"""M3 受控实时视频接入。

视频源永远不直接写入记忆存储。适配器读取实际帧，``StreamSessionService``
负责状态、采样、背压和凭据生命周期，再把受管片段交给 ``MemoryBrain.ingest``。
视觉/语音语义仍由 M3 已有提取器如实报告为 unavailable，除非另行配置模型。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from brains.memory.src.brain import MemoryBrain
from brains.memory.src.ingest import IngestItem


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StreamStatus(str, Enum):
    draft = "draft"
    connecting = "connecting"
    running = "running"
    degraded = "degraded"
    failed = "failed"
    stopped = "stopped"


class StreamKind(str, Enum):
    camera = "camera"
    rtsp = "rtsp"


class StreamFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class StreamSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    kind: StreamKind
    source_summary: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    permission_scope: str = Field(min_length=1)
    device_index: int | None = Field(default=None, ge=0)
    status: StreamStatus = StreamStatus.draft
    sample_interval_seconds: float = Field(default=2.0, ge=0.25, le=60.0)
    max_frames: int = Field(default=48, ge=1, le=240)
    reconnect_limit: int = Field(default=3, ge=0, le=10)
    frames_captured: int = Field(default=0, ge=0)
    segments_ingested: int = Field(default=0, ge=0)
    reconnect_attempts: int = Field(default=0, ge=0)
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    stopped_at: str | None = None


class StreamSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    media_ref: str = Field(min_length=1)
    captured_at: str = Field(min_length=1)
    source_summary: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    permission_scope: str = Field(min_length=1)
    source_kind: str = "live_stream"
    annotation: str | None = None


class VideoStreamAdapter(Protocol):
    def connect(self) -> None: ...

    def read_frame(self) -> bytes: ...

    def close(self) -> None: ...


def mask_rtsp_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"rtsp", "rtsps"} or not parsed.hostname:
        raise StreamFailure("INVALID_STREAM_SOURCE", "RTSP 地址必须包含 rtsp/rtsps 协议和主机名")
    try:
        port_value = parsed.port
    except ValueError as exc:
        raise StreamFailure("INVALID_STREAM_SOURCE", "RTSP 地址端口无效") from exc
    port = f":{port_value}" if port_value else ""
    return f"{parsed.scheme.lower()}://{parsed.hostname}{port}/…"


class OpenCVCameraAdapter:
    """本机摄像头适配器。只有 ``connect`` 才打开硬件。"""

    def __init__(self, device_index: int) -> None:
        self.device_index = device_index
        self._capture = None

    def connect(self) -> None:
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise StreamFailure("STREAM_RUNTIME_UNAVAILABLE", "未安装 OpenCV") from exc
        backend = getattr(cv2, "CAP_DSHOW", 0) if sys.platform == "win32" else 0
        self._capture = cv2.VideoCapture(self.device_index, backend)
        if not self._capture or not self._capture.isOpened():
            self.close()
            raise StreamFailure("SOURCE_UNAVAILABLE", f"无法打开本机摄像头设备 {self.device_index}")

    def read_frame(self) -> bytes:
        if self._capture is None:
            raise StreamFailure("STREAM_NOT_CONNECTED", "摄像头尚未连接")
        ok, frame = self._capture.read()
        if not ok or frame is None:
            raise StreamFailure("FRAME_READ_FAILED", "未能从摄像头读取视频帧")
        import cv2

        encoded, jpeg = cv2.imencode(".jpg", frame)
        if not encoded:
            raise StreamFailure("FRAME_ENCODE_FAILED", "摄像头帧 JPEG 编码失败")
        return bytes(jpeg)

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class PyAvRtspAdapter:
    """RTSP 适配器。PyAV 使用其捆绑的解码能力，不需要 ffmpeg.exe。"""

    def __init__(self, url: str) -> None:
        self._url = url
        self._container = None
        self._frames = None

    def connect(self) -> None:
        try:
            import av
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise StreamFailure("STREAM_RUNTIME_UNAVAILABLE", "未安装 PyAV") from exc
        try:
            self._container = av.open(
                self._url,
                mode="r",
                options={"rtsp_transport": "tcp", "stimeout": "5000000"},
            )
            stream = next((item for item in self._container.streams if item.type == "video"), None)
            if stream is None:
                raise StreamFailure("STREAM_NO_VIDEO", "RTSP 源未提供视频轨")
            self._frames = self._container.decode(stream)
        except StreamFailure:
            self.close()
            raise
        except Exception as exc:  # noqa: BLE001 - PyAV errors are source-safe after masking
            self.close()
            raise StreamFailure("STREAM_CONNECT_FAILED", f"无法连接 RTSP 视频源：{type(exc).__name__}") from exc

    def read_frame(self) -> bytes:
        if self._frames is None:
            raise StreamFailure("STREAM_NOT_CONNECTED", "RTSP 尚未连接")
        try:
            frame = next(self._frames)
            array = frame.to_ndarray(format="bgr24")
            import cv2

            encoded, jpeg = cv2.imencode(".jpg", array)
            if not encoded:
                raise StreamFailure("FRAME_ENCODE_FAILED", "RTSP 帧 JPEG 编码失败")
            return bytes(jpeg)
        except StopIteration as exc:
            raise StreamFailure("STREAM_EOF", "RTSP 视频流已结束") from exc
        except StreamFailure:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StreamFailure("FRAME_READ_FAILED", f"RTSP 帧读取失败：{type(exc).__name__}") from exc

    def close(self) -> None:
        if self._container is not None:
            self._container.close()
            self._container = None
        self._frames = None


AdapterBuilder = Callable[[StreamSession, str | None], VideoStreamAdapter]


class StreamSessionService:
    """会话内存态流控制器；不保存 URL/密码且可替换适配器用于测试。"""

    def __init__(
        self,
        memory: MemoryBrain,
        *,
        camera_builder: AdapterBuilder | None = None,
        rtsp_builder: AdapterBuilder | None = None,
    ) -> None:
        self.memory = memory
        self._sessions: dict[str, StreamSession] = {}
        self._secrets: dict[str, str] = {}
        self._previews: dict[str, bytes] = {}
        self._segments: dict[str, list[StreamSegment]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._adapters: dict[str, VideoStreamAdapter] = {}
        self._stops: dict[str, threading.Event] = {}
        self._builders = {
            StreamKind.camera: camera_builder or self._build_camera,
            StreamKind.rtsp: rtsp_builder or self._build_rtsp,
        }
        self._lock = threading.RLock()

    @staticmethod
    def camera_sources() -> list[dict[str, object]]:
        """以 Windows PnP 元数据枚举设备，不调用 VideoCapture 打开硬件。"""
        if sys.platform != "win32":
            return [{"device_index": 0, "label": "默认摄像头", "status": "unknown"}]
        command = (
            "Get-CimInstance Win32_PnPEntity | Where-Object { "
            "$_.PNPClass -in @('Image','Camera') -or $_.Name -match 'camera|webcam|摄像头' "
            "} | Select-Object Name,PNPClass,Status,DeviceID | ConvertTo-Json -Compress"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if completed.returncode != 0 or not completed.stdout.strip():
                return []
            items = json.loads(completed.stdout)
            if isinstance(items, dict):
                items = [items]
            return [
                {
                    "device_index": index,
                    "label": str(item.get("Name") or f"摄像头 {index}"),
                    "status": str(item.get("Status") or "unknown"),
                    "device_id": str(item.get("DeviceID") or ""),
                }
                for index, item in enumerate(items)
                if isinstance(item, dict)
            ]
        except Exception:  # noqa: BLE001 - enumeration must not affect stream creation
            return []

    def create_camera(
        self,
        *,
        owner: str,
        device_index: int,
        permission_scope: str = "owner",
        sample_interval_seconds: float = 2.0,
    ) -> StreamSession:
        return self._create(
            kind=StreamKind.camera,
            owner=owner,
            source_summary=f"camera://device/{device_index}",
            device_index=device_index,
            permission_scope=permission_scope,
            sample_interval_seconds=sample_interval_seconds,
        )

    def create_rtsp(
        self,
        *,
        owner: str,
        url: str,
        permission_scope: str = "owner",
        sample_interval_seconds: float = 2.0,
    ) -> StreamSession:
        summary = mask_rtsp_url(url)
        session = self._create(
            kind=StreamKind.rtsp,
            owner=owner,
            source_summary=summary,
            device_index=None,
            permission_scope=permission_scope,
            sample_interval_seconds=sample_interval_seconds,
        )
        self._secrets[session.session_id] = url
        return session

    def _create(self, **values) -> StreamSession:
        session_id = f"stream-{uuid.uuid4().hex[:12]}"
        session = StreamSession(session_id=session_id, **values)
        with self._lock:
            self._sessions[session_id] = session
            self._segments[session_id] = []
            self._stops[session_id] = threading.Event()
        return session.model_copy(deep=True)

    def get(self, session_id: str) -> StreamSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise StreamFailure("STREAM_NOT_FOUND", f"流会话不存在：{session_id}")
            return session.model_copy(deep=True)

    def sessions(self) -> list[StreamSession]:
        with self._lock:
            return [item.model_copy(deep=True) for item in self._sessions.values()]

    def segments(self, session_id: str) -> list[StreamSegment]:
        self.get(session_id)
        with self._lock:
            return [item.model_copy(deep=True) for item in self._segments.get(session_id, [])]

    def preview(self, session_id: str) -> bytes:
        self.get(session_id)
        with self._lock:
            preview = self._previews.get(session_id)
        if preview is None:
            raise StreamFailure("PREVIEW_UNAVAILABLE", "尚未采集到可预览的视频帧")
        return preview

    def start(self, session_id: str) -> StreamSession:
        with self._lock:
            session = self._require_live(session_id)
            if session.status in {StreamStatus.connecting, StreamStatus.running}:
                return session.model_copy(deep=True)
            if session.status is StreamStatus.stopped:
                self._stops[session_id] = threading.Event()
            session.status = StreamStatus.connecting
            session.last_error_code = None
            session.last_error_message = None
            session.stopped_at = None
            session.updated_at = _now_iso()
            worker = threading.Thread(target=self._run, args=(session_id,), daemon=True)
            self._threads[session_id] = worker
            worker.start()
            return session.model_copy(deep=True)

    def stop(self, session_id: str) -> StreamSession:
        with self._lock:
            session = self._require_live(session_id)
            self._stops[session_id].set()
            session.status = StreamStatus.stopped
            session.stopped_at = _now_iso()
            session.updated_at = session.stopped_at
            self._secrets.pop(session_id, None)
            adapter = self._adapters.get(session_id)
            worker = self._threads.get(session_id)
            snapshot = session.model_copy(deep=True)
        # ``close`` 需在锁外执行：它可能解除一个正在阻塞的 read_frame。
        if adapter is not None:
            try:
                adapter.close()
            except Exception:  # noqa: BLE001 - 停止路径不得把资源清理异常暴露为源详情
                pass
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.5)
        return snapshot

    def stop_all(self) -> None:
        for session_id in list(self._sessions):
            self.stop(session_id)

    def stop_for_owner(self, owner: str) -> None:
        """在本地登录会话结束时释放该主体的设备和瞬态 RTSP 凭据。"""
        with self._lock:
            session_ids = [
                session_id
                for session_id, session in self._sessions.items()
                if session.owner == owner
            ]
        for session_id in session_ids:
            self.stop(session_id)

    def annotate(self, session_id: str, text: str) -> StreamSegment:
        session = self.get(session_id)
        value = text.strip()
        if not value:
            raise StreamFailure("INVALID_ANNOTATION", "事件说明不能为空")
        captured_at = _now_iso()
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        segment = StreamSegment(
            segment_id=f"seg-{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            media_ref=f"memory://stream/{session_id}/annotation/{uuid.uuid4().hex[:10]}",
            captured_at=captured_at,
            source_summary=session.source_summary,
            content_hash=digest,
            permission_scope=session.permission_scope,
            annotation=value,
        )
        self.memory.ingest(
            IngestItem(
                media_ref=segment.media_ref,
                modality="text",
                content=value,
                timestamp=captured_at,
                source_ref=session.source_summary,
                source_kind="live_stream",
                trust="user",
                permission_scope=session.permission_scope,
                metadata={"stream_session": session_id, "source_kind": "live_stream"},
            )
        )
        self._append_segment(segment, increment=False)
        return segment

    def _run(self, session_id: str) -> None:
        adapter: VideoStreamAdapter | None = None
        while True:
            with self._lock:
                session = self._require_live(session_id)
                stop = self._stops[session_id]
                if stop.is_set():
                    return
                secret = self._secrets.get(session_id)
            try:
                # 服务层自身校验，避免测试/替换适配器意外绕过 RTSP 瞬态凭据边界。
                if session.kind is StreamKind.rtsp and not secret:
                    raise StreamFailure("STREAM_CREDENTIALS_EXPIRED", "RTSP 会话凭据已失效，请重新创建会话")
                adapter = self._builders[session.kind](session, secret)
                with self._lock:
                    self._adapters[session_id] = adapter
                adapter.connect()
                self._set_status(session_id, StreamStatus.running)
                last_sample_at = 0.0
                while not stop.is_set():
                    frame = adapter.read_frame()
                    now = time.monotonic()
                    if now - last_sample_at < session.sample_interval_seconds:
                        time.sleep(min(0.01, session.sample_interval_seconds / 10))
                        continue
                    last_sample_at = now
                    self._ingest_frame(session_id, frame)
                return
            except StreamFailure as exc:
                if stop.is_set():
                    return
                if not self._retry_or_fail(session_id, exc):
                    return
            except Exception as exc:  # noqa: BLE001 - keep source details private
                if stop.is_set():
                    return
                if not self._retry_or_fail(session_id, StreamFailure("STREAM_RUNTIME_ERROR", type(exc).__name__)):
                    return
            finally:
                if adapter is not None:
                    adapter.close()
                    with self._lock:
                        if self._adapters.get(session_id) is adapter:
                            self._adapters.pop(session_id, None)
                    adapter = None

    def _retry_or_fail(self, session_id: str, error: StreamFailure) -> bool:
        with self._lock:
            session = self._require_live(session_id)
            session.last_error_code = error.code
            session.last_error_message = error.message
            session.reconnect_attempts += 1
            session.updated_at = _now_iso()
            if session.reconnect_attempts > session.reconnect_limit:
                session.status = StreamStatus.failed
                self._secrets.pop(session_id, None)
                return False
            session.status = StreamStatus.degraded
        time.sleep(min(0.25 * self.get(session_id).reconnect_attempts, 1.0))
        if self._stops[session_id].is_set():
            return False
        self._set_status(session_id, StreamStatus.connecting)
        return True

    def _ingest_frame(self, session_id: str, frame: bytes) -> None:
        session = self.get(session_id)
        captured_at = _now_iso()
        digest = hashlib.sha256(frame).hexdigest()
        segment_id = f"seg-{uuid.uuid4().hex[:12]}"
        media_ref = f"memory://stream/{session_id}/frame/{segment_id}"
        segment = StreamSegment(
            segment_id=segment_id,
            session_id=session_id,
            media_ref=media_ref,
            captured_at=captured_at,
            source_summary=session.source_summary,
            content_hash=digest,
            permission_scope=session.permission_scope,
        )
        self.memory.ingest(
            IngestItem(
                media_ref=media_ref,
                modality="keyframe",
                raw_content=frame,
                timestamp=captured_at,
                source_ref=session.source_summary,
                source_kind="live_stream",
                trust="device",
                permission_scope=session.permission_scope,
                metadata={
                    "captured_at": captured_at,
                    "duration": "0",
                    "stream_session": session_id,
                    "source_kind": "live_stream",
                    "content_hash": digest,
                },
            )
        )
        with self._lock:
            self._previews[session_id] = frame
            self._sessions[session_id].frames_captured += 1
        self._append_segment(segment, increment=True)

    def _append_segment(self, segment: StreamSegment, *, increment: bool) -> None:
        with self._lock:
            items = self._segments[segment.session_id]
            items.append(segment)
            session = self._sessions[segment.session_id]
            if len(items) > session.max_frames:
                expired = items[: len(items) - session.max_frames]
                del items[: len(items) - session.max_frames]
                for old_segment in expired:
                    # 仅回收真实采样帧；用户的独立文本标注仍作为记忆记录保留。
                    if old_segment.annotation is None:
                        self.memory.store.raw.delete(old_segment.media_ref)
            if increment:
                session.segments_ingested += 1
            session.updated_at = _now_iso()

    def _set_status(self, session_id: str, status: StreamStatus) -> None:
        with self._lock:
            session = self._require_live(session_id)
            if not self._stops[session_id].is_set():
                session.status = status
                session.updated_at = _now_iso()

    def _require_live(self, session_id: str) -> StreamSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise StreamFailure("STREAM_NOT_FOUND", f"流会话不存在：{session_id}")
        return session

    @staticmethod
    def _build_camera(session: StreamSession, _secret: str | None) -> VideoStreamAdapter:
        return OpenCVCameraAdapter(session.device_index or 0)

    @staticmethod
    def _build_rtsp(_session: StreamSession, secret: str | None) -> VideoStreamAdapter:
        if not secret:
            raise StreamFailure("STREAM_CREDENTIALS_EXPIRED", "RTSP 会话凭据已失效，请重新创建会话")
        return PyAvRtspAdapter(secret)


__all__ = [
    "OpenCVCameraAdapter",
    "PyAvRtspAdapter",
    "StreamFailure",
    "StreamKind",
    "StreamSegment",
    "StreamSession",
    "StreamSessionService",
    "StreamStatus",
    "VideoStreamAdapter",
    "mask_rtsp_url",
]
