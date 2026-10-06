"""Save a workspace's WhatsApp number independently of platform availability."""
from alembic import op
import sqlalchemy as sa
revision = '0007_whatsapp_setup_request'
down_revision = '0006_whatsapp_agent'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('whatsapp_setup_requests',
        sa.Column('organization_id', sa.String(36), sa.ForeignKey('organizations.id'), primary_key=True),
        sa.Column('phone', sa.String(16), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('whatsapp_setup_requests')
