from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.config import Settings, get_settings
from app.db import get_session
from app.keitaro import KeitaroClient, KeitaroError, close_keitaro, get_keitaro
from app.schemas import CampaignCreate, CampaignDetail, OfferAdd, OfferOut, OffersPush, SharesUpdate
from app.services import ServiceError

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await close_keitaro()


app = FastAPI(title="Keitaro wrapper", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

Session = Annotated[AsyncSession, Depends(get_session)]
Keitaro = Annotated[KeitaroClient, Depends(get_keitaro)]
Conf = Annotated[Settings, Depends(get_settings)]


@app.exception_handler(KeitaroError)
async def keitaro_error_handler(_: Request, exc: KeitaroError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(ServiceError)
async def service_error_handler(_: Request, exc: ServiceError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


# --------------------------------------------------------------------------- pages
@app.get("/", include_in_schema=False)
async def index():
    # ?v=<mtime> on app.css / app.js: the browser always gets the current files after an update
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("app.css", "app.js"):
        version = int((STATIC / name).stat().st_mtime)
        html = html.replace(f"/static/{name}", f"/static/{name}?v={version}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


# --------------------------------------------------------------------------- dictionaries
@app.get("/api/meta", tags=["meta"])
async def meta(kt: Keitaro, settings: Conf):
    """Domains, campaign groups and traffic sources available to the API key + defaults."""
    domains = await kt.domains()
    groups = await kt.campaign_groups()
    sources = await kt.traffic_sources()
    return {
        "prefix": settings.campaign_prefix,
        "defaults": {
            "domain_id": settings.default_domain_id,
            "group_id": settings.default_group_id,
            "traffic_source_id": settings.default_traffic_source_id,
        },
        "domains": [{"id": d["id"], "name": d["name"]} for d in domains],
        "groups": [{"id": g["id"], "name": g["name"]} for g in groups],
        "traffic_sources": [{"id": t["id"], "name": t["name"]} for t in sources],
        "intervals": services.STATS_INTERVALS,
    }


@app.get("/api/offers", response_model=list[OfferOut], tags=["offers"])
async def search_offers(
    kt: Keitaro,
    q: str = Query("", description="ID or part of the offer name"),
    limit: int = Query(20, ge=1, le=100),
):
    """Offer autocomplete: matches by exact/partial ID or by name (case-insensitive)."""
    q = q.strip().lower()
    result = []
    for o in await kt.offers():
        if o.get("state") not in (None, "active"):
            continue
        if q and not (str(o["id"]).startswith(q) or q in o["name"].lower()):
            continue
        result.append(
            OfferOut(id=o["id"], name=o["name"], group=o.get("group"), country=o.get("country") or [], state=o.get("state"))
        )
    exact = [o for o in result if str(o.id) == q]
    rest = [o for o in result if str(o.id) != q]
    return (exact + rest)[:limit]


# --------------------------------------------------------------------------- campaigns
@app.get("/api/campaigns", tags=["campaigns"])
async def list_campaigns(kt: Keitaro, settings: Conf, mine: bool = True, q: str = ""):
    """Campaigns visible to the API key. `mine=true` keeps only names with our prefix."""
    q = q.strip().lower()
    out = []
    for c in await kt.campaigns():
        if mine and not c["name"].startswith(settings.campaign_prefix):
            continue
        if q and q not in c["name"].lower() and q != str(c["id"]):
            continue
        out.append({"id": c["id"], "name": c["name"], "state": c.get("state"), "group": c.get("group"),
                    "created_at": c.get("created_at")})
    out.sort(key=lambda c: c["id"], reverse=True)
    return out


@app.post("/api/campaigns", status_code=201, tags=["campaigns"])
async def create_campaign(data: CampaignCreate, session: Session, kt: Keitaro, settings: Conf):
    """Part 1: create a campaign with a geo->Google flow and an offer flow."""
    return await services.create_campaign(session, kt, settings, data)


@app.get("/api/campaigns/{campaign_id}", response_model=CampaignDetail, response_model_by_alias=True,
         tags=["campaigns"])
async def get_campaign(campaign_id: int, session: Session, kt: Keitaro, settings: Conf, interval: str = "today"):
    return await services.campaign_detail(session, kt, settings, campaign_id, interval)


@app.post("/api/campaigns/{campaign_id}/offers", response_model=CampaignDetail, response_model_by_alias=True,
          tags=["editor"])
async def add_offer(campaign_id: int, data: OfferAdd, session: Session, kt: Keitaro, settings: Conf, interval: str = "today"):
    """Part 2: add one offer (`offer_id`) or several (`offer_ids`) to the offer flow in one update;
    shares are re-split evenly."""
    await services.add_offers(session, kt, campaign_id, data.ids, data.stream_id)
    return await services.campaign_detail(session, kt, settings, campaign_id, interval)


@app.put("/api/campaigns/{campaign_id}/offers", response_model=CampaignDetail, response_model_by_alias=True,
         tags=["editor"])
async def push_offers(campaign_id: int, data: OffersPush, session: Session, kt: Keitaro, settings: Conf,
                      interval: str = "today"):
    """Push the editor's draft: the final list of offers left in the flow (added and removed in one update);
    shares are re-split evenly."""
    await services.push_offers(session, kt, campaign_id, data.offer_ids, data.stream_id)
    return await services.campaign_detail(session, kt, settings, campaign_id, interval)


@app.delete("/api/campaigns/{campaign_id}/offers/{offer_id}", response_model=CampaignDetail,
            response_model_by_alias=True, tags=["editor"])
async def remove_offer(campaign_id: int, offer_id: int, session: Session, kt: Keitaro, settings: Conf,
                       stream_id: int | None = None, interval: str = "today"):
    """Part 2: remove an offer from the flow; the remaining shares are re-split evenly."""
    await services.remove_offer(session, kt, campaign_id, offer_id, stream_id)
    return await services.campaign_detail(session, kt, settings, campaign_id, interval)


@app.put("/api/campaigns/{campaign_id}/shares", response_model=CampaignDetail, response_model_by_alias=True,
         tags=["editor"])
async def update_shares(campaign_id: int, data: SharesUpdate, session: Session, kt: Keitaro, settings: Conf, interval: str = "today"):
    """Set shares manually (must add up to 100)."""
    await services.set_shares(session, kt, campaign_id, {o.offer_id: o.share for o in data.offers}, data.stream_id)
    return await services.campaign_detail(session, kt, settings, campaign_id, interval)


@app.put("/api/campaigns/{campaign_id}/offers/{offer_id}/pin", response_model=CampaignDetail,
         response_model_by_alias=True, tags=["editor"])
async def pin_offer(campaign_id: int, offer_id: int, session: Session, kt: Keitaro, settings: Conf,
                    stream_id: int | None = None, interval: str = "today"):
    """Pin the offer's share: on the next add / remove it keeps its share, the other offers are re-split."""
    await services.set_pin(session, kt, campaign_id, offer_id, True, stream_id)
    return await services.campaign_detail(session, kt, settings, campaign_id, interval)


@app.delete("/api/campaigns/{campaign_id}/offers/{offer_id}/pin", response_model=CampaignDetail,
            response_model_by_alias=True, tags=["editor"])
async def unpin_offer(campaign_id: int, offer_id: int, session: Session, kt: Keitaro, settings: Conf,
                      stream_id: int | None = None, interval: str = "today"):
    """Unpin the offer's share."""
    await services.set_pin(session, kt, campaign_id, offer_id, False, stream_id)
    return await services.campaign_detail(session, kt, settings, campaign_id, interval)


@app.get("/api/campaigns/{campaign_id}/history", tags=["editor"])
async def campaign_history(campaign_id: int, session: Session):
    return await services.history(session, campaign_id)
