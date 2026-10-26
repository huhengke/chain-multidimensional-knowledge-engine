# -*- coding: utf-8 -*-
"""读取已登记的数据源配置，并集中处理数据源 id 校验。"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import base64
import hashlib
import json
import os
from threading import RLock
from cryptography.fernet import Fernet
import yaml

from engine.core.config import ENGINE_CONFIG_DIR, load_yaml_config
from engine.core.errors import NotFoundError

DATASOURCE_FILE = ENGINE_CONFIG_DIR / "datasource.yaml"
_write_lock=RLock()

def _cipher():
    from engine.core.config import get_settings
    key=hashlib.sha256(('datasource-v1:'+get_settings().JWT_SECRET).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def list_datasource_configs() -> list[dict[str, Any]]:
    raw = load_yaml_config(DATASOURCE_FILE)
    output=[]
    for item in raw.get('datasources',[]) or []:
        item=dict(item)
        if item.get('encrypted_credentials'):
            item.update(json.loads(_cipher().decrypt(item.pop('encrypted_credentials').encode())))
        output.append(item)
    return output

def save_datasource(config):
    with _write_lock:
        items=list_datasource_configs()
        old=next((x for x in items if x.get('id')==config['id']),{})
        if not config.get('password') or config.get('password')=='***':
            config['password']=old.get('password','')
        items=[x for x in items if x.get('id')!=config['id']]+[config]
        serialized=[]
        for item in items:
            plain={k:v for k,v in item.items() if k.lower() in _SENSITIVE_KEYS}
            public={k:v for k,v in item.items() if k.lower() not in _SENSITIVE_KEYS}
            if plain:public['encrypted_credentials']=_cipher().encrypt(json.dumps(plain).encode()).decode()
            serialized.append(public)
        target=DATASOURCE_FILE.with_suffix('.yaml.tmp')
        with target.open('w',encoding='utf-8') as out:
            yaml.safe_dump({'version':1,'datasources':serialized},out,allow_unicode=True)
            out.flush();os.fsync(out.fileno())
        target.replace(DATASOURCE_FILE)
        return safe_datasource_config(config)


def get_datasource_config(source_id: str) -> dict[str, Any]:
    for item in list_datasource_configs():
        if str(item.get("id") or item.get("name") or "") == source_id:
            return item
    raise NotFoundError("数据源不存在", source_id)


_SENSITIVE_KEYS = {
    "password", "passwd", "secret", "api_key", "token", "access_token",
    "refresh_token", "private_key", "connection_string", "dsn",
}


def safe_datasource_config(item: dict[str, Any]) -> dict[str, Any]:
    """返回数据源元数据；任何凭据或可直接连接的字符串都不出现在响应中。"""
    return {
        key: ("***" if key.lower() in _SENSITIVE_KEYS and value else value)
        for key, value in item.items()
    }
