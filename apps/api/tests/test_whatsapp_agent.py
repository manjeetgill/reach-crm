import hashlib
import hmac
import json
import time
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_workflows import client, engine, org
from app import whatsapp_agent as wa
from app.models import WhatsAppAgent, Message, OutboundJob


def test_signed_turns_consent_replay_and_handoff(monkeypatch):
    org_id, _ = org('WhatsAppBridge')
    channel, app_id = str(uuid4()), str(uuid4())
    cfg = {'app_id':app_id,'secret':'s'*48}
    monkeypatch.setattr(wa,'settings',lambda organization:cfg)
    with Session(engine) as db:
        db.add(WhatsAppAgent(organization_id=org_id,channel_id=channel,app_id=app_id,phone_id='123456789',bridge_ready=True))
        db.commit()
    def send(text,event=None,stamp=None,bad=False,organization=None):
        payload={'event_id':event or str(uuid4()),'channel_id':channel,'app_id':app_id,'clinic_id':organization or org_id,'from':'919123456789','text':text}
        raw=json.dumps(payload).encode()
        stamp=stamp or str(int(time.time()))
        signature=hmac.new(wa.binding_secret(cfg,org_id,channel).encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        return client.post('/integrations/agent-platform/whatsapp',content=raw,headers={'x-agent-timestamp':stamp,'x-agent-signature':'bad' if bad else signature})
    assert send('hello',bad=True).status_code==403
    assert send('hello',stamp=str(int(time.time())-600)).status_code==403
    assert send('hello',organization=str(uuid4())).status_code==403
    assert 'START' in send('hello').json()['replies'][0]
    assert send('START').status_code==200
    monkeypatch.setattr(wa,'answer',lambda *args:{'answer':'Office opens at 9 AM.','mode':'extractive','citations':[{'title':'Office hours'}]})
    event=str(uuid4())
    reply=send('office hours',event=event)
    assert reply.json()['replies']==['Office opens at 9 AM.']
    assert send('office hours',event=event).json()==reply.json()
    assert send('changed',event=event).status_code==409
    with Session(engine) as db:
        messages=db.scalars(select(Message).where(Message.organization_id==org_id,Message.content=='Office opens at 9 AM.')).all()
        assert len(messages)==1 and messages[0].status=='handed_to_platform'
        assert not db.scalars(select(OutboundJob).where(OutboundJob.organization_id==org_id)).all()
    assert send('STOP').json()['replies']==[]
    assert send('office hours').json()['replies']==[]
    send('START')
    assert send('I want a human supervisor').json()['handledBy']=='human'
    assert send('office hours').json()['replies']==[]
    from app.models import Conversation
    with Session(engine) as db:
        conversation = db.scalar(select(Conversation).where(Conversation.organization_id == org_id))
        conversation.status = 'closed'
        db.commit()
    # A new incoming turn after closure must create a fresh conversation.
    assert send('office hours').json()['replies'] == ['Office opens at 9 AM.']
    with Session(engine) as db:
        assert len(db.scalars(select(Conversation).where(Conversation.organization_id == org_id)).all()) == 2


def test_setup_role_isolation_and_retry(monkeypatch):
    org_id,headers=org('WhatsAppSetup')
    _,other=org('WhatsAppOther')
    app_id,channel_id=str(uuid4()),str(uuid4())
    monkeypatch.setattr(wa,'settings',lambda org:{'app_id':app_id,'secret':'s'*48,'bridge_url':'http://127.0.0.1:18000/integrations/agent-platform/whatsapp'})
    calls=[]
    def remote(cfg,method,path,body=None):
        calls.append((method,path,body))
        if path=='/v1/whatsapp/setup':return {'phone_ids':['123456789'],'worker_online':True}
        if path.endswith('?limit=100'):return []
        if path=='/v1/whatsapp/channels':return {'id':channel_id}
        return {'configured':True}
    monkeypatch.setattr(wa,'platform',remote)
    assert client.post('/api/v1/whatsapp-agent/setup',headers=headers,json={'phone_id':'999999'}).status_code==422
    for _ in range(2):
        assert client.post('/api/v1/whatsapp-agent/setup',headers=headers,json={'phone_id':'123456789'}).status_code==200
    assert sum(method=='POST' and path=='/v1/whatsapp/channels' for method,path,_ in calls)==1
    assert client.get('/api/v1/whatsapp-agent',headers=other).json()['bridge_ready'] is False
    user=client.post('/api/v1/users',headers=headers,json={'name':'Operator','email':'waoperator@example.com','password':'operator-password-123','role':'operator'}).json()
    token=client.post('/api/v1/auth/login',json={'organization_id':org_id,'email':user['email'],'password':'operator-password-123'}).json()['token']
    assert client.post('/api/v1/whatsapp-agent/activation',headers={'Authorization':'Bearer '+token},json={'enabled':True}).status_code==403
    assert client.get('/api/v1/whatsapp-agent').status_code==401


def test_actual_platform_client_contract(monkeypatch):
    import asyncio
    import importlib.util
    import os
    from pathlib import Path
    import httpx
    import pytest
    source=os.environ.get('AI_AGENT_PLATFORM_DIR')
    if not source:
        pytest.skip('Set AI_AGENT_PLATFORM_DIR to verify the installed platform client')
    spec=importlib.util.spec_from_file_location('actual_platform_bridge',Path(source)/'src/agent_platform/sahayak_bridge.py')
    bridge=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    org_id,_=org('PlatformContract')
    app_id,channel=str(uuid4()),str(uuid4())
    cfg={'app_id':app_id,'secret':'s'*48}
    monkeypatch.setattr(wa,'settings',lambda org:cfg)
    with Session(engine) as db:
        db.add(WhatsAppAgent(organization_id=org_id,channel_id=channel,app_id=app_id,phone_id='123456789',bridge_ready=True))
        db.commit()
    monkeypatch.setenv('AI_SAHAYAK_BRIDGES',json.dumps({channel:{'app_id':app_id,'clinic_id':org_id,'url':'http://127.0.0.1/integrations/agent-platform/whatsapp','secret':wa.binding_secret(cfg,org_id,channel)}}))
    def transport(request):
        result=client.post(request.url.path,content=request.content,headers=dict(request.headers))
        return httpx.Response(result.status_code,json=result.json())
    async def exercise():
        context={'id':channel,'app_id':app_id}
        kwargs={'transport':httpx.MockTransport(transport)}
        probe=await bridge.request(context,uuid4(),'919000001111','connection check',probe=True,**kwargs)
        assert probe['ready'] is True
        result=await bridge.request(context,uuid4(),'919000001111','START',**kwargs)
        assert result['handledBy']=='agent' and result['replies']
        monkeypatch.setattr(wa,'answer',lambda *args:{'mode':'abstain','answer':'','citations':[]})
        result=await bridge.request(context,uuid4(),'919000001111','unknown information',**kwargs)
        assert result['handledBy']=='human'
    asyncio.run(exercise())


def test_readiness_blocks_activation_but_allows_pause(monkeypatch):
    org_id, headers = org('WhatsAppReadiness')
    app_id, channel = str(uuid4()), str(uuid4())
    monkeypatch.setattr(wa, 'settings', lambda org: {'app_id': app_id})
    remote = {'id': channel, 'name': 'Reach', 'phone_id': '123456789', 'enabled': False,
              'webhook_verified_at': None, 'execution_mode': 'SAHAYAK'}
    worker = False
    mutations = []
    def platform(cfg, method, path, body=None):
        if method == 'POST':
            mutations.append(body)
            return body
        if path.endswith('/setup'):
            return {'app_id': app_id, 'phone_ids': ['123456789'], 'worker_online': worker}
        return [remote]
    monkeypatch.setattr(wa, 'platform', platform)
    with Session(engine) as db:
        db.add(WhatsAppAgent(organization_id=org_id, channel_id=channel, app_id=app_id,
                             phone_id='123456789', bridge_ready=True))
        db.commit()
    status = client.get('/api/v1/whatsapp-agent', headers=headers).json()
    assert status['platform_online'] and not status['can_activate']
    assert 'webhook' in status['notice'] and 'worker' in status['notice']
    assert client.post('/api/v1/whatsapp-agent/activation', headers=headers, json={'enabled': True}).status_code == 409
    assert not mutations
    assert client.post('/api/v1/whatsapp-agent/activation', headers=headers, json={'enabled': False}).status_code == 200
    remote['webhook_verified_at'] = '2026-10-06T00:00:00Z'
    worker = True
    assert client.get('/api/v1/whatsapp-agent', headers=headers).json()['can_activate']
    assert client.post('/api/v1/whatsapp-agent/activation', headers=headers, json={'enabled': True}).status_code == 200
    remote['execution_mode'] = 'RAG'
    assert not client.get('/api/v1/whatsapp-agent', headers=headers).json()['can_activate']
    monkeypatch.setattr(wa, 'settings', lambda org: {'app_id': str(uuid4())})
    assert client.post('/api/v1/whatsapp-agent/activation', headers=headers, json={'enabled': False}).status_code == 409


def test_outage_status_and_unconfigured_workspace(monkeypatch):
    _, headers = org('WhatsAppOffline')
    def missing(org):
        raise wa.HTTPException(503, 'Connect this workspace first.')
    monkeypatch.setattr(wa, 'settings', missing)
    result = client.get('/api/v1/whatsapp-agent', headers=headers).json()
    assert not result['configured'] and not result['can_activate']
    assert result['blockers'] == ['Connect this workspace first.']
    monkeypatch.setattr(wa, 'settings', lambda org: {'app_id': str(uuid4())})
    def offline(*args):
        raise wa.HTTPException(503, 'Platform offline')
    monkeypatch.setattr(wa, 'platform', offline)
    result = client.get('/api/v1/whatsapp-agent', headers=headers).json()
    assert result['configured'] and not result['platform_online'] and not result['can_activate']
    assert result['notice'] == 'Platform offline'
    monkeypatch.setattr(wa, 'platform', lambda *args: {})
    assert not client.get('/api/v1/whatsapp-agent', headers=headers).json()['can_activate']


def test_channels_pagination(monkeypatch):
    first = [{'id': str(uuid4())} for _ in range(100)]
    older = {'id': str(uuid4())}
    calls = []
    def platform(cfg, method, path):
        calls.append(path)
        return [older] if '&before=' in path else first
    monkeypatch.setattr(wa, 'platform', platform)
    assert wa.remote_channels({}) == first + [older]
    assert calls[1].endswith('&before=' + first[-1]['id'])


def test_save_business_number_without_platform(monkeypatch):
    org_id, headers = org('NumberOnly')
    _, other = org('NumberOnlyOther')
    def unavailable(*args):
        raise AssertionError('Saving a number must not call the platform')
    monkeypatch.setattr(wa, 'platform', unavailable)
    response = client.post('/api/v1/whatsapp-agent/details', headers=headers, json={'phone': '+91 (82196) 13387'})
    assert response.status_code == 200
    assert response.json() == {'phone': '+918219613387', 'saved': True}
    from app.models import WhatsAppSetupRequest
    with Session(engine) as db:
        assert db.get(WhatsAppSetupRequest, org_id).phone == '+918219613387'
    def missing(*args):
        raise wa.HTTPException(503, 'Platform not configured')
    monkeypatch.setattr(wa, 'settings', missing)
    result = client.get('/api/v1/whatsapp-agent', headers=headers).json()
    assert result['saved_number'] == '+918219613387' and result['number_editable']
    assert client.get('/api/v1/whatsapp-agent', headers=other).json()['saved_number'] is None
    for phone in ['8219613387', '+012345678', '+91abc123456', '+1234567890123456']:
        assert client.post('/api/v1/whatsapp-agent/details', headers=headers, json={'phone': phone}).status_code == 422
    assert client.post('/api/v1/whatsapp-agent/details', json={'phone': '+918219613387'}).status_code == 401
    operator = client.post('/api/v1/users', headers=headers, json={'name': 'Number Operator', 'email': 'numberoperator@example.com', 'password': 'operator-password-123', 'role': 'operator'}).json()
    token = client.post('/api/v1/auth/login', json={'organization_id': org_id, 'email': operator['email'], 'password': 'operator-password-123'}).json()['token']
    assert client.post('/api/v1/whatsapp-agent/details', headers={'Authorization': 'Bearer '+token}, json={'phone': '+918219613387'}).status_code == 403
    with Session(engine) as db:
        db.add(WhatsAppAgent(organization_id=org_id, channel_id=str(uuid4()), app_id=str(uuid4()), phone_id='123456789', bridge_ready=True))
        db.commit()
    assert client.post('/api/v1/whatsapp-agent/details', headers=headers, json={'phone': '+919876543210'}).status_code == 409
    assert client.post('/api/v1/whatsapp-agent/details', headers=headers, json={'phone': '+918219613387'}).status_code == 200


def test_durable_authorization_scoped_and_unknown_not_retried(monkeypatch):
    from app.models import WhatsAppAuthorization, WhatsAppSetupRequest
    from fastapi import HTTPException
    organization, headers = org('AuthorizationFlow')
    other, other_headers = org('AuthorizationOther')
    app_id, remote_session = str(uuid4()), str(uuid4())
    monkeypatch.setattr(wa, 'settings', lambda organization: {'app_id': app_id})
    calls = []
    def remote(cfg, method, path, body=None):
        calls.append((method, path))
        if path.endswith('/config'): return {'available': True}
        if path.endswith('/onboarding'): return {'id': remote_session, 'connect_token': 'x'*30}
        if path.endswith('/resume'): return {'id': remote_session, 'connect_token': 'x'*30}
        if path.endswith('/complete'): return {'state': 'OWNERSHIP_CHECK_REQUIRED'}
        return {'state': 'OWNERSHIP_CHECK_REQUIRED'}
    monkeypatch.setattr(wa, 'platform', remote)
    path = '/api/v1/whatsapp-agent/authorization'
    assert client.post(path, headers=headers).status_code == 409
    with Session(engine) as db:
        db.add(WhatsAppSetupRequest(organization_id=organization, phone='+919000000001'))
        db.commit()
    assert client.post(path, headers=headers).headers['cache-control'] == 'no-store'
    assert client.post(path, headers=headers).status_code == 200
    assert sum(m == 'POST' and p.endswith('/onboarding') for m,p in calls) == 1
    assert client.get(path, headers=other_headers).json()['state'] == 'NOT_STARTED'
    assert client.post(path+'/complete', headers=other_headers, json={'plugin_id':str(uuid4())}).status_code == 409
    assert client.post(path+'/complete', headers=headers, json={'plugin_id':str(uuid4())}).status_code == 200
    assert client.post(path+'/prepare', headers=headers).status_code == 409
    assert client.post(path, headers=headers).status_code == 409
    with Session(engine) as db:
        db.add(WhatsAppSetupRequest(organization_id=other, phone='+919000000002'))
        db.commit()
    def failing(cfg, method, path, body=None):
        if path.endswith('/config'): return {'available':True}
        raise HTTPException(503, 'timeout')
    monkeypatch.setattr(wa, 'platform', failing)
    assert client.post(path, headers=other_headers).status_code == 503
    assert client.post(path, headers=other_headers).status_code == 409
    with Session(engine) as db:
        assert db.get(WhatsAppAuthorization, other).state == 'CHECK_REQUIRED'
