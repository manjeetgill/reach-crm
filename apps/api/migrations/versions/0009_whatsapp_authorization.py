"""Durable business authorization; no provider secrets in the browser or database row."""
from alembic import op
import sqlalchemy as sa
revision = '0009_whatsapp_authorization'
down_revision = '0008_platform_dashboard'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('whatsapp_authorizations',
        sa.Column('organization_id', sa.String(36), sa.ForeignKey('organizations.id'), primary_key=True),
        sa.Column('app_id', sa.String(36), nullable=False),
        sa.Column('session_id', sa.String(36), nullable=True),
        sa.Column('state', sa.String(40), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))

def downgrade():
    op.drop_table('whatsapp_authorizations')
