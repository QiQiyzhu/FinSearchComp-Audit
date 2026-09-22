from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"true", "1", "yes"}


def integer(name: str, default: int, low: int, high: int) -> int:
    return max(low, min(high, int(os.getenv(name, str(default)))))


@dataclass(frozen=True)
class Settings:
    database: Path = field(default_factory=lambda: ROOT / ".workbench/research.sqlite3")
    cache_dir: Path = field(default_factory=lambda: ROOT / ".workbench/cache")
    enable_live: bool = False
    public_live: bool = False
    api_token: str = field(default="", repr=False)
    sec_user_agent: str = ""
    deepseek_api_key: str = field(default="", repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    tavily_api_key: str = field(default="", repr=False)
    trust_env: bool = False
    max_workers: int = 2
    max_pending: int = 16
    live_requests_per_hour: int = 10
    live_global_per_day: int = 40
    demo_requests_per_hour: int = 120
    retention_days: int = 7
    max_history: int = 1000
    allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
        settings = cls(
            database=Path(os.getenv("FINAGENT_DB_PATH", str(ROOT / ".workbench/research.sqlite3"))),
            cache_dir=Path(os.getenv("FINAGENT_CACHE_DIR", str(ROOT / ".workbench/cache"))),
            enable_live=flag("FINAGENT_ENABLE_LIVE"),
            public_live=flag("FINAGENT_PUBLIC_LIVE"),
            api_token=os.getenv("FINAGENT_API_TOKEN", "").strip(),
            sec_user_agent=os.getenv("SEC_USER_AGENT", "").strip(),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            tavily_api_key=os.getenv("TAVILY_API_KEY", "").strip(),
            trust_env=flag("FINAGENT_HTTP_TRUST_ENV"),
            max_workers=integer("FINAGENT_MAX_WORKERS", 2, 1, 4),
            max_pending=integer("FINAGENT_MAX_PENDING", 16, 1, 100),
            live_requests_per_hour=integer("FINAGENT_LIVE_REQUESTS_PER_HOUR", 10, 1, 1000),
            live_global_per_day=integer("FINAGENT_LIVE_GLOBAL_PER_DAY", 40, 1, 10000),
            retention_days=integer("FINAGENT_RETENTION_DAYS", 7, 1, 90),
            allowed_origins=tuple(origin.strip().rstrip("/") for origin in os.getenv("FINAGENT_ALLOWED_ORIGINS", "").split(",") if origin.strip()),
        )
        # A configured provider URL is administrator-controlled, never request input.
        if urlparse(settings.deepseek_base_url).scheme != "https":
            raise ValueError("DEEPSEEK_BASE_URL must use HTTPS")
        return settings

    def available(self, mode: str) -> bool:
        if mode == "demo":
            return True
        if not self.enable_live or not (self.api_token or self.public_live):
            return False
        return bool(self.deepseek_api_key) if mode == "snapshot" else bool(self.sec_user_agent)
