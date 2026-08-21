from __future__ import annotations

import json
import os
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import keyring
from keyring.errors import KeyringError
from platformdirs import user_config_dir, user_data_dir

APP_NAME = "Anko Director"
APP_SLUG = "anko-director"
KEYRING_SERVICE = "anko-director"


@dataclass(slots=True)
class ProviderSettings:
    provider: str = "openai"
    parameter_model: str = "gpt-5.4-nano"
    story_model: str = "gpt-5.4"
    base_url: str | None = None


@dataclass(slots=True)
class AppSettings:
    provider: ProviderSettings = field(default_factory=ProviderSettings)
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True


class ConfigStore:
    """Stores non-secrets as JSON and credentials in the OS credential vault."""

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or Path(user_config_dir(APP_SLUG, roaming=True))
        self.path = self.config_dir / "settings.json"
        self._session_secrets: dict[str, str] = {}

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            provider = ProviderSettings(**raw.pop("provider", {}))
            return AppSettings(provider=provider, **raw)
        except (OSError, ValueError, TypeError):
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def set_secret(self, name: str, value: str) -> bool:
        if not value:
            self.delete_secret(name)
            return True
        try:
            keyring.set_password(KEYRING_SERVICE, name, value)
            self._session_secrets.pop(name, None)
            return True
        except KeyringError:
            self._session_secrets[name] = value
            return False

    def get_secret(self, name: str) -> str | None:
        env_name = {
            "openai_api_key": "OPENAI_API_KEY",
            "anthropic_api_key": "ANTHROPIC_API_KEY",
            "poe_api_key": "POE_API_KEY",
            "discord_bot_token": "DISCORD_BOT_TOKEN",
        }.get(name)
        if env_name and os.getenv(env_name):
            return os.environ[env_name]
        if name in self._session_secrets:
            return self._session_secrets[name]
        try:
            return keyring.get_password(KEYRING_SERVICE, name)
        except KeyringError:
            return None

    def delete_secret(self, name: str) -> None:
        self._session_secrets.pop(name, None)
        with suppress(KeyringError):
            keyring.delete_password(KEYRING_SERVICE, name)


def data_dir() -> Path:
    override = os.getenv("ANKO_DATA_DIR")
    path = Path(override) if override else Path(user_data_dir(APP_SLUG))
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return data_dir() / "anko-director.sqlite3"


def public_settings(settings: AppSettings, store: ConfigStore) -> dict[str, Any]:
    return {
        "provider": asdict(settings.provider),
        "host": settings.host,
        "port": settings.port,
        "open_browser": settings.open_browser,
        "secrets": {
            key: bool(store.get_secret(key))
            for key in (
                "openai_api_key",
                "anthropic_api_key",
                "poe_api_key",
                "discord_bot_token",
            )
        },
    }
