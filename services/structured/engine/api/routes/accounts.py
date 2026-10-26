"""Installation-owned accounts; no demo users or imported passwords."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from sqlalchemy import text
from engine.security.deps import get_db, require_roles
from engine.security.models import User
from engine.security.auth import hash_password
from threading import RLock

_account_lock=RLock()

router=APIRouter(prefix='/api/v1/engine/users',tags=['accounts'])
class Account(BaseModel):
    model_config=ConfigDict(extra='forbid')
    username:str=Field(min_length=1,max_length=64,pattern=r'^[a-zA-Z0-9_@.\-]+$')
    display_name:str=Field(default='',max_length=64)
    role:Literal['admin','operator','viewer']='viewer'
    is_active:bool=True
    password:str=Field(default='',max_length=1000,repr=False)

@router.get('')
def users(_=Depends(require_roles('admin')),db:Session=Depends(get_db)):
    return {'items':[{'username':u.username,'display_name':u.display_name,'role':u.role,'is_active':u.is_active} for u in db.query(User).order_by(User.username)]}

@router.put('/{username}')
def save(username:str,body:Account,_=Depends(require_roles('admin')),db:Session=Depends(get_db)):
    with _account_lock:
        return _save(username,body,db)

def _save(username,body,db):
    if username!=body.username:raise HTTPException(422,'用户名不能更改')
    user=db.query(User).filter(User.username==username).one_or_none()
    if not user and not body.password:raise HTTPException(422,'新账号必须设置密码')
    if body.password and (len(body.password)<12 or len(set(body.password))<6):raise HTTPException(422,'密码至少 12 位且至少含 6 个不同字符')
    if user and user.role=='admin' and user.is_active and (body.role!='admin' or not body.is_active):
        if db.query(User).filter(User.role=='admin',User.is_active==True).count()<=1:raise HTTPException(409,'不能停用或降级最后一个管理员')
    if not user:user=User(username=username)
    user.role=body.role;user.is_active=body.is_active;user.display_name=body.display_name or username
    if body.password:user.password_salt,user.password_hash=hash_password(body.password)
    db.add(user);db.commit()
    return {'username':user.username,'display_name':user.display_name,'role':user.role,'is_active':user.is_active}
