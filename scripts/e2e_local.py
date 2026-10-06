"""Exercise all locally implemented phases against real HTTP and the outbound worker.

Run only against a disposable, migrated database and the synthetic fake gateway.
The script creates test organizations and never calls an external model.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
PYTHON = ROOT / ".venv" / "bin" / "python"
API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18002"
GATEWAY = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:18003"
DATABASE = sys.argv[3] if len(sys.argv) > 3 else "/private/tmp/reach-crm-e2e-20261005.db"
SECRET = "e2e-webhook-secret"
client = httpx.Client(base_url=API, timeout=15)
checks = []


def check(name: str, condition: bool):
    if not condition:
        raise AssertionError(name)
    checks.append(name)
    print("PASS", name, flush=True)


def call(method, path, *, token=None, expected=200, **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = "Bearer " + token
    response = client.request(method, path, headers=headers, **kwargs)
    if response.status_code != expected:
        raise AssertionError(f"{method} {path}: expected {expected}, got {response.status_code}: {response.text[:400]}")
    return response.json()


def signed(path, payload, expected=200):
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return call("POST", path, content=body, headers={"x-signature": signature, "content-type": "application/json"}, expected=expected)


def run_worker_once():
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": f"sqlite:////{DATABASE.lstrip('/')}",
        "JWT_SECRET": "e2e-local-jwt-secret-at-least-32-bytes",
        "WHATSAPP_ENGINE_URL": GATEWAY,
        "VOICE_ENGINE_URL": GATEWAY,
        "ENGINE_API_TOKEN": "e2e-gateway-token",
        "RAG_MODEL_BASE_URL": "", "RAG_MODEL_NAME": "", "RAG_MODEL_KEY_FILE": "", "RAG_MODEL_API_KEY": "",
    })
    result = subprocess.run([str(PYTHON), "-c", "from app.worker import process_once; print(int(process_once()))"], cwd=API_DIR, env=env, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise AssertionError("Worker failed: " + result.stderr[-500:])
    return result.stdout.strip() == "1"


def main():
    check("API health", call("GET", "/health")["status"] == "ok")
    suffix = uuid.uuid4().hex[:8]
    credentials = {"owner_name": "Synthetic Owner", "owner_email": f"e2e-{suffix}@example.com", "password": "synthetic-password-123"}
    first = call("POST", "/api/v1/organizations", json={"name": f"E2E A {suffix}", **credentials}, expected=201)
    second = call("POST", "/api/v1/organizations", json={"name": f"E2E B {suffix}", **credentials}, expected=201)
    org, token, other_token = first["organization"]["id"], first["token"], second["token"]
    found = call("POST", "/api/v1/auth/workspaces", json={"email": credentials["owner_email"], "password": credentials["password"]})
    check("multi-workspace discovery", {row["id"] for row in found} == {org, second["organization"]["id"]})
    user = call("POST", "/api/v1/users", token=token, json={"name": "Synthetic Operator", "email": f"operator-{suffix}@example.com", "password": "operator-password-123", "role": "operator"}, expected=201)
    operator_token = call("POST", "/api/v1/auth/login", json={"organization_id": org, "email": user["email"], "password": "operator-password-123"})["token"]
    check("authentication and RBAC", call("GET", "/api/v1/auth/me", token=operator_token)["role"] == "operator" and client.post(API + "/api/v1/hygiene/scan", headers={"Authorization": "Bearer " + operator_token}).status_code == 403)

    a = call("POST", "/api/v1/contacts", token=token, json={"first_name": "Asha", "phone": "+919000000001", "email": "duplicate@example.com"}, expected=201)
    b = call("POST", "/api/v1/contacts", token=token, json={"first_name": "Bela", "phone": "+919000000002", "email": "duplicate@example.com"}, expected=201)
    imported = call("POST", "/api/v1/contacts/import", token=token, files={"file": ("contacts.csv", b"first_name,email\nChinmay,chinmay@example.com\nDevi,devi@example.com\n", "text/csv")})
    check("contacts, CSV import, search", imported["imported"] == 2 and call("GET", "/api/v1/contacts?q=Asha", token=token)["total"] == 1)
    check("tenant isolation", client.get(API + f"/api/v1/contacts/{a['id']}", headers={"Authorization": "Bearer " + other_token}).status_code == 404)
    for channel in ("web", "whatsapp", "voice"):
        call("POST", f"/api/v1/contacts/{a['id']}/consents", token=token, json={"channel": channel, "status": "opt_in", "source": "synthetic e2e form"}, expected=201)
    check("consent history", len(call("GET", f"/api/v1/contacts/{a['id']}", token=token)["consents"]) == 3)

    doc = call("POST", "/api/v1/knowledge/documents", token=token, json={"title": "Synthetic office hours", "source": "Synthetic notice", "content": "The local community office opens Monday to Friday from 9 AM to 5 PM. It is closed on Sunday."}, expected=201)
    question = {"question": "When does the local community office open?", "idempotency_key": "e2e-knowledge-" + suffix}
    check("unapproved knowledge is excluded", call("POST", "/api/v1/knowledge/ask", token=token, json=question)["mode"] == "abstain")
    call("POST", f"/api/v1/knowledge/documents/{doc['id']}/approve", token=token)
    answer = call("POST", "/api/v1/knowledge/ask", token=token, json={**question, "idempotency_key": "e2e-approved-" + suffix})
    check("grounded RAG with citations", answer["mode"] == "extractive" and answer["citations"][0]["document_id"] == doc["id"])
    check("unsupported question abstains", call("POST", "/api/v1/knowledge/ask", token=token, json={"question": "What is the spaceship launch date?", "idempotency_key": "e2e-unsupported-" + suffix})["mode"] == "abstain")

    web = call("POST", "/api/v1/conversations", token=token, json={"contact_id": a["id"], "channel": "web"}, expected=201)
    web_token = call("POST", f"/api/v1/conversations/{web['id']}/web-token", token=token)["token"]
    guest = {"Authorization": "Bearer " + web_token}
    call("POST", f"/api/v1/web-chat/{web['id']}/messages", headers=guest, json={"content": "When does the local community office open?"}, expected=201)
    draft = call("POST", f"/api/v1/conversations/{web['id']}/suggest-reply", token=token, json={"idempotency_key": "e2e-suggestion-" + suffix})
    check("conversation RAG draft", draft["draft"] and draft["citations"] and len(call("GET", f"/api/v1/conversations/{web['id']}", token=token)["messages"]) == 1)
    call("POST", f"/api/v1/conversations/{web['id']}/messages", token=operator_token, json={"content": draft["answer"]}, expected=201)
    check("operator web reply", len(call("GET", f"/api/v1/conversations/{web['id']}", token=token)["messages"]) == 2)
    call("POST", f"/api/v1/web-chat/{web['id']}/messages", headers=guest, json={"content": "Please let me speak to a human supervisor"}, expected=201)
    escalation = call("GET", "/api/v1/escalations", token=token)[0]
    check("escalation creates human task", escalation["priority"] == "high" and call("GET", f"/api/v1/conversations/{web['id']}", token=token)["conversation"]["status"] == "waiting")
    call("POST", f"/api/v1/escalations/{escalation['id']}/resolve", token=operator_token)
    check("escalation resolution", call("GET", "/api/v1/escalations", token=token) == [])

    inbound = signed("/api/v1/webhooks/whatsapp", {"organization_id": org, "external_event_id": "e2e-wa-" + suffix, "contact_phone": a["phone"], "content": "Synthetic WhatsApp hello"})
    wa_id = inbound["conversation_id"]
    check("signed WhatsApp webhook", inbound["status"] == "received" and call("GET", f"/api/v1/conversations/{wa_id}", token=token)["messages"][0]["direction"] == "inbound")
    queued = call("POST", f"/api/v1/conversations/{wa_id}/messages", token=token, json={"content": "Synthetic WhatsApp reply"}, expected=201)
    check("outbound queued", queued["status"] == "queued")
    check("worker dispatches through local gateway", run_worker_once() and call("GET", f"/api/v1/conversations/{wa_id}", token=token)["messages"][-1]["status"] == "sent")
    signed("/api/v1/webhooks/whatsapp/delivery", {"organization_id": org, "external_event_id": "e2e-delivery-" + suffix, "message_id": queued["id"], "status": "delivered", "external_id": "fake-delivered"})
    check("delivery callback persists status", call("GET", f"/api/v1/conversations/{wa_id}", token=token)["messages"][-1]["status"] == "delivered")
    second_queued = call("POST", f"/api/v1/conversations/{wa_id}/messages", token=token, json={"content": "Should be cancelled"}, expected=201)
    call("POST", f"/api/v1/contacts/{a['id']}/consents", token=token, json={"channel": "whatsapp", "status": "opt_out", "source": "synthetic withdrawal"}, expected=201)
    run_worker_once()
    check("opt-out cancels queued send", any(item["id"] == second_queued["id"] and item["status"] == "cancelled" for item in call("GET", f"/api/v1/conversations/{wa_id}", token=token)["messages"]))
    voice = signed("/api/v1/webhooks/voice", {"organization_id": org, "external_event_id": "e2e-voice-" + suffix, "contact_phone": a["phone"], "content": "Synthetic voice transcript"})
    voice_queued = call("POST", f"/api/v1/conversations/{voice['conversation_id']}/messages", token=token, json={"content": "Synthetic voice text response"}, expected=201)
    run_worker_once()
    check("voice transcript and text adapter", any(item["id"] == voice_queued["id"] and item["status"] == "sent" for item in call("GET", f"/api/v1/conversations/{voice['conversation_id']}", token=token)["messages"]))

    web_campaign = call("POST", "/api/v1/campaigns", token=token, json={"name": "Synthetic web update", "channel": "web", "content": "The community meeting is on Friday.", "contact_ids": [a["id"], b["id"]]}, expected=201)
    cid = web_campaign["id"]
    check("campaign requires approval", client.post(API + f"/api/v1/campaigns/{cid}/start", headers={"Authorization": "Bearer " + token}).status_code == 409)
    call("POST", f"/api/v1/campaigns/{cid}/submit", token=token)
    call("POST", f"/api/v1/campaigns/{cid}/approve", token=token)
    result = call("POST", f"/api/v1/campaigns/{cid}/start", token=token)
    check("campaign consent filtering", result["recipient_statuses"] == {"sent": 1, "skipped_no_consent": 1})
    call("POST", f"/api/v1/contacts/{a['id']}/consents", token=token, json={"channel": "whatsapp", "status": "opt_in", "source": "synthetic renewed form"}, expected=201)
    wa_campaign = call("POST", "/api/v1/campaigns", token=token, json={"name": "Synthetic WhatsApp update", "channel": "whatsapp", "content": "The community meeting is on Saturday.", "contact_ids": [a["id"]]}, expected=201)
    call("POST", f"/api/v1/campaigns/{wa_campaign['id']}/submit", token=token)
    call("POST", f"/api/v1/campaigns/{wa_campaign['id']}/approve", token=token)
    call("POST", f"/api/v1/campaigns/{wa_campaign['id']}/start", token=token)
    call("POST", f"/api/v1/campaigns/{wa_campaign['id']}/pause", token=token)
    call("POST", f"/api/v1/campaigns/{wa_campaign['id']}/resume", token=token)
    run_worker_once()
    check("campaign pause/resume and queue", call("GET", f"/api/v1/campaigns/{wa_campaign['id']}", token=token)["recipient_statuses"].get("sent") == 1)

    survey = call("POST", "/api/v1/surveys", token=token, json={"name": "Synthetic feedback", "questions": [{"id": "rating", "label": "Rate the service", "type": "rating"}, {"id": "helpful", "label": "Was it helpful?", "type": "choice", "options": ["Yes", "No"]}]}, expected=201)
    call("POST", f"/api/v1/surveys/{survey['id']}/publish", token=token)
    call("POST", f"/api/v1/surveys/{survey['id']}/responses", token=operator_token, json={"contact_id": a["id"], "answers": {"rating": 4, "helpful": "Yes"}}, expected=201)
    check("survey report", call("GET", f"/api/v1/surveys/{survey['id']}/report", token=token)["breakdown"]["rating"]["average"] == 4)
    hygiene = call("POST", "/api/v1/hygiene/scan", token=token)
    check("hygiene duplicate proposal", hygiene["issues_created"].get("duplicate_email") == 1)
    issue = call("GET", "/api/v1/hygiene/issues", token=token)[0]
    call("POST", f"/api/v1/hygiene/issues/{issue['id']}/dismiss", token=token)
    check("hygiene review", call("GET", "/api/v1/hygiene/issues", token=token) == [])

    follow = call("POST", "/api/v1/conversations", token=token, json={"contact_id": b["id"], "channel": "web"}, expected=201)
    follow_token = call("POST", f"/api/v1/conversations/{follow['id']}/web-token", token=token)["token"]
    old_message = call("POST", f"/api/v1/web-chat/{follow['id']}/messages", headers={"Authorization": "Bearer " + follow_token}, json={"content": "I have a question about local office hours"}, expected=201)
    with sqlite3.connect(DATABASE) as db:
        db.execute("update messages set timestamp = datetime('now','-25 hours') where id = ?", (old_message["id"],))
        db.commit()
    check("follow-up task", call("POST", "/api/v1/follow-ups/scan", token=token)["tasks_created"] == 1)
    dashboard = call("GET", "/api/v1/analytics/dashboard", token=token)
    analytics = call("POST", "/api/v1/analytics/query", token=token, json={"question": "Show interaction trends over the last 14 days by channel"})
    check("advanced dashboard and natural-language analytics", len(dashboard["interaction_series"]) == 14 and analytics["breakdown"] and len(analytics["series"]) == 14)
    usage = call("GET", "/api/v1/usage/summary", token=token)
    check("usage metering without billing", usage["total_messages"] > 0 and usage["measured_events"]["knowledge_query"] >= 1 and usage["amount_due"] is None)
    with sqlite3.connect(DATABASE) as db:
        audit_count = db.execute("select count(*) from audit_logs where organization_id = ?", (org,)).fetchone()[0]
        run_types = {row[0] for row in db.execute("select distinct agent_type from agent_runs where organization_id = ?", (org,))}
    check("audit and agent traces", audit_count > 10 and {"knowledge", "survey", "analytics", "data_hygiene", "escalation", "follow_up"} <= run_types)
    check("fake engine saw only synthetic sends", httpx.get(GATEWAY + "/events").json()["count"] >= 3)
    print(f"E2E_HTTP_COMPLETE {len(checks)} checks; org={org}; email={credentials['owner_email']}; password={credentials['password']}", flush=True)


if __name__ == "__main__":
    main()
