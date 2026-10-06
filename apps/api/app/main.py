from __future__ import annotations

import csv
import hashlib
import hmac
import io
import os
import time
import uuid
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import get_db
from .document_parser import MAX_UPLOAD_BYTES, extract_text
from .gateway import gateway
from .knowledge import answer as knowledge_answer, chunk_text, search as knowledge_search
from .models import AgentRun, AuditLog, Consent, Contact, Conversation, Interaction, KnowledgeBase, KnowledgeChunk, KnowledgeDocument, Message, Organization, OutboundJob, User, now
from .schemas import ConsentCreate, ContactCreate, ContactUpdate, ConversationCreate, DeliveryEvent, KnowledgeAsk, KnowledgeBaseCreate, KnowledgeDocumentCreate, KnowledgeDocumentUpdate, KnowledgeQuestion, Login, MessageCreate, OrgCreate, SuggestReply, UserCreate, WebhookMessage
from .security import current_user, passwords, require, token_for, web_chat_claims, web_token_for
from .phase4 import router as phase4_router
from .phase5 import auto_escalate, record_usage, router as phase5_router

app = FastAPI(title="Reach CRM API", version="0.1.0")
app.include_router(phase4_router)
app.include_router(phase5_router)
from .whatsapp_agent import router as whatsapp_agent_router
app.include_router(whatsapp_agent_router)
from .platform_admin import router as platform_admin_router
app.include_router(platform_admin_router)
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("WEB_ORIGIN", "http://localhost:3000")], allow_methods=["*"], allow_headers=["*"])

def obj(row):
    return jsonable_encoder({property.columns[0].name: getattr(row, property.key) for property in row.__mapper__.column_attrs if property.key != "password_hash"})

def audit(db: Session, user: User | None, org: str, action: str, kind: str, resource: str, before=None, after=None):
    db.add(AuditLog(organization_id=org, actor_id=user.id if user else None, action=action, resource_type=kind, resource_id=resource, before=before, after=after))

def contact_or_404(db: Session, org: str, contact_id: str) -> Contact:
    item = db.scalar(select(Contact).where(Contact.id == contact_id, Contact.organization_id == org))
    if not item:
        raise HTTPException(404, "Contact not found")
    return item

def conversation_or_404(db: Session, org: str, conversation_id: str) -> Conversation:
    item = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.organization_id == org))
    if not item:
        raise HTTPException(404, "Conversation not found")
    return item

def consent_allowed(db: Session, org: str, contact_id: str, channel: str) -> bool:
    latest = db.scalar(select(Consent).where(Consent.organization_id == org, Consent.contact_id == contact_id, Consent.channel == channel).order_by(Consent.captured_at.desc(), Consent.id.desc()).limit(1))
    return bool(latest and latest.status == "opt_in")

def knowledge_doc_or_404(db: Session, org: str, document_id: str) -> KnowledgeDocument:
    document = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id, KnowledgeDocument.organization_id == org))
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    return document

