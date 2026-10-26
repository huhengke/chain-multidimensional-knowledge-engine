"""Explicitly started camera/RTSP sessions with durable sampled-frame imports."""
import threading

from brains.memory.src.brain import MemoryBrain
from brains.memory.src.streaming import StreamFailure, StreamSessionService, StreamStatus
from platform_server.store import now


class DurableStreamService(StreamSessionService):
    def __init__(self, ingestion, owner, source, limit, **adapters):
        super().__init__(MemoryBrain(), **adapters)
        self.ingestion, self.owner, self.source, self.limit = ingestion, owner, source, limit

    def _ingest_frame(self, session_id, frame):
        session = self.get(session_id)
        if session.frames_captured >= self.limit:
            raise StreamFailure("FRAME_LIMIT", "本次采集已达到帧数上限")
        self.ingestion.submit(self.owner, self.source, f"{session_id}-{session.frames_captured:06d}.jpg", frame,
                              {"timestamp": now(), "stream_session": session_id})
        super()._ingest_frame(session_id, frame)
        if self.get(session_id).frames_captured >= self.limit:
            self._stops[session_id].set()
            with self._lock:
                self._sessions[session_id].status = StreamStatus.stopped
                self._sessions[session_id].stopped_at = now()
                self._secrets.pop(session_id, None)
        self.persist(session_id)

    def persist(self, session_id):
        session = self.get(session_id).model_dump(mode="json")
        self.ingestion.store.put("stream", self.owner, dict(session, id=session_id, source_id=self.source["id"]))

    def _run(self, session_id):
        try:
            super()._run(session_id)
        finally:
            self.persist(session_id)


class Streams:
    def __init__(self, ingestion):
        self.ingestion = ingestion
        self.live = {}
        self.lock = threading.RLock()
        for record in ingestion.store.list("stream"):
            if record["status"] in {"connecting", "running", "degraded"}:
                record.update(status="stopped", last_error_message="服务已重启，请手动重新启动采集")
                ingestion.store.put("stream", record["owner"], record)

    def start(self, owner, source, limit):
        with self.lock:
            for service, session_id in self.live.values():
                if service.owner == owner and service.source["id"] == source["id"] and service.get(session_id).status in {StreamStatus.running, StreamStatus.connecting, StreamStatus.degraded}:
                    raise ValueError("该来源正在采集，请先停止")
            service = DurableStreamService(self.ingestion, owner, source, limit)
            options = dict(owner=owner, sample_interval_seconds=source["sample_interval"])
            if source["kind"] == "rtsp":
                session = service.create_rtsp(url=self.ingestion.store.reveal(source["secret_ref"]), **options)
            elif source["kind"] == "camera":
                session = service.create_camera(device_index=source["device_index"], **options)
            else:
                raise ValueError("请选择摄像头或 RTSP 来源")
            self.live[session.session_id] = (service, session.session_id)
            service.persist(session.session_id)
            session = service.start(session.session_id)
            service.persist(session.session_id)
            self.ingestion.store.audit(owner, "stream.start", session.session_id)
            return session.model_dump(mode="json")

    def stop(self, owner, session_id):
        with self.lock:
            pair = self.live.get(session_id)
            if not pair or pair[0].owner != owner:
                raise ValueError("采集会话不存在或已终止")
            service = pair[0]
            session = service.stop(session_id)
            service.persist(session_id)
            self.ingestion.store.audit(owner, "stream.stop", session_id)
            return session.model_dump(mode="json")

    def list(self, owner):
        with self.lock:
            for service, session_id in self.live.values():
                if service.owner == owner:
                    service.persist(session_id)
            return self.ingestion.store.list("stream", owner)

    def close(self):
        for service, session_id in self.live.values():
            service.stop(session_id)
            service.persist(session_id)
