from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # Portal Credentials & URL
    portal_base_url: str = Field(default="https://urja-ops.flockenergy.tech", alias="URJA_PORTAL_BASE_URL")
    portal_email: str = Field(default="operator@urja.local", alias="URJA_PORTAL_EMAIL")
    portal_password: str = Field(default="urja-ops-2026", alias="URJA_PORTAL_PASSWORD")

    # API Configuration
    api_title: str = "Flock Energy - Urja Meter Ops API"
    api_version: str = "1.0.0"
    api_description: str = (
        "Enterprise-grade REST API proxy and data normalization service for the legacy "
        "Urja Meter Ops portal. Provides automated session persistence, structured JSON models, "
        "consumption analytics, and network hierarchy mappings."
    )
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    # Client Request Settings
    request_timeout: float = 20.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

settings = Settings()
