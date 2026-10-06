"""Cross-workspace management with separately granted access and token scope."""
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import (AuditLog, Contact, Conversation, KnowledgeDocument, Organization,
                     PlatformOwner, PlatformWorkspaceProfile, User, WhatsAppAgent, WhatsAppSetupRequest)
from .security import SECRET, bearer, passwords

router = APIRouter(prefix='/api/v1/platform', tags=['Platform owner'])


class Login(BaseModel):
    email: EmailStr
    password: str = Field(max_length=1000)


def current_owner(credentials: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(get_db)):
    try:
        claims = jwt.decode(credentials.credentials, SECRET, algorithms=['HS256'])
        if claims.get('kind') != 'platform':
            raise HTTPException(403, 'Platform owner access required.')
        user = db.scalar(select(User).join(PlatformOwner, PlatformOwner.user_id == User.id).where(
            User.id == claims['sub'], User.status == 'active'))
        if user:
            return user
    except (jwt.PyJWTError, KeyError):
        pass
    raise HTTPException(401, 'Platform session expired or access revoked.')


@router.post('/auth/login')
def login(body: Login, response: Response, db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    candidates = db.scalars(select(User).join(PlatformOwner, PlatformOwner.user_id == User.id).where(
        User.email == body.email.lower(), User.status == 'active')).all()
    user = next((u for u in candidates if passwords.verify(body.password, u.password_hash)), None)
    if not user:
        raise HTTPException(401, 'Invalid credentials or no platform access.')
    token = jwt.encode({'sub': user.id, 'kind': 'platform', 'exp': datetime.now(timezone.utc) + timedelta(hours=2)}, SECRET, algorithm='HS256')
    return {'token': token, 'name': user.name, 'email': user.email}


@router.get('/auth/me')
def me(user: User = Depends(current_owner)):
    return {'name': user.name, 'email': user.email}


def count(db, model, org=None):
    query = select(func.count()).select_from(model)
    if org:
        query = query.where(model.organization_id == org)
    return db.scalar(query)


def workspace(db, org):
    profile = db.get(PlatformWorkspaceProfile, org.id)
    request = db.get(WhatsAppSetupRequest, org.id)
    agent = db.get(WhatsAppAgent, org.id)
    owners = db.scalars(select(User).where(User.organization_id == org.id, User.role == 'owner')).all()
    return {'id': org.id, 'name': org.name, 'type': org.type, 'created_at': org.created_at,
            'stage': profile.stage if profile else 'new',
            'owners': [{'name': u.name, 'email': u.email} for u in owners],
            'users': count(db, User, org.id), 'contacts': count(db, Contact, org.id),
            'conversations': count(db, Conversation, org.id),
            'documents': count(db, KnowledgeDocument, org.id),
            'whatsapp_number': request.phone if request else None,
            'whatsapp_status': 'prepared' if agent and agent.bridge_ready else 'requested' if request else 'not_requested'}


@router.get('/workspaces')
def workspaces(q: str = Query('', max_length=200), stage: Literal['all', 'new', 'onboarding', 'live', 'needs_attention'] = 'all',
               whatsapp: Literal['all', 'pending'] = 'all', offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
               user: User = Depends(current_owner), db: Session = Depends(get_db)):
    query = select(Organization).outerjoin(PlatformWorkspaceProfile, PlatformWorkspaceProfile.organization_id == Organization.id)
    if q.strip():
        term = '%' + q.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        owner_match = select(User.organization_id).where(User.role == 'owner', User.email.ilike(term, escape='\\'))
        query = query.where(or_(Organization.name.ilike(term, escape='\\'), Organization.id == q.strip(), Organization.id.in_(owner_match)))
    if whatsapp == 'pending':
        pending = select(WhatsAppSetupRequest.organization_id).outerjoin(WhatsAppAgent, WhatsAppAgent.organization_id == WhatsAppSetupRequest.organization_id).where(or_(WhatsAppAgent.organization_id.is_(None), WhatsAppAgent.bridge_ready.is_(False)))
        query = query.where(Organization.id.in_(pending))
    if stage != 'all':
        query = query.where(func.coalesce(PlatformWorkspaceProfile.stage, 'new') == stage)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Organization.created_at.desc(), Organization.id).offset(offset).limit(limit)).all()
    return jsonable_encoder({'items': [workspace(db, row) for row in rows], 'total': total})


@router.get('/summary')
def summary(user: User = Depends(current_owner), db: Session = Depends(get_db)):
    requested = db.scalar(select(func.count()).select_from(WhatsAppSetupRequest).outerjoin(
        WhatsAppAgent, WhatsAppAgent.organization_id == WhatsAppSetupRequest.organization_id).where(
        or_(WhatsAppAgent.organization_id.is_(None), WhatsAppAgent.bridge_ready.is_(False))))
    return {'workspaces': count(db, Organization), 'users': count(db, User),
            'contacts': count(db, Contact), 'whatsapp_pending': requested}


def org_or_404(db, org_id):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, 'Workspace not found.')
    return org


@router.get('/workspaces/{org_id}')
def details(org_id: str, user: User = Depends(current_owner), db: Session = Depends(get_db)):
    org = org_or_404(db, org_id)
    profile = db.get(PlatformWorkspaceProfile, org.id)
    events = db.scalars(select(AuditLog).where(AuditLog.organization_id == org.id,
        AuditLog.action == 'platform.workspace_updated').order_by(AuditLog.created_at.desc()).limit(15)).all()
    return jsonable_encoder({**workspace(db, org), 'notes': profile.notes if profile else '',
        'history': [{'at': e.created_at, 'action': e.action, 'actor_id': e.actor_id} for e in events]})


class Update(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    stage: Literal['new', 'onboarding', 'live', 'needs_attention']
    notes: str = Field(default='', max_length=5000)


@router.patch('/workspaces/{org_id}')
def update(org_id: str, body: Update, user: User = Depends(current_owner), db: Session = Depends(get_db)):
    org = org_or_404(db, org_id)
    if len(body.name.strip()) < 2:
        raise HTTPException(422, 'Enter a workspace name of at least two characters.')
    profile = db.get(PlatformWorkspaceProfile, org.id)
    if not profile:
        profile = PlatformWorkspaceProfile(organization_id=org.id)
        db.add(profile)
    before = {'name': org.name, 'stage': profile.stage or 'new'}
    org.name, profile.stage, profile.notes = body.name.strip(), body.stage, body.notes
    db.add(AuditLog(organization_id=org.id, actor_id=user.id, action='platform.workspace_updated',
                   resource_type='organization', resource_id=org.id, before=before,
                   after={'name': org.name, 'stage': body.stage, 'notes_updated': True}))
    db.commit()
    return details(org_id, user, db)
