"""Configuration is validated at startup, not on first use."""

from pathlib import Path

import pytest

from app.config import ConfigError, Settings, load_settings

REQUIRED = ("SECRET_KEY", "DATABASE_URL", "JWT_SECRET")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No inherited variables and no .env within reach."""
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.mark.usefixtures("clean_env")
def test_missing_required_variables_raise_naming_them() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_settings()
    message = str(exc_info.value)
    for name in REQUIRED:
        assert name in message, f"{name} not named in the error"
    assert "infra/env/.env.example" in message


@pytest.mark.usefixtures("clean_env")
def test_partial_configuration_names_only_what_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_KEY", "s")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u@localhost/db")
    with pytest.raises(ConfigError) as exc_info:
        load_settings()
    message = str(exc_info.value)
    assert "JWT_SECRET" in message
    assert "SECRET_KEY" not in message
    assert "DATABASE_URL" not in message


@pytest.mark.usefixtures("clean_env")
def test_invalid_value_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in REQUIRED:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("JWT_ACCESS_TTL_SECONDS", "0")
    with pytest.raises(ConfigError, match="JWT_ACCESS_TTL_SECONDS"):
        load_settings()


def test_secrets_are_not_exposed_by_repr() -> None:
    """A settings object reaching a log or a traceback must not leak keys."""
    settings = Settings(
        secret_key="super-secret",
        database_url="postgresql+psycopg://u@localhost/db",
        jwt_secret="also-secret",
    )
    rendered = repr(settings) + str(settings.secret_key) + str(settings.jwt_secret)
    assert "super-secret" not in rendered
    assert "also-secret" not in rendered
    # The real value is still reachable where it is actually needed.
    assert settings.secret_key.get_secret_value() == "super-secret"


def test_optional_integration_settings_default_to_none() -> None:
    """Redis, Stripe, S3 and SES are not required until their features exist.

    Requiring a variable for code that does not run yet means a deployment
    fails for a dependency it never contacts.
    """
    settings = Settings(
        secret_key="s",
        database_url="postgresql+psycopg://u@localhost/db",
        jwt_secret="j",
    )
    assert settings.redis_url is None
    assert settings.stripe_secret_key is None
    assert settings.s3_bucket_media is None
    assert settings.ses_from_address is None


@pytest.mark.usefixtures("clean_env")
def test_settings_are_re_read_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_settings() reflects the current environment every call.

    A cached settings factory would hand back a stale object here, which is
    exactly what makes such a cache awkward to test around.
    """
    for name in REQUIRED:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    assert load_settings().log_level == "INFO"
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    assert load_settings().log_level == "DEBUG"
