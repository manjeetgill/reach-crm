"""Frozen Phase 1 tenant-aware CRM schema.

Later ORM models must be created by their own migrations on a fresh database.
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("organizations",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("type", sa.String(40), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("users",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(300), nullable=False), sa.Column("role", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "email"))
    op.create_index("ix_users_organization_id", "users", ["organization_id"])
    op.create_index("ix_users_email", "users", ["email"])
    op.create_table("contacts",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("first_name", sa.String(120), nullable=False), sa.Column("last_name", sa.String(120), nullable=False),
        sa.Column("phone", sa.String(40)), sa.Column("email", sa.String(320)), sa.Column("language", sa.String(20)),
        sa.Column("timezone", sa.String(80)), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(80), nullable=False), sa.Column("external_id", sa.String(150)),
        sa.Column("metadata", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_contacts_organization_id", "contacts", ["organization_id"])
    op.create_index("ix_contact_tenant_name", "contacts", ["organization_id", "first_name", "last_name"])
    op.create_index("ix_contact_tenant_phone", "contacts", ["organization_id", "phone"])
    op.create_table("consents",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("contact_id", sa.String(36), sa.ForeignKey("contacts.id"), nullable=False), sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("purpose", sa.String(100), nullable=False), sa.Column("status", sa.String(10), nullable=False),
        sa.Column("source", sa.String(100), nullable=False), sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(36)))
    op.create_index("ix_consents_organization_id", "consents", ["organization_id"])
    op.create_index("ix_consents_contact_id", "consents", ["contact_id"])
    op.create_index("ix_consent_lookup", "consents", ["organization_id", "contact_id", "channel", "captured_at"])
    op.create_table("conversations",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("contact_id", sa.String(36), sa.ForeignKey("contacts.id"), nullable=False), sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("summary", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("ended_at", sa.DateTime(timezone=True)))
    op.create_index("ix_conversations_organization_id", "conversations", ["organization_id"])
    op.create_index("ix_conversations_contact_id", "conversations", ["contact_id"])
    op.create_table("messages",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("sender_type", sa.String(20), nullable=False), sa.Column("content", sa.Text(), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_messages_organization_id", "messages", ["organization_id"])
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_table("interactions",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("contact_id", sa.String(36), sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id")), sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("type", sa.String(30), nullable=False), sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_interactions_organization_id", "interactions", ["organization_id"])
    op.create_index("ix_interactions_contact_id", "interactions", ["contact_id"])
    op.create_table("audit_logs",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("actor_id", sa.String(36)), sa.Column("action", sa.String(80), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False), sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("before", sa.JSON()), sa.Column("after", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_audit_logs_organization_id", "audit_logs", ["organization_id"])
    op.create_index("uq_webhook_event", "audit_logs", ["organization_id", "action", "resource_id"], unique=True,
        sqlite_where=sa.text("action = 'webhook.received'"), postgresql_where=sa.text("action = 'webhook.received'"))

def downgrade():
    for table in ("audit_logs", "interactions", "messages", "conversations", "consents", "contacts", "users", "organizations"):
        op.drop_table(table)
