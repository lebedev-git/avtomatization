from __future__ import annotations

from datetime import timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_host: str = "127.0.0.1"
    app_port: int = 8099
    app_log_level: str = "info"
    app_timezone: str = "Europe/Moscow"

    gemini_secure_1psid: str | None = None
    gemini_secure_1psidts: str | None = None
    gemini_cookie_json_path: str | None = None
    gemini_allow_browser_cookie_fallback: bool = True

    gemini_proxy: str | None = None
    gemini_verify_ssl: bool = True
    gemini_timeout_sec: float = 300
    gemini_auto_close: bool = False
    gemini_close_delay_sec: float = 300
    gemini_auto_refresh: bool = True
    gemini_refresh_interval_sec: float = 540
    gemini_watchdog_timeout_sec: float = 30
    gemini_verbose: bool = False

    downloads_dir: str = "downloads"
    captures_dir: str = "captures"
    agents_dir: str = "data/agents"
    n8n_inbox_dir: str = "downloads/n8n-inbox"
    n8n_latest_json_url: str = "https://lebedev2408.ru/webhook/8fca9f68-5b79-43f1-a3c9-317d49a554cf"
    n8n_base_url: str | None = None
    n8n_api_key: str | None = None
    n8n_fetch_workflow_id: str = "h9vyvsEgeLRw8SoybyRj4"
    n8n_report_ingest_url: str | None = None
    gemini_web_profile_dir: str = "profiles/gemini-runner"
    default_image_subdir: str = "generated"
    node_command: str = "node"
    chrome_executable_path: str = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    notebooklm_default_profile: str = "default"
    notebooklm_default_language: str = "ru"

    @field_validator(
        "gemini_secure_1psid",
        "gemini_secure_1psidts",
        "gemini_cookie_json_path",
        "gemini_proxy",
        "n8n_base_url",
        "n8n_api_key",
        "n8n_report_ingest_url",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, value: str | None) -> str | None:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    def project_root(self) -> Path:
        return Path.cwd().resolve()

    def downloads_root(self) -> Path:
        path = Path(self.downloads_dir)
        if not path.is_absolute():
            path = self.project_root() / path
        return path.resolve()

    def captures_root(self) -> Path:
        path = Path(self.captures_dir)
        if not path.is_absolute():
            path = self.project_root() / path
        return path.resolve()

    def agents_root(self) -> Path:
        path = Path(self.agents_dir)
        if not path.is_absolute():
            path = self.project_root() / path
        return path.resolve()

    def n8n_inbox_root(self) -> Path:
        path = Path(self.n8n_inbox_dir)
        if not path.is_absolute():
            path = self.project_root() / path
        return path.resolve()

    def web_profile_root(self) -> Path:
        path = Path(self.gemini_web_profile_dir)
        if not path.is_absolute():
            path = self.project_root() / path
        return path.resolve()

    def cookie_json_file(self) -> Path | None:
        if not self.gemini_cookie_json_path:
            return None
        path = Path(self.gemini_cookie_json_path)
        if not path.is_absolute():
            path = self.project_root() / path
        return path.resolve()

    def auth_sources_hint(self) -> list[str]:
        sources: list[str] = []
        if self.gemini_secure_1psid:
            sources.append("env_cookie")
        if self.gemini_cookie_json_path:
            sources.append("cookie_json")
        if not sources and self.gemini_allow_browser_cookie_fallback:
            sources.append("browser_auto")
        return sources

    def web_runner_script(self) -> Path:
        return (self.project_root() / "tools" / "gemini_web_runner.mjs").resolve()

    def timezone(self):
        try:
            return ZoneInfo(self.app_timezone)
        except ZoneInfoNotFoundError:
            return timezone.utc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
