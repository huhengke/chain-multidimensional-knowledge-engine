"""M2 运行配置档（RuntimeProfile）与配置档固定（profile）。

- **一次请求只固定一个不可变配置档**，请求期内冻结。
- ``status ∈ {draft, ready, active}``；候选档须经验收 → 锁定 → 管理员人工激活。
- **禁止静默回退**：引擎故障时抛 ``ProfileUnavailableError``，绝不回退其它配置档，
  也不允许混用两套配置档。
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


def _asset_hash(name: str) -> str:
    """确定性的语义资产指纹（非占位：由内容标识派生）。"""
    digest = hashlib.sha256(f"semantic-asset:{name}".encode("utf-8")).hexdigest()
    return f"hash://{name}/{digest[:24]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProfileStatus(str, Enum):
    draft = "draft"
    ready = "ready"
    active = "active"


class RuntimeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(min_length=1)
    status: ProfileStatus
    engine: str = Field(min_length=1)
    generation_pins: dict[str, int] = Field(default_factory=dict)
    semantic_assets: dict[str, str] = Field(default_factory=dict)


class FrozenProfile(BaseModel):
    """请求期内固定的配置档快照（不可变）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    status: ProfileStatus
    engine: str
    generation_pins: dict[str, int]
    semantic_assets: dict[str, str]
    frozen_at: str


class ProfileUnavailableError(RuntimeError):
    """配置档不可用。**禁止回退**：调用方必须显式失败。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = code


def default_profiles() -> list[RuntimeProfile]:
    """现状（§5.4）：活动档 ``legacy-local-v1``；候选档 ``cnts-candidate-20261007-v2``（ready，未激活）。"""
    return [
        RuntimeProfile(
            profile_id="legacy-local-v1",
            status=ProfileStatus.active,
            engine="legacy",
            generation_pins={"oil_sales_daily": 7, "oil_monthly_sales": 3},
            semantic_assets={
                "foundation": _asset_hash("foundation"),
                "time_rules": _asset_hash("time_rules"),
                "organization_bridge": _asset_hash("organization_bridge"),
                "topology_contract": _asset_hash("topology_contract"),
                "query_contract": _asset_hash("query_contract"),
            },
        ),
        RuntimeProfile(
            profile_id="cnts-candidate-20261007-v2",
            status=ProfileStatus.ready,
            engine="cnts",
            generation_pins={},
            semantic_assets={
                "foundation": _asset_hash("foundation"),
                "time_rules": _asset_hash("time_rules"),
                "organization_bridge": _asset_hash("organization_bridge"),
                "topology_contract": _asset_hash("topology_contract"),
                "query_contract": _asset_hash("query_contract"),
            },
        ),
    ]


class ProfileRegistry:
    """配置档注册表：激活、冻结、后端健康。线程安全。"""

    def __init__(
        self,
        profiles: list[RuntimeProfile] | None = None,
        backend_health: dict[str, bool] | None = None,
    ) -> None:
        profiles = profiles if profiles is not None else default_profiles()
        self._profiles: dict[str, RuntimeProfile] = {p.profile_id: p for p in profiles}
        self._active_id: str | None = None
        for profile in profiles:
            if profile.status is ProfileStatus.active:
                self._active_id = profile.profile_id
                break
        self._backend_health: dict[str, bool] = dict(backend_health or {})
        self._pins: dict[str, FrozenProfile] = {}
        self._lock = threading.RLock()

    # ---- 查询 ------------------------------------------------------------
    def get(self, profile_id: str) -> RuntimeProfile:
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ProfileUnavailableError(
                "PROFILE_NOT_FOUND",
                f"运行配置档不存在：{profile_id!r}；禁止回退到其它配置档",
            )
        return profile

    def list_profiles(self) -> list[RuntimeProfile]:
        return list(self._profiles.values())

    def active_profile(self) -> RuntimeProfile:
        if self._active_id is None:
            raise ProfileUnavailableError("NO_ACTIVE_PROFILE", "当前无活动配置档")
        return self._profiles[self._active_id]

    # ---- 后端健康 --------------------------------------------------------
    def set_backend_health(self, engine: str, healthy: bool) -> None:
        with self._lock:
            self._backend_health[engine] = healthy

    def backend_healthy(self, engine: str) -> bool:
        return self._backend_health.get(engine, True)

    # ---- 激活 ------------------------------------------------------------
    def activate(self, profile_id: str) -> RuntimeProfile:
        """人工激活：仅 ``ready`` 档可激活（创建新的不可变 active 档）。"""
        with self._lock:
            profile = self.get(profile_id)
            if profile.status is not ProfileStatus.ready and profile.status is not ProfileStatus.active:
                raise ProfileUnavailableError(
                    "PROFILE_NOT_READY",
                    f"配置档 {profile_id!r} 状态为 {profile.status.value}，不可激活",
                )
            activated = RuntimeProfile(
                profile_id=profile.profile_id,
                status=ProfileStatus.active,
                engine=profile.engine,
                generation_pins=dict(profile.generation_pins),
                semantic_assets=dict(profile.semantic_assets),
            )
            self._profiles[profile_id] = activated
            self._active_id = profile_id
            return activated

    # ---- 冻结 ------------------------------------------------------------
    def freeze(self, request_id: str, profile_id: str | None = None) -> FrozenProfile:
        """为一次请求固定一个不可变配置档。

        同一 ``request_id`` 幂等返回同一快照（请求期内冻结）；
        配置档不存在 / 未激活 / 依赖引擎不可用时抛 ``ProfileUnavailableError``，
        **绝不回退**到其它配置档。
        """
        if not request_id:
            raise ValueError("request_id 不得为空")
        with self._lock:
            existing = self._pins.get(request_id)
            if existing is not None:
                return existing

            target_id = profile_id if profile_id is not None else self._active_id
            if target_id is None:
                raise ProfileUnavailableError("NO_ACTIVE_PROFILE", "当前无活动配置档，禁止回退")
            profile = self.get(target_id)
            if profile.status is not ProfileStatus.active:
                raise ProfileUnavailableError(
                    "PROFILE_NOT_ACTIVE",
                    f"配置档 {target_id!r} 状态为 {profile.status.value}，未激活；禁止回退",
                )
            if not self.backend_healthy(profile.engine):
                raise ProfileUnavailableError(
                    "ENGINE_UNAVAILABLE",
                    f"配置档 {target_id!r} 依赖引擎 {profile.engine!r} 不可用；"
                    "禁止静默回退到其它配置档",
                )

            pin = FrozenProfile(
                profile_id=profile.profile_id,
                status=profile.status,
                engine=profile.engine,
                generation_pins=dict(profile.generation_pins),
                semantic_assets=dict(profile.semantic_assets),
                frozen_at=_now_iso(),
            )
            self._pins[request_id] = pin
            return pin

    def frozen(self, request_id: str) -> FrozenProfile | None:
        return self._pins.get(request_id)

    def release(self, request_id: str) -> None:
        with self._lock:
            self._pins.pop(request_id, None)