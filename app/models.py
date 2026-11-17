from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Campaign(Base):
    """Local mirror of a Keitaro campaign managed through this service."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    alias: Mapped[str] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(String(512))
    geo: Mapped[list[str]] = mapped_column(JSON, default=list)
    domain_id: Mapped[int | None] = mapped_column(Integer)
    group_id: Mapped[int | None] = mapped_column(Integer)
    traffic_source_id: Mapped[int | None] = mapped_column(Integer)
    geo_stream_id: Mapped[int | None] = mapped_column(Integer)
    offer_stream_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    offers: Mapped[list["CampaignOffer"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", order_by="CampaignOffer.position"
    )


class CampaignOffer(Base):
    """Offer currently attached to the offer stream of a campaign (mirror of Keitaro state)."""

    __tablename__ = "campaign_offers"
    __table_args__ = (UniqueConstraint("campaign_id", "stream_id", "offer_id", name="uq_campaign_stream_offer"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    stream_id: Mapped[int] = mapped_column(Integer)
    offer_id: Mapped[int] = mapped_column(Integer)
    offer_name: Mapped[str | None] = mapped_column(String(512))
    share: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer, default=0)

    campaign: Mapped[Campaign] = relationship(back_populates="offers")


class OperationLog(Base):
    """Audit log of every change this service made in Keitaro."""

    __tablename__ = "operation_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_campaign_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(32))  # create_campaign, add_offer, remove_offer, change_offers, set_shares, pin_offer, unpin_offer
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RemovedOffer(Base):
    """Offer removed from a stream through the editor.

    It is really deleted from the stream in Keitaro, but the editor keeps
    showing it (share 0%, not counted) so it can be returned in one click.
    The row is dropped as soon as the offer is back in the stream.
    """

    __tablename__ = "removed_offers"
    __table_args__ = (
        UniqueConstraint("keitaro_campaign_id", "stream_id", "offer_id", name="uq_removed_campaign_stream_offer"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_campaign_id: Mapped[int] = mapped_column(Integer, index=True)
    stream_id: Mapped[int] = mapped_column(Integer)
    offer_id: Mapped[int] = mapped_column(Integer)
    offer_name: Mapped[str | None] = mapped_column(String(512))
    removed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PinnedOffer(Base):
    """Offer whose share is pinned in the editor: when offers are added or removed,
    its share stays the same and only the other offers are re-split."""

    __tablename__ = "pinned_offers"
    __table_args__ = (
        UniqueConstraint("keitaro_campaign_id", "stream_id", "offer_id", name="uq_pinned_campaign_stream_offer"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_campaign_id: Mapped[int] = mapped_column(Integer, index=True)
    stream_id: Mapped[int] = mapped_column(Integer)
    offer_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

