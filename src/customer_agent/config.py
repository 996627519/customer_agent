"""Environment-backed application settings."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    app_name: str = "Customer Support Agent"
    app_env: str = "development"
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_database: str = "customer_agent"
    mysql_user: str = "customer_agent"
    mysql_password: str = ""
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"
    session_hours: int = 12
    allow_registration: bool = True
    cookie_secure: bool = False
    allowed_hosts: str = "localhost,127.0.0.1,[::1]"
    max_history_messages: int = 24
    max_context_chars: int = 18000
    policy_top_k: int = 5
    max_message_chars: int = 4000
    log_level: str = "INFO"


settings = Settings()
