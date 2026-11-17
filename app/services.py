"""Business logic: creating campaigns and editing offers of existing ones.

Keitaro is the source of truth. After every change the local DB mirror
(campaigns / campaign_offers) is re-synced from what Keitaro returned, and
the change is written to operation_log.
"""

import secrets
import string
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.keitaro import KeitaroClient, KeitaroError
from app.models import Campaign, CampaignOffer, OperationLog, PinnedOffer, RemovedOffer
from app.schemas import CampaignCreate, CampaignDetail, StreamOffer, StreamOut, Trend
from app.shares import shares_with_pins, validate_shares

STATS_INTERVALS = {
    "today": "Сегодня",
    "yesterday": "Вчера",
    "7_days_ago": "7 дней",
    "1_month_ago": "30 дней",
    "all_time": "Всё время",
}


class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# --------------------------------------------------------------------------- helpers
def with_prefix(name: str, prefix: str) -> str:
    return name if name.startswith(prefix) else prefix + name


def make_alias(length: int = 7) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def campaign_url(campaign: dict) -> str | None:
    domain = campaign.get("domain")
    if not domain:
        return None
    return domain.rstrip("/") + "/" + campaign["alias"]


def pick_offer_stream(streams: list[dict], preferred_id: int | None = None) -> dict | None:
    """The stream we edit offers in: explicitly requested one, else the first
    stream that sends traffic to offers (schema=landings) or already has offers."""
    if preferred_id is not None:
        for s in streams:
            if s["id"] == preferred_id:
                return s
        raise ServiceError(f"Поток {preferred_id} не найден в кампании", 404)
    for s in sorted(streams, key=lambda s: s.get("position", 0)):
        if s.get("schema") == "landings" or s.get("offers"):
            return s
    return None


async def offer_names(kt: KeitaroClient, ids: set[int]) -> dict[int, str]:
    names = {o["id"]: o["name"] for o in await kt.offers() if o["id"] in ids}
    for missing in ids - names.keys():  # offers outside the cached list (e.g. archived)
        try:
            names[missing] = (await kt.offer(missing))["name"]
        except KeitaroError:
            names[missing] = f"Оффер #{missing}"
    return names


async def get_active_offer(kt: KeitaroClient, offer_id: int) -> dict:
    """Offer must exist and be active, otherwise Keitaro would route traffic nowhere."""
    try:
        offer = await kt.offer(offer_id)
    except KeitaroError as exc:
        if exc.status_code == 404:
            raise ServiceError(f"Оффер {offer_id} не найден в Keitaro", 404) from exc
        raise
    if offer.get("state", "active") != "active":
        raise ServiceError(f"Оффер {offer_id} неактивен (state={offer.get('state')})", 400)
    return offer


async def log(session: AsyncSession, campaign_id: int, action: str, payload: dict) -> None:
    session.add(OperationLog(keitaro_campaign_id=campaign_id, action=action, payload=payload))


async def sync_campaign(
    session: AsyncSession,
    kt_campaign: dict,
    streams: list[dict],
    names: dict[int, str],
    geo: list[str] | None = None,
) -> Campaign:
    """Upsert the local mirror of a Keitaro campaign and its offer-stream offers."""
    result = await session.execute(
        select(Campaign).options(selectinload(Campaign.offers)).where(Campaign.keitaro_id == kt_campaign["id"])
    )
    camp = result.scalar_one_or_none()
    if camp is None:
        camp = Campaign(keitaro_id=kt_campaign["id"], geo=[])
        session.add(camp)
        camp.offers = []

    geo_stream = next((s for s in streams if s.get("schema") == "redirect" and s.get("filters")), None)
    offer_stream = pick_offer_stream(streams, None)

    camp.name = kt_campaign["name"]
    camp.alias = kt_campaign["alias"]
    camp.url = campaign_url(kt_campaign)
    camp.domain_id = kt_campaign.get("domain_id")
    camp.group_id = kt_campaign.get("group_id")
    camp.traffic_source_id = kt_campaign.get("traffic_source_id")
    camp.geo_stream_id = geo_stream["id"] if geo_stream else None
    camp.offer_stream_id = offer_stream["id"] if offer_stream else None
    if geo is not None:
        camp.geo = geo
    elif geo_stream:
        for f in geo_stream["filters"]:
            if f.get("name") == "country":
                camp.geo = list(f.get("payload") or [])

    camp.offers.clear()
    await session.flush()
    if offer_stream:
        for pos, o in enumerate(offer_stream.get("offers") or []):
            camp.offers.append(
                CampaignOffer(
                    stream_id=offer_stream["id"],
                    offer_id=o["offer_id"],
                    offer_name=names.get(o["offer_id"]),
                    share=o["share"],
                    position=pos,
                )
            )
    return camp


