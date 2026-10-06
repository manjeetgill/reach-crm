"""Separate platform access and private customer management notes."""
from alembic import op
import sqlalchemy as sa
revision = '0008_platform_dashboard'
down_revision = '0007_whatsapp_setup_request'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('platform_owners',
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('platform_workspace_profiles',
        sa.Column('organization_id', sa.String(36), sa.ForeignKey('organizations.id'), primary_key=True),
        sa.Column('stage', sa.String(30), nullable=False),
        sa.Column('notes', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('platform_workspace_profiles')
    op.drop_table('platform_owners')
