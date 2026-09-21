"""
Centralized configuration. Every secret and environment-specific value
comes from the environment, never hardcoded. In production these are
injected by the host (Docker secrets, cloud env vars); locally they come
from .env (see .env.example).
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "FarmLink"
    environment: str = "development"  # development | staging | production
    api_v1_prefix: str = "/api/v1"

    # Postgres
    database_url: str  # e.g. postgresql+asyncpg://user:pass@host:5432/farmlink

    # Redis (cache + rate limiter)
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24h — revisit for production

    # Rate limiting - general API traffic
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # Stricter limit for auth endpoints specifically (login/register).
    # The general limit above is tuned for normal API browsing; login
    # attempts need a much tighter budget or the general limit does
    # nothing to slow down credential stuffing / registration spam.
    auth_rate_limit_requests: int = 5
    auth_rate_limit_window_seconds: int = 300

    # Cache (Redis, cache-aside pattern)
    pool_cache_ttl_seconds: int = 30

    # Caching - short TTL is deliberate: pool availability changes on every
    # order, so a stale cache directly risks showing a buyer stock that's
    # already gone. This is a "smooth out read traffic between writes"
    # cache, not a "data barely changes" cache - the invalidation on every
    # write matters more here than the TTL does.
    pool_cache_ttl_seconds: int = 30

    # CORS — comma-separated list of allowed origins, tightened per environment
    cors_allowed_origins: str = "http://localhost:3000"

    # Paystack — test keys in dev/staging, live keys only in production.
    # NEVER commit real keys; these are read from the environment only.
    paystack_secret_key: str
    paystack_public_key: str
    paystack_webhook_ip_allowlist: bool = True  # Paystack publishes fixed webhook IPs

    # Firestore (real-time feed)
    firestore_project_id: str | None = None
    firestore_credentials_path: str | None = None  # path to service account json

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
