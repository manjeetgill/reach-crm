import hashlib
import hmac
import json
import os

os.environ["JWT_SECRET"] = "test-secret-unique-for-this-suite"
os.environ["ENGINE_WEBHOOK_SECRET"] = "test-webhook-secret"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app import worker
from app import knowledge
from app.models import Message, OutboundJob, Task, now
from sqlalchemy import select

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(engine)

def session():
    with Session(engine) as db:
        yield db

app.dependency_overrides[get_db] = session
client = TestClient(app)

def org(name):
    response = client.post("/api/v1/organizations", json={"name": name, "owner_name": "Test Owner", "owner_email": f"{name.lower()}@example.com", "password": "test-password-123"})
    assert response.status_code == 201, response.text
    data = response.json()
    return data["organization"]["id"], {"Authorization": f"Bearer {data['token']}"}

def test_tenant_isolation_and_consent():
    org_a, a = org("Alpha")
    _, b = org("Beta")
    made = client.post("/api/v1/contacts", headers=a, json={"first_name": "Asha", "phone": "+910000000001"})
    assert made.status_code == 201
    contact = made.json()
    assert client.get("/api/v1/contacts", headers=b).json()["total"] == 0
    assert client.get(f"/api/v1/contacts/{contact['id']}", headers=b).status_code == 404
    assert client.patch(f"/api/v1/contacts/{contact['id']}", headers=b, json={"first_name": "Changed"}).status_code == 404
    assert client.post(f"/api/v1/contacts/{contact['id']}/consents", headers=b, json={"channel": "web", "status": "opt_in", "source": "form"}).status_code == 404
    conversation = client.post("/api/v1/conversations", headers=a, json={"contact_id": contact["id"], "channel": "web"}).json()
    url = f"/api/v1/conversations/{conversation['id']}/messages"
    assert client.get(f"/api/v1/conversations/{conversation['id']}", headers=b).status_code == 404
    assert client.post(url, headers=a, json={"content": "Hello"}).status_code == 403
    consent = client.post(f"/api/v1/contacts/{contact['id']}/consents", headers=a, json={"channel": "web", "status": "opt_in", "source": "form"})
    assert consent.status_code == 201
    assert client.post(url, headers=a, json={"content": "Hello"}).status_code == 201
    assert client.post(f"/api/v1/contacts/{contact['id']}/consents", headers=a, json={"channel": "web", "status": "opt_out", "source": "request"}).status_code == 201
    assert client.post(url, headers=a, json={"content": "Again"}).status_code == 403
    assert client.get("/api/v1/analytics/overview", headers=a).json()["contacts"] == 1
    assert org_a == contact["organization_id"]

def test_webhook_signature_and_idempotency():
    org_id, headers = org("Gamma")
    client.post("/api/v1/contacts", headers=headers, json={"first_name": "Mira", "phone": "+910000000002"})
    body = {"organization_id": org_id, "external_event_id": "evt-1", "contact_phone": "+910000000002", "content": "I need help"}
    raw = json.dumps(body).encode()
    signature = hmac.new(b"test-webhook-secret", raw, hashlib.sha256).hexdigest()
    url = "/api/v1/webhooks/whatsapp"
    assert client.post(url, content=raw, headers={"x-signature": "bad"}).status_code == 401
    first = client.post(url, content=raw, headers={"x-signature": signature})
    assert first.status_code == 200, first.text
    assert client.post(url, content=raw, headers={"x-signature": signature}).json()["status"] == "duplicate"
    assert len(client.get("/api/v1/conversations", headers=headers).json()) == 1