def knowledge_doc_view(document: KnowledgeDocument) -> dict:
    return {key: value for key, value in obj(document).items() if key != "content"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/api/v1/organizations", status_code=201)
def create_organization(body: OrgCreate, db: Session = Depends(get_db)):
    if body.type not in {"business", "nonprofit", "community", "public_organization", "other"}:
        raise HTTPException(422, "Invalid organization type")
    org = Organization(name=body.name, type=body.type)
    db.add(org)
    db.flush()
    user = User(organization_id=org.id, name=body.owner_name, email=body.owner_email.lower(), password_hash=passwords.hash(body.password), role="owner")
    db.add(user)
    db.flush()
    audit(db, user, org.id, "organization.created", "organization", org.id)
    db.commit()
    return {"organization": obj(org), "token": token_for(user), "user": obj(user)}

@app.post("/api/v1/auth/login")
def login(body: Login, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.organization_id == body.organization_id, User.email == body.email.lower(), User.status == "active"))
    if not user or not passwords.verify(body.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    return {"token": token_for(user), "user": obj(user)}

@app.get("/api/v1/auth/me")
def me(user: User = Depends(current_user)):
    return obj(user)

@app.post("/api/v1/users", status_code=201)
def create_user(body: UserCreate, user: User = Depends(require("owner", "admin")), db: Session = Depends(get_db)):
    if body.role not in {"admin", "manager", "operator", "analyst", "read_only"}:
        raise HTTPException(422, "Invalid role")
    if db.scalar(select(User.id).where(User.organization_id == user.organization_id, User.email == body.email.lower())):
        raise HTTPException(409, "User already exists")
    created = User(organization_id=user.organization_id, name=body.name, email=body.email.lower(), password_hash=passwords.hash(body.password), role=body.role)
    db.add(created)
    db.flush()
    audit(db, user, user.organization_id, "user.created", "user", created.id)
    db.commit()
    return obj(created)

@app.get("/api/v1/contacts")
def list_contacts(q: str = "", limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Contact).where(Contact.organization_id == user.organization_id)
    if q:
        term = f"%{q.strip()}%"
        query = query.where(or_(Contact.first_name.ilike(term), Contact.last_name.ilike(term), Contact.phone.ilike(term), Contact.email.ilike(term)))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Contact.created_at.desc()).offset(offset).limit(limit)).all()
    return {"items": [obj(row) for row in rows], "total": total, "limit": limit, "offset": offset}

@app.post("/api/v1/contacts", status_code=201)
def create_contact(body: ContactCreate, user: User = Depends(require("owner", "admin", "manager", "operator")), db: Session = Depends(get_db)):
    item = Contact(organization_id=user.organization_id, **body.model_dump())
    db.add(item)
    db.flush()
    audit(db, user, user.organization_id, "contact.created", "contact", item.id)
    db.commit()
    return obj(item)

