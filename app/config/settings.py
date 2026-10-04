"""Application configuration (environment driven, 12-factor style)."""
from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "REACT"
    # Domain pack a fresh seed produces: merchant (REACT Merchant, default) | manufacturing (reference pack)
    react_domain: str = "merchant"
    # local | development | test | staging | production  (local/development behave the same)
    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://react:react@localhost:5432/react"
    test_database_url: str | None = None

    # ---- Phase 6: AWS / container deployment ------------------------------------------------------
    # On ECS the database connection arrives as separate variables: DB_HOST / DB_PORT / DB_NAME as plain
    # environment, DB_USER / DB_PASSWORD injected by ECS from the RDS-managed Secrets Manager secret.
    # When DB_HOST is set, DATABASE_URL is composed from them (the password is URL-encoded, never logged).
    db_host: str | None = None
    db_port: int = 5432
    db_name: str = "react"
    db_user: str | None = None
    db_password: str | None = None
    db_sslmode: str | None = None  # e.g. "require" on RDS
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # Evidence packs and other artifacts: "local" (a directory, default) or "s3" (ARTIFACTS_BUCKET)
    storage_backend: str = "local"
    storage_local_dir: str = "var/artifacts"
    artifacts_bucket: str | None = None
    artifacts_prefix: str = "evidence-packs/"

    # CloudWatch Embedded Metric Format lines on stdout (ECS awslogs ships them; no AWS API call is made)
    metrics_enabled: bool = False
    metrics_namespace: str = "REACT"

    # CloudFront forwards /api/* to the API unchanged; this prefix is stripped before routing
    api_path_prefix: str = "/api"

    # Cost guard: real LLM calls allowed per rolling hour across ALL API tasks (counted in the database).
    # When reached, runs use the deterministic fallback (or fail safely if fallback is disabled).
    llm_hourly_call_budget: int = 200

    # Allows POST /demo/ai-outage (simulated AI failure drill). Disable in production.
    demo_controls_enabled: bool = True

    auto_create_schema: bool = True
    seed_on_startup: bool = True

    # Deterministic synthetic data generation
    seed: int = 42
    history_days: int = 120
    sensor_interval_minutes: int = 60

    # Reserved for later phases (not used in Phase 1)
    redis_url: str | None = None

    # ---- Phase 5: product UI --------------------------------------------------------------
    # Comma-separated origins allowed by CORS (the Vite dev server proxies /api, so this is only for other hosts)
    cors_origins: str = "http://localhost:5180,http://127.0.0.1:5180"

    # ---- Phase 3: agentic layer ---------------------------------------------------------
    agents_enabled: bool = True
    # deterministic (rule-based, no LLM; always available) | bedrock (Amazon Bedrock converse API) | mock (tests)
    llm_provider: str = "deterministic"
    # Bedrock model or inference-profile ID. Deliberately no default: it must come from configuration (LLM_MODEL).
    llm_model: str = ""
    llm_max_tokens: int = 2048
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 1
    aws_region: str = "us-east-1"
    # AWS credentials are NEVER configured here: boto3 uses its standard credential chain (env, profile, role).
    agent_max_iterations: int = 4
    agent_max_tool_calls: int = 12
    agent_max_llm_calls: int = 10
    agent_max_execution_seconds: float = 120.0
    # When the configured LLM provider fails, fall back to the deterministic provider (recorded in the trace) instead of failing the run
    agent_fallback_to_deterministic: bool = True

    @model_validator(mode="after")
    def _compose_database_url(self) -> "Settings":
        if self.db_host:
            user = quote(self.db_user or "", safe="")
            password = quote(self.db_password or "", safe="")
            auth = f"{user}:{password}@" if password else (f"{user}@" if user else "")
            url = f"postgresql+psycopg://{auth}{self.db_host}:{self.db_port}/{self.db_name}"
            if self.db_sslmode:
                url += f"?sslmode={self.db_sslmode}"
            self.database_url = url
        return self

    @property
    def is_local(self) -> bool:
        return self.app_env.lower() in ("local", "development", "dev", "test")

    @property
    def effective_test_database_url(self) -> str:
        if self.test_database_url:
            return self.test_database_url
        return self.database_url.rstrip("/") + "_test"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
