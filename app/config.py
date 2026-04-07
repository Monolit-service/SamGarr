from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _get_required(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        raise RuntimeError(f"Environment variable {name} is required")
    return value


def _get_optional(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def _get_int(name: str, default: int | None = None) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        if default is None:
            raise RuntimeError(f"Environment variable {name} is required")
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer") from exc


def _get_float(name: str, default: float | None = None) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        if default is None:
            raise RuntimeError(f"Environment variable {name} is required")
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be a float") from exc


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int_list(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    items: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            items.append(int(part))
        except ValueError as exc:
            raise RuntimeError(f"Environment variable {name} must contain integers separated by commas") from exc
    return tuple(items) if items else default


def _get_str_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    items = tuple(part.strip() for part in raw.split(",") if part.strip())
    return items or default


@dataclass(frozen=True)
class Settings:
    bot_token: str
    bot_username: str
    database_url: str
    channel_1_id: int
    channel_2_id: int
    channel_1_name: str
    channel_2_name: str
    private_30_price_xtr: int
    private_60_price_xtr: int
    invite_link_expire_hours: int
    check_expired_every_minutes: int
    check_pending_crypto_every_minutes: int
    trial_mode: bool
    donate_url: str
    crypto_pay_token: str
    crypto_pay_base_url_override: str
    crypto_pay_testnet: bool
    crypto_pay_asset: str
    crypto_usdt_per_star: float
    admin_ids: tuple[int, ...]

    @property
    def crypto_pay_enabled(self) -> bool:
        return bool(self.crypto_pay_token)

    @property
    def normalized_bot_username(self) -> str:
        return self.bot_username.strip().lstrip("@").strip()

    @property
    def bot_link(self) -> str | None:
        username = self.normalized_bot_username
        if not username:
            return None
        return f"https://t.me/{username}"

    @property
    def crypto_pay_base_url(self) -> str:
        override = self.crypto_pay_base_url_override.strip().rstrip("/")
        if override:
            return override
        domain = "https://testnet-pay.crypt.bot/api" if self.crypto_pay_testnet else "https://pay.crypt.bot/api"
        return domain


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        bot_token=_get_required("BOT_TOKEN"),
        bot_username=_get_required("BOT_USERNAME"),
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot.db"),
        channel_1_id=_get_int("CHANNEL_1_ID"),
        channel_2_id=_get_int("CHANNEL_2_ID", 0),
        channel_1_name=os.getenv("CHANNEL_1_NAME", "Приват"),
        channel_2_name=os.getenv("CHANNEL_2_NAME", ""),
        private_30_price_xtr=_get_int("PRIVATE_30_PRICE_XTR", 250),
        private_60_price_xtr=_get_int("PRIVATE_60_PRICE_XTR", 450),
        invite_link_expire_hours=_get_int("INVITE_LINK_EXPIRE_HOURS", 24),
        check_expired_every_minutes=_get_int("CHECK_EXPIRED_EVERY_MINUTES", 10),
        check_pending_crypto_every_minutes=_get_int("CHECK_PENDING_CRYPTO_EVERY_MINUTES", 3),
        trial_mode=_get_bool("TRIAL_MODE", False),
        donate_url=_get_optional("DONATE_URL", ""),
        crypto_pay_token=_get_optional("CRYPTO_PAY_TOKEN", ""),
        crypto_pay_base_url_override=_get_optional("CRYPTO_PAY_BASE_URL", ""),
        crypto_pay_testnet=_get_bool("CRYPTO_PAY_TESTNET", False),
        crypto_pay_asset=_get_optional("CRYPTO_PAY_ASSET", "USDT") or "USDT",
        crypto_usdt_per_star=_get_float("CRYPTO_USDT_PER_STAR", 0.01),
        admin_ids=_get_int_list("ADMIN_IDS", ()),
    )