@app.post("/api/v1/contacts/import")
async def import_contacts(file: UploadFile = File(...), user: User = Depends(require("owner", "admin", "manager")), db: Session = Depends(get_db)):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(422, "CSV file required")
    data = await file.read(12_000_001)
    if len(data) > 12_000_000:
        raise HTTPException(413, "File too large; split imports into batches")
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        if not reader.fieldnames or "first_name" not in reader.fieldnames:
            raise ValueError("first_name column required")
        count = 0
        for row in reader:
            body = ContactCreate.model_validate({k: row[k] for k in ContactCreate.model_fields if k in row and row[k]})
            db.add(Contact(organization_id=user.organization_id, **body.model_dump()))
            count += 1
            if count > 10000:
                raise ValueError("Maximum 10,000 rows per import")
        audit(db, user, user.organization_id, "contacts.imported", "contact", user.organization_id, after={"count": count})
        db.commit()
        return {"imported": count}
    except (UnicodeDecodeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc

@app.get("/api/v1/contacts/{contact_id}")
def get_contact(contact_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = contact_or_404(db, user.organization_id, contact_id)
    consents = db.scalars(select(Consent).where(Consent.organization_id == user.organization_id, Consent.contact_id == item.id).order_by(Consent.captured_at.desc())).all()
    interactions = db.scalars(select(Interaction).where(Interaction.organization_id == user.organization_id, Interaction.contact_id == item.id).order_by(Interaction.timestamp.desc()).limit(100)).all()
    conversations = db.scalars(select(Conversation).where(Conversation.organization_id == user.organization_id, Conversation.contact_id == item.id).order_by(Conversation.started_at.desc()).limit(50)).all()
    return {"contact": obj(item), "consents": [obj(c) for c in consents], "interactions": [obj(i) for i in interactions], "conversations": [obj(c) for c in conversations]}

@app.patch("/api/v1/contacts/{contact_id}")
def update_contact(contact_id: str, body: ContactUpdate, user: User = Depends(require("owner", "admin", "manager", "operator")), db: Session = Depends(get_db)):
    item = contact_or_404(db, user.organization_id, contact_id)
    before = obj(item)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.flush()
    audit(db, user, user.organization_id, "contact.updated", "contact", item.id, before=before, after=obj(item))
    db.commit()
    return obj(item)

@app.post("/api/v1/contacts/{contact_id}/consents", status_code=201)
def record_consent(contact_id: str, body: ConsentCreate, user: User = Depends(require("owner", "admin", "manager")), db: Session = Depends(get_db)):
    contact_or_404(db, user.organization_id, contact_id)
    if body.channel not in {"whatsapp", "voice", "sms", "email", "web"} or body.status not in {"opt_in", "opt_out"}:
        raise HTTPException(422, "Invalid channel or status")
    item = Consent(organization_id=user.organization_id, contact_id=contact_id, actor_id=user.id, **body.model_dump())
    db.add(item)
    db.flush()
    audit(db, user, user.organization_id, "consent.recorded", "consent", item.id, after=obj(item))
    db.commit()
    return obj(item)

@app.get("/api/v1/conversations")
def list_conversations(status: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Conversation).where(Conversation.organization_id == user.organization_id)
    if status:
        query = query.where(Conversation.status == status)
    return [obj(c) for c in db.scalars(query.order_by(Conversation.started_at.desc()).limit(100)).all()]

@app.post("/api/v1/conversations", status_code=201)
def create_conversation(body: ConversationCreate, user: User = Depends(require("owner", "admin", "manager", "operator")), db: Session = Depends(get_db)):
    contact_or_404(db, user.organization_id, body.contact_id)
    if body.channel not in {"whatsapp", "voice", "sms", "email", "web"}:
        raise HTTPException(422, "Invalid channel")
    item = Conversation(organization_id=user.organization_id, **body.model_dump())
    db.add(item)
    db.flush()
    audit(db, user, user.organization_id, "conversation.created", "conversation", item.id)
    db.commit()
    return obj(item)

@app.get("/api/v1/conversations/{conversation_id}")
def get_conversation(conversation_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = conversation_or_404(db, user.organization_id, conversation_id)
    messages = db.scalars(select(Message).where(Message.organization_id == user.organization_id, Message.conversation_id == item.id).order_by(Message.timestamp)).all()
    return {"conversation": obj(item), "messages": [obj(m) for m in messages]}

@app.post("/api/v1/conversations/{conversation_id}/web-token")
def issue_web_chat_token(conversation_id: str, user: User = Depends(require("owner", "admin", "manager", "operator")), db: Session = Depends(get_db)):
    conversation = conversation_or_404(db, user.organization_id, conversation_id)
    if conversation.channel != "web" or conversation.status not in {"active", "waiting"}:
        raise HTTPException(409, "Open web conversation required")
    token = web_token_for(user.organization_id, conversation.contact_id, conversation.id)
    audit(db, user, user.organization_id, "web_token.issued", "conversation", conversation.id)
    db.commit()
    return {"token": token, "expires_in_seconds": 86400}

@app.post("/api/v1/conversations/{conversation_id}/suggest-reply")
def suggest_conversation_reply(conversation_id: str, body: SuggestReply, user: User = Depends(require("owner", "admin", "manager", "operator")), db: Session = Depends(get_db)):
    conversation = conversation_or_404(db, user.organization_id, conversation_id)
    incoming = db.scalar(select(Message).where(Message.organization_id == user.organization_id, Message.conversation_id == conversation.id, Message.direction == "inbound").order_by(Message.timestamp.desc()).limit(1))
    if not incoming:
        raise HTTPException(409, "No incoming message to answer")
    result = ask_knowledge(KnowledgeAsk(question=incoming.content, idempotency_key=body.idempotency_key), user, db)
    return {**result, "draft": True, "incoming_message_id": incoming.id}

@app.get("/api/v1/web-chat/{conversation_id}")
def get_web_chat(conversation_id: str, claims: dict = Depends(web_chat_claims), db: Session = Depends(get_db)):
    if claims["conversation"] != conversation_id:
        raise HTTPException(404, "Conversation not found")
    conversation = conversation_or_404(db, claims["org"], conversation_id)
    if conversation.contact_id != claims["sub"] or conversation.channel != "web":
        raise HTTPException(404, "Conversation not found")
    messages = db.scalars(select(Message).where(Message.organization_id == claims["org"], Message.conversation_id == conversation_id).order_by(Message.timestamp)).all()
    return {"status": conversation.status, "messages": [obj(m) for m in messages]}

@app.post("/api/v1/web-chat/{conversation_id}/messages", status_code=201)
def receive_web_chat(conversation_id: str, body: MessageCreate, claims: dict = Depends(web_chat_claims), db: Session = Depends(get_db)):
    if claims["conversation"] != conversation_id:
        raise HTTPException(404, "Conversation not found")
    conversation = conversation_or_404(db, claims["org"], conversation_id)
    if conversation.contact_id != claims["sub"] or conversation.channel != "web" or conversation.status not in {"active", "waiting"}:
        raise HTTPException(404, "Conversation not found")
    message = Message(organization_id=claims["org"], conversation_id=conversation.id, sender_type="contact", content=body.content, direction="inbound", status="received")
    db.add(message)
    db.add(Interaction(organization_id=claims["org"], contact_id=conversation.contact_id, conversation_id=conversation.id, channel="web", type="message", direction="inbound"))
    db.flush()
    auto_escalate(db, conversation, message)
    record_usage(db, claims["org"], f"inbound:{message.id}", "inbound_message")
    audit(db, None, claims["org"], "web_message.received", "message", message.id)
    db.commit()
    return obj(message)

@app.post("/api/v1/conversations/{conversation_id}/messages", status_code=201)
def send_message(conversation_id: str, body: MessageCreate, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), user: User = Depends(require("owner", "admin", "manager", "operator")), db: Session = Depends(get_db)):
    conversation = conversation_or_404(db, user.organization_id, conversation_id)
    if conversation.status not in {"active", "waiting"}:
        raise HTTPException(409, "Conversation is not open")
    if idempotency_key and len(idempotency_key) > 120:
        raise HTTPException(422, "Idempotency key too long")
    if idempotency_key:
        existing = db.scalar(select(Message).where(Message.organization_id == user.organization_id, Message.idempotency_key == idempotency_key))
        if existing:
            if existing.conversation_id != conversation.id or existing.content != body.content:
                raise HTTPException(409, "Idempotency key already used for a different message")
            return obj(existing)
    if not consent_allowed(db, user.organization_id, conversation.contact_id, conversation.channel):
        raise HTTPException(403, "Channel opt-in required")
    key = idempotency_key or str(uuid.uuid4())
    status = "sent" if conversation.channel == "web" else "queued"
    message = Message(organization_id=user.organization_id, conversation_id=conversation.id, sender_type="user", content=body.content, direction="outbound", status=status, idempotency_key=key)
    db.add(message)
    db.flush()
    record_usage(db, user.organization_id, f"outbound:{message.id}", "outbound_message")
    if conversation.channel != "web":
        db.add(OutboundJob(organization_id=user.organization_id, message_id=message.id))
    db.add(Interaction(organization_id=user.organization_id, contact_id=conversation.contact_id, conversation_id=conversation.id, channel=conversation.channel, type="message", direction="outbound"))
    audit(db, user, user.organization_id, "message.sent" if status == "sent" else "message.queued", "message", message.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Message).where(Message.organization_id == user.organization_id, Message.idempotency_key == key))
        if existing and existing.conversation_id == conversation.id and existing.content == body.content:
            return obj(existing)
        raise HTTPException(409, "Idempotency key already used")
    return obj(message)

@app.get("/api/v1/analytics/overview")
def overview(user: User = Depends(current_user), db: Session = Depends(get_db)):
    org = user.organization_id
    def count(model, *criteria):
        return db.scalar(select(func.count()).select_from(model).where(model.organization_id == org, *criteria))
    return {"contacts": count(Contact), "active_conversations": count(Conversation, Conversation.status == "active"), "messages": count(Message), "interactions": count(Interaction), "consent_records": count(Consent)}

@app.get("/api/v1/knowledge/bases")
def list_knowledge_bases(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [obj(base) for base in db.scalars(select(KnowledgeBase).where(KnowledgeBase.organization_id == user.organization_id).order_by(KnowledgeBase.created_at)).all()]

@app.post("/api/v1/knowledge/bases", status_code=201)
def create_knowledge_base(body: KnowledgeBaseCreate, user: User = Depends(require("owner", "admin", "manager")), db: Session = Depends(get_db)):
    base = KnowledgeBase(organization_id=user.organization_id, **body.model_dump())
    db.add(base)
    db.flush()
    audit(db, user, user.organization_id, "knowledge_base.created", "knowledge_base", base.id)
    db.commit()
    return obj(base)

@app.get("/api/v1/knowledge/documents")
def list_knowledge_documents(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.organization_id == user.organization_id).order_by(KnowledgeDocument.updated_at.desc()).limit(100)).all()
    return [knowledge_doc_view(row) for row in rows]

@app.post("/api/v1/knowledge/documents", status_code=201)
def create_knowledge_document(body: KnowledgeDocumentCreate, user: User = Depends(require("owner", "admin", "manager")), db: Session = Depends(get_db)):
    if body.knowledge_base_id:
        base = db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == body.knowledge_base_id, KnowledgeBase.organization_id == user.organization_id))
        if not base:
            raise HTTPException(404, "Knowledge base not found")
    else:
        base = db.scalar(select(KnowledgeBase).where(KnowledgeBase.organization_id == user.organization_id, KnowledgeBase.name == "General"))
        if not base:
            base = KnowledgeBase(organization_id=user.organization_id, name="General", description="General approved organization information")
            db.add(base)
            db.flush()
    document = KnowledgeDocument(organization_id=user.organization_id, knowledge_base_id=base.id, title=body.title, source=body.source, content=body.content, status="draft")
    db.add(document)
    db.flush()
    for position, content in enumerate(chunk_text(body.content)):
        db.add(KnowledgeChunk(organization_id=user.organization_id, document_id=document.id, version=1, position=position, content=content))
    audit(db, user, user.organization_id, "knowledge_document.created", "knowledge_document", document.id, after={"title": document.title, "version": 1, "status": "draft"})
    db.commit()
    return knowledge_doc_view(document)

