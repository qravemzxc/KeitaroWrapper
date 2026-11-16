"""Business logic: creating campaigns and editing offers of existing ones.

Keitaro is the source of truth. After every change the local DB mirror
(campaigns / campaign_offers) is re-synced from what Keitaro returned, and
the change is written to operation_log.
"""

import secrets
import string
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.keitaro import KeitaroClient, KeitaroError
from app.models import Campaign, CampaignOffer, OperationLog
from app.schemas import CampaignCreate, CampaignDetail, StreamOffer, StreamOut
from app.shares import even_shares, validate_shares

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
    try:
        offer = await kt.offer(data.offer_id)
    except KeitaroError as exc:
        if exc.status_code == 404:
            raise ServiceError(f"Оффер {data.offer_id} не найден в Keitaro", 404) from exc
        raise

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
async def _stats(kt: KeitaroClient, campaign_id: int, interval: str):
    """Clicks per stream and per (stream, offer) from the Keitaro report builder."""
    report = await kt.report(
        {
            "range": {"interval": interval, "timezone": "Europe/Moscow"},
            "dimensions": ["stream_id", "offer_id"],
            "measures": ["clicks", "campaign_unique_clicks", "bots", "conversions", "revenue"],
            "filters": [{"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}],
        }
    )
    per_stream: dict[int, dict] = defaultdict(lambda: defaultdict(float))
    per_offer: dict[tuple[int, int], dict] = defaultdict(lambda: defaultdict(float))
    for row in report.get("rows", []):
        sid = int(row.get("stream_id") or 0)
        oid = int(row.get("offer_id") or 0)
        for m in ("clicks", "campaign_unique_clicks", "bots", "conversions", "revenue"):
            val = float(row.get(m) or 0)
            per_stream[sid][m] += val
            if oid:
                per_offer[(sid, oid)][m] += val
    return per_stream, per_offer


async def campaign_detail(
    session: AsyncSession, kt: KeitaroClient, campaign_id: int, interval: str = "today"
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
        per_stream, per_offer = await _stats(kt, campaign_id, interval)
    except KeitaroError as exc:
        stats_error = str(exc)

    await sync_campaign(session, campaign, streams, names)
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
                    unique=int(os_.get("campaign_unique_clicks", 0)),
                    conversions=int(os_.get("conversions", 0)),
                    revenue=round(os_.get("revenue", 0), 2),
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
                clicks=int(st.get("clicks", 0)),
                unique=int(st.get("campaign_unique_clicks", 0)),
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
    updated = await kt.update_stream(stream["id"], {"offers": offers})
    # re-read the campaign to keep the mirror exact
    campaign = await kt.campaign(campaign_id)
    streams = await kt.streams(campaign_id)
    for i, s in enumerate(streams):
        if s["id"] == updated["id"]:
            streams[i] = updated
    names = await offer_names(kt, {o["offer_id"] for s in streams for o in s.get("offers") or []})
    await sync_campaign(session, campaign, streams, names)
    await log(session, campaign_id, action, {**payload, "stream_id": stream["id"], "result": offers})
    await session.commit()


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


def _rebalanced(offers: list[dict]) -> list[dict]:
    shares = even_shares(len(offers))
    return [
        {"offer_id": o["offer_id"], "share": share, "state": o.get("state", "active")}
        for o, share in zip(offers, shares)
    ]


async def add_offer(
    session: AsyncSession, kt: KeitaroClient, campaign_id: int, offer_id: int, stream_id: int | None = None
) -> None:
    stream = await _load_stream(kt, campaign_id, stream_id)
    current = stream.get("offers") or []
    if any(o["offer_id"] == offer_id for o in current):
        raise ServiceError(f"Оффер {offer_id} уже есть в потоке", 409)
    try:
        await kt.offer(offer_id)
    except KeitaroError as exc:
        if exc.status_code == 404:
            raise ServiceError(f"Оффер {offer_id} не найден в Keitaro", 404) from exc
        raise
    offers = _rebalanced([*current, {"offer_id": offer_id, "state": "active"}])
    await _write_offers(session, kt, campaign_id, stream, offers, "add_offer", {"offer_id": offer_id})


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
    offers = _rebalanced(rest)
    await _write_offers(session, kt, campaign_id, stream, offers, "remove_offer", {"offer_id": offer_id})


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
