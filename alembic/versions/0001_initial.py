"""initial

Revision ID: 0001
Revises:
Create Date: 2026-11-16 17:51:47.554471

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('campaigns',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('keitaro_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('alias', sa.String(length=64), nullable=False),
    sa.Column('url', sa.String(length=512), nullable=True),
    sa.Column('geo', sa.JSON(), nullable=False),
    sa.Column('domain_id', sa.Integer(), nullable=True),
    sa.Column('group_id', sa.Integer(), nullable=True),
    sa.Column('traffic_source_id', sa.Integer(), nullable=True),
    sa.Column('geo_stream_id', sa.Integer(), nullable=True),
    sa.Column('offer_stream_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_campaigns_keitaro_id'), ['keitaro_id'], unique=True)

    op.create_table('operation_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('keitaro_campaign_id', sa.Integer(), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('operation_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_operation_log_keitaro_campaign_id'), ['keitaro_campaign_id'], unique=False)

    op.create_table('campaign_offers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('campaign_id', sa.Integer(), nullable=False),
    sa.Column('stream_id', sa.Integer(), nullable=False),
    sa.Column('offer_id', sa.Integer(), nullable=False),
    sa.Column('offer_name', sa.String(length=512), nullable=True),
    sa.Column('share', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('campaign_id', 'stream_id', 'offer_id', name='uq_campaign_stream_offer')
    )
    with op.batch_alter_table('campaign_offers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_campaign_offers_campaign_id'), ['campaign_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('campaign_offers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_campaign_offers_campaign_id'))

    op.drop_table('campaign_offers')
    with op.batch_alter_table('operation_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_operation_log_keitaro_campaign_id'))

    op.drop_table('operation_log')
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_campaigns_keitaro_id'))

    op.drop_table('campaigns')
