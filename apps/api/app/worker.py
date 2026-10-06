"""Durable database-backed outbound worker.

Run with ``python -m app.worker``. PostgreSQL workers claim jobs using
``FOR UPDATE SKIP LOCKED``; leases allow a job to recover after a crash.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import timedelta

from sqlalchemy import and_, or_, select

from .db import SessionLocal
from .gateway import gateway
from .models import AuditLog, Campaign, CampaignRecipient, Consent, Conversation, Message, OutboundJob, now

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reach.worker")
MAX_ATTEMPTS = 5


def claim_job() -> str | None:
    with SessionLocal() as db:
        current = now()
        job = db.scalar(
            select(OutboundJob)
            .where(
                or_(
                    and_(OutboundJob.status.in_(["pending", "retry"]), OutboundJob.run_at <= current),
                    and_(OutboundJob.status == "processing", OutboundJob.lease_until < current),
                )
            )
            .order_by(OutboundJob.run_at, OutboundJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return None
        job.status = "processing"
        job.attempts += 1
        job.lease_until = current + timedelta(seconds=60)
        db.commit()
        return job.id


def _consent_allowed(db, job: OutboundJob, conversation: Conversation) -> bool:
    latest = db.scalar(
        select(Consent)
        .where(
            Consent.organization_id == job.organization_id,
            Consent.contact_id == conversation.contact_id,
            Consent.channel == conversation.channel,
        )
        .order_by(Consent.captured_at.desc(), Consent.id.desc())
        .limit(1)
    )
    return bool(latest and latest.status == "opt_in")


def process_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(OutboundJob, job_id)
        if not job or job.status != "processing":
            return
        message = db.scalar(select(Message).where(Message.id == job.message_id, Message.organization_id == job.organization_id))
        conversation = db.scalar(select(Conversation).where(Conversation.id == message.conversation_id, Conversation.organization_id == job.organization_id)) if message else None
        if not message or not conversation:
            job.status = "failed"
            job.last_error = "Related message or conversation missing"
            db.commit()
            return
        recipient = db.scalar(select(CampaignRecipient).where(CampaignRecipient.organization_id == job.organization_id, CampaignRecipient.message_id == message.id))
        if recipient:
            campaign = db.scalar(select(Campaign).where(Campaign.id == recipient.campaign_id, Campaign.organization_id == job.organization_id))
            if not campaign or campaign.status not in {"running", "paused"}:
                job.status = "cancelled"
                message.status = "cancelled"
                db.commit()
                return
            if campaign.status == "paused":
                job.status = "retry"
                job.attempts -= 1
                job.run_at = now() + timedelta(minutes=1)
                job.lease_until = None
                db.commit()
                return
        if not _consent_allowed(db, job, conversation):
            job.status = "cancelled"
            job.lease_until = None
            message.status = "cancelled"
            db.add(AuditLog(organization_id=job.organization_id, action="message.cancelled", resource_type="message", resource_id=message.id, after={"reason": "channel consent withdrawn"}))
            db.commit()
            return
        payload = dict(
            organization_id=job.organization_id,
            contact_id=conversation.contact_id,
            conversation_id=conversation.id,
            content=message.content,
            idempotency_key=message.idempotency_key or message.id,
        )
        channel = conversation.channel

    try:
        result = gateway.send_text(channel, **payload)
    except Exception as exc:
        with SessionLocal() as db:
            job = db.get(OutboundJob, job_id)
            if not job:
                return
            message = db.get(Message, job.message_id)
            job.last_error = str(exc)[:500]
            job.lease_until = None
            if job.attempts >= MAX_ATTEMPTS:
                job.status = "failed"
                if message and message.status == "queued":
                    message.status = "failed"
                db.add(AuditLog(organization_id=job.organization_id, action="message.failed", resource_type="message", resource_id=job.message_id, after={"attempts": job.attempts, "error": job.last_error}))
            else:
                job.status = "retry"
                job.run_at = now() + timedelta(seconds=min(2 ** job.attempts, 300))
            db.commit()
        logger.warning("outbound job %s failed on attempt %s", job_id, job.attempts)
        return

    with SessionLocal() as db:
        job = db.get(OutboundJob, job_id)
        if not job:
            return
        message = db.get(Message, job.message_id)
        job.status = "completed"
        job.lease_until = None
        job.last_error = None
        if message:
            message.external_id = result.get("external_id")
            ranking = {"queued": 0, "sent": 1, "delivered": 2}
            if ranking.get(result["status"], 0) > ranking.get(message.status, 0):
                message.status = result["status"]
            db.add(AuditLog(organization_id=job.organization_id, action="message.dispatched", resource_type="message", resource_id=message.id, after={"status": message.status, "external_id": message.external_id}))
        recipient = db.scalar(select(CampaignRecipient).where(CampaignRecipient.organization_id == job.organization_id, CampaignRecipient.message_id == job.message_id))
        if recipient and message:
            recipient.status = message.status
        db.commit()
    logger.info("outbound job %s completed", job_id)


def process_once() -> bool:
    job_id = claim_job()
    if not job_id:
        return False
    process_job(job_id)
    return True


def main():
    logger.info("outbound worker started")
    next_followup_scan = 0.0
    while True:
        if time.monotonic() >= next_followup_scan:
            from .phase4 import scan_followups
            try:
                with SessionLocal() as db:
                    scan_followups(db)
            except Exception:
                logger.exception("follow-up scan failed")
            next_followup_scan = time.monotonic() + 60
        if not process_once():
            time.sleep(1)


if __name__ == "__main__":
    main()
