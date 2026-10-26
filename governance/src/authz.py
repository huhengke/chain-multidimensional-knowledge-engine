"""M7 权限判定（authz）。

真实实现基于 **scope 匹配**：

- ``Principal{subject, roles, scopes}`` 的有效 scope = 直接 scope ∪ 各角色映射 scope；
- 缺少所需 scope → **显式拒绝**（返回 ``AuthzDecision(allowed=False)`` 并列出 ``missing``），
  绝不返回数据；
- **原始媒体不暴露给无权限 Agent**：原始媒体引用需对应 scope（默认 ``memory:media:read``）
  才可回放，否则治理层返回空的媒体引用集合（不含任何媒体内容）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# ---- 权限作用域常量 -------------------------------------------------------
SCOPE_MEMORY_MEDIA_READ = "memory:media:read"
SCOPE_KNOWLEDGE_PUBLISH = "knowledge:publish"
SCOPE_STRUCTURED_QUERY = "structured:query"
SCOPE_COGNITION_QUERY = "cognition:query"


class Principal(BaseModel):
    """调用主体（Agent / 用户）。"""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1)
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)


class Policy(BaseModel):
    """权限策略：角色 → scope 映射 + 原始媒体回放 scope。"""

    model_config = ConfigDict(extra="forbid")

    role_scopes: dict[str, list[str]] = Field(default_factory=dict)
    media_scope: str = SCOPE_MEMORY_MEDIA_READ

    def effective_scopes(self, principal: Principal) -> set[str]:
        scopes: set[str] = set(principal.scopes)
        for role in principal.roles:
            scopes.update(self.role_scopes.get(role, ()))
        return scopes

    def has_scope(self, principal: Principal, scope: str) -> bool:
        return scope in self.effective_scopes(principal)


class AuthzDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    subject: str
    required: list[str] = Field(default_factory=list)
    granted: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    reason: str


class MediaAccessResult(BaseModel):
    """原始媒体回放判定结果。拒绝时 ``refs`` 恒为空（不含任何媒体内容）。"""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    refs: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    reason: str


class GovernanceDeniedError(RuntimeError):
    """治理层显式拒绝（结构化，不含任何被保护数据）。"""

    def __init__(self, code: str, message: str, *, missing=()) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.missing = list(missing)

    def as_error(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "missing": list(self.missing),
            }
        }


def default_policy() -> Policy:
    """默认真实可用策略：owner/admin 全量，analyst 可查询与治理发布评审。"""
    return Policy(
        role_scopes={
            "owner": [
                SCOPE_COGNITION_QUERY,
                SCOPE_MEMORY_MEDIA_READ,
                SCOPE_KNOWLEDGE_PUBLISH,
                SCOPE_STRUCTURED_QUERY,
            ],
            "admin": [
                SCOPE_COGNITION_QUERY,
                SCOPE_MEMORY_MEDIA_READ,
                SCOPE_KNOWLEDGE_PUBLISH,
                SCOPE_STRUCTURED_QUERY,
            ],
            "analyst": [
                SCOPE_COGNITION_QUERY,
                SCOPE_STRUCTURED_QUERY,
                SCOPE_KNOWLEDGE_PUBLISH,
            ],
            "reviewer": [SCOPE_COGNITION_QUERY, SCOPE_KNOWLEDGE_PUBLISH],
        }
    )


def authorize(principal: Principal, required_scope: str, policy: Policy) -> AuthzDecision:
    """单 scope 授权判定（越权显式拒绝）。"""
    granted = policy.effective_scopes(principal)
    ok = required_scope in granted
    return AuthzDecision(
        allowed=ok,
        subject=principal.subject,
        required=[required_scope],
        granted=sorted(granted),
        missing=[] if ok else [required_scope],
        reason=(
            f"授予 scope={required_scope}"
            if ok
            else f"拒绝：主体 {principal.subject!r} 缺少 scope={required_scope!r}"
        ),
    )


def guard_media_access(
    principal: Principal, policy: Policy, media_refs
) -> MediaAccessResult:
    """原始媒体回放门禁：无 ``media_scope`` 时拒绝且不返回任何媒体引用/内容。"""
    refs = list(media_refs or [])
    if policy.has_scope(principal, policy.media_scope):
        return MediaAccessResult(
            allowed=True,
            refs=refs,
            missing=[],
            reason=f"授予原始媒体回放 scope={policy.media_scope}",
        )
    return MediaAccessResult(
        allowed=False,
        refs=[],
        missing=[policy.media_scope],
        reason=(
            f"拒绝原始媒体回放：主体 {principal.subject!r} 缺少 scope={policy.media_scope!r}；"
            "不返回任何媒体内容"
        ),
    )


__all__ = [
    "Principal",
    "Policy",
    "AuthzDecision",
    "MediaAccessResult",
    "GovernanceDeniedError",
    "default_policy",
    "authorize",
    "guard_media_access",
    "SCOPE_MEMORY_MEDIA_READ",
    "SCOPE_KNOWLEDGE_PUBLISH",
    "SCOPE_STRUCTURED_QUERY",
    "SCOPE_COGNITION_QUERY",
]
