"""M7 治理源码包（governance.src）。"""

from __future__ import annotations

from .audit import GovernanceAudit, GovernanceEvent
from .authz import (
    SCOPE_COGNITION_QUERY,
    SCOPE_KNOWLEDGE_PUBLISH,
    SCOPE_MEMORY_MEDIA_READ,
    SCOPE_STRUCTURED_QUERY,
    AuthzDecision,
    GovernanceDeniedError,
    MediaAccessResult,
    Policy,
    Principal,
    authorize,
    default_policy,
    guard_media_access,
)
from .facade import Governance
from .policy_gate import (
    HIGH_RISK_KEYWORDS,
    GateDecision,
    GateStatus,
    PolicyGate,
    RiskCategory,
)
from .versions import VersionRecord, VersionRegistry

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
    "SCOPE_COGNITION_QUERY",
    "SCOPE_MEMORY_MEDIA_READ",
    "SCOPE_KNOWLEDGE_PUBLISH",
    "SCOPE_STRUCTURED_QUERY",
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
