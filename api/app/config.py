"""Application configuration.

Everything the app reads comes from the environment and is validated once, at
startup. A missing or malformed value stops the process with a message naming
the variable, rather than surfacing as a 500 on whichever request first needs
it (design 12.7).
"""

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Raised at startup when the environment is incomplete or invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # --- required: the process must not start without these ---
    secret_key: SecretStr
    database_url: str
    jwt_secret: SecretStr

    # --- core, with sensible defaults ---
    flask_env: str = "production"
    log_level: str = "INFO"
    frontend_origin: str = "http://localhost:5173"

    jwt_access_ttl_seconds: int = Field(default=900, gt=0)
    jwt_refresh_ttl_days: int = Field(default=30, gt=0)

    # How long a connection attempt may block. Short, because the readiness
    # probe must report a down database rather than hang on libpq's default.
    db_connect_timeout_seconds: int = Field(default=5, gt=0)

    # --- optional until the features that need them land ---
    # These become required alongside their integrations rather than blocking
    # startup now for code that does not exist yet.
    s3_endpoint_url: str | None = None
    s3_bucket_media: str | None = None
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    ses_from_address: str | None = None
    aws_region: str | None = None

    @property
    def is_debug(self) -> bool:
        return self.flask_env.lower() in {"development", "dev", "debug"}


def load_settings() -> Settings:
    """Build Settings, converting pydantic's error into an actionable one.

    The default ValidationError names fields in their python form; operators
    are looking for the environment variable, so it is spelled out here.
    """
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        problems = []
        for error in exc.errors():
            variable = str(error["loc"][0]).upper()
            problems.append(f"  {variable}: {error['msg']}")
        raise ConfigError(
            "Invalid configuration. Fix these environment variables "
            "(see infra/env/.env.example):\n" + "\n".join(problems)
        ) from exc
