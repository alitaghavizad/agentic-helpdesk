import pytest

from app.config import ConfigError, Settings


def _base_kwargs(**overrides):
    kwargs = dict(
        anthropic_api_key="sk-ant-test",
        database_url="postgresql+psycopg://postgres:123@localhost:5432/ticketing",
        jwt_secret="a-real-secret-value",
    )
    kwargs.update(overrides)
    return kwargs


def test_valid_config_passes_boot_validation():
    settings = Settings(**_base_kwargs())
    settings.validate_boot()  # must not raise


def test_missing_anthropic_key_fails_boot():
    settings = Settings(**_base_kwargs(anthropic_api_key=""))
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        settings.validate_boot()


def test_default_jwt_secret_fails_boot():
    settings = Settings(**_base_kwargs(jwt_secret="changeme-generate-a-real-secret"))
    with pytest.raises(ConfigError, match="JWT_SECRET"):
        settings.validate_boot()


def test_empty_jwt_secret_fails_boot():
    settings = Settings(**_base_kwargs(jwt_secret=""))
    with pytest.raises(ConfigError, match="JWT_SECRET"):
        settings.validate_boot()


def test_get_settings_loads_real_env_file_and_is_cached():
    from app.config import get_settings

    settings_a = get_settings()
    settings_b = get_settings()
    assert settings_a is settings_b
    assert settings_a.anthropic_api_key
