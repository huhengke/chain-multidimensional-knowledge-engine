"""M3 多模态时空记忆脑（MemoryBrain）。

五层记忆管道：ingest / extract / normalize / trace+growth / recall。
复用 M1 拓扑库（Ruler / PayloadStore / TopologyNode / TraceRecord /
OccurrenceRef / StarClusterIndex / GrowthEngine / GenerationStore）。
"""

from brains.memory.src.actions import ActionDescriptor, build_action_feature
from brains.memory.src.brain import MemoryBrain
from brains.memory.src.extract import Feature, ExtractorRegistry, default_registry
from brains.memory.src.ingest import IngestItem, MemoryPolicy, MemoryUnit
from brains.memory.src.media_store import MediaStore
from brains.memory.src.normalize import Anchors, Normalizer
from brains.memory.src.streaming import (
    StreamFailure,
    StreamKind,
    StreamSegment,
    StreamSession,
    StreamSessionService,
    StreamStatus,
)

__all__ = [
    "MemoryBrain",
    "MemoryUnit",
    "IngestItem",
    "MemoryPolicy",
    "Anchors",
    "Normalizer",
    "Feature",
    "ExtractorRegistry",
    "default_registry",
    "MediaStore",
    "ActionDescriptor",
    "build_action_feature",
    "StreamFailure",
    "StreamKind",
    "StreamSegment",
    "StreamSession",
    "StreamSessionService",
    "StreamStatus",
]
