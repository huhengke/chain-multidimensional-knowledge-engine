"""M7 数据治理·隐私·安全（governance）。

- ``authz``：基于 scope 的权限判定（越权显式拒绝；原始媒体需 ``memory:media:read`` 才可回放）；
- ``policy_gate``：高风险结论 / 不可逆语义进受控复核（approve / reject / rollback）；
- ``audit``：治理审计（与 M2 ``audit`` 协同，可回放）；
- ``versions``：版本与可回滚指针（知识版本、拓扑 generation）。

本包自举 ``sys.path``，使 ``governance.src`` 在不依赖 ``conftest`` 时也能导入 M2 顶层 ``audit``。
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[1]
_CP_SRC = _ROOT / "control-plane" / "src"
for _p in (_ROOT, _CP_SRC):
    if str(_p) not in _sys.path:
        _sys.path.insert(0, str(_p))

from .src import (  # noqa: E402  (自举路径之后)
    HIGH_RISK_KEYWORDS,
    AuthzDecision,
    GateDecision,
    GateStatus,
    Governance,
    GovernanceAudit,
    GovernanceDeniedError,
    GovernanceEvent,
    MediaAccessResult,
    Policy,
    PolicyGate,
    Principal,
    RiskCategory,
    VersionRecord,
    VersionRegistry,
    authorize,
    default_policy,
    guard_media_access,
)

__all__ = [
    "Governance",
    "Principal",
    "Policy",
    "AuthzDecision",
    "MediaAccessResult",
    "GovernanceDeniedError",
    "authorize",
    "guard_media_access",
    "default_policy",
    "PolicyGate",
    "GateDecision",
    "GateStatus",
    "RiskCategory",
    "HIGH_RISK_KEYWORDS",
    "GovernanceAudit",
    "GovernanceEvent",
    "VersionRegistry",
    "VersionRecord",
]
