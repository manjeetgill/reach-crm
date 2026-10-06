"""Phase 4 workflows: reviewed campaigns, surveys, follow-ups and controlled analytics."""
from __future__ import annotations

from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import AgentRun, AuditLog, Campaign, CampaignRecipient, Consent, Contact, Conversation, Escalation, Interaction, Message, OutboundJob, Survey, SurveyResponse, Task, User, now
from .security import current_user, require

router = APIRouter(prefix="/api/v1")


def view(row):
    from fastapi.encoders import jsonable_encoder
    return jsonable_encoder({column.key: getattr(row, column.key) for column in row.__mapper__.columns})


def audit(db, user, action, kind, resource, after=None):
    db.add(AuditLog(organization_id=user.organization_id, actor_id=user.id, action=action, resource_type=kind, resource_id=resource, after=after))


def get_campaign(db, org, campaign_id):
    campaign = db.scalar(select(Campaign).where(Campaign.id == campaign_id, Campaign.organization_id == org))
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    return campaign


def get_survey(db, org, survey_id):
    survey = db.scalar(select(Survey).where(Survey.id == survey_id, Survey.organization_id == org))
    if not survey:
        raise HTTPException(404, "Survey not found")
    return survey


class CampaignCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=2000)
    channel: str
    content: str = Field(min_length=5, max_length=2000)
    contact_ids: list[str] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_campaign(self):
        if self.channel not in {"web", "whatsapp"}:
            raise ValueError("Campaign channel must be web or whatsapp")
        if len(set(self.contact_ids)) != len(self.contact_ids):
            raise ValueError("Contact IDs must be unique")
        return self


class ContentDraftRequest(BaseModel):
    objective: str = Field(min_length=5, max_length=500)
    channel: str


class SurveyQuestion(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,39}$")
    label: str = Field(min_length=3, max_length=300)
    type: str
    required: bool = True
    options: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_question(self):
        if self.type not in {"text", "choice", "rating"}:
            raise ValueError("Question type must be text, choice, or rating")
        if self.type == "choice" and (len(self.options) < 2 or len(set(self.options)) != len(self.options)):
            raise ValueError("Choice questions need at least two unique options")
        if self.type != "choice" and self.options:
            raise ValueError("Only choice questions can have options")
        return self


class SurveyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=2000)
    questions: list[SurveyQuestion] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("Survey question IDs must be unique")
        return self


class SurveyAnswer(BaseModel):
    contact_id: str
    answers: dict[str, str | int] = Field(max_length=20)


class AnalyticsQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=300)


@router.post("/campaigns/content-draft")
def content_draft(body: ContentDraftRequest, user: User = Depends(require("owner", "admin", "manager", "operator")), db: Session = Depends(get_db)):
    if body.channel not in {"web", "whatsapp"}:
        raise HTTPException(422, "Unsupported channel")
    text = body.objective.strip().rstrip(".!?")
    content = f"Hello, {text}. Please reply if you have questions."
    run = AgentRun(organization_id=user.organization_id, agent_type="content", trigger_type="manual", input={"objective": body.objective, "channel": body.channel}, output={"content": content, "draft": True, "method": "template"}, status="completed", completed_at=now())
    db.add(run)
    db.commit()
    return {"content": content, "draft": True, "method": "template", "run_id": run.id}


@router.get("/campaigns")
def list_campaigns(user: User = Depends(current_user), db: Session = Depends(get_db)):
    campaigns = db.scalars(select(Campaign).where(Campaign.organization_id == user.organization_id).order_by(Campaign.created_at.desc())).all()
    return [campaign_view(db, item) for item in campaigns]


def campaign_view(db, campaign):
    recipients = db.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.organization_id == campaign.organization_id)).all()
    result = view(campaign)
    result["recipients"] = len(recipients)
    result["recipient_statuses"] = {}
    for item in recipients:
        status = item.status
        if item.message_id:
            message = db.scalar(select(Message).where(Message.id == item.message_id, Message.organization_id == campaign.organization_id))
            status = message.status if message else status
        result["recipient_statuses"][status] = result["recipient_statuses"].get(status, 0) + 1
    return result


