from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Keitaro
    keitaro_url: str = "https://tlgk.host"
    keitaro_api_key: str = ""
    keitaro_timeout: float = 20.0

    # Defaults for new campaigns (IDs in Keitaro). Can be changed in the UI.
    default_domain_id: int = 1813  # kylovoa.com
    default_group_id: int = 10332  # FORTESTS
    default_traffic_source_id: int = 836  # FORTESTS

    # Every campaign name is forced to start with this prefix (test_<surname>_)
    campaign_prefix: str = "test_candidate_"

    # Where the "geo" flow sends visitors
    google_url: str = "https://google.com"

    database_url: str = "sqlite+aiosqlite:///./keitaro_wrapper.db"

    # How long the offers list from Keitaro is cached in memory (seconds)
    offers_cache_ttl: int = 300

    @property
    def admin_api_url(self) -> str:
        return self.keitaro_url.rstrip("/") + "/admin_api/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