@app.post("/api/v1/knowledge/documents/upload", status_code=201)
async def upload_knowledge_document(file: UploadFile = File(...), title: str = Form(..., min_length=2, max_length=200), source: str = Form("", max_length=300), user: User = Depends(require("owner", "admin", "manager")), db: Session = Depends(get_db)):
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        extracted = extract_text(file.filename or "", data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        raise HTTPException(422, "Unable to extract text from this file") from None
    body = KnowledgeDocumentCreate(title=title, source=source or file.filename or "", content=extracted)
    return create_knowledge_document(body, user, db)

@app.get("/api/v1/knowledge/documents/{document_id}")
def get_knowledge_document(document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return obj(knowledge_doc_or_404(db, user.organization_id, document_id))

@app.put("/api/v1/knowledge/documents/{document_id}")
def update_knowledge_document(document_id: str, body: KnowledgeDocumentUpdate, user: User = Depends(require("owner", "admin", "manager")), db: Session = Depends(get_db)):
    document = knowledge_doc_or_404(db, user.organization_id, document_id)
    old_version = document.version
    document.version += 1
    document.title = body.title
    document.source = body.source
    document.content = body.content
    document.status = "draft"
    document.approved_by = None
    document.approved_at = None
    for chunk in db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.organization_id == user.organization_id, KnowledgeChunk.document_id == document.id)).all():
        db.delete(chunk)
    db.flush()
    for position, content in enumerate(chunk_text(body.content)):
        db.add(KnowledgeChunk(organization_id=user.organization_id, document_id=document.id, version=document.version, position=position, content=content))
    audit(db, user, user.organization_id, "knowledge_document.updated", "knowledge_document", document.id, before={"version": old_version}, after={"version": document.version, "status": "draft"})
    db.commit()
    return knowledge_doc_view(document)

