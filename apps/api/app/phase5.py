"""Phase 5: reviewable data hygiene, human escalations, analytics and usage metering."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import AgentRun, AuditLog, Campaign, Contact, Conversation, Escalation, HygieneIssue, Interaction, Message, Organization, OutboundJob, Survey, SurveyResponse, Task, UsageEvent, User, now
from .security import current_user, passwords, require

router = APIRouter(prefix="/api/v1")


def view(row):
    return jsonable_encoder({column.key: getattr(row, column.key) for column in row.__mapper__.columns})


def audit(db, user, action, kind, resource, after=None):
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id, action=action, resource_type=kind, resource_id=resource, after=after))


def record_usage(db: Session, org: str, key: str, kind: str, quantity: int = 1, unit: str = "events"):
    db.add(UsageEvent(organization_id=org, event_key=key, kind=kind, quantity=max(0, quantity), unit=unit))


class WorkspaceLookup(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


@router.post("/auth/workspaces")
def discover_workspaces(body: WorkspaceLookup, db: Session = Depends(get_db)):
    candidates = db.scalars(select(User).where(User.email == body.email.lower(), User.status == "active")).all()
    matched = [user for user in candidates if passwords.verify(body.password, user.password_hash)]
    if not matched:
        raise HTTPException(401, "Invalid credentials")
    org_ids = {user.organization_id for user in matched}
    organizations = db.scalars(select(Organization).where(Organization.id.in_(org_ids), Organization.status == "active")).all()
    return [{"id": org.id, "name": org.name, "type": org.type} for org in sorted(organizations, key=lambda item: item.name.casefold())]


@router.get("/organizations/current")
def current_workspace(user: User = Depends(current_user), db: Session = Depends(get_db)):
    org = db.scalar(select(Organization).where(Organization.id == user.organization_id))
    return view(org)


def _issue(db, org, seen, key, kind, contact_id, related_id, detail):
    seen.add(key)
    existing = db.scalar(select(HygieneIssue).where(HygieneIssue.organization_id == org, HygieneIssue.issue_key == key))
    if existing:
        if existing.status == "resolved":
            existing.status = "open"
            existing.reviewed_at = None
        return False
    db.add(HygieneIssue(organization_id=org, issue_key=key, kind=kind, contact_id=contact_id, related_contact_id=related_id, detail=detail))
    return True


def scan_hygiene(db: Session, org: str) -> dict:
    contacts = db.scalars(select(Contact).where(Contact.organization_id == org, Contact.status == "active").order_by(Contact.id)).all()
    email_seen, phone_seen = {}, {}
    found = Counter()
    seen = set()
    stale_before = now() - timedelta(days=365)
    for contact in contacts:
        email = (contact.email or "").strip().casefold()
        phone = re.sub(r"\D", "", contact.phone or "")
        if email:
            if email in email_seen:
                ids = sorted([contact.id, email_seen[email]])
                if _issue(db, org, seen, f"email:{ids[0]}:{ids[1]}", "duplicate_email", ids[0], ids[1], "Two contacts share the same email. Review before merging."):
                    found["duplicate_email"] += 1
            else:
                email_seen[email] = contact.id
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                if _issue(db, org, seen, f"invalid_email:{contact.id}", "invalid_email", contact.id, None, "Email format needs review."):
                    found["invalid_email"] += 1
        if phone:
            if phone in phone_seen:
                ids = sorted([contact.id, phone_seen[phone]])
                if _issue(db, org, seen, f"phone:{ids[0]}:{ids[1]}", "duplicate_phone", ids[0], ids[1], "Two contacts share the same phone number. Review before merging."):
                    found["duplicate_phone"] += 1
            else:
                phone_seen[phone] = contact.id
            if len(phone) < 7 or len(phone) > 15:
                if _issue(db, org, seen, f"invalid_phone:{contact.id}", "invalid_phone", contact.id, None, "Phone length needs review."):
                    found["invalid_phone"] += 1
        touched = contact.updated_at.replace(tzinfo=contact.updated_at.tzinfo or timezone.utc)
        if touched < stale_before:
            if _issue(db, org, seen, f"stale:{contact.id}", "stale_contact", contact.id, None, "Contact has not been updated for over a year."):
                found["stale_contact"] += 1
    for issue in db.scalars(select(HygieneIssue).where(HygieneIssue.organization_id == org, HygieneIssue.status == "open")).all():
        if issue.issue_key not in seen:
            issue.status = "resolved"
            issue.reviewed_at = now()
    run = AgentRun(organization_id=org, agent_type="data_hygiene", trigger_type="manual", input={"contacts_scanned": len(contacts)}, output={"issues_created": dict(found)}, status="completed", completed_at=now())
    db.add(run)
    db.commit()
    return {"contacts_scanned": len(contacts), "issues_created": dict(found), "run_id": run.id}


@router.post("/hygiene/scan")
def scan_own_hygiene(user: User = Depends(require("owner", "admin", "manager")), db: Session = Depends(get_db)):
    return scan_hygiene(db, user.organization_id)


@router.get("/hygiene/issues")
def hygiene_issues(status: str = "open", user: User = Depends(current_user), db: Session = Depends(get_db)):
    if status not in {"open", "dismissed", "resolved", "all"}:
        raise HTTPException(422, "Invalid status")
    query = select(HygieneIssue).where(HygieneIssue.organization_id == user.organization_id)
    if status != "all":
        query = query.where(HygieneIssue.status == status)
    result = []
    for item in db.scalars(query.order_by(HygieneIssue.created_at.desc()).limit(500)).all():
        contact = db.scalar(select(Contact).where(Contact.id == item.contact_id, Contact.organization_id == user.organization_id))
        related = db.scalar(select(Contact).where(Contact.id == item.related_contact_id, Contact.organization_id == user.organization_id)) if item.related_contact_id else None
        result.append({**view(item), "contact_name": f"{contact.first_name} {contact.last_name}".strip() if contact else "Unknown", "related_contact_name": f"{related.first_name} {related.last_name}".strip() if related else None})
    return result


@router.post("/hygiene/issues/{issue_id}/dismiss")
def dismiss_issue(issue_id: str, user: User = Depends(require("owner", "admin", "manager")), db: Session = Depends(get_db)):
    issue = db.scalar(select(HygieneIssue).where(HygieneIssue.id == issue_id, HygieneIssue.organization_id == user.organization_id))
    if not issue:
        raise HTTPException(404, "Hygiene issue not found")
    issue.status = "dismissed"
    issue.reviewed_at = now()
    audit(db, user, "hygiene.dismissed", "hygiene_issue", issue.id)
    db.commit()
    return view(issue)


ESCALATION_RULES = [
    (re.compile(r"\b(emergency|unsafe|danger|threat|harass(?:ment|ed)?|abuse)\b", re.I), "urgent_safety", "urgent"),
    (re.compile(r"\b(human|person|supervisor|manager|complaint|appeal|call me|speak to someone)\b", re.I), "human_requested", "high"),
]


def escalation_reason(content: str):
    for pattern, reason, priority in ESCALATION_RULES:
        if pattern.search(content):
            return reason, priority
    return None


def create_escalation(db: Session, conversation: Conversation, message: Message, reason: str, priority: str):
    if db.scalar(select(Escalation.id).where(Escalation.organization_id == conversation.organization_id, Escalation.message_id == message.id)):
        return None
    task = Task(organization_id=conversation.organization_id, contact_id=conversation.contact_id, conversation_id=conversation.id, title="Human review requested", description=f"{reason.replace('_', ' ').capitalize()}: {message.content[:400]}", priority=priority, due_at=now(), source_key=f"escalation:{message.id}")
    db.add(task)
    db.flush()
    escalation = Escalation(organization_id=conversation.organization_id, conversation_id=conversation.id, message_id=message.id, task_id=task.id, reason=reason, priority=priority)
    db.add(escalation)
    db.flush()
    conversation.status = "waiting"
    db.add(AgentRun(organization_id=conversation.organization_id, agent_type="escalation", trigger_type="message", input={"message_id": message.id, "conversation_id": conversation.id}, output={"reason": reason, "priority": priority, "task_id": task.id}, status="completed", completed_at=now()))
    db.add(AuditLog(organization_id=conversation.organization_id, action="conversation.escalated", resource_type="conversation", resource_id=conversation.id, after={"reason": reason, "priority": priority}))
    return escalation


def auto_escalate(db: Session, conversation: Conversation, message: Message):
    result = escalation_reason(message.content)
    return create_escalation(db, conversation, message, *result) if result else None


class ManualEscalation(BaseModel):
    reason: str = Field(min_length=3, max_length=100)
    priority: str = "high"


@router.post("/conversations/{conversation_id}/escalate")
def escalate_conversation(conversation_id: str, body: ManualEscalation, user: User = Depends(require("owner", "admin", "manager", "operator")), db: Session = Depends(get_db)):
    if body.priority not in {"normal", "high", "urgent"}:
        raise HTTPException(422, "Invalid priority")
    conversation = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.organization_id == user.organization_id))
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    message = db.scalar(select(Message).where(Message.organization_id == user.organization_id, Message.conversation_id == conversation.id).order_by(Message.timestamp.desc(), Message.id.desc()).limit(1))
    if not message:
        raise HTTPException(409, "Conversation has no messages")
    escalation = create_escalation(db, conversation, message, body.reason.strip(), body.priority)
    if not escalation:
        raise HTTPException(409, "Latest message is already escalated")
    db.commit()
    return view(escalation)


@router.get("/escalations")
def list_escalations(status: str = "open", user: User = Depends(current_user), db: Session = Depends(get_db)):
    if status not in {"open", "resolved", "all"}:
        raise HTTPException(422, "Invalid status")
    query = select(Escalation).where(Escalation.organization_id == user.organization_id)
    if status != "all":
        query = query.where(Escalation.status == status)
    result = []
    for item in db.scalars(query.order_by(Escalation.created_at.desc()).limit(200)).all():
        task = db.scalar(select(Task).where(Task.id == item.task_id, Task.organization_id == user.organization_id))
        result.append({**view(item), "summary": task.description if task else item.reason})
    return result


@router.post("/escalations/{escalation_id}/resolve")
def resolve_escalation(escalation_id: str, user: User = Depends(require("owner", "admin", "manager", "operator")), db: Session = Depends(get_db)):
    escalation = db.scalar(select(Escalation).where(Escalation.id == escalation_id, Escalation.organization_id == user.organization_id))
    if not escalation:
        raise HTTPException(404, "Escalation not found")
    escalation.status = "resolved"
    escalation.resolved_at = now()
    task = db.scalar(select(Task).where(Task.id == escalation.task_id, Task.organization_id == user.organization_id))
    if task:
        task.status = "completed"
    conversation = db.scalar(select(Conversation).where(Conversation.id == escalation.conversation_id, Conversation.organization_id == user.organization_id))
    if conversation and conversation.status == "waiting":
        conversation.status = "active"
    audit(db, user, "escalation.resolved", "escalation", escalation.id)
    db.commit()
    return view(escalation)


def dashboard(db: Session, org: str):
    def count(model, *where):
        return db.scalar(select(func.count()).select_from(model).where(model.organization_id == org, *where)) or 0
    messages = db.scalars(select(Message).where(Message.organization_id == org)).all()
    interactions = db.scalars(select(Interaction).where(Interaction.organization_id == org)).all()
    runs = db.scalars(select(AgentRun).where(AgentRun.organization_id == org)).all()
    buckets = Counter()
    for item in interactions:
        buckets[item.timestamp.date().isoformat()] += 1
    today = now().date()
    series = [{"date": (today - timedelta(days=day)).isoformat(), "interactions": buckets[(today - timedelta(days=day)).isoformat()]} for day in range(13, -1, -1)]
    channels = Counter(item.channel for item in interactions)
    return {
        "contacts": count(Contact), "active_conversations": count(Conversation, Conversation.status == "active"),
        "messages": len(messages), "messages_today": sum(item.timestamp.date() == today for item in messages),
        "interactions": len(interactions), "campaigns": count(Campaign), "survey_responses": count(SurveyResponse),
        "open_tasks": count(Task, Task.status == "open"), "open_escalations": count(Escalation, Escalation.status == "open"),
        "agent_runs": len(runs), "agent_failures": sum(item.status not in {"completed"} for item in runs),
        "outbound_sent": sum(item.direction == "outbound" and item.status in {"sent", "delivered"} for item in messages),
        "outbound_failed": sum(item.direction == "outbound" and item.status == "failed" for item in messages),
        "channel_distribution": dict(channels), "interaction_series": series,
    }


@router.get("/analytics/dashboard")
def analytics_dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return dashboard(db, user.organization_id)


def analyze_question(db: Session, org: str, question: str):
    q = question.casefold()
    data = dashboard(db, org)
    metric_names = {
        "contact": (r"\bcontacts?\b", "contacts"), "conversation": (r"\bconversations?\b", "active_conversations"),
        "message": (r"\bmessages?\b", "messages"), "campaign": (r"\bcampaigns?\b", "campaigns"),
        "survey": (r"\b(survey|surveys)\b", "survey_responses"), "task": (r"\b(tasks?|follow-ups?)\b", "open_tasks"),
        "escalation": (r"\bescalations?\b", "open_escalations"), "interaction": (r"\binteractions?\b", "interactions"),
        "agent": (r"\bagents?\b", "agent_runs"), "failure": (r"\bfailures?\b", "agent_failures"),
    }
    selected = {label: data[key] for label, (pattern, key) in metric_names.items() if re.search(pattern, q)}
    if "today" in q and "message" in q:
        selected = {"messages_today": data["messages_today"]}
    breakdown = data["channel_distribution"] if "channel" in q else None
    series = data["interaction_series"] if any(word in q for word in ("trend", "over time", "last 14 days", "past 14 days")) else None
    if not selected and breakdown is None and series is None:
        raise HTTPException(422, "Ask about contacts, conversations, messages, campaigns, surveys, tasks, escalations, agents, channels, or interaction trends")
    parts = [f"{name.replace('_', ' ').capitalize()}: {value}" for name, value in selected.items()]
    if breakdown is not None:
        parts.append("Interactions by channel: " + (", ".join(f"{key} {value}" for key, value in sorted(breakdown.items())) or "none"))
    if series is not None:
        parts.append(f"Interactions in the last 14 days: {sum(row['interactions'] for row in series)}")
    return {"answer": "; ".join(parts) + ".", "metrics": selected, "breakdown": breakdown, "series": series, "method": "controlled_metrics"}


@router.get("/usage/summary")
def usage_summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    org = user.organization_id
    events = db.scalars(select(UsageEvent).where(UsageEvent.organization_id == org)).all()
    by_kind = Counter()
    for event in events:
        by_kind[event.kind] += event.quantity
    def count(model):
        return db.scalar(select(func.count()).select_from(model).where(model.organization_id == org)) or 0
    return {"measured_events": dict(by_kind), "total_messages": count(Message), "total_agent_runs": count(AgentRun), "total_knowledge_queries": db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.organization_id == org, AgentRun.agent_type == "knowledge")) or 0, "billing_status": "not_configured", "amount_due": None, "currency": None}
