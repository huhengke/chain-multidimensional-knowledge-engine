"""M2 统一认知控制面服务（service）。

流程：理解问题 → 校验时间条件 → 固定配置档 → 路由到脑 → 聚合证据 →
脑内/跨脑合并 → 冲突消解 → 组织三阶段输出 → 写审计。

控制面是**唯一编排者**，不是事实源；对未接入的脑返回**显式错误**，不伪造数值。
"""

from __future__ import annotations

import sys as _sys
from datetime import datetime, timezone
from pathlib import Path as _Path
from typing import Any

# 让本模块既可被 `python -m uvicorn --app-dir control-plane/src` 导入，
# 也可被 `python control-plane/src/service.py` 直接执行：
# 同时暴露工程根（brains/topology 包）与 M2 源码目录（顶层模块）。
for _p in (_Path(__file__).resolve().parents[2], _Path(__file__).resolve().parent):
    if str(_p) not in _sys.path:
        _sys.path.insert(0, str(_p))

from pydantic import BaseModel, ConfigDict, Field

from brains.knowledge import KnowledgeBrain
from brains.knowledge.src.workbench import KnowledgeWorkbenchService
from brains.protocol import (
    Brain,
    BrainError,
    BrainName,
    BrainQuery,
    BrainResult,
    Constraints,
    EvidenceRef,
    MergeMode,
    ResultStatus,
    TimeScope,
)
from topology.src.ruler import Ruler

from audit import AuditEntry, AuditLog
from evidence import EvidenceAggregator
from governance.src import (
    SCOPE_COGNITION_QUERY,
    Governance,
    GovernanceDeniedError,
    Principal,
)
from orchestration.src.conflict import resolve_conflicts, resolved_values
from orchestration.src.orchestrator import Stages
from orchestration.src.stage1_fast import build_fast_recall
from orchestration.src.stage2_verify import build_verification, run_brains_parallel
from orchestration.src.stage3_answer import build_final_answer
from profile import FrozenProfile, ProfileRegistry, ProfileUnavailableError
from router import ALL_TARGETS, Router