@app.post("/api/v1/knowledge/documents/{document_id}/approve")
def approve_knowledge_document(document_id: str, user: User = Depends(require("owner", "admin")), db: Session = Depends(get_db)):
    document = knowledge_doc_or_404(db, user.organization_id, document_id)
    if document.status != "draft":
        raise HTTPException(409, "Only draft documents can be approved")
    document.status = "approved"
    document.approved_by = user.id
    document.approved_at = now()
    audit(db, user, user.organization_id, "knowledge_document.approved", "knowledge_document", document.id, after={"version": document.version})
    db.commit()
    return knowledge_doc_view(document)

@app.post("/api/v1/knowledge/documents/{document_id}/archive")
def archive_knowledge_document(document_id: str, user: User = Depends(require("owner", "admin")), db: Session = Depends(get_db)):
    document = knowledge_doc_or_404(db, user.organization_id, document_id)
    document.status = "archived"
    audit(db, user, user.organization_id, "knowledge_document.archived", "knowledge_document", document.id)
    db.commit()
    return knowledge_doc_view(document)

@app.post("/api/v1/knowledge/search")
def search_knowledge(body: KnowledgeQuestion, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"passages": knowledge_search(db, user.organization_id, body.question)}

@app.post("/api/v1/knowledge/ask")
def ask_knowledge(body: KnowledgeAsk, user: User = Depends(current_user), db: Session = Depends(get_db)):
    existing = db.scalar(select(AgentRun).where(AgentRun.organization_id == user.organization_id, AgentRun.request_key == body.idempotency_key))
    if existing:
        if existing.input.get("question") != body.question:
            raise HTTPException(409, "Request key already used for another question")
        if existing.status != "completed" or not existing.output:
            raise HTTPException(409, "Previous answer is incomplete; do not retry automatically")
        for citation in existing.output.get("citations", []):
            document = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == citation["document_id"], KnowledgeDocument.organization_id == user.organization_id, KnowledgeDocument.status == "approved", KnowledgeDocument.version == citation["version"]))
            if not document:
                raise HTTPException(409, "Sources changed; ask again with a new request key")
        return existing.output
    run = AgentRun(organization_id=user.organization_id, agent_type="knowledge", trigger_type="api", request_key=body.idempotency_key, input={"question": body.question}, status="started")
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Question is already being processed")
    started = time.monotonic()
    try:
        result = knowledge_answer(db, user.organization_id, body.question)
        run.status = "completed"
        run.output = result
        run.latency_ms = int((time.monotonic() - started) * 1000)
        run.completed_at = now()
        record_usage(db, user.organization_id, f"knowledge:{run.id}", "knowledge_query")
        if result.get("tokens"):
            record_usage(db, user.organization_id, f"model_tokens:{run.id}", "model_tokens", result["tokens"], "tokens")
        audit(db, user, user.organization_id, "knowledge.answer", "agent_run", run.id, after={"mode": result["mode"], "citations": len(result["citations"])})
        db.commit()
        return result
    except Exception:
        db.rollback()
        run = db.get(AgentRun, run.id)
        run.status = "unknown"
        run.completed_at = now()
        run.latency_ms = int((time.monotonic() - started) * 1000)
        db.commit()
        raise HTTPException(503, "Knowledge answer unavailable; inspect run before trying a new request") from None

