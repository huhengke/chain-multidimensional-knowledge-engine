"""Start only services owned by this project; existing ports must have the expected identity."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import httpx
from platform_server.installation import configure, ROOT

def read_env(path):
    values={}
    if Path(path).is_file():
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            key,marker,value=line.partition('=')
            if marker and not key.strip().startswith('#'):
                values[key.strip()]=value.strip()
    return values

def start_weknora(data):
    runtime=ROOT/'.runtime/weknora'
    settings=read_env(runtime/'weknora.env')
    port=int(settings.get('SERVER_PORT') or 18090)
    base=f'http://127.0.0.1:{port}'
    os.environ['WEKNORA_BASE_URL']=base
    try:
        response=httpx.get(base+'/health',timeout=2)
        if response.status_code==200 and (response.json() or {}).get('status')=='ok':
            print(f'weknora: already running on {port}');return
    except (httpx.HTTPError,ValueError):
        pass
    binary=runtime/'weknora-lite.exe'
    if not binary.is_file():
        raise RuntimeError(f'WeKnora Lite sidecar missing: {binary}')
    environment=os.environ.copy();environment.update(settings)
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    with (data/'weknora.stdout.log').open('ab') as out,(data/'weknora.stderr.log').open('ab') as err:
        child=subprocess.Popen([str(binary)],cwd=runtime,env=environment,stdout=out,stderr=err,stdin=subprocess.DEVNULL,creationflags=flags)
    for _ in range(150):
        if child.poll() is not None: raise RuntimeError(f'weknora exited; see {data/"weknora.stderr.log"}')
        try:
            response=httpx.get(base+'/health',timeout=1)
            if response.status_code==200:
                print(f'weknora: ready on {port}, PID {child.pid}');break
        except httpx.HTTPError: pass
        time.sleep(1)
    else: raise RuntimeError('weknora did not become ready')
    (data/'weknora.pid').write_text(str(child.pid),encoding='ascii')

def start(node, port):
    data=configure()
    platform_python = ROOT / '.runtime/platform-venv/Scripts/python.exe'
    platform_python = str(platform_python) if platform_python.is_file() else sys.executable
    os.environ['PLATFORM_BASE_URL']=f'http://127.0.0.1:{port}'
    start_weknora(data)
    services=[('structured',18000,'chain-neuron-engine',ROOT/'services/structured',[sys.executable,'-m','uvicorn','main:app','--host','127.0.0.1','--port','18000']),
              ('agent',18001,'chain-neuron-agent-gateway',ROOT/'services/agent',[node,'src/server.js']),
              ('platform',port,'three-brain-platform',ROOT,[platform_python,'-m','uvicorn','platform_server.app:app','--host','127.0.0.1','--port',str(port)])]
    for name,p,identity,cwd,args in services:
        try:
            r=httpx.get(f'http://127.0.0.1:{p}/healthz',timeout=2)
        except httpx.HTTPError:
            r=None
        if r is not None:
            health=r.json()
            if health.get('service')!=identity or health.get('installation')!=str(data):
                raise RuntimeError(f'Port {p} belongs to another installation; refusing to reuse it')
            print(f'{name}: already running on {p}');continue
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        with (data/f'{name}.stdout.log').open('ab') as out,(data/f'{name}.stderr.log').open('ab') as err:
            child=subprocess.Popen(args,cwd=cwd,stdout=out,stderr=err,stdin=subprocess.DEVNULL,creationflags=flags)
        for _ in range(100):
            if child.poll() is not None: raise RuntimeError(f'{name} exited; see {data/name}.stderr.log')
            try:
                response=httpx.get(f'http://127.0.0.1:{p}/healthz',timeout=1)
                if response.status_code==200:
                    print(f'{name}: ready on {p}, PID {child.pid}');break
            except httpx.HTTPError: pass
            time.sleep(.2)
        else: raise RuntimeError(f'{name} did not become ready')
        (data/f'{name}.pid').write_text(str(child.pid),encoding='ascii')
    print(f'Open http://127.0.0.1:{port}/login and create this installation\'s first administrator.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--node',default='node');parser.add_argument('--port',type=int,default=8079)
    args=parser.parse_args();start(args.node,args.port)
