"""Bind Reach workspaces to platform WhatsApp channels."""
from alembic import op
import sqlalchemy as sa
revision = '0006_whatsapp_agent'
down_revision = 'aeeb77ec2424'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('whatsapp_agents',
        sa.Column('organization_id', sa.String(36), sa.ForeignKey('organizations.id'), primary_key=True),
        sa.Column('channel_id', sa.String(36), nullable=False, unique=True),
        sa.Column('app_id', sa.String(36), nullable=False),
        sa.Column('phone_id', sa.String(30), nullable=False),
        sa.Column('bridge_ready', sa.Boolean(), nullable=False))

def downgrade():
    op.drop_table('whatsapp_agents')