@app.get("/api/v1/knowledge/runs")
def knowledge_runs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(AgentRun).where(AgentRun.organization_id == user.organization_id, AgentRun.agent_type == "knowledge").order_by(AgentRun.started_at.desc()).limit(50)).all()
    return [obj(row) for row in rows]

@app.post("/api/v1/webhooks/{channel}")
async def receive_webhook(channel: str, request: Request, x_signature: str = Header(default=""), db: Session = Depends(get_db)):
    if channel not in {"whatsapp", "voice"}:
        raise HTTPException(404)
    secret = os.getenv("ENGINE_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(503, "Webhook secret not configured")
    raw = await request.body()
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(x_signature, expected):
        raise HTTPException(401, "Invalid signature")
    body = WebhookMessage.model_validate_json(raw)
    event_key = hashlib.sha256(body.external_event_id.encode()).hexdigest()[:36]
    contact = db.scalar(select(Contact).where(Contact.organization_id == body.organization_id, Contact.phone == body.contact_phone))
    if not contact:
        raise HTTPException(404, "Contact not found")
    # Engine events are idempotent using an organization-scoped external event id.
    if db.scalar(select(AuditLog.id).where(AuditLog.organization_id == body.organization_id, AuditLog.action == "webhook.received", AuditLog.resource_id == event_key)):
        return {"status": "duplicate"}
    conversation = db.scalar(select(Conversation).where(Conversation.organization_id == body.organization_id, Conversation.contact_id == contact.id, Conversation.channel == channel, Conversation.status.in_(["active", "waiting"])).order_by(Conversation.started_at.desc()))
    if not conversation:
        conversation = Conversation(organization_id=body.organization_id, contact_id=contact.id, channel=channel)
        db.add(conversation)
        db.flush()
    message = Message(organization_id=body.organization_id, conversation_id=conversation.id, sender_type="contact", content=body.content, direction="inbound")
    db.add(message)
    db.flush()
    auto_escalate(db, conversation, message)
    record_usage(db, body.organization_id, f"inbound:{message.id}", "inbound_message")
    db.add(Interaction(organization_id=body.organization_id, contact_id=contact.id, conversation_id=conversation.id, channel=channel, type="message", direction="inbound"))
    audit(db, None, body.organization_id, "webhook.received", "engine_event", event_key)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"status": "duplicate"}
    return {"status": "received", "conversation_id": conversation.id}

