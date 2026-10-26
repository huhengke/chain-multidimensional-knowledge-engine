"""Test the migrated engine in an isolated installation, with no old service access."""
import os
from pathlib import Path
import subprocess
import sys

def test_independent_bootstrap_roles_and_empty_catalog(tmp_path):
    root=Path(__file__).resolve().parents[2]
    script=r'''
import os
from fastapi.testclient import TestClient
from main import app
from engine.storage.database import DB_PATH
from engine.storage.adapters.cnts import CntsAdapter
from engine.core.config import ENGINE_ROOT
assert str(DB_PATH).startswith(os.environ['STRUCTURED_DATA_DIR'])
adapter=CntsAdapter()
assert adapter.python_root == (ENGINE_ROOT.parent/'cnts/python').resolve()
with TestClient(app) as c:
    assert c.get('/api/v1/auth/status').json()['setup_required'] is True
    account={'username':'acceptance_admin','password':'Acceptance-only-98765!'}
    assert c.post('/api/v1/auth/setup',json=account).status_code==403
    assert c.post('/api/v1/auth/setup',json=account,headers={'X-Service-Token':os.environ['ENGINE_SERVICE_TOKEN']}).status_code==200
    assert c.post('/api/v1/auth/setup',json=account,headers={'X-Service-Token':os.environ['ENGINE_SERVICE_TOKEN']}).status_code==409
    token=c.post('/api/v1/auth/login',json=account).json()['access_token']
    headers={'Authorization':'Bearer '+token}
    result=c.get('/api/v1/engine/datasets',headers=headers)
    assert result.status_code==200,result.text
    assert not result.json().get('items'),result.text
    viewer={'username':'acceptance_viewer','password':'Viewer-only-12345!','role':'viewer'}
    assert c.put('/api/v1/engine/users/acceptance_viewer',headers=headers,json=viewer).status_code==200
    result=c.get('/api/v1/engine/users',headers=headers)
    assert len(result.json()['items'])==2 and 'password' not in result.text
    vt=c.post('/api/v1/auth/login',json={'username':viewer['username'],'password':viewer['password']}).json()['access_token']
    assert c.get('/api/v1/engine/users',headers={'Authorization':'Bearer '+vt}).status_code==403
    viewer['is_active']=False
    assert c.put('/api/v1/engine/users/acceptance_viewer',headers=headers,json=viewer).status_code==200
    assert c.get('/api/v1/auth/me',headers={'Authorization':'Bearer '+vt}).status_code==401
    assert c.get('/api/v1/engine/derive-rules',headers=headers).status_code==200
    source={'id':'acceptance_db','name':'验收数据库','type':'mysql','host':'127.0.0.1','port':3306,'password':'source-only-password!'}
    assert c.put('/api/v1/engine/datasources',headers=headers,json=source).status_code==200
    from engine.ingestion.datasource_config import DATASOURCE_FILE,get_datasource_config
    assert source['password'] not in DATASOURCE_FILE.read_text(encoding='utf-8')
    assert get_datasource_config(source['id'])['password']==source['password']
    result=c.get('/api/v1/engine/datasources',headers=headers)
    assert result.status_code==200 and source['password'] not in result.text
    source['password']=''
    assert c.put('/api/v1/engine/datasources',headers=headers,json=source).status_code==200
    assert get_datasource_config(source['id'])['password']=='source-only-password!'
print('isolated account, roles, empty catalog and local CNTS paths verified')
'''
    env=dict(os.environ,PLATFORM_INSTALLATION_DIR=str(tmp_path/'installation'),STRUCTURED_DATA_DIR=str(tmp_path/'structured'),PYTHONIOENCODING='utf-8')
    result=subprocess.run([sys.executable,'-c',script],cwd=root/'services/structured',env=env,capture_output=True,text=True,encoding='utf-8',timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