def test_outbound_queue_delivery_and_revoked_consent(monkeypatch):
    org_id, headers = org("Delta")
    contact = client.post("/api/v1/contacts", headers=headers, json={"first_name": "Leela", "phone": "+910000000003"}).json()
    consent_url = f"/api/v1/contacts/{contact['id']}/consents"
    assert client.post(consent_url, headers=headers, json={"channel": "whatsapp", "status": "opt_in", "source": "signed form"}).status_code == 201
    conversation = client.post("/api/v1/conversations", headers=headers, json={"contact_id": contact["id"], "channel": "whatsapp"}).json()
    url = f"/api/v1/conversations/{conversation['id']}/messages"
    keyed_headers = {**headers, "Idempotency-Key": "delta-message-1"}
    first = client.post(url, headers=keyed_headers, json={"content": "Hello"})
    assert first.status_code == 201
    message = first.json()
    assert message["status"] == "queued"
    assert client.post(url, headers=keyed_headers, json={"content": "Hello"}).json()["id"] == message["id"]
    assert client.post(url, headers=keyed_headers, json={"content": "Different"}).status_code == 409
    monkeypatch.setattr(worker, "SessionLocal", lambda: Session(engine))
    calls = []
    def fake_send(channel, **payload):
        calls.append((channel, payload))
        return {"status": "sent", "external_id": "provider-1"}
    monkeypatch.setattr(worker.gateway, "send_text", fake_send)
    assert worker.process_once() is True
    assert len(calls) == 1 and calls[0][0] == "whatsapp"
    assert calls[0][1]["idempotency_key"] == "delta-message-1"
    assert client.get(f"/api/v1/conversations/{conversation['id']}", headers=headers).json()["messages"][0]["status"] == "sent"
    delivery = {"organization_id": org_id, "external_event_id": "delivery-1", "message_id": message["id"], "status": "delivered", "external_id": "provider-1"}
    raw = json.dumps(delivery).encode()
    signature = hmac.new(b"test-webhook-secret", raw, hashlib.sha256).hexdigest()
    callback = client.post("/api/v1/webhooks/whatsapp/delivery", content=raw, headers={"x-signature": signature})
    assert callback.status_code == 200 and callback.json()["status"] == "delivered"
    assert client.post("/api/v1/webhooks/whatsapp/delivery", content=raw, headers={"x-signature": signature}).json()["status"] == "duplicate"

    second = client.post(url, headers=headers, json={"content": "Another message"}).json()
    assert second["status"] == "queued"
    assert client.post(consent_url, headers=headers, json={"channel": "whatsapp", "status": "opt_out", "source": "contact request"}).status_code == 201
    assert worker.process_once() is True
    with Session(engine) as db:
        assert db.scalar(select(Message).where(Message.id == second["id"])).status == "cancelled"
        assert db.scalar(select(OutboundJob).where(OutboundJob.message_id == second["id"])).status == "cancelled"
    assert len(calls) == 1


def test_web_chat_token_is_scoped_to_one_conversation():
    _, staff = org("WebTenant")
    contact = client.post("/api/v1/contacts", headers=staff, json={"first_name": "Guest"}).json()
    conversation = client.post("/api/v1/conversations", headers=staff, json={"contact_id": contact["id"], "channel": "web"}).json()
    other = client.post("/api/v1/conversations", headers=staff, json={"contact_id": contact["id"], "channel": "web"}).json()
    token = client.post(f"/api/v1/conversations/{conversation['id']}/web-token", headers=staff).json()["token"]
    guest = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me", headers=guest).status_code == 401
    assert client.get(f"/api/v1/web-chat/{other['id']}", headers=guest).status_code == 404
    result = client.post(f"/api/v1/web-chat/{conversation['id']}/messages", headers=guest, json={"content": "Can you help?"})
    assert result.status_code == 201
    assert client.get(f"/api/v1/web-chat/{conversation['id']}", headers=guest).json()["messages"][0]["content"] == "Can you help?"


def test_knowledge_approval_tenant_isolation_and_answer_invalidation():
    _, a = org("KnowledgeA")
    _, b = org("KnowledgeB")
    document = client.post("/api/v1/knowledge/documents", headers=a, json={"title": "Opening hours", "source": "Approved office notice", "content": "Our community office opens Monday to Friday from 9 AM to 5 PM. The office is closed on Sunday."})
    assert document.status_code == 201, document.text
    doc_id = document.json()["id"]
    question = {"question": "When does the office open?"}
    assert client.post("/api/v1/knowledge/search", headers=a, json=question).json()["passages"] == []
    assert client.post(f"/api/v1/knowledge/documents/{doc_id}/approve", headers=b).status_code == 404
    assert client.post(f"/api/v1/knowledge/documents/{doc_id}/approve", headers=a).status_code == 200
    assert client.post("/api/v1/knowledge/search", headers=b, json=question).json()["passages"] == []
    payload = {**question, "idempotency_key": "opening-hours-1"}
    result = client.post("/api/v1/knowledge/ask", headers=a, json=payload)
    assert result.status_code == 200, result.text
    answer = result.json()
    assert answer["mode"] == "extractive" and answer["citations"][0]["document_id"] == doc_id
    assert client.post("/api/v1/knowledge/ask", headers=a, json=payload).json() == answer
    assert client.post("/api/v1/knowledge/ask", headers=b, json=payload).json()["mode"] == "abstain"
    revised = client.put(f"/api/v1/knowledge/documents/{doc_id}", headers=a, json={"title": "Opening hours", "source": "Revised notice", "content": "Our community office opens Tuesday to Saturday from 10 AM to 4 PM. It is closed on Monday."})
    assert revised.status_code == 200 and revised.json()["status"] == "draft"
    assert client.post("/api/v1/knowledge/ask", headers=a, json=payload).status_code == 409
    assert client.post("/api/v1/knowledge/ask", headers=a, json={**question, "idempotency_key": "opening-hours-2"}).json()["mode"] == "abstain"


