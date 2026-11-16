from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Campaign, CampaignOffer, OperationLog


async def create(api, **kw):
    body = {"name": "camp", "geo": "au, mx", "offer_id": 1, **kw}
    return await api.post("/api/campaigns", json=body)


def offer_stream(detail):
    return next(s for s in detail["streams"] if s["id"] == detail["offer_stream_id"])


async def test_create_campaign(api, fake):
    r = await create(api)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["name"] == "test_unit_camp"  # prefix enforced
    assert data["geo"] == ["AU", "MX"]
    assert data["url"].startswith("https://example.com/")

    camp = fake.campaigns[data["id"]]
    assert (camp["domain_id"], camp["group_id"], camp["traffic_source_id"]) == (1813, 10332, 836)
    assert camp["type"] == "position"

    s1, s2 = sorted((s for s in fake.streams.values()), key=lambda s: s["position"])
    assert s1["action_payload"] == "https://google.com" and s1["schema"] == "redirect"
    assert s1["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU", "MX"]}]
    assert s2["schema"] == "landings"
    assert [(o["offer_id"], o["share"]) for o in s2["offers"]] == [(1, 100)]

    async with SessionLocal() as s:
        c = (await s.execute(select(Campaign).where(Campaign.keitaro_id == data["id"]))).scalar_one()
        assert c.geo == ["AU", "MX"] and c.geo_stream_id == s1["id"] and c.offer_stream_id == s2["id"]


async def test_prefix_not_duplicated(api):
    r = await create(api, name="test_unit_x")
    assert r.json()["name"] == "test_unit_x"


async def test_create_validation(api, fake):
    assert (await create(api, geo="AUS")).status_code == 422
    assert (await create(api, geo="")).status_code == 422
    r = await create(api, offer_id=999)
    assert r.status_code == 404
    assert fake.campaigns == {}  # nothing created in Keitaro


async def test_create_rolls_back_on_stream_error(api, fake):
    fake.fail_stream_create = True
    r = await create(api)
    assert r.status_code == 502
    assert fake.campaigns == {}


async def test_add_and_remove_offers(api, fake):
    cid = (await create(api)).json()["id"]

    d = (await api.post(f"/api/campaigns/{cid}/offers", json={"offer_id": 2})).json()
    assert [(o["offer_id"], o["share"]) for o in offer_stream(d)["offers"]] == [(1, 50), (2, 50)]

    d = (await api.post(f"/api/campaigns/{cid}/offers", json={"offer_id": 3})).json()
    assert [(o["offer_id"], o["share"]) for o in offer_stream(d)["offers"]] == [(1, 34), (2, 33), (3, 33)]

    r = await api.post(f"/api/campaigns/{cid}/offers", json={"offer_id": 3})
    assert r.status_code == 409

    d = (await api.delete(f"/api/campaigns/{cid}/offers/1")).json()
    assert [(o["offer_id"], o["share"]) for o in offer_stream(d)["offers"]] == [(2, 50), (3, 50)]

    # local mirror and log follow Keitaro
    async with SessionLocal() as s:
        rows = (await s.execute(select(CampaignOffer.offer_id, CampaignOffer.share)
                                .order_by(CampaignOffer.position))).all()
        assert [tuple(r) for r in rows] == [(2, 50), (3, 50)]
        actions = (await s.execute(select(OperationLog.action).order_by(OperationLog.id))).scalars().all()
        assert actions == ["create_campaign", "add_offer", "add_offer", "remove_offer"]


async def test_cannot_remove_last_or_missing_offer(api):
    cid = (await create(api)).json()["id"]
    assert (await api.delete(f"/api/campaigns/{cid}/offers/1")).status_code == 400
    assert (await api.delete(f"/api/campaigns/{cid}/offers/2")).status_code == 404


async def test_manual_shares(api):
    cid = (await create(api)).json()["id"]
    await api.post(f"/api/campaigns/{cid}/offers", json={"offer_id": 2})
    r = await api.put(f"/api/campaigns/{cid}/shares",
                      json={"offers": [{"offer_id": 1, "share": 80}, {"offer_id": 2, "share": 20}]})
    assert [(o["offer_id"], o["share"]) for o in offer_stream(r.json())["offers"]] == [(1, 80), (2, 20)]
    r = await api.put(f"/api/campaigns/{cid}/shares",
                      json={"offers": [{"offer_id": 1, "share": 80}, {"offer_id": 2, "share": 30}]})
    assert r.status_code == 400


async def test_detail_stats_and_names(api):
    cid = (await create(api)).json()["id"]
    d = (await api.get(f"/api/campaigns/{cid}")).json()
    s = offer_stream(d)
    assert s["offers"][0]["name"] == "Offer one"
    assert (s["clicks"], s["unique"], s["bots"]) == (10, 7, 1)
    assert s["offers"][0]["conversions"] == 2


async def test_offer_search(api):
    r = (await api.get("/api/offers", params={"q": "two"})).json()
    assert [o["id"] for o in r] == [2]
    r = (await api.get("/api/offers", params={"q": ""})).json()
    assert 4 not in [o["id"] for o in r]  # archived offers hidden


async def test_unknown_campaign(api):
    assert (await api.get("/api/campaigns/5555")).status_code == 404


async def test_list_mine(api, fake):
    await create(api)
    fake.campaigns[1] = {"id": 1, "name": "someone_else", "alias": "x"}
    names = [c["name"] for c in (await api.get("/api/campaigns")).json()]
    assert names == ["test_unit_camp"]
    async with SessionLocal() as s:
        assert (await s.execute(select(func.count(Campaign.id)))).scalar() == 1
