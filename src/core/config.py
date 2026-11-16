import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv(override=True)


class Settings(BaseSettings):
    app_env: str = os.getenv("APP_ENV", "development")
    api_port: int = int(os.getenv("API_PORT", 8000))
    skyvern_base_url: str = os.getenv("SKYVERN_BASE_URL", "http://localhost:8000")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    browser_debugging_url: str = os.getenv("BROWSER_REMOTE_DEBUGGING_URL", "")
    artifacts_dir: str = os.getenv("ARTIFACTS_DIR", "artifacts")
    artifact_retention_days: int = int(os.getenv("ARTIFACT_RETENTION_DAYS", 7))
    auto_prune_artifacts: bool = os.getenv("AUTO_PRUNE_ARTIFACTS", "true").lower() in ("true", "1", "yes")
    default_cdp_url: str = os.getenv("DEFAULT_CDP_URL", "http://localhost:9222")
    enable_cdp: bool = os.getenv("ENABLE_CDP", "false").lower() in ("true", "1", "yes")

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
