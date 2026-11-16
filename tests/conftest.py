import json
import os
import re
import tempfile

import httpx
import pytest

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test.db"
os.environ["CAMPAIGN_PREFIX"] = "test_unit_"
os.environ["KEITARO_API_KEY"] = "test"

from app.config import get_settings  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.keitaro import KeitaroClient, get_keitaro  # noqa: E402
from app.main import app  # noqa: E402


class FakeKeitaro:
    """In-memory imitation of the parts of Keitaro Admin API we use."""

    def __init__(self):
        self.offers = {
            1: {"id": 1, "name": "Offer one", "state": "active", "group": "G", "country": ["Peru"]},
            2: {"id": 2, "name": "Offer two", "state": "active", "group": "G", "country": []},
            3: {"id": 3, "name": "Offer three", "state": "active", "group": "G", "country": []},
            4: {"id": 4, "name": "Old offer", "state": "archived", "group": "G", "country": []},
        }
        self.campaigns: dict[int, dict] = {}
        self.streams: dict[int, dict] = {}
        self.seq = 100
        self.fail_stream_create = False

    def next_id(self):
        self.seq += 1
        return self.seq

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.replace("/admin_api/v1", "")
        body = json.loads(request.content) if request.content else None
        m = request.method

        if m == "GET" and path == "/domains":
            return httpx.Response(200, json=[{"id": 1813, "name": "example.com"}])
        if m == "GET" and path == "/groups":
            return httpx.Response(200, json=[{"id": 10332, "name": "FORTESTS"}])
        if m == "GET" and path == "/traffic_sources":
            return httpx.Response(200, json=[{"id": 836, "name": "FORTESTS"}])
        if m == "GET" and path == "/offers":
            return httpx.Response(200, json=list(self.offers.values()))
        if mo := re.fullmatch(r"/offers/(\d+)", path):
            o = self.offers.get(int(mo[1]))
            return httpx.Response(200, json=o) if o else httpx.Response(404, json={"error": "Not found"})
        if m == "GET" and path == "/campaigns":
            return httpx.Response(200, json=list(self.campaigns.values()))
        if m == "POST" and path == "/campaigns":
            if not body.get("alias"):
                return httpx.Response(400, json={"alias": ["Alias is required"]})
            cid = self.next_id()
            c = {**body, "id": cid, "domain": "https://example.com/", "group": "FORTESTS"}
            self.campaigns[cid] = c
            return httpx.Response(200, json=c)
        if mo := re.fullmatch(r"/campaigns/(\d+)", path):
            cid = int(mo[1])
            if cid not in self.campaigns:
                return httpx.Response(404, json={"error": "Not found"})
            if m == "DELETE":
                del self.campaigns[cid]
                return httpx.Response(200, json={})
            return httpx.Response(200, json=self.campaigns[cid])
        if mo := re.fullmatch(r"/campaigns/(\d+)/streams", path):
            cid = int(mo[1])
            if cid not in self.campaigns:
                return httpx.Response(404, json={"error": "Not found"})
            return httpx.Response(200, json=[s for s in self.streams.values() if s["campaign_id"] == cid])
        if m == "POST" and path == "/streams":
            if self.fail_stream_create and body.get("schema") == "landings":
                return httpx.Response(500, json={"error": "boom"})
            sid = self.next_id()
            s = {"filters": [], "offers": [], **body, "id": sid}
            s["offers"] = [{**o, "id": self.next_id(), "stream_id": sid} for o in s["offers"]]
            self.streams[sid] = s
            return httpx.Response(200, json=s)
        if mo := re.fullmatch(r"/streams/(\d+)", path):
            s = self.streams[int(mo[1])]
            if m == "PUT":
                if "offers" in body:
                    s["offers"] = [{**o, "stream_id": s["id"]} for o in body["offers"]]
                s.update({k: v for k, v in body.items() if k != "offers"})
            return httpx.Response(200, json=s)
        if m == "POST" and path == "/report/build":
            cid = body["filters"][0]["expression"]
            rows = []
            for s in self.streams.values():
                if s["campaign_id"] == cid:
                    for o in s["offers"]:
                        rows.append({"stream_id": s["id"], "offer_id": o["offer_id"], "clicks": 10,
                                     "campaign_unique_clicks": 7, "bots": 1, "conversions": 2, "revenue": 3.5})
            return httpx.Response(200, json={"rows": rows, "total": len(rows)})
        return httpx.Response(404, json={"error": f"unhandled {m} {path}"})


@pytest.fixture
async def fake():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    fk = FakeKeitaro()
    client = KeitaroClient(get_settings(), transport=httpx.MockTransport(fk.handler))
    app.dependency_overrides[get_keitaro] = lambda: client
    yield fk
    app.dependency_overrides.clear()
    await client.aclose()


@pytest.fixture
async def api(fake):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