def test_knowledge_html_upload_extracts_visible_text_only():
    _, headers = org("KnowledgeUpload")
    response = client.post(
        "/api/v1/knowledge/documents/upload",
        headers=headers,
        data={"title": "Service desk", "source": "Synthetic help page"},
        files={"file": ("help.html", b"<html><script>secret-instruction</script><p>The service desk opens at 10 AM every weekday.</p></html>", "text/html")},
    )
    assert response.status_code == 201, response.text
    document = client.get(f"/api/v1/knowledge/documents/{response.json()['id']}", headers=headers).json()
    assert "service desk opens" in document["content"]
    assert "secret-instruction" not in document["content"]


def test_model_answer_requires_a_retrieved_citation(monkeypatch):
    _, headers = org("KnowledgeModel")
    document = client.post("/api/v1/knowledge/documents", headers=headers, json={"title": "Community hours", "content": "The community centre opens at 8 AM on Tuesday and closes at 4 PM."}).json()
    client.post(f"/api/v1/knowledge/documents/{document['id']}/approve", headers=headers)

    class ValidModel:
        def generate(self, question, passages):
            return knowledge.Generated(claims=[knowledge.Claim(text="The community centre opens at 8 AM on Tuesday.", sources=[passages[0]["id"]])]), {"model": "test-model", "tokens": 12}

    monkeypatch.setattr(knowledge, "configured_provider", lambda: ValidModel())
    result = client.post("/api/v1/knowledge/ask", headers=headers, json={"question": "When does the community centre open?", "idempotency_key": "model-valid-1"})
    assert result.status_code == 200 and result.json()["mode"] == "model"
    assert result.json()["citations"][0]["document_id"] == document["id"]

    class InvalidModel:
        def generate(self, question, passages):
            return knowledge.Generated(claims=[knowledge.Claim(text="A made-up answer", sources=["another-tenant-source"])]), {"model": "test-model", "tokens": 12}

    monkeypatch.setattr(knowledge, "configured_provider", lambda: InvalidModel())
    rejected = client.post("/api/v1/knowledge/ask", headers=headers, json={"question": "When does the community centre open?", "idempotency_key": "model-invalid-1"})
    assert rejected.status_code == 503


def test_conversation_suggest_reply_uses_approved_sources_without_sending():
    _, headers = org("SuggestReply")
    contact = client.post("/api/v1/contacts", headers=headers, json={"first_name": "Guest"}).json()
    conversation = client.post("/api/v1/conversations", headers=headers, json={"contact_id": contact["id"], "channel": "web"}).json()
    path = f"/api/v1/conversations/{conversation['id']}"
    assert client.post(f"{path}/suggest-reply", headers=headers, json={"idempotency_key": "suggest-empty"}).status_code == 409
    document = client.post("/api/v1/knowledge/documents", headers=headers, json={"title": "Office notice", "content": "The community office opens at 9 AM on Monday."}).json()
    client.post(f"/api/v1/knowledge/documents/{document['id']}/approve", headers=headers)
    token = client.post(f"{path}/web-token", headers=headers).json()["token"]
    client.post(f"/api/v1/web-chat/{conversation['id']}/messages", headers={"Authorization": f"Bearer {token}"}, json={"content": "When does the community office open?"})
    response = client.post(f"{path}/suggest-reply", headers=headers, json={"idempotency_key": "suggest-1"})
    assert response.status_code == 200, response.text
    assert response.json()["draft"] is True
    assert response.json()["citations"][0]["document_id"] == document["id"]
    assert len(client.get(path, headers=headers).json()["messages"]) == 1


