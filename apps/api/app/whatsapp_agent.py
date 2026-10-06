"""Reach workflows behind AI-Agent-Platform's existing signed application bridge."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid5

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import get_db
from .knowledge import answer
from .models import AgentRun, AuditLog, Consent, Contact, Conversation, Interaction, Message, Organization, User, WhatsAppAgent, WhatsAppSetupRequest, KnowledgeDocument, WhatsAppAuthorization, now
from .phase5 import auto_escalate, create_escalation, record_usage
from .security import current_user, require

router = APIRouter()
DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / '.local' / 'agent-platform.json'


def settings(org: str) -> dict:
    try:
        config = json.loads(Path((os.getenv('REACH_AGENT_PLATFORM_FILE') or str(DEFAULT_CONFIG))).read_text())
        value = config['organizations'][org]
        url = urlsplit(config['base_url'])
        if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'127.0.0.1', 'localhost'}):
            raise ValueError()
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError()
        if len(value['secret']) < 32 or not value['key'] or not value['external_user_id']:
            raise ValueError()
        bridge = urlsplit(config['bridge_url'])
        if bridge.scheme != 'https' and not (bridge.scheme == 'http' and bridge.hostname in {'localhost', '127.0.0.1'}):
            raise ValueError()
        if not bridge.hostname or bridge.username or bridge.password or bridge.query or bridge.fragment or bridge.path != '/integrations/agent-platform/whatsapp':
            raise ValueError()
        UUID(value['app_id'])
        if any(k != org and v.get('app_id') == value['app_id'] for k,v in config['organizations'].items()):
            raise ValueError('Each business requires a separate platform app')
        return {**value, 'base_url': config['base_url'].rstrip('/'), 'bridge_url': config['bridge_url']}
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, 'Your setup team needs to connect this workspace to the agent platform.') from None


def platform(cfg: dict, method: str, path: str, body=None):
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.request(method, cfg['base_url'] + path, json=body, headers={
                'Authorization': 'Bearer ' + cfg['key'], 'X-External-User-ID': cfg['external_user_id']})
        if response.status_code >= 300:
            raise HTTPException(502, f'Agent platform rejected setup (HTTP {response.status_code}). Check number ownership, callback origin and channel readiness in the platform.')
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, 'Agent service unavailable. Check saved status before retrying setup.') from None


def binding_secret(cfg, org, channel):
    return hmac.new(cfg['secret'].encode(), f"{org}:{cfg['app_id']}:{channel}".encode(), hashlib.sha256).hexdigest()


def remote_channels(cfg):
    """Read all pages so an older Reach channel is not mistaken for a missing one."""
    rows, before = [], None
    while True:
        path = '/v1/whatsapp/channels?limit=100' + (f'&before={before}' if before else '')
        page = platform(cfg, 'GET', path)
        if not isinstance(page, list):
            raise HTTPException(502, 'Unexpected channel response from the platform.')
        rows.extend(page)
        if len(page) < 100:
            return rows
        cursor = str(UUID(page[-1]['id']))
        if cursor == before:
            raise HTTPException(502, 'Platform channel pagination did not advance.')
        before = cursor


@router.get('/api/v1/whatsapp-agent')
def status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(WhatsAppAgent, user.organization_id)
    saved = db.get(WhatsAppSetupRequest, user.organization_id)
    result = {'saved_number': saved.phone if saved else None,
              'number_editable': row is None,
              'configured': False, 'platform_online': False, 'worker_online': False,
              'phone_ids': [], 'channel': None, 'notice': '', 'bridge_ready': False,
              'can_activate': False, 'blockers': [],
              'organization_id': user.organization_id,
              'intended_number': saved.phone if saved else os.getenv('REACH_WHATSAPP_BUSINESS_NUMBER', ''),
              'approved_documents': db.scalar(select(func.count()).select_from(KnowledgeDocument).where(
                  KnowledgeDocument.organization_id == user.organization_id, KnowledgeDocument.status == 'approved'))}
    try:
        cfg = settings(user.organization_id)
        result['configured'] = True
        if row and row.app_id != cfg['app_id']:
            raise HTTPException(409, 'The saved channel belongs to another platform application. Restore its original Reach credential.')
        setup = platform(cfg, 'GET', '/v1/whatsapp/setup')
        if setup.get('app_id', cfg['app_id']) != cfg['app_id']:
            raise HTTPException(409, 'Platform credential identity does not match the Reach connection.')
        channels = remote_channels(cfg)
        result.update(platform_online=True, phone_ids=setup['phone_ids'], worker_online=setup['worker_online'])
        if row:
            remote = next((c for c in channels if c['id'] == row.channel_id), None)
            if remote and remote.get('execution_mode') == 'SAHAYAK' and remote['phone_id'] == row.phone_id:
                result['channel'] = {k: remote.get(k) for k in ('id', 'name', 'phone_id', 'enabled', 'webhook_verified_at')}
                result['bridge_ready'] = row.bridge_ready
            else:
                result['blockers'].append('The saved Reach channel is missing or its workflow changed. Review it in the platform.')
        if not result['phone_ids']:
            result['blockers'].append('Authorize the business number for the Reach application in the platform.')
        if not result['bridge_ready']:
            result['blockers'].append('Prepare the CRM connection after selecting the correct Meta phone-number ID.')
        if not result['channel'] or not result['channel']['webhook_verified_at']:
            result['blockers'].append('Finish Meta webhook verification in the platform.')
        if result['channel'] and result['channel']['phone_id'] not in result['phone_ids']:
            result['blockers'].append('The channel number is no longer authorized for this connection.')
        if not result['worker_online']:
            result['blockers'].append('Start the WhatsApp message worker in the platform.')
    except HTTPException as exc:
        result['blockers'].append(exc.detail)
    except (KeyError, TypeError, ValueError):
        result['blockers'].append('The platform returned an incompatible setup response. Check the integration contract.')
    result['can_activate'] = not result['blockers']
    result['notice'] = ' '.join(result['blockers']) if result['blockers'] else 'Connection checks passed. Activation will also test the signed CRM callback.'
    return result


class NumberDetails(BaseModel):
    phone: str = Field(min_length=8, max_length=40)

    @field_validator('phone')
    @classmethod
    def normalize_phone(cls, value):
        phone = re.sub(r'[\s()-]', '', value)
        if not re.fullmatch(r'\+[1-9]\d{7,14}', phone):
            raise ValueError('Include the country code, for example +918219613387.')
        return phone


@router.post('/api/v1/whatsapp-agent/details')
def save_number(body: NumberDetails, user: User = Depends(require('owner', 'admin')), db: Session = Depends(get_db)):
    saved = db.get(WhatsAppSetupRequest, user.organization_id)
    channel = db.get(WhatsAppAgent, user.organization_id)
    if channel and (not saved or saved.phone != body.phone):
        raise HTTPException(409, 'A channel is already prepared. Ask your setup team to review the connection before changing its number.')
    if not saved:
        saved = WhatsAppSetupRequest(organization_id=user.organization_id, phone=body.phone)
        db.add(saved)
    else:
        saved.phone = body.phone
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id,
                    action='whatsapp.number_saved', resource_type='whatsapp_setup', resource_id=user.organization_id))
    db.commit()
    return {'phone': saved.phone, 'saved': True}


class Setup(BaseModel):
    phone_id: str = Field(pattern=r'^\d{5,30}$')
    name: str = Field(default='Reach WhatsApp agent', min_length=1, max_length=80)


@router.post('/api/v1/whatsapp-agent/setup')
def setup_agent(body: Setup, user: User = Depends(require('owner', 'admin')), db: Session = Depends(get_db)):
    cfg = settings(user.organization_id)
    remote_setup = platform(cfg, 'GET', '/v1/whatsapp/setup')
    if body.phone_id not in remote_setup['phone_ids']:
        raise HTTPException(422, 'Select a verified number belonging to this Reach workspace.')
    row = db.get(WhatsAppAgent, user.organization_id)
    if row and row.app_id != cfg['app_id']:
        raise HTTPException(409, 'Restore the original application credential before preparing this channel.')
    if row and row.phone_id != body.phone_id:
        raise HTTPException(409, 'This workspace already has a channel; pause and review it before changing numbers.')
    if not row:
        channels = remote_channels(cfg)
        channel = next((c for c in channels if c['phone_id'] == body.phone_id), None)
        if channel and (channel['execution_mode'] != 'SAHAYAK' or channel['enabled']):
            raise HTTPException(409, 'The number already has another active workflow. Review it in the platform.')
        if not channel:
            channel = platform(cfg, 'POST', '/v1/whatsapp/channels', {'phone_id': body.phone_id, 'name': body.name, 'documents': [], 'execution_mode': 'SAHAYAK'})
        row = WhatsAppAgent(organization_id=user.organization_id, channel_id=channel['id'], phone_id=body.phone_id, app_id=cfg['app_id'])
        db.add(row)
        db.commit()
    platform(cfg, 'PUT', f'/v1/whatsapp/channels/{row.channel_id}/sahayak', {
        'clinic_id': user.organization_id, 'url': cfg['bridge_url'], 'secret': binding_secret(cfg, user.organization_id, row.channel_id)})
    row.bridge_ready = True
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id, action='whatsapp.agent_configured', resource_type='whatsapp_agent', resource_id=row.channel_id))
    db.commit()
    return {'configured': True}


class Activation(BaseModel):
    enabled: bool


@router.post('/api/v1/whatsapp-agent/activation')
def activation(body: Activation, user: User = Depends(require('owner', 'admin')), db: Session = Depends(get_db)):
    row = db.get(WhatsAppAgent, user.organization_id)
    if not row or not row.bridge_ready:
        raise HTTPException(409, 'Prepare the WhatsApp connection first.')
    cfg = settings(user.organization_id)
    if row.app_id != cfg['app_id']:
        raise HTTPException(409, 'Restore the original application credential before changing this channel.')
    if body.enabled:
        readiness = status(user, db)
        if not readiness['can_activate']:
            raise HTTPException(409, readiness['notice'])
    result = platform(cfg, 'POST', f'/v1/whatsapp/channels/{row.channel_id}/activation', body.model_dump())
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id, action='whatsapp.agent_activation', resource_type='whatsapp_agent', resource_id=row.channel_id, after={'enabled': body.enabled}))
    db.commit()
    return result


class Preview(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


@router.post('/api/v1/whatsapp-agent/preview')
def preview(body: Preview, user: User = Depends(current_user), db: Session = Depends(get_db)):
    result = answer(db, user.organization_id, body.question)
    return {**result, 'sent_to_whatsapp': False}


class Turn(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    event_id: UUID
    channel_id: UUID
    app_id: UUID
    clinic_id: UUID  # Compatibility field: maps to a Reach organization, not a Sahayak clinic.
    sender: str = Field(alias='from', pattern=r'^\+?[1-9]\d{7,14}$')
    text: str = Field(min_length=1, max_length=4000)
    probe: bool = False
    voice_available: bool = False
    voice_resume: bool = False
    voice_turn: bool = False


@router.post('/integrations/agent-platform/whatsapp')
async def receive(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    if len(raw) > 20000:
        raise HTTPException(413, 'Message too large')
    try:
        body = Turn.model_validate_json(raw)
    except ValueError:
        raise HTTPException(400, 'Invalid message') from None
    org, channel = str(body.clinic_id), str(body.channel_id)
    cfg = settings(org)
    row = db.get(WhatsAppAgent, org)
    stamp = request.headers.get('x-agent-timestamp', '')
    signature = request.headers.get('x-agent-signature', '')
    if not row or row.channel_id != channel or row.app_id != str(body.app_id) or cfg['app_id'] != str(body.app_id):
        raise HTTPException(403, 'Invalid bridge binding')
    if len(stamp) != 10 or not stamp.isdigit() or abs(time.time() - int(stamp)) > 300:
        raise HTTPException(403, 'Expired bridge signature')
    expected = hmac.new(binding_secret(cfg, org, channel).encode(), stamp.encode() + b'.' + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(403, 'Invalid bridge signature')
    if body.probe:
        return {'ready': bool(db.get(Organization, org)), 'clinic_id': org}
    if not row.bridge_ready:
        raise HTTPException(409, 'CRM connection preparation is incomplete')
    if body.voice_turn or body.voice_resume:
        raise HTTPException(409, 'Voice workflow is not enabled for this connection')
    digest = hashlib.sha256(raw).hexdigest()
    key = f'wa:{channel}:{body.event_id}'
    prior = db.scalar(select(AgentRun).where(AgentRun.organization_id == org, AgentRun.request_key == key))
    if prior:
        if prior.input['digest'] != digest:
            raise HTTPException(409, 'Event content changed')
        if prior.status == 'completed':
            return prior.output['response']
        raise HTTPException(409, 'Event outcome needs review before retrying')
    run = AgentRun(organization_id=org, agent_type='whatsapp', trigger_type='platform', request_key=key, input={'digest': digest}, status='processing')
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Event is already being processed') from None
    try:
        phone = '+' + body.sender.lstrip('+')
        contacts = db.scalars(select(Contact).where(Contact.organization_id == org, Contact.phone == phone).limit(2)).all()
        if len(contacts) > 1:
            raise HTTPException(409, 'Duplicate contact numbers need review')
        contact = contacts[0] if contacts else Contact(id=str(uuid5(UUID(org), phone)), organization_id=org, first_name='WhatsApp contact', phone=phone, source='agent_platform')
        if not contacts:
            db.add(contact)
            db.flush()
        conversation = db.scalar(select(Conversation).where(Conversation.organization_id == org, Conversation.contact_id == contact.id, Conversation.channel == 'whatsapp', Conversation.status.in_(['active', 'waiting'])).order_by(Conversation.started_at.desc()))
        if not conversation:
            conversation = Conversation(organization_id=org, contact_id=contact.id, channel='whatsapp')
            db.add(conversation)
            db.flush()
        message = Message(organization_id=org, conversation_id=conversation.id, sender_type='contact', content=body.text, direction='inbound', status='received')
        db.add(message)
        db.flush()
        db.add(Interaction(organization_id=org, contact_id=contact.id, conversation_id=conversation.id, channel='whatsapp', type='message', direction='inbound'))
        text = body.text.strip().lower()
        replies, citations = [], []
        consent = db.scalar(select(Consent).where(Consent.organization_id == org, Consent.contact_id == contact.id, Consent.channel == 'whatsapp').order_by(Consent.captured_at.desc(), Consent.id.desc()).limit(1))
        if text in {'stop', 'unsubscribe'}:
            db.add(Consent(organization_id=org, contact_id=contact.id, channel='whatsapp', status='opt_out', source='WhatsApp STOP'))
        elif text == 'start':
            db.add(Consent(organization_id=org, contact_id=contact.id, channel='whatsapp', status='opt_in', source='WhatsApp START'))
            if conversation.status != 'waiting':
                replies = ['WhatsApp replies are enabled. How can we help? Reply STOP to opt out.']
        elif conversation.status == 'waiting':
            pass
        elif auto_escalate(db, conversation, message):
            replies = ['Your message has been passed to our team for a human response.'] if consent and consent.status == 'opt_in' else []
        elif not consent:
            replies = ['To receive replies from this organization on WhatsApp, reply START. Reply STOP to opt out.']
        elif consent.status == 'opt_in':
            result = answer(db, org, body.text)
            citations = result.get('citations', [])
            if result.get('answer') and result.get('mode') != 'abstain':
                replies = [result['answer'][:4000]]
            else:
                create_escalation(db, conversation, message, 'knowledge_answer_unavailable', 'normal')
                replies = ['I could not find a supported answer. Our team will review your question.']
        for reply in replies:
            db.add(Message(organization_id=org, conversation_id=conversation.id, sender_type='agent', content=reply, direction='outbound', status='handed_to_platform'))
        response = {'event_id': str(body.event_id), 'replies': replies, 'handledBy': 'human' if conversation.status == 'waiting' else 'agent', 'redFlag': False}
        run.status = 'completed'
        run.completed_at = now()
        run.output = {'response': response, 'citations': citations, 'conversation_id': conversation.id}
        record_usage(db, org, f'wa:{body.event_id}', 'whatsapp_agent_turn')
        db.add(AuditLog(organization_id=org, action='whatsapp.agent_turn', resource_type='agent_run', resource_id=run.id))
        db.commit()
        return response
    except Exception:
        db.rollback()
        run = db.get(AgentRun, run.id)
        run.status = 'unknown'
        db.commit()
        raise HTTPException(503, 'Message outcome needs human review; no automatic retry') from None


# Both apps use /v1/whatsapp/chakra; Reach never handles a Meta token or app secret.
@router.get('/api/v1/whatsapp-agent/authorization')
def authorization_status(user: User = Depends(require('owner', 'admin')), db: Session = Depends(get_db)):
    cfg = settings(user.organization_id)
    row = db.get(WhatsAppAuthorization, user.organization_id)
    if row and row.app_id != cfg['app_id']:
        raise HTTPException(409, 'Restore the original business credential before continuing.')
    config = platform(cfg, 'GET', '/v1/whatsapp/chakra/config')
    if row and row.session_id:
        remote = platform(cfg, 'GET', '/v1/whatsapp/chakra/onboarding/'+row.session_id)
        row.state = remote['state']
        db.commit()
    return {'available': config.get('available', False), 'state': row.state if row else 'NOT_STARTED'}


@router.post('/api/v1/whatsapp-agent/authorization')
def authorization_start(response: Response, user: User = Depends(require('owner', 'admin')), db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    cfg = settings(user.organization_id)
    if not db.get(WhatsAppSetupRequest, user.organization_id):
        raise HTTPException(409, 'Save the business number first.')
    row = db.get(WhatsAppAuthorization, user.organization_id)
    if row:
        if row.app_id != cfg['app_id'] or not row.session_id or row.state != 'CONNECTING':
            raise HTTPException(409, 'Check saved authorization; do not start a second signup.')
        return platform(cfg, 'POST', '/v1/whatsapp/chakra/onboarding/'+row.session_id+'/resume', {})
    if not platform(cfg, 'GET', '/v1/whatsapp/chakra/config').get('available'):
        raise HTTPException(503, 'Your setup team needs to enable Chakra partner onboarding.')
    row = WhatsAppAuthorization(organization_id=user.organization_id, app_id=cfg['app_id'], state='STARTING')
    db.add(row)
    try:
        db.commit()  # Unique organization row is the dispatch fence.
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Authorization already started. Refresh its status.') from None
    try:
        session = platform(cfg, 'POST', '/v1/whatsapp/chakra/onboarding', {})
        row.session_id = str(UUID(session['id']))
        row.state = 'CONNECTING'
        db.commit()
        return {'id': row.session_id, 'connect_token': session['connect_token']}
    except Exception:
        db.rollback()
        row = db.get(WhatsAppAuthorization, user.organization_id)
        row.state = 'CHECK_REQUIRED'
        db.commit()
        raise HTTPException(503, 'Authorization outcome needs setup-team review; do not restart signup.') from None


class AuthorizationComplete(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plugin_id: UUID


@router.post('/api/v1/whatsapp-agent/authorization/complete')
def authorization_complete(body: AuthorizationComplete, user: User = Depends(require('owner', 'admin')), db: Session = Depends(get_db)):
    cfg = settings(user.organization_id)
    row = db.get(WhatsAppAuthorization, user.organization_id)
    if not row or not row.session_id or row.app_id != cfg['app_id'] or row.state != 'CONNECTING':
        raise HTTPException(409, 'Refresh the saved authorization state.')
    result = platform(cfg, 'POST', '/v1/whatsapp/chakra/onboarding/'+row.session_id+'/complete', body.model_dump(mode='json'))
    row.state = result['state']
    db.commit()
    return {'state': row.state}


@router.post('/api/v1/whatsapp-agent/authorization/prepare')
def authorization_prepare(user: User = Depends(require('owner', 'admin')), db: Session = Depends(get_db)):
    cfg = settings(user.organization_id)
    row = db.get(WhatsAppAuthorization, user.organization_id)
    if not row or not row.session_id or row.app_id != cfg['app_id']:
        raise HTTPException(409, 'Complete authorization first.')
    base = '/v1/whatsapp/chakra/onboarding/'+row.session_id
    remote = platform(cfg, 'GET', base)
    if remote['state'] != 'CONNECTED':
        raise HTTPException(409, 'The setup team must verify number ownership first.')
    channel = platform(cfg, 'POST', base+'/channel', {'name': 'Reach WhatsApp agent', 'execution_mode': 'SAHAYAK', 'documents': []})
    setup_agent(Setup(phone_id=channel['phone_id']), user, db)
    hook = platform(cfg, 'POST', '/v1/whatsapp/chakra/channels/'+channel['id']+'/webhook', {})
    row.state = 'CONNECTED'
    db.commit()
    return {'prepared': True, 'webhook_verified': hook.get('verified', False), 'enabled': False}
