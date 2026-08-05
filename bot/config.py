from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    bot_token: str
    admin_ids: set[int]
    proxy6_api_key: str
    crypto_pay_token: str
    yookassa_shop_id: str
    yookassa_secret_key: str
    db_path: Path
    start_image_path: Path | None
    profile_image_path: Path | None
    top_up_image_path: Path | None


def load_settings() -> Settings:
    env_path = Path(".env")
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set")

    admin_ids_raw = os.getenv("ADMIN_IDS", "").strip()
    admin_ids = {
        int(value.strip())
        for value in admin_ids_raw.split(",")
        if value.strip()
    }
    if not admin_ids:
        raise RuntimeError("ADMIN_IDS is not set")

    proxy6_api_key = os.getenv("PROXY6_API_KEY", "").strip()
    crypto_pay_token = os.getenv("CRYPTO_PAY_TOKEN", "").strip()
    yookassa_shop_id = os.getenv("YOOKASSA_SHOP_ID", "").strip()
    yookassa_secret_key = os.getenv("YOOKASSA_SECRET_KEY", "").strip()
    db_path = Path(os.getenv("DB_PATH", "data/bot.sqlite3")).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    start_image_path_raw = os.getenv("START_IMAGE_PATH", "").strip()
    profile_image_path_raw = os.getenv("PROFILE_IMAGE_PATH", "").strip()
    top_up_image_path_raw = os.getenv("TOP_UP_IMAGE_PATH", "").strip()

    start_image_path: Path | None = None
    if start_image_path_raw:
        candidate = Path(start_image_path_raw).expanduser()
        if candidate.exists():
            start_image_path = candidate
    else:
        for pattern in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            matches = sorted(Path(".").glob(pattern))
            if matches:
                start_image_path = matches[0]
                break

    profile_image_path: Path | None = None
    if profile_image_path_raw:
        candidate = Path(profile_image_path_raw).expanduser()
        if candidate.exists():
            profile_image_path = candidate

    top_up_image_path: Path | None = None
    if top_up_image_path_raw:
        candidate = Path(top_up_image_path_raw).expanduser()
        if candidate.exists():
            top_up_image_path = candidate

    return Settings(
        bot_token=bot_token,
        admin_ids=admin_ids,
        proxy6_api_key=proxy6_api_key,
        crypto_pay_token=crypto_pay_token,
        yookassa_shop_id=yookassa_shop_id,
        yookassa_secret_key=yookassa_secret_key,
        db_path=db_path,
        start_image_path=start_image_path,
        profile_image_path=profile_image_path,
        top_up_image_path=top_up_image_path,
    )