def test_nemotron_provider_reads_private_config_and_sets_plain_json_mode(monkeypatch, tmp_path):
    config = tmp_path / "platform.json"
    config.write_text(json.dumps({"NVIDIA_API_KEY": "test-private-key"}))
    monkeypatch.setenv("RAG_MODEL_BASE_URL", "https://integrate.api.nvidia.com/v1")
    monkeypatch.setenv("RAG_MODEL_NAME", "nvidia/nemotron-3-ultra-550b-a55b")
    monkeypatch.setenv("RAG_MODEL_KEY_FILE", str(config))
    monkeypatch.delenv("RAG_MODEL_API_KEY", raising=False)
    calls = []
    def fake_handler(request):
        calls.append((request.headers, json.loads(request.content)))
        return knowledge.httpx.Response(200, json={"choices": [{"message": {"content": '{"claims":[],"missing":[]}'}}], "usage": {"total_tokens": 4}})
    transport = knowledge.httpx.MockTransport(fake_handler)
    original = knowledge.httpx.Client
    monkeypatch.setattr(knowledge.httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    provider = knowledge.configured_provider()
    generated, metrics = provider.generate("When?", [])
    assert generated.claims == [] and metrics["tokens"] == 4
    assert calls[0][1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert calls[0][0]["authorization"] == "Bearer test-private-key"


def test_campaign_requires_review_and_uses_only_opted_in_contacts():
    _, owner = org("CampaignTenant")
    _, other = org("CampaignOther")
    first = client.post("/api/v1/contacts", headers=owner, json={"first_name": "Allowed"}).json()
    second = client.post("/api/v1/contacts", headers=owner, json={"first_name": "Skipped"}).json()
    client.post(f"/api/v1/contacts/{first['id']}/consents", headers=owner, json={"channel": "web", "status": "opt_in", "source": "signed form"})
    created = client.post("/api/v1/campaigns", headers=owner, json={"name": "Community update", "channel": "web", "content": "Community event on Friday.", "contact_ids": [first["id"], second["id"]]})
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]
    assert client.get(f"/api/v1/campaigns/{campaign_id}", headers=other).status_code == 404
    assert client.post(f"/api/v1/campaigns/{campaign_id}/start", headers=owner).status_code == 409
    assert client.post(f"/api/v1/campaigns/{campaign_id}/submit", headers=owner).status_code == 200
    assert client.post(f"/api/v1/campaigns/{campaign_id}/approve", headers=owner).status_code == 200
    started = client.post(f"/api/v1/campaigns/{campaign_id}/start", headers=owner)
    assert started.status_code == 200, started.text
    assert started.json()["recipient_statuses"] == {"sent": 1, "skipped_no_consent": 1}
    assert client.post(f"/api/v1/campaigns/{campaign_id}/start", headers=owner).status_code == 409


def test_survey_response_validation_and_controlled_analytics():
    _, owner = org("SurveyTenant")
    contact = client.post("/api/v1/contacts", headers=owner, json={"first_name": "Respondent"}).json()
    survey = client.post("/api/v1/surveys", headers=owner, json={"name": "Service feedback", "questions": [{"id": "rating", "label": "Rate the service", "type": "rating"}, {"id": "choice", "label": "Was it helpful?", "type": "choice", "options": ["Yes", "No"]}]}).json()
    path = f"/api/v1/surveys/{survey['id']}"
    assert client.post(f"{path}/responses", headers=owner, json={"contact_id": contact["id"], "answers": {"rating": 5, "choice": "Yes"}}).status_code == 409
    assert client.post(f"{path}/publish", headers=owner).status_code == 200
    assert client.post(f"{path}/responses", headers=owner, json={"contact_id": contact["id"], "answers": {"rating": 7, "choice": "Yes"}}).status_code == 422
    response = client.post(f"{path}/responses", headers=owner, json={"contact_id": contact["id"], "answers": {"rating": 5, "choice": "Yes"}})
    assert response.status_code == 201, response.text
    assert client.post(f"{path}/responses", headers=owner, json={"contact_id": contact["id"], "answers": {"rating": 5, "choice": "Yes"}}).status_code == 409
    report = client.get(f"{path}/report", headers=owner).json()
    assert report["breakdown"]["rating"]["average"] == 5
    assert report["breakdown"]["choice"]["Yes"] == 1
    analytics = client.post("/api/v1/analytics/query", headers=owner, json={"question": "How many contacts and survey responses?"})
    assert analytics.status_code == 200
    assert analytics.json()["metrics"] == {"contact": 1, "survey": 1}
    assert client.post("/api/v1/analytics/query", headers=owner, json={"question": "How many surveys?"}).json()["metrics"] == {"survey": 1}


def test_followup_scan_is_idempotent_and_tenant_scoped():
    _, owner = org("FollowupTenant")
    _, other = org("FollowupOther")
    contact = client.post("/api/v1/contacts", headers=owner, json={"first_name": "Guest"}).json()
    conversation = client.post("/api/v1/conversations", headers=owner, json={"contact_id": contact["id"], "channel": "web"}).json()
    token = client.post(f"/api/v1/conversations/{conversation['id']}/web-token", headers=owner).json()["token"]
    incoming = client.post(f"/api/v1/web-chat/{conversation['id']}/messages", headers={"Authorization": f"Bearer {token}"}, json={"content": "I have a question about the office schedule"}).json()
    from datetime import timedelta
    with Session(engine) as db:
        message = db.get(Message, incoming["id"])
        message.timestamp = now() - timedelta(hours=25)
        db.commit()
    assert client.post("/api/v1/follow-ups/scan", headers=owner).json()["tasks_created"] == 1
    assert client.post("/api/v1/follow-ups/scan", headers=owner).json()["tasks_created"] == 0
    assert len(client.get("/api/v1/tasks", headers=owner).json()) == 1
    assert client.get("/api/v1/tasks", headers=other).json() == []


def test_hygiene_findings_are_reviewable_and_tenant_scoped():
    _, owner = org("HygieneTenant")
    _, other = org("HygieneOther")
    for name in ("Asha", "Mira"):
        assert client.post("/api/v1/contacts", headers=owner, json={"first_name": name, "email": "shared@example.com"}).status_code == 201
    scan = client.post("/api/v1/hygiene/scan", headers=owner)
    assert scan.status_code == 200 and scan.json()["issues_created"]["duplicate_email"] == 1
    assert client.post("/api/v1/hygiene/scan", headers=owner).json()["issues_created"] == {}
    issue = client.get("/api/v1/hygiene/issues", headers=owner).json()[0]
    assert issue["kind"] == "duplicate_email" and issue["related_contact_name"]
    assert client.get("/api/v1/hygiene/issues", headers=other).json() == []
    assert client.post(f"/api/v1/hygiene/issues/{issue['id']}/dismiss", headers=other).status_code == 404
    assert client.post(f"/api/v1/hygiene/issues/{issue['id']}/dismiss", headers=owner).status_code == 200
    assert client.get("/api/v1/hygiene/issues", headers=owner).json() == []


def test_inbound_human_request_creates_and_resolves_escalation():
    _, owner = org("EscalationTenant")
    contact = client.post("/api/v1/contacts", headers=owner, json={"first_name": "Guest"}).json()
    conversation = client.post("/api/v1/conversations", headers=owner, json={"contact_id": contact["id"], "channel": "web"}).json()
    path = f"/api/v1/conversations/{conversation['id']}"
    token = client.post(f"{path}/web-token", headers=owner).json()["token"]
    response = client.post(f"/api/v1/web-chat/{conversation['id']}/messages", headers={"Authorization": f"Bearer {token}"}, json={"content": "I need to speak to a human supervisor"})
    assert response.status_code == 201
    escalations = client.get("/api/v1/escalations", headers=owner).json()
    assert len(escalations) == 1 and escalations[0]["priority"] == "high"
    assert "human supervisor" in escalations[0]["summary"]
    assert client.get(path, headers=owner).json()["conversation"]["status"] == "waiting"
    assert client.post("/api/v1/follow-ups/scan", headers=owner).json()["tasks_created"] == 0
    assert client.post(f"/api/v1/escalations/{escalations[0]['id']}/resolve", headers=owner).status_code == 200
    assert client.get("/api/v1/escalations", headers=owner).json() == []
    assert client.get(path, headers=owner).json()["conversation"]["status"] == "active"


def test_workspace_discovery_dashboard_and_usage_are_scoped():
    credentials = {"owner_name": "Shared Owner", "owner_email": "multi@example.com", "password": "shared-password-123"}
    a = client.post("/api/v1/organizations", json={"name": "Multi A", **credentials}).json()
    b = client.post("/api/v1/organizations", json={"name": "Multi B", **credentials}).json()
    headers = {"Authorization": f"Bearer {a['token']}"}
    found = client.post("/api/v1/auth/workspaces", json={"email": credentials["owner_email"], "password": credentials["password"]})
    assert found.status_code == 200
    assert {item["id"] for item in found.json()} == {a["organization"]["id"], b["organization"]["id"]}
    assert client.post("/api/v1/auth/workspaces", json={"email": credentials["owner_email"], "password": "wrong"}).status_code == 401
    assert client.get("/api/v1/organizations/current", headers=headers).json()["id"] == a["organization"]["id"]
    client.post("/api/v1/contacts", headers=headers, json={"first_name": "Local"})
    dashboard = client.get("/api/v1/analytics/dashboard", headers=headers).json()
    assert dashboard["contacts"] == 1 and len(dashboard["interaction_series"]) == 14
    assert client.post("/api/v1/analytics/query", headers=headers, json={"question": "Show channel distribution and interaction trend over the last 14 days"}).status_code == 200
    usage = client.get("/api/v1/usage/summary", headers=headers).json()
    assert usage["billing_status"] == "not_configured" and usage["amount_due"] is None
