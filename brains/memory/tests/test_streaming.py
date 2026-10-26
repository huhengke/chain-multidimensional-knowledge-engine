from __future__ import annotations

import time

import pytest

from brains.memory import MemoryBrain, StreamFailure, StreamSessionService, StreamStatus


class FakeAdapter:
    def __init__(self, frame: bytes = b"jpeg-frame") -> None:
        self.frame = frame
        self.connected = False
        self.closed = False

    def connect(self) -> None:
        self.connected = True

    def read_frame(self) -> bytes:
        if not self.connected:
            raise StreamFailure("STREAM_NOT_CONNECTED", "fake 未连接")
        return self.frame

    def close(self) -> None:
        self.closed = True
        self.connected = False


def test_rtsp_url_is_masked_and_invalid_port_is_rejected():
    from brains.memory.src.streaming import mask_rtsp_url

    assert mask_rtsp_url("rtsp://alice:secret@example.test:554/live") == "rtsp://example.test:554/…"
    with pytest.raises(StreamFailure) as exc:
        mask_rtsp_url("rtsp://example.test:not-a-port/live")
    assert exc.value.code == "INVALID_STREAM_SOURCE"
    assert "secret" not in str(exc.value)


def test_fake_stream_ingests_bounded_segments_and_clears_secret():
    adapters: list[FakeAdapter] = []

    def builder(_session, _secret):
        adapter = FakeAdapter()
        adapters.append(adapter)
        return adapter

    service = StreamSessionService(MemoryBrain(), rtsp_builder=builder)
    session = service.create_rtsp(
        owner="admin",
        url="rtsp://alice:secret@example.test/live",
        sample_interval_seconds=0.25,
    )
    assert "secret" not in session.model_dump_json()
    service.start(session.session_id)
    deadline = time.monotonic() + 2
    while service.get(session.session_id).frames_captured < 1 and time.monotonic() < deadline:
        time.sleep(0.03)
    running = service.get(session.session_id)
    assert running.status is StreamStatus.running
    assert running.frames_captured >= 1
    assert len(service.segments(session.session_id)) <= running.max_frames
    service.stop(session.session_id)
    stopped = service.get(session.session_id)
    assert stopped.status is StreamStatus.stopped
    assert adapters and adapters[0].closed is True
    service.start(session.session_id)
    deadline = time.monotonic() + 2
    while service.get(session.session_id).status not in {StreamStatus.failed, StreamStatus.running} and time.monotonic() < deadline:
        time.sleep(0.03)
    failed = service.get(session.session_id)
    assert failed.status is StreamStatus.failed
    assert failed.last_error_code == "STREAM_CREDENTIALS_EXPIRED"
