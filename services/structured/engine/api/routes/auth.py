# -*- coding: utf-8 -*-
"""认证路由：登录、当前用户。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from engine.api.schemas_api import LoginRequest, TokenResponse, UserInfo
from engine.core.errors import UnauthorizedError
from engine.security.auth import create_access_token, verify_password
from engine.security.deps import get_current_user, get_db
from engine.security.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

@router.get("/status")
def status(session: Session = Depends(get_db)):
    return {"setup_required": session.query(User).count() == 0}

@router.post("/setup")
def setup(body: LoginRequest, x_service_token: str = Header(default=""), session: Session = Depends(get_db)):
    import hmac
    from engine.core.config import get_settings
    from engine.security.auth import hash_password
    if not hmac.compare_digest(x_service_token, get_settings().ENGINE_SERVICE_TOKEN):
        raise HTTPException(403, "仅允许本项目平台初始化")
    # IMMEDIATE serializes concurrent first-account creation across workers.
    from sqlalchemy import text
    session.execute(text("BEGIN IMMEDIATE"))
    if session.query(User).count():
        raise HTTPException(409, "已经初始化，禁止重复创建管理员")
    if len(body.password) < 12 or len(set(body.password)) < 6:
        raise HTTPException(422, "管理员密码至少 12 位，且至少包含 6 个不同字符")
    import re
    if not re.fullmatch(r'[a-zA-Z0-9_@.\-]{1,64}', body.username):
        raise HTTPException(422, '用户名只允许字母、数字、下划线、点、@ 和短横线')
    salt, hashed = hash_password(body.password)
    session.add(User(username=body.username, display_name=body.username, role="admin", password_salt=salt, password_hash=hashed, is_active=True))
    session.commit()
    return {"initialized": True}


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, session: Session = Depends(get_db)) -> TokenResponse:
    user = session.query(User).filter(User.username == body.username).one_or_none()
    if user is None or not verify_password(
        body.password, user.password_salt, user.password_hash
    ):
        raise UnauthorizedError("用户名或密码错误")
    if not user.is_active:
        raise UnauthorizedError("账号已停用，请联系管理员")

    token = create_access_token(user.username, extra={"role": user.role, "credential_version": user.password_hash[-24:]})
    return TokenResponse(
        access_token=token,
        user=UserInfo(
            username=user.username,
            display_name=user.display_name,
            role=user.role,
        ),
    )


@router.get("/me", response_model=UserInfo)
def me(current: User = Depends(get_current_user)) -> UserInfo:
    return UserInfo(
        username=current.username,
        display_name=current.display_name,
        role=current.role,
    )
