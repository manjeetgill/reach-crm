"""Durable outbound queue and delivery correlation."""
import sqlalchemy as sa
from alembic import op

revision = "0002_outbound_jobs"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.String(120), nullable=True))
        batch.add_column(sa.Column("external_id", sa.String(200), nullable=True))
        batch.create_unique_constraint("uq_message_idempotency", ["organization_id", "idempotency_key"])
    op.create_table(
        "outbound_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_outbound_due", "outbound_jobs", ["status", "run_at"])
    op.create_index("ix_outbound_jobs_organization_id", "outbound_jobs", ["organization_id"])
    op.create_index("uq_webhook_delivery", "audit_logs", ["organization_id", "action", "resource_id"], unique=True, sqlite_where=sa.text("action = 'webhook.delivery'"), postgresql_where=sa.text("action = 'webhook.delivery'"))

def downgrade():
    op.drop_index("uq_webhook_delivery", "audit_logs")
    op.drop_index("ix_outbound_jobs_organization_id", "outbound_jobs")
    op.drop_index("ix_outbound_due", "outbound_jobs")
    op.drop_table("outbound_jobs")
    with op.batch_alter_table("messages") as batch:
        batch.drop_constraint("uq_message_idempotency", type_="unique")
        batch.drop_column("external_id")
        batch.drop_column("idempotency_key")