# 目标脑未接入时的**显式**错误文案（结构化，不含任何数值）
BRAIN_NOT_CONNECTED_MESSAGE: dict[BrainName, str] = {
    BrainName.memory: "记忆脑（M3）尚未接入",
    BrainName.knowledge: "知识脑（M4）尚未接入",
    BrainName.structured: "结构化脑（M5）尚未接入",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CognitiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    session_id: str | None = None
    profile_id: str | None = None
    day_key: str | None = None
    raw_query: str
    time_scope: TimeScope | None = None
    targets: list[BrainName] | None = None
    constraints: Constraints = Field(
        default_factory=lambda: Constraints(
            no_fabricated_precision=True, merge_mode=MergeMode.separate
        )
    )


# StageFastRecall / StageVerification / StageFinalAnswer / Stages 已抽取到 M6 编排模块
# （orchestration.src），此处直接复用，避免双份定义。


class AuditMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started_at: str
    ended_at: str
    profile_frozen_at: str


class CognitiveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    trace_id: str
    day_key: str
    profile_id: str
    stages: Stages
    evidence: list[EvidenceRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    audit: AuditMeta


class CognitionService:
    def __init__(
        self,
        profiles=None,
        router: Router | None = None,
        brains: dict[BrainName, Brain] | None = None,
        governance: Governance | None = None,
    ) -> None:
        self.registry = ProfileRegistry(profiles)
        self.router = router or Router()
        # 知识脑是当前唯一已完成宿主接入的脑；记忆/结构化脑的注册状态不被本增量改变。
        self.brains: dict[BrainName, Brain] = (
            dict(brains) if brains is not None else {BrainName.knowledge: KnowledgeBrain()}
        )
        self.ruler = Ruler()
        self.evidence = EvidenceAggregator()
        self.audit = AuditLog()
        # M7 治理：默认构造一个真实可用的 Governance（治理审计镜像到本服务审计）。
        self.governance = governance if governance is not None else Governance(control_log=self.audit)
        knowledge_brain = self.brains.get(BrainName.knowledge)
        self.knowledge_workbench = (
            KnowledgeWorkbenchService(knowledge_brain, self.governance)
            if isinstance(knowledge_brain, KnowledgeBrain)
            else None
        )

    # ---- 只读接口 --------------------------------------------------------
    def healthz(self) -> dict[str, Any]:
        try:
            active_id: str | None = self.registry.active_profile().profile_id
        except ProfileUnavailableError:
            active_id = None
        return {
            "status": "ok",
            "service": "cognition-control-plane",
            "version": "0.1.0",
            "timezone": self.ruler.timezone_name,
            "active_profile": active_id,
            "brains": {
                brain.value: ("connected" if brain in self.brains else "unavailable")
                for brain in ALL_TARGETS
            },
        }

    def profile_view(self) -> dict[str, Any]:
        active = self.registry.active_profile()
        return {
            "active": active.model_dump(mode="json"),
            "profiles": [p.model_dump(mode="json") for p in self.registry.list_profiles()],
            "backend_health": {
                profile.engine: self.registry.backend_healthy(profile.engine)
                for profile in self.registry.list_profiles()
            },
        }

    # ---- 编排 ------------------------------------------------------------
    def build_brain_query(
        self,
        request: CognitiveRequest,
        day_key: str,
        profile_id: str,
        constraints: dict[str, Any],
    ) -> BrainQuery:
        """构造统一脑调用入参（三脑共享同一 day_key）。"""
        return BrainQuery(
            request_id=request.request_id,
            trace_id=request.trace_id,
            day_key=day_key,
            raw_query=request.raw_query,
            profile_id=profile_id,
            time_scope=request.time_scope,
            constraints=constraints,
        )

    def query(
        self, request: CognitiveRequest, principal: Principal | None = None
    ) -> CognitiveResponse:
        """执行一次统一认知请求。

        ``principal`` 为可选调用主体：非空时经 M7 治理鉴权，越权**显式拒绝**（不静默放行）；
        缺省为匿名（保持既有契约与行为不变）。
        """
        started_at = _now_iso()
        day_key = request.day_key if request.day_key is not None else self.ruler.day_key()
        if self.ruler.day_key(day_key) != day_key:
            raise ValueError(f"day_key 非法：{request.day_key!r}")

        # M7 治理：显式主体越权一律拒绝（不静默放行、不伪造结果）。
        if principal is not None:
            decision = self.governance.authorize(
                principal,
                SCOPE_COGNITION_QUERY,
                request_id=request.request_id,
                trace_id=request.trace_id,
            )
            if not decision.allowed:
                raise GovernanceDeniedError(
                    "AUTHZ_DENIED", decision.reason, missing=decision.missing
                )

        try:
            pin: FrozenProfile = self.registry.freeze(request.request_id, request.profile_id)
        except ProfileUnavailableError as exc:
            self.audit.record(
                AuditEntry(
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    day_key=day_key,
                    profile_id=request.profile_id or "<unresolved>",
                    profile_frozen_at="",
                    engine="<unknown>",
                    targets=[],
                    status="profile_unavailable",
                    started_at=started_at,
                    ended_at=_now_iso(),
                )
            )
            raise exc

        targets = self.router.route(request.raw_query, request.targets)
        constraints = request.constraints.model_dump(mode="json")

        def _unavailable(target: BrainName, _query: BrainQuery) -> BrainResult:
            return BrainResult(
                brain=target,
                request_id=request.request_id,
                trace_id=request.trace_id,
                day_key=day_key,
                status=ResultStatus.unavailable,
                error=BrainError(
                    code="BRAIN_NOT_CONNECTED",
                    message=BRAIN_NOT_CONNECTED_MESSAGE[target],
                ),
            )

        # 三脑先独立并发计算（M6 编排模块），未接入脑返回显式错误。
        per_brain: dict[BrainName, BrainResult] = run_brains_parallel(
            targets,
            self.brains,
            lambda _target: self.build_brain_query(request, day_key, pin.profile_id, constraints),
            unavailable_factory=_unavailable,
        )
        evidence_map: dict[BrainName, list[EvidenceRef]] = {
            brain: list(result.evidence) for brain, result in per_brain.items()
        }
        warnings: list[str] = []
        for result in per_brain.values():
            warnings.extend(result.warnings)

        # 证据聚合（ev-N 分配）
        evidence = self.evidence.aggregate(evidence_map)

        # 合并与冲突消解（异类不相加；无命中不返回 0；冲突不静默）
        outcome = resolve_conflicts(list(per_brain.values()), request.constraints.merge_mode)
        warnings.extend(outcome.notes)

        # 三阶段输出（委派给 M6 编排模块）
        fast_recall = build_fast_recall(per_brain)
        verification = build_verification(
            per_brain, outcome.conflicts, resolved_values(outcome)
        )

        # M7 治理门禁：高风险结论显式标注（进入受控复核，不静默发布）。
        gate_limitations: list[str] = []
        gate_decision = self.governance.submit_gate(
            request.request_id,
            request.trace_id,
            text=request.raw_query,
            object_id=f"answer:{request.request_id}",
            subject=(principal.subject if principal is not None else "anonymous"),
        )
        if gate_decision.high_risk:
            gate_limitations.append(
                f"高风险结论（类别={gate_decision.category}）已进入受控复核，"
                f"状态={gate_decision.status.value}；未经人工 approve 不得发布。"
            )

        final_answer = build_final_answer(
            evidence=evidence,
            outcome=outcome,
            warnings=warnings,
            time_scope=request.time_scope,
            extra_limitations=gate_limitations,
        )
        warnings.extend(gate_limitations)
        citations = list(final_answer.citations)

        ended_at = _now_iso()
        self.audit.record(
            AuditEntry(
                request_id=request.request_id,
                trace_id=request.trace_id,
                day_key=day_key,
                profile_id=pin.profile_id,
                profile_frozen_at=pin.frozen_at,
                engine=pin.engine,
                targets=[t.value for t in targets],
                evidence_ids=citations,
                datasets=sorted(pin.generation_pins),
                generations=dict(pin.generation_pins),
                warnings=warnings,
                status="ok",
                started_at=started_at,
                ended_at=ended_at,
            )
        )

        return CognitiveResponse(
            request_id=request.request_id,
            trace_id=request.trace_id,
            day_key=day_key,
            profile_id=pin.profile_id,
            stages=Stages(
                fast_recall=fast_recall,
                verification=verification,
                final_answer=final_answer,
            ),
            evidence=evidence,
            warnings=warnings,
            audit=AuditMeta(
                started_at=started_at,
                ended_at=ended_at,
                profile_frozen_at=pin.frozen_at,
            ),
        )


def main() -> None:  # pragma: no cover - 启动入口
    import asyncio

    import uvicorn

    from api import app as _app

    # 直接驱动 ASGI server（等价于 uvicorn.run），避免个别环境下 CLI loop 工厂的兼容问题。
    server = uvicorn.Server(
        uvicorn.Config(_app, host="127.0.0.1", port=8077, log_level="info")
    )
    asyncio.run(server.serve())


if __name__ == "__main__":  # pragma: no cover
    main()
