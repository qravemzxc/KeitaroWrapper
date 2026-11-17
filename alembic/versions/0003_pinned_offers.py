"""pinned offer shares

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-17 10:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pinned_offers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("keitaro_campaign_id", sa.Integer(), nullable=False),
        sa.Column("stream_id", sa.Integer(), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("keitaro_campaign_id", "stream_id", "offer_id", name="uq_pinned_campaign_stream_offer"),
    )
    with op.batch_alter_table("pinned_offers", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_pinned_offers_keitaro_campaign_id"), ["keitaro_campaign_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("pinned_offers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_pinned_offers_keitaro_campaign_id"))
    op.drop_table("pinned_offers")