# --------------------------------------------------------------------------- part 1: creator
async def create_campaign(session: AsyncSession, kt: KeitaroClient, settings: Settings, data: CampaignCreate) -> dict:
    # Validate the offer before creating anything in Keitaro.
    offer = await get_active_offer(kt, data.offer_id)

    name = with_prefix(data.name, settings.campaign_prefix)
    campaign = await kt.create_campaign(
        {
            "name": name,
            "alias": make_alias(),
            "type": "position",  # flows are checked top-down: geo flow first, offer flow second
            "state": "active",
            "domain_id": data.domain_id or settings.default_domain_id,
            "group_id": data.group_id or settings.default_group_id,
            "traffic_source_id": data.traffic_source_id or settings.default_traffic_source_id,
            "cost_type": "CPC",
            "cost_value": 0,
            "cost_currency": "USD",
            "cost_auto": True,
            "uniqueness_method": "ip",
            "cookies_ttl": 24,
        }
    )
    try:
        geo_stream = await kt.create_stream(
            {
                "campaign_id": campaign["id"],
                "type": "regular",
                "name": f"{','.join(data.geo)} -> Google",
                "position": 1,
                "weight": 100,
                "state": "active",
                "schema": "redirect",
                "action_type": "http",
                "action_payload": settings.google_url,
                "collect_clicks": True,
                "filter_or": False,
                "filters": [{"name": "country", "mode": "accept", "payload": data.geo}],
            }
        )
        offer_stream = await kt.create_stream(
            {
                "campaign_id": campaign["id"],
                "type": "regular",
                "name": "Offers",
                "position": 2,
                "weight": 100,
                "state": "active",
                "schema": "landings",
                "action_type": "http",
                "offer_selection": "before_click",
                "collect_clicks": True,
                "offers": [{"offer_id": data.offer_id, "share": 100, "state": "active"}],
            }
        )
    except KeitaroError:
        # Do not leave a half-configured campaign behind.
        try:
            await kt.delete_campaign(campaign["id"])
        except KeitaroError:
            pass
        raise

    streams = [geo_stream, offer_stream]
    camp = await sync_campaign(session, campaign, streams, {offer["id"]: offer["name"]}, geo=data.geo)
    await log(
        session,
        campaign["id"],
        "create_campaign",
        {"name": name, "geo": data.geo, "offer_id": data.offer_id, "streams": [s["id"] for s in streams]},
    )
    await session.commit()
    return {
        "id": campaign["id"],
        "name": name,
        "alias": campaign["alias"],
        "url": camp.url,
        "domain": campaign.get("domain"),
        "group": campaign.get("group"),
        "geo": data.geo,
        "offer": {"id": offer["id"], "name": offer["name"]},
        "streams": [{"id": s["id"], "name": s["name"]} for s in streams],
    }


