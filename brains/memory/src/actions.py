"""M3 动作特征结构化描述（actions）。

一期只保存**可查询、可追溯的结构化描述**：动作段落、关键点序列引用、姿态阶段、
方向、节奏、持续时间、置信度、镜头条件、来源时间码。
**不生成机器人控制代码**：描述模型 ``extra="forbid"``，任何控制指令字段都会被拒绝；
导出的 ``detail`` 键集合被限制在 ``ACTION_DESCRIPTOR_FIELDS`` 内。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from brains.memory.src.extract import Feature, hash_vector, quantize, to_decimal

# 允许出现在结构化描述中的字段（对外可查询/可追溯）。
ACTION_DESCRIPTOR_FIELDS: tuple[str, ...] = (
    "action_segment",
    "keypoint_series_ref",
    "pose_stage",
    "direction",
    "rhythm",
    "duration_seconds",
    "confidence",
    "camera_condition",
    "source_timecode",
)

# 任何“控制指令”字段：出现即拒绝（不生成控制代码）。
FORBIDDEN_CONTROL_FIELDS: frozenset[str] = frozenset(
    {
        "command",
        "commands",
        "control_code",
        "control_sequence",
        "motor_command",
        "servo_command",
        "joint_angle",
        "joint_angles",
        "setpoint",
        "torque",
        "actuation",
        "robot_command",
        "velocity_command",
        "trajectory_plan",
    }
)


class ActionDescriptor(BaseModel):
    """动作的结构化描述。禁止任何机器人控制指令字段。"""

    model_config = ConfigDict(extra="forbid")

    action_segment: str = Field(min_length=1)
    keypoint_series_ref: str | None = None
    pose_stage: str | None = None
    direction: str | None = None
    rhythm: str | None = None
    duration_seconds: str | None = None
    confidence: str = "0"
    camera_condition: str | None = None
    source_timecode: str | None = None

    @field_validator("duration_seconds")
    @classmethod
    def _check_duration(cls, value: str | None) -> str | None:
        if value is not None:
            to_decimal(value)
        return value

    @field_validator("confidence")
    @classmethod
    def _check_confidence(cls, value: str) -> str:
        number = to_decimal(value)
        if number < 0 or number > 1:
            raise ValueError(f"confidence 必须在 [0,1]：{value!r}")
        return value

    def assert_no_control_fields(self) -> None:
        """双保险：即使模型被改动，也拒绝控制指令字段。"""
        for key in self.model_dump():
            if key in FORBIDDEN_CONTROL_FIELDS:
                raise ValueError(f"动作特征禁止包含控制指令字段：{key!r}")


def build_action_feature(descriptor: ActionDescriptor, model_version: str) -> Feature:
    """把结构化动作描述转为可查询、可追溯的特征（含确定性向量）。不生成控制代码。"""
    descriptor.assert_no_control_fields()
    detail = descriptor.model_dump()
    # 限定 detail 键集合在允许字段内，杜绝任何控制指令外泄。
    detail = {key: detail[key] for key in ACTION_DESCRIPTOR_FIELDS if key in detail}

    tokens = [
        descriptor.action_segment,
        descriptor.pose_stage or "",
        descriptor.direction or "",
        descriptor.rhythm or "",
    ]
    vector = hash_vector([t for t in tokens if t])
    if descriptor.duration_seconds is not None:
        vector = [quantize(to_decimal(descriptor.duration_seconds) / 3600), *vector]

    confidence = descriptor.confidence or "0"
    return Feature(
        name="action.descriptor",
        tree_id="memory.action",
        kind="action",
        status="available",
        vector=vector,
        detail=detail,
        confidence=confidence,
        model_version=model_version,
    )