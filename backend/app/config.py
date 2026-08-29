from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class ConfigError(RuntimeError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"

    # Default is already covered by .gitignore's `storage/uploads/`, so stored
    # files can never become untracked working-tree noise. Tests point this at
    # a temporary directory.
    attachment_storage_dir: str = "storage/uploads"

    database_url: str = ""
    chroma_url: str = "http://localhost:8000"
    chroma_backend: str = "mcp"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    # True selects implicit TLS (SMTP_SSL); false selects STARTTLS. Port 465
    # implies implicit TLS regardless, because no server speaks STARTTLS
    # there. Spec 9.3 assumes 587/STARTTLS; the configured account is
    # 465/implicit. Supporting both is amendment 2.4 of the phase 6 design.
    smtp_secure: bool = False
    # Comma-separated glob patterns. EMPTY MEANS SEND TO NOBODY -- this fails
    # closed on purpose, so a missing config value can never widen the blast
    # radius of an approved send_email action.
    email_recipient_allowlist: str = ""

    jwt_secret: str = ""

    admin_username: str = "admin"
    admin_password: str = "admin"
    seed_user_password: str = "Passw0rd!dev"

    max_cost_per_conversation_usd: float = 0.50
    max_tool_iterations: int = 12

    model_pricing_overrides: str = ""

    agent_enabled: bool = True
    backend_host: str = "127.0.0.1"
    backend_port: int = 8080
    frontend_origin: str = "http://localhost:5173"

    def validate_boot(self) -> None:
        missing = []
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.database_url:
            missing.append("DATABASE_URL")
        if not self.jwt_secret or self.jwt_secret == "changeme-generate-a-real-secret":
            missing.append("JWT_SECRET (unset or still the example placeholder)")
        if missing:
            raise ConfigError(
                "Missing or invalid required configuration: " + ", ".join(missing)
            )
        if self.admin_password == "admin":
            print(
                "WARNING: ADMIN_PASSWORD is the default 'admin'. "
                "This is insecure outside local development.",
                flush=True,
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_boot()
    return settings