# --------------------------------------------------------------------------- part 2: editor
async def _stats(kt: KeitaroClient, settings: Settings, campaign_id: int, interval: str):
    """Stream and offer stats from the Keitaro report builder.

    Two reports are built, because uniques are not additive: the stream row
    ("Клики / Уник. / Боты" in the Keitaro campaign page) uses
    stream_unique_clicks grouped by stream only, offer rows are grouped by
    (stream, offer).
    """
    rng: dict = {"interval": interval}
    if settings.stats_timezone:
        rng["timezone"] = settings.stats_timezone
    flt = [{"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}]

    by_stream = await kt.report(
        {"range": rng, "dimensions": ["stream_id"], "measures": ["clicks", "stream_unique_clicks", "bots"],
         "filters": flt}
    )
    by_offer = await kt.report(
        {"range": rng, "dimensions": ["stream_id", "offer_id"],
         "measures": ["clicks", "stream_unique_clicks", "bots", "conversions", "revenue"], "filters": flt}
    )
    per_stream: dict[int, dict] = {}
    for row in by_stream.get("rows", []):
        per_stream[int(row.get("stream_id") or 0)] = {m: float(row.get(m) or 0) for m in ("clicks", "stream_unique_clicks", "bots")}
    per_offer: dict[tuple[int, int], dict] = {}
    for row in by_offer.get("rows", []):
        sid, oid = int(row.get("stream_id") or 0), int(row.get("offer_id") or 0)
        if oid:
            per_offer[(sid, oid)] = {
                m: float(row.get(m) or 0) for m in ("clicks", "stream_unique_clicks", "bots", "conversions", "revenue")
            }
    return per_stream, per_offer


def trend_windows(interval: str, now: datetime) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]] | None:
    """(current, previous) windows for the trend of the selected period, in tracker local time.

    today       -> today 00:00..now        vs yesterday 00:00..same time yesterday
    yesterday   -> yesterday (whole day)   vs the day before
    7_days_ago  -> last 7 days             vs the 7 days before
    1_month_ago -> last 30 days            vs the 30 days before
    all_time    -> no trend
    """
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day = timedelta(days=1)
    if interval == "today":
        return (midnight, now), (midnight - day, now - day)
    if interval == "yesterday":
        return (midnight - day, midnight - timedelta(seconds=1)), (midnight - 2 * day, midnight - day - timedelta(seconds=1))
    span = {"7_days_ago": 7, "1_month_ago": 30}.get(interval)
    if span is None:
        return None
    return (now - span * day, now), (now - 2 * span * day, now - span * day)


def _tracker_now(settings: Settings) -> tuple[datetime, str]:
    tz_name = settings.stats_timezone or settings.tracker_timezone or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # unknown name / no tz database on the machine
        tz_name, tz = "UTC", timezone.utc
    return datetime.now(tz).replace(tzinfo=None), tz_name


