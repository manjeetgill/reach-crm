"""Create an idempotent, synthetic demo workspace with 1,000 contacts."""
import os
from sqlalchemy import select

from .db import SessionLocal
from .models import Consent, Contact, Conversation, Interaction, Message, Organization, User
from .security import passwords


def main():
    email = os.getenv("SEED_ADMIN_EMAIL", "").lower()
    password = os.getenv("SEED_ADMIN_PASSWORD", "")
    if not email or len(password) < 12:
        raise SystemExit("Set SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD (12+ characters)")
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.email == email))
        if existing:
            print(f"Demo workspace already exists: {existing.organization_id}")
            return
        org = Organization(name="Demo Organization", type="community")
        db.add(org)
        db.flush()
        db.add(User(organization_id=org.id, name="Demo Admin", email=email, password_hash=passwords.hash(password), role="owner"))
        for index in range(1000):
            contact = Contact(organization_id=org.id, first_name=f"Demo {index + 1}", last_name="Contact", phone=f"+91000{index:07d}", email=f"demo{index + 1}@example.invalid", language="English", source="demo")
            db.add(contact)
            db.flush()
            if index < 20:
                db.add(Consent(organization_id=org.id, contact_id=contact.id, channel="web", status="opt_in", source="demo seed"))
            if index < 10:
                conversation = Conversation(organization_id=org.id, contact_id=contact.id, channel="web")
                db.add(conversation)
                db.flush()
                db.add(Message(organization_id=org.id, conversation_id=conversation.id, sender_type="contact", content="Hello, I would like some information.", direction="inbound"))
                db.add(Interaction(organization_id=org.id, contact_id=contact.id, conversation_id=conversation.id, channel="web", type="message", direction="inbound"))
        db.commit()
        print(f"Created Demo Organization: {org.id} with 1,000 synthetic contacts")


if __name__ == "__main__":
    main()