@app.post("/api/v1/webhooks/{channel}/delivery")
async def receive_delivery(channel: str, request: Request, x_signature: str = Header(default=""), db: Session = Depends(get_db)):
    if channel not in {"whatsapp", "voice"}:
        raise HTTPException(404)
    secret = os.getenv("ENGINE_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(503, "Webhook secret not configured")
    raw = await request.body()
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(x_signature, expected):
        raise HTTPException(401, "Invalid signature")
    body = DeliveryEvent.model_validate_json(raw)
    if body.status not in {"sent", "delivered", "failed"}:
        raise HTTPException(422, "Invalid delivery status")
    message = db.scalar(select(Message).where(Message.id == body.message_id, Message.organization_id == body.organization_id, Message.direction == "outbound"))
    if not message:
        raise HTTPException(404, "Message not found")
    conversation = conversation_or_404(db, body.organization_id, message.conversation_id)
    if conversation.channel != channel:
        raise HTTPException(409, "Channel mismatch")
    event_key = hashlib.sha256(f"{channel}:{body.external_event_id}".encode()).hexdigest()[:36]
    if db.scalar(select(AuditLog.id).where(AuditLog.organization_id == body.organization_id, AuditLog.action == "webhook.delivery", AuditLog.resource_id == event_key)):
        return {"status": "duplicate"}
    before = {"status": message.status}
    if body.status == "delivered" or (body.status == "sent" and message.status in {"queued", "failed"}) or (body.status == "failed" and message.status in {"queued", "sent"}):
        message.status = body.status
    if body.external_id:
        message.external_id = body.external_id
    audit(db, None, body.organization_id, "webhook.delivery", "engine_event", event_key, before=before, after={"message_id": message.id, "status": message.status})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"status": "duplicate"}
    return {"status": message.status, "message_id": message.id}