async def _trends(kt: KeitaroClient, settings: Settings, campaign_id: int, interval: str) -> dict[tuple[int, int], Trend] | None:
    """Clicks per (stream, offer) in the current and the previous window -> Trend."""
    now, tz_name = _tracker_now(settings)
    windows = trend_windows(interval, now)
    if windows is None:
        return None
    fmt = "%Y-%m-%d %H:%M:%S"
    flt = [{"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}]
    clicks: list[dict[tuple[int, int], int]] = []
    for start, end in windows:
        report = await kt.report({
            "range": {"from": start.strftime(fmt), "to": end.strftime(fmt), "timezone": tz_name},
            "dimensions": ["stream_id", "offer_id"], "measures": ["clicks"], "filters": flt,
        })
        clicks.append({
            (int(r.get("stream_id") or 0), int(r.get("offer_id") or 0)): int(r.get("clicks") or 0)
            for r in report.get("rows", []) if r.get("offer_id")
        })
    cur, prev = clicks
    out = {}
    for key in cur.keys() | prev.keys():
        c, p = cur.get(key, 0), prev.get(key, 0)
        out[key] = Trend(current=c, previous=p, change_pct=round((c - p) / p * 100, 1) if p else None)
    return out


def _trend_of(trends: dict | None, stream_id: int, offer_id: int) -> Trend | None:
    if trends is None:
        return None
    return trends.get((stream_id, offer_id), Trend(current=0, previous=0, change_pct=None))


async def campaign_detail(
    session: AsyncSession, kt: KeitaroClient, settings: Settings, campaign_id: int, interval: str = "today"
) -> CampaignDetail:
    if interval not in STATS_INTERVALS:
        raise ServiceError(f"Неизвестный период {interval!r}")
    try:
        campaign = await kt.campaign(campaign_id)
    except KeitaroError as exc:
        if exc.status_code in (403, 404):
            raise ServiceError(f"Кампания {campaign_id} не найдена или нет доступа", 404) from exc
        raise
    streams = await kt.streams(campaign_id)
    ids = {o["offer_id"] for s in streams for o in s.get("offers") or []}
    names = await offer_names(kt, ids)

    stats_error = None
    per_stream, per_offer = {}, {}
    try:
        per_stream, per_offer = await _stats(kt, settings, campaign_id, interval)
    except KeitaroError as exc:
        stats_error = str(exc)
    try:
        trends = await _trends(kt, settings, campaign_id, interval)
    except KeitaroError:
        trends = None  # the column just shows "—"

    # Offers that were in the stream last time we looked but are gone now were deleted
    # directly in Keitaro: keep them in the editor as removed (share 0%, "Вернуть").
    prev = (
        await session.execute(
            select(CampaignOffer.stream_id, CampaignOffer.offer_id)
            .join(Campaign, CampaignOffer.campaign_id == Campaign.id)
            .where(Campaign.keitaro_id == campaign_id)
        )
    ).all()
    present = {(s["id"], o["offer_id"]) for s in streams for o in s.get("offers") or []}
    stream_ids = {s["id"] for s in streams}
    gone: dict[int, set[int]] = {}
    for sid, oid in prev:
        if (sid, oid) not in present and sid in stream_ids:
            gone.setdefault(sid, set()).add(oid)

    await sync_campaign(session, campaign, streams, names)
    for sid, ids in gone.items():
        await _remember_removed(session, kt, campaign_id, sid, ids)
    removed = await _removed_by_stream(session, campaign_id, streams)
    pins = await _pins_by_stream(session, campaign_id, streams)
    await session.commit()

    offer_stream = pick_offer_stream(streams)
    out_streams = []
    for s in sorted(streams, key=lambda s: s.get("position", 0)):
        st = per_stream.get(s["id"], {})
        offers = []
        for o in s.get("offers") or []:
            os_ = per_offer.get((s["id"], o["offer_id"]), {})
            offers.append(
                StreamOffer(
                    offer_id=o["offer_id"],
                    name=names.get(o["offer_id"], f"Оффер #{o['offer_id']}"),
                    share=o["share"],
                    state=o.get("state", "active"),
                    clicks=int(os_.get("clicks", 0)),
                    unique=int(os_.get("stream_unique_clicks", 0)),
                    bots=int(os_.get("bots", 0)),
                    conversions=int(os_.get("conversions", 0)),
                    revenue=round(os_.get("revenue", 0), 2),
                    pinned=o["offer_id"] in pins.get(s["id"], set()),
                    trend=_trend_of(trends, s["id"], o["offer_id"]),
                )
            )
        out_streams.append(
            StreamOut(
                id=s["id"],
                name=s.get("name") or f"Flow {s.get('position')}",
                position=s.get("position", 0),
                schema=s.get("schema") or "",
                action_payload=s.get("action_payload"),
                filters=[
                    {"name": f["name"], "mode": f.get("mode"), "payload": f.get("payload")}
                    for f in s.get("filters") or []
                ],
                offers=offers,
                removed_offers=[
                    StreamOffer(
                        offer_id=r.offer_id,
                        name=r.offer_name or f"Оффер #{r.offer_id}",
                        share=0,
                        state="removed",
                        clicks=int(per_offer.get((s["id"], r.offer_id), {}).get("clicks", 0)),
                        unique=int(per_offer.get((s["id"], r.offer_id), {}).get("stream_unique_clicks", 0)),
                        bots=int(per_offer.get((s["id"], r.offer_id), {}).get("bots", 0)),
                        conversions=int(per_offer.get((s["id"], r.offer_id), {}).get("conversions", 0)),
                        revenue=round(per_offer.get((s["id"], r.offer_id), {}).get("revenue", 0), 2),
                        removed_at=r.removed_at.isoformat() if r.removed_at else None,
                        trend=_trend_of(trends, s["id"], r.offer_id),
                    )
                    for r in removed.get(s["id"], [])
                ],
                clicks=int(st.get("clicks", 0)),
                unique=int(st.get("stream_unique_clicks", 0)),
                bots=int(st.get("bots", 0)),
            )
        )
    return CampaignDetail(
        id=campaign["id"],
        name=campaign["name"],
        alias=campaign["alias"],
        url=campaign_url(campaign),
        state=campaign.get("state", ""),
        group=campaign.get("group"),
        domain=campaign.get("domain"),
        traffic_source_id=campaign.get("traffic_source_id"),
        offer_stream_id=offer_stream["id"] if offer_stream else None,
        streams=out_streams,
        stats_interval=interval,
        stats_error=stats_error,
    )


async def _write_offers(
    session: AsyncSession,
    kt: KeitaroClient,
    campaign_id: int,
    stream: dict,
    offers: list[dict],
    action: str,
    payload: dict,
) -> None:
    validate_shares([o["share"] for o in offers])
    before = _offer_state(stream.get("offers") or [])
    updated = await kt.update_stream(stream["id"], {"offers": offers})
    # re-read the campaign to keep the mirror exact
    campaign = await kt.campaign(campaign_id)
    streams = await kt.streams(campaign_id)
    for i, s in enumerate(streams):
        if s["id"] == updated["id"]:
            streams[i] = updated
    names = await offer_names(kt, {o["offer_id"] for s in streams for o in s.get("offers") or []})
    await sync_campaign(session, campaign, streams, names)
    gone = {o["offer_id"] for o in before} - {o["offer_id"] for o in offers}
    await _remember_removed(session, kt, campaign_id, stream["id"], gone)
    if gone:
        for row in (
            await session.execute(
                select(PinnedOffer).where(
                    PinnedOffer.keitaro_campaign_id == campaign_id,
                    PinnedOffer.stream_id == stream["id"],
                    PinnedOffer.offer_id.in_(gone),
                )
            )
        ).scalars():
            await session.delete(row)
    await log(session, campaign_id, action, {**payload, "stream_id": stream["id"], "before": before, "result": offers})
    await session.commit()


async def _remember_removed(
    session: AsyncSession, kt: KeitaroClient, campaign_id: int, stream_id: int, offer_ids: set[int]
) -> None:
    """Keep offers removed from the stream visible in the editor (see RemovedOffer)."""
    if not offer_ids:
        return
    names = await offer_names(kt, offer_ids)
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, like CURRENT_TIMESTAMP
    existing = {
        r.offer_id: r
        for r in (
            await session.execute(
                select(RemovedOffer).where(
                    RemovedOffer.keitaro_campaign_id == campaign_id,
                    RemovedOffer.stream_id == stream_id,
                    RemovedOffer.offer_id.in_(offer_ids),
                )
            )
        ).scalars()
    }
    for oid in offer_ids:
        row = existing.get(oid)
        if row is None:
            session.add(RemovedOffer(keitaro_campaign_id=campaign_id, stream_id=stream_id, offer_id=oid,
                                     offer_name=names.get(oid), removed_at=now))
        else:
            row.offer_name = names.get(oid) or row.offer_name
            row.removed_at = now


async def _removed_by_stream(session: AsyncSession, campaign_id: int, streams: list[dict]) -> dict[int, list]:
    """Removed offers per stream. Records of offers that are in the stream again
    (returned through the editor or re-added in Keitaro) are deleted."""
    present = {(s["id"], o["offer_id"]) for s in streams for o in s.get("offers") or []}
    stream_ids = {s["id"] for s in streams}
    rows = (
        await session.execute(
            select(RemovedOffer)
            .where(RemovedOffer.keitaro_campaign_id == campaign_id)
            .order_by(RemovedOffer.removed_at, RemovedOffer.id)
        )
    ).scalars().all()
    out: dict[int, list] = {}
    for r in rows:
        if (r.stream_id, r.offer_id) in present or r.stream_id not in stream_ids:
            await session.delete(r)
        else:
            out.setdefault(r.stream_id, []).append(r)
    return out


async def _load_stream(kt: KeitaroClient, campaign_id: int, stream_id: int | None) -> dict:
    try:
        streams = await kt.streams(campaign_id)
    except KeitaroError as exc:
        if exc.status_code in (403, 404):
            raise ServiceError(f"Кампания {campaign_id} не найдена или нет доступа", 404) from exc
        raise
    stream = pick_offer_stream(streams, stream_id)
    if stream is None:
        raise ServiceError("В кампании нет потока с офферами", 400)
    return stream


def _offer_state(offers: list[dict]) -> list[dict]:
    """Normalized offers list of a stream: what we need to restore it later."""
    return [
        {"offer_id": o["offer_id"], "share": o["share"], "state": o.get("state", "active")} for o in offers
    ]


def _rebalanced(offers: list[dict], pinned: dict[int, int] | None = None) -> list[dict]:
    """New shares for the final offers list: pinned offers keep their share, the rest is split evenly."""
    try:
        shares = shares_with_pins([o["offer_id"] for o in offers], pinned or {})
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    return [
        {"offer_id": o["offer_id"], "share": share, "state": o.get("state", "active")}
        for o, share in zip(offers, shares)
    ]


async def _pinned_ids(session: AsyncSession, campaign_id: int, stream_id: int) -> set[int]:
    rows = await session.execute(
        select(PinnedOffer.offer_id).where(
            PinnedOffer.keitaro_campaign_id == campaign_id, PinnedOffer.stream_id == stream_id
        )
    )
    return set(rows.scalars())


async def _pinned_shares(session: AsyncSession, campaign_id: int, stream: dict) -> dict[int, int]:
    """Current Keitaro shares of the pinned offers of the stream."""
    ids = await _pinned_ids(session, campaign_id, stream["id"])
    return {o["offer_id"]: o["share"] for o in stream.get("offers") or [] if o["offer_id"] in ids}


async def set_pin(
    session: AsyncSession, kt: KeitaroClient, campaign_id: int, offer_id: int, pinned: bool,
    stream_id: int | None = None,
) -> None:
    """Pin / unpin the share of an offer.

    Pinning changes nothing in Keitaro: the pin only affects how shares are re-split
    on the next add / remove. Unpinning re-splits the shares right away: the offer
    joins the even split with the other unpinned offers (pinned ones keep their
    share), and the new shares are written to Keitaro."""
    stream = await _load_stream(kt, campaign_id, stream_id)
    offer = next((o for o in stream.get("offers") or [] if o["offer_id"] == offer_id), None)
    if offer is None:
        raise ServiceError(f"Оффера {offer_id} нет в потоке", 404)
    row = (
        await session.execute(
            select(PinnedOffer).where(
                PinnedOffer.keitaro_campaign_id == campaign_id,
                PinnedOffer.stream_id == stream["id"],
                PinnedOffer.offer_id == offer_id,
            )
        )
    ).scalar_one_or_none()
    if pinned and row is None:
        session.add(PinnedOffer(keitaro_campaign_id=campaign_id, stream_id=stream["id"], offer_id=offer_id))
    elif not pinned and row is not None:
        current = stream.get("offers") or []
        other_pins = {k: v for k, v in (await _pinned_shares(session, campaign_id, stream)).items() if k != offer_id}
        offers = _rebalanced(current, other_pins)  # raises before anything is changed if the split is impossible
        await session.delete(row)
        payload = {"offer_id": offer_id, "stream_id": stream["id"], "share": offer["share"]}
        if [o["share"] for o in offers] != [o["share"] for o in current]:
            await _write_offers(session, kt, campaign_id, stream, offers, "unpin_offer", payload)
            return
    else:
        return  # already in the requested state
    await log(session, campaign_id, "pin_offer" if pinned else "unpin_offer",
              {"offer_id": offer_id, "stream_id": stream["id"], "share": offer["share"]})
    await session.commit()


async def _pins_by_stream(session: AsyncSession, campaign_id: int, streams: list[dict]) -> dict[int, set[int]]:
    """Pinned offers per stream; pins of offers that left the stream are deleted."""
    present = {(s["id"], o["offer_id"]) for s in streams for o in s.get("offers") or []}
    rows = (
        await session.execute(select(PinnedOffer).where(PinnedOffer.keitaro_campaign_id == campaign_id))
    ).scalars().all()
    out: dict[int, set[int]] = {}
    for r in rows:
        if (r.stream_id, r.offer_id) in present:
            out.setdefault(r.stream_id, set()).add(r.offer_id)
        else:
            await session.delete(r)
    return out


async def add_offers(
    session: AsyncSession, kt: KeitaroClient, campaign_id: int, offer_ids: list[int], stream_id: int | None = None
) -> None:
    """Push one or several new offers to the stream in a single update; shares are re-split evenly.
    All offers are validated first, so either all of them are added or none."""
    offer_ids = list(dict.fromkeys(offer_ids))  # dedupe, keep order
    if not offer_ids:
        raise ServiceError("Не выбран ни один оффер")
    stream = await _load_stream(kt, campaign_id, stream_id)
    current = stream.get("offers") or []
    present = {o["offer_id"] for o in current}
    for oid in offer_ids:
        if oid in present:
            raise ServiceError(f"Оффер {oid} уже есть в потоке", 409)
    for oid in offer_ids:
        await get_active_offer(kt, oid)
    offers = _rebalanced(
        [*current, *({"offer_id": oid, "state": "active"} for oid in offer_ids)],
        await _pinned_shares(session, campaign_id, stream),
    )
    payload = {"offer_ids": offer_ids}
    if len(offer_ids) == 1:
        payload["offer_id"] = offer_ids[0]
    await _write_offers(session, kt, campaign_id, stream, offers, "add_offer", payload)


async def remove_offer(
    session: AsyncSession, kt: KeitaroClient, campaign_id: int, offer_id: int, stream_id: int | None = None
) -> None:
    stream = await _load_stream(kt, campaign_id, stream_id)
    current = stream.get("offers") or []
    if not any(o["offer_id"] == offer_id for o in current):
        raise ServiceError(f"Оффера {offer_id} нет в потоке", 404)
    rest = [o for o in current if o["offer_id"] != offer_id]
    if not rest:
        raise ServiceError("Нельзя удалить последний оффер: поток останется пустым", 400)
    offers = _rebalanced(rest, await _pinned_shares(session, campaign_id, stream))
    await _write_offers(session, kt, campaign_id, stream, offers, "remove_offer", {"offer_id": offer_id})


async def push_offers(
    session: AsyncSession, kt: KeitaroClient, campaign_id: int, offer_ids: list[int], stream_id: int | None = None
) -> None:
    """Push the final list of offers of the stream (what is left after the editor's draft):
    offers missing from the list are removed, new ones are added, shares are re-split evenly.
    New offers are validated first, so the stream is updated with all changes or with none."""
    offer_ids = list(dict.fromkeys(offer_ids))  # dedupe, keep order
    if not offer_ids:
        raise ServiceError("Нельзя оставить поток без офферов", 400)
    stream = await _load_stream(kt, campaign_id, stream_id)
    current = {o["offer_id"]: o for o in stream.get("offers") or []}
    added = [oid for oid in offer_ids if oid not in current]
    removed = [oid for oid in current if oid not in offer_ids]
    if not added and not removed:
        raise ServiceError("Нет изменений для отправки", 400)
    for oid in added:
        await get_active_offer(kt, oid)
    offers = _rebalanced(
        [current.get(oid, {"offer_id": oid, "state": "active"}) for oid in offer_ids],
        await _pinned_shares(session, campaign_id, stream),
    )
    action = "change_offers" if added and removed else "add_offer" if added else "remove_offer"
    await _write_offers(
        session, kt, campaign_id, stream, offers, action, {"offer_ids": added, "removed_offer_ids": removed}
    )


async def set_shares(
    session: AsyncSession, kt: KeitaroClient, campaign_id: int, shares: dict[int, int], stream_id: int | None = None
) -> None:
    stream = await _load_stream(kt, campaign_id, stream_id)
    current = stream.get("offers") or []
    if {o["offer_id"] for o in current} != set(shares):
        raise ServiceError("Список офферов изменился, обновите страницу", 409)
    offers = [
        {"offer_id": o["offer_id"], "share": shares[o["offer_id"]], "state": o.get("state", "active")}
        for o in current
    ]
    try:
        validate_shares([o["share"] for o in offers])
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    await _write_offers(session, kt, campaign_id, stream, offers, "set_shares", {"shares": shares})


async def history(session: AsyncSession, campaign_id: int, limit: int = 50) -> list[dict]:
    rows = await session.execute(
        select(OperationLog)
        .where(OperationLog.keitaro_campaign_id == campaign_id)
        .order_by(OperationLog.id.desc())
        .limit(limit)
    )
    return [
        {"id": r.id, "action": r.action, "payload": r.payload, "created_at": r.created_at.isoformat()}
        for r in rows.scalars()
    ]
