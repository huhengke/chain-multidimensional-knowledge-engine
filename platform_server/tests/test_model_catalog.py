import httpx
from fastapi.testclient import TestClient
from platform_server.app import create_app
from platform_server.tests.test_platform import identity_server, login


def test_catalog_checks_actual_id_and_probe_calls_inference(tmp_path):
    calls=[]
    def upstream(request):
        if request.url.host!='model':
            return identity_server(request)
        calls.append(request)
        assert request.headers['authorization']=='Bearer hidden-provider-key'
        if request.url.path=='/v1/models':
            return httpx.Response(200,json={'data':[{'id':'gpt-5.6-sol'}]})
        assert request.url.path=='/v1/chat/completions'
        return httpx.Response(404,json={'error':{'type':'model_not_found','message':'raw upstream includes hidden-provider-key'}})
    app=create_app(tmp_path,engine_url='http://engine',transport=httpx.MockTransport(upstream))
    with TestClient(app,headers={'X-Platform-Request':'1'}) as client:
        login(client)
        config={'name':'OpenAI provider','brain':'structured','purpose':'chat','base_url':'http://model/v1','model':'OpenAI','api_key':'hidden-provider-key','enabled':True}
        saved=client.put('/api/v1/platform/models/new',json=config).json()
        path='/api/v1/platform/models/'+saved['id']
        result=client.post(path+'/check')
        assert result.status_code==200
        assert result.json()['reachable'] and not result.json()['ok']
        assert result.json()['code']=='MODEL_NOT_FOUND'
        assert result.json()['model_ids']==['gpt-5.6-sol']
        assert 'hidden-provider-key' not in result.text
        discovered=client.post('/api/v1/platform/models/discover',json={'base_url':config['base_url'],'model_id':saved['id']})
        assert discovered.json()['model_ids']==['gpt-5.6-sol']
        count=len(calls)
        changed=client.post('/api/v1/platform/models/discover',json={'base_url':'http://another-host/v1','model_id':saved['id']})
        assert changed.status_code==400 and len(calls)==count
        probe=client.post(path+'/probe')
        assert not probe.json()['ok'] and 'OpenAI' in probe.json()['message']
        assert 'hidden-provider-key' not in probe.text
        config.update(model='gpt-5.6-sol',api_key='')
        assert client.put(path,json=config).status_code==200
        assert client.post(path+'/check').json()['ok']
        login(client,'reader')
        assert client.post(path+'/check').status_code==403
        assert client.post(path+'/probe').status_code==403
        assert client.post('/api/v1/platform/models/discover',json={'base_url':'http://model/v1'}).status_code==403


def test_successful_catalog_http_status_does_not_accept_invalid_body(tmp_path):
    def upstream(request):
        if request.url.host!='model': return identity_server(request)
        if request.url.path.endswith('/models'):return httpx.Response(200,text='<html>not a catalog</html>')
        return httpx.Response(200,json={'choices':[{'message':{'content':'OK'}}]})
    app=create_app(tmp_path,engine_url='http://engine',transport=httpx.MockTransport(upstream))
    with TestClient(app,headers={'X-Platform-Request':'1'}) as client:
        login(client)
        model=client.put('/api/v1/platform/models/new',json={'name':'manual-id','brain':'structured','base_url':'http://model/v1','model':'actual-id'}).json()
        assert not client.post('/api/v1/platform/models/'+model['id']+'/check').json()['ok']
        assert client.post('/api/v1/platform/models/'+model['id']+'/probe').json()['ok']
