import re

from pydantic import BaseModel, Field, field_validator, model_validator

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
    """One offer (`offer_id`) or several at once (`offer_ids`), e.g. a draft pushed from the editor."""

    offer_id: int | None = Field(None, gt=0)
    offer_ids: list[int] = []
    stream_id: int | None = None

    @model_validator(mode="after")
    def _need_offer(self):
        if self.offer_id is None and not self.offer_ids:
            raise ValueError("Укажите offer_id или offer_ids")
        if any(i <= 0 for i in self.offer_ids):
            raise ValueError("ID офферов должны быть положительными")
        return self

    @property
    def ids(self) -> list[int]:
        return ([self.offer_id] if self.offer_id else []) + self.offer_ids


class OffersPush(BaseModel):
    """Final list of offers of the stream: missing ones are removed, new ones are added."""

    stream_id: int | None = None
    offer_ids: list[int] = Field(min_length=1)


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


class Trend(BaseModel):
    """Clicks of the offer in the selected period vs the previous period of the same length."""

    current: int
    previous: int
    change_pct: float | None  # None when the previous period had no clicks


class StreamOffer(BaseModel):
    offer_id: int
    name: str
    share: int
    state: str
    clicks: int = 0
    unique: int = 0
    bots: int = 0
    conversions: int = 0
    revenue: float = 0
    removed_at: str | None = None  # set for offers removed through the editor (share 0, not counted)
    pinned: bool = False  # share is pinned: kept as is when other offers are added/removed
    trend: Trend | None = None  # None for "all time" or when the trend report failed


class StreamOut(BaseModel):
    id: int
    name: str
    position: int
    schema_: str = Field(alias="schema", serialization_alias="schema")
    action_payload: str | None = None
    filters: list[dict] = []
    offers: list[StreamOffer] = []
    # removed from the stream in Keitaro, kept in the editor so they can be returned
    removed_offers: list[StreamOffer] = []
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