@router.post("/campaigns", status_code=201)
def create_campaign(body: CampaignCreate, user: User = Depends(require("owner", "admin", "manager")), db: Session = Depends(get_db)):
    contacts = db.scalars(select(Contact).where(Contact.organization_id == user.organization_id, Contact.id.in_(body.contact_ids))).all()
    if len(contacts) != len(body.contact_ids):
        raise HTTPException(422, "Every selected contact must belong to this workspace")
    campaign = Campaign(organization_id=user.organization_id, name=body.name, description=body.description, channel=body.channel, content=body.content, created_by=user.id)
    db.add(campaign)
    db.flush()
    for contact in contacts:
        db.add(CampaignRecipient(organization_id=user.organization_id, campaign_id=campaign.id, contact_id=contact.id))
    audit(db, user, "campaign.created", "campaign", campaign.id, {"recipients": len(contacts), "channel": body.channel})
    db.commit()
    return campaign_view(db, campaign)


@router.get("/campaigns/{campaign_id}")
def read_campaign(campaign_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return campaign_view(db, get_campaign(db, user.organization_id, campaign_id))


@router.post("/campaigns/{campaign_id}/submit")
def submit_campaign(campaign_id: str, user: User = Depends(require("owner", "admin", "manager")), db: Session = Depends(get_db)):
    campaign = get_campaign(db, user.organization_id, campaign_id)
    if campaign.status != "draft":
        raise HTTPException(409, "Only drafts can be submitted")
    campaign.status = "pending_approval"
    audit(db, user, "campaign.submitted", "campaign", campaign.id)
    db.commit()
    return campaign_view(db, campaign)


@router.post("/campaigns/{campaign_id}/approve")
def approve_campaign(campaign_id: str, user: User = Depends(require("owner", "admin")), db: Session = Depends(get_db)):
    campaign = get_campaign(db, user.organization_id, campaign_id)
    if campaign.status != "pending_approval":
        raise HTTPException(409, "Campaign must be submitted first")
    campaign.status = "approved"
    campaign.approved_by = user.id
    campaign.approved_at = now()
    audit(db, user, "campaign.approved", "campaign", campaign.id)
    db.commit()
    return campaign_view(db, campaign)


def has_consent(db, org, contact_id, channel):
    latest = db.scalar(select(Consent).where(Consent.organization_id == org, Consent.contact_id == contact_id, Consent.channel == channel).order_by(Consent.captured_at.desc(), Consent.id.desc()).limit(1))
    return bool(latest and latest.status == "opt_in")


@router.post("/campaigns/{campaign_id}/start")
def start_campaign(campaign_id: str, user: User = Depends(require("owner", "admin")), db: Session = Depends(get_db)):
    from .phase5 import record_usage
    campaign = get_campaign(db, user.organization_id, campaign_id)
    if campaign.status != "approved":
        raise HTTPException(409, "Approved campaign required")
    if campaign.channel == "whatsapp":
        from .gateway import gateway
        gateway.adapter("whatsapp")  # Fail before any message is queued if no engine is configured.
    recipients = db.scalars(select(CampaignRecipient).where(CampaignRecipient.organization_id == user.organization_id, CampaignRecipient.campaign_id == campaign.id)).all()
    queued = skipped = 0
    for recipient in recipients:
        if not has_consent(db, user.organization_id, recipient.contact_id, campaign.channel):
            recipient.status = "skipped_no_consent"
            skipped += 1
            continue
        conversation = Conversation(organization_id=user.organization_id, contact_id=recipient.contact_id, channel=campaign.channel)
        db.add(conversation)
        db.flush()
        message = Message(organization_id=user.organization_id, conversation_id=conversation.id, sender_type="campaign", content=campaign.content, direction="outbound", status="sent" if campaign.channel == "web" else "queued", idempotency_key=f"campaign:{campaign.id}:{recipient.contact_id}")
        db.add(message)
        db.flush()
        record_usage(db, user.organization_id, f"outbound:{message.id}", "outbound_message")
        recipient.message_id = message.id
        recipient.status = message.status
        if campaign.channel != "web":
            db.add(OutboundJob(organization_id=user.organization_id, message_id=message.id))
        db.add(Interaction(organization_id=user.organization_id, contact_id=recipient.contact_id, conversation_id=conversation.id, channel=campaign.channel, type="campaign", direction="outbound"))
        queued += 1
    campaign.status = "running" if queued and campaign.channel != "web" else "completed"
    campaign.started_at = now()
    audit(db, user, "campaign.started", "campaign", campaign.id, {"queued": queued, "skipped_no_consent": skipped})
    db.commit()
    return campaign_view(db, campaign)


@router.post("/campaigns/{campaign_id}/pause")
def pause_campaign(campaign_id: str, user: User = Depends(require("owner", "admin")), db: Session = Depends(get_db)):
    campaign = get_campaign(db, user.organization_id, campaign_id)
    if campaign.status != "running":
        raise HTTPException(409, "Only running campaigns can be paused")
    campaign.status = "paused"
    audit(db, user, "campaign.paused", "campaign", campaign.id)
    db.commit()
    return campaign_view(db, campaign)


@router.post("/campaigns/{campaign_id}/resume")
def resume_campaign(campaign_id: str, user: User = Depends(require("owner", "admin")), db: Session = Depends(get_db)):
    campaign = get_campaign(db, user.organization_id, campaign_id)
    if campaign.status != "paused":
        raise HTTPException(409, "Only paused campaigns can be resumed")
    campaign.status = "running"
    audit(db, user, "campaign.resumed", "campaign", campaign.id)
    db.commit()
    return campaign_view(db, campaign)


@router.get("/surveys")
def list_surveys(user: User = Depends(current_user), db: Session = Depends(get_db)):
    surveys = db.scalars(select(Survey).where(Survey.organization_id == user.organization_id).order_by(Survey.created_at.desc())).all()
    return [{**view(item), "responses": db.scalar(select(func.count()).select_from(SurveyResponse).where(SurveyResponse.organization_id == user.organization_id, SurveyResponse.survey_id == item.id))} for item in surveys]


@router.post("/surveys", status_code=201)
def create_survey(body: SurveyCreate, user: User = Depends(require("owner", "admin", "manager")), db: Session = Depends(get_db)):
    survey = Survey(organization_id=user.organization_id, name=body.name, description=body.description, questions=[q.model_dump() for q in body.questions])
    db.add(survey)
    db.flush()
    audit(db, user, "survey.created", "survey", survey.id)
    db.commit()
    return view(survey)


@router.post("/surveys/{survey_id}/publish")
def publish_survey(survey_id: str, user: User = Depends(require("owner", "admin")), db: Session = Depends(get_db)):
    survey = get_survey(db, user.organization_id, survey_id)
    if survey.status != "draft":
        raise HTTPException(409, "Only draft surveys can be published")
    survey.status = "active"
    audit(db, user, "survey.published", "survey", survey.id)
    db.commit()
    return view(survey)


@router.post("/surveys/{survey_id}/responses", status_code=201)
def record_survey_response(survey_id: str, body: SurveyAnswer, user: User = Depends(require("owner", "admin", "manager", "operator")), db: Session = Depends(get_db)):
    survey = get_survey(db, user.organization_id, survey_id)
    if survey.status != "active":
        raise HTTPException(409, "Survey is not active")
    if not db.scalar(select(Contact.id).where(Contact.id == body.contact_id, Contact.organization_id == user.organization_id)):
        raise HTTPException(404, "Contact not found")
    if db.scalar(select(SurveyResponse.id).where(SurveyResponse.organization_id == user.organization_id, SurveyResponse.survey_id == survey.id, SurveyResponse.contact_id == body.contact_id)):
        raise HTTPException(409, "Contact has already responded")
    questions = {q["id"]: q for q in survey.questions}
    if set(body.answers) - set(questions):
        raise HTTPException(422, "Unknown survey answer")
    normalized = {}
    for key, question in questions.items():
        value = body.answers.get(key)
        if value is None or value == "":
            if question["required"]:
                raise HTTPException(422, f"Missing answer: {key}")
            continue
        if question["type"] == "choice" and value not in question["options"]:
            raise HTTPException(422, f"Invalid choice: {key}")
        if question["type"] == "rating" and (not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5):
            raise HTTPException(422, f"Rating must be 1-5: {key}")
        if question["type"] == "text" and (not isinstance(value, str) or len(value) > 2000):
            raise HTTPException(422, f"Invalid text: {key}")
        normalized[key] = value.strip() if isinstance(value, str) else value
    summary = "; ".join(f"{questions[key]['label']}: {value}" for key, value in normalized.items())[:2000]
    response = SurveyResponse(organization_id=user.organization_id, survey_id=survey.id, contact_id=body.contact_id, answers=normalized, summary=summary)
    db.add(response)
    db.flush()
    db.add(AgentRun(organization_id=user.organization_id, agent_type="survey", trigger_type="response", input={"survey_id": survey.id, "response_id": response.id}, output={"summary": summary}, status="completed", completed_at=now()))
    audit(db, user, "survey.response_recorded", "survey_response", response.id)
    db.commit()
    return view(response)


@router.get("/surveys/{survey_id}/report")
def survey_report(survey_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    survey = get_survey(db, user.organization_id, survey_id)
    responses = db.scalars(select(SurveyResponse).where(SurveyResponse.organization_id == user.organization_id, SurveyResponse.survey_id == survey.id)).all()
    breakdown = {}
    for question in survey.questions:
        values = [item.answers[question["id"]] for item in responses if question["id"] in item.answers]
        if question["type"] == "choice":
            breakdown[question["id"]] = {option: values.count(option) for option in question["options"]}
        elif question["type"] == "rating":
            breakdown[question["id"]] = {"count": len(values), "average": round(sum(values) / len(values), 2) if values else None}
        else:
            breakdown[question["id"]] = {"count": len(values)}
    return {"survey": view(survey), "total_responses": len(responses), "breakdown": breakdown}


def scan_followups(db: Session, organization_id: str | None = None) -> int:
    cutoff = now() - timedelta(hours=24)
    statement = select(Conversation).where(Conversation.status.in_(["active", "waiting"]))
    if organization_id:
        statement = statement.where(Conversation.organization_id == organization_id)
    count = 0
    for conversation in db.scalars(statement).all():
        latest = db.scalar(select(Message).where(Message.organization_id == conversation.organization_id, Message.conversation_id == conversation.id).order_by(Message.timestamp.desc(), Message.id.desc()).limit(1))
        if not latest or latest.direction != "inbound" or latest.timestamp.replace(tzinfo=latest.timestamp.tzinfo or timezone.utc) > cutoff:
            continue
        if db.scalar(select(Escalation.id).where(Escalation.organization_id == conversation.organization_id, Escalation.message_id == latest.id, Escalation.status == "open")):
            continue
        key = f"unanswered:{latest.id}"
        if db.scalar(select(Task.id).where(Task.organization_id == conversation.organization_id, Task.source_key == key)):
            continue
        task = Task(organization_id=conversation.organization_id, contact_id=conversation.contact_id, conversation_id=conversation.id, title="Follow up on unanswered message", description=latest.content[:500], priority="normal", due_at=now(), source_key=key)
        db.add(task)
        db.flush()
        db.add(AgentRun(organization_id=conversation.organization_id, agent_type="follow_up", trigger_type="scheduled", input={"conversation_id": conversation.id, "message_id": latest.id}, output={"task_id": task.id}, status="completed", completed_at=now()))
        count += 1
    db.commit()
    return count


@router.post("/follow-ups/scan")
def scan_own_followups(user: User = Depends(require("owner", "admin", "manager", "operator")), db: Session = Depends(get_db)):
    return {"tasks_created": scan_followups(db, user.organization_id)}


@router.get("/tasks")
def list_tasks(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [view(item) for item in db.scalars(select(Task).where(Task.organization_id == user.organization_id).order_by(Task.created_at.desc()).limit(200)).all()]


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: str, user: User = Depends(require("owner", "admin", "manager", "operator")), db: Session = Depends(get_db)):
    task = db.scalar(select(Task).where(Task.id == task_id, Task.organization_id == user.organization_id))
    if not task:
        raise HTTPException(404, "Task not found")
    task.status = "completed"
    audit(db, user, "task.completed", "task", task.id)
    db.commit()
    return view(task)


@router.post("/analytics/query")
def analytics_query(body: AnalyticsQuestion, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .phase5 import analyze_question
    result = analyze_question(db, user.organization_id, body.question)
    run = AgentRun(organization_id=user.organization_id, agent_type="analytics", trigger_type="manual", input={"question": body.question}, output=result, status="completed", completed_at=now())
    db.add(run)
    db.commit()
    return {**result, "run_id": run.id}
