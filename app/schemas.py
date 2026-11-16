import re

from pydantic import BaseModel, Field, field_validator

_GEO_RE = re.compile(r"^[A-Z]{2}$")


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    geo: list[str] = Field(min_length=1, description="ISO 3166-1 alpha-2 country codes, e.g. ['MX', 'AU']")
    offer_id: int = Field(gt=0)
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Название не может быть пустым")
        return v

    @field_validator("geo", mode="before")
    @classmethod
    def _parse_geo(cls, v):
        if isinstance(v, str):
            v = re.split(r"[\s,;]+", v)
        codes: list[str] = []
        for code in v:
            code = str(code).strip().upper()
            if not code:
                continue
            if code == "UK":  # common alias, Keitaro uses ISO code GB
                code = "GB"
            if not _GEO_RE.match(code):
                raise ValueError(f"Неверный код страны: {code!r} (нужен ISO-код из 2 букв, например AU)")
            if code not in codes:
                codes.append(code)
        if not codes:
            raise ValueError("Укажите хотя бы одну страну")
        return codes


class OfferAdd(BaseModel):
    offer_id: int = Field(gt=0)
    stream_id: int | None = None


class ShareItem(BaseModel):
    offer_id: int
    share: int = Field(ge=0, le=100)


class SharesUpdate(BaseModel):
    stream_id: int | None = None
    offers: list[ShareItem]


class OfferOut(BaseModel):
    id: int
    name: str
    group: str | None = None
    country: list[str] = []
    state: str | None = None


class StreamOffer(BaseModel):
    offer_id: int
    name: str
    share: int
    state: str
    clicks: int = 0
    unique: int = 0
    conversions: int = 0
    revenue: float = 0


class StreamOut(BaseModel):
    id: int
    name: str
    position: int
    schema_: str = Field(alias="schema", serialization_alias="schema")
    action_payload: str | None = None
    filters: list[dict] = []
    offers: list[StreamOffer] = []
    clicks: int = 0
    unique: int = 0
    bots: int = 0

    model_config = {"populate_by_name": True}


class CampaignDetail(BaseModel):
    id: int
    name: str
    alias: str
    url: str | None
    state: str
    group: str | None = None
    domain: str | None = None
    traffic_source_id: int | None = None
    offer_stream_id: int | None
    streams: list[StreamOut]
    stats_interval: str
    stats_error: str | None = None
