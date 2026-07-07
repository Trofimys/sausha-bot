from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict


class ServerItem(TypedDict):
    code: str
    name: str
    price_rub: float
    markup_percent: float
    allowed_versions: list[str]
    allowed_countries: list[str]
    allowed_periods: list[int]


BASE_DIR = Path(__file__).resolve().parent.parent
CATALOG_PATH = BASE_DIR / "data" / "server_prices.json"

DEFAULT_SERVER_CATALOG: list[ServerItem] = [
    {
        "code": "holyworld",
        "name": "Holyworld",
        "price_rub": 39.0,
        "markup_percent": 20.0,
        "allowed_versions": [],
        "allowed_countries": [],
        "allowed_periods": [],
    },
    {
        "code": "funtime",
        "name": "Funtime",
        "price_rub": 59.0,
        "markup_percent": 20.0,
        "allowed_versions": [],
        "allowed_countries": [],
        "allowed_periods": [],
    },
    {
        "code": "reallyworld",
        "name": "Reallyworld",
        "price_rub": 59.0,
        "markup_percent": 20.0,
        "allowed_versions": [],
        "allowed_countries": [],
        "allowed_periods": [],
    },
    {
        "code": "spookytime",
        "name": "Spookytime",
        "price_rub": 59.0,
        "markup_percent": 20.0,
        "allowed_versions": [],
        "allowed_countries": [],
        "allowed_periods": [],
    },
]


def _ensure_catalog_file() -> None:
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CATALOG_PATH.exists():
        CATALOG_PATH.write_text(
            json.dumps(DEFAULT_SERVER_CATALOG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def load_server_catalog() -> list[ServerItem]:
    _ensure_catalog_file()
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_SERVER_CATALOG[:]

    items: list[ServerItem] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip()
            name = str(item.get("name") or code).strip()
            price = item.get("price_rub", 0)
            markup = item.get("markup_percent", 20)
            try:
                price_rub = float(price)
            except (TypeError, ValueError):
                price_rub = 0.0
            try:
                markup_percent = float(markup)
            except (TypeError, ValueError):
                markup_percent = 20.0
            if not code:
                continue
            items.append(
                {
                    "code": code,
                    "name": name,
                    "price_rub": price_rub,
                    "markup_percent": max(0.0, markup_percent),
                    "allowed_versions": _normalize_string_list(item.get("allowed_versions")),
                    "allowed_countries": _normalize_string_list(item.get("allowed_countries")),
                    "allowed_periods": _normalize_int_list(item.get("allowed_periods")),
                }
            )
    return items or DEFAULT_SERVER_CATALOG[:]


def get_server_map() -> dict[str, ServerItem]:
    return {item["code"]: item for item in load_server_catalog()}


def save_server_catalog(items: list[ServerItem]) -> None:
    _ensure_catalog_file()
    CATALOG_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def update_server_item(server_code: str, updates: dict[str, Any]) -> ServerItem | None:
    catalog = load_server_catalog()
    for item in catalog:
        if item["code"] != server_code:
            continue
        if "name" in updates:
            item["name"] = str(updates["name"] or item["name"]).strip() or item["name"]
        if "price_rub" in updates:
            try:
                item["price_rub"] = float(updates["price_rub"])
            except (TypeError, ValueError):
                pass
        if "markup_percent" in updates:
            try:
                item["markup_percent"] = max(0.0, float(updates["markup_percent"]))
            except (TypeError, ValueError):
                pass
        if "allowed_versions" in updates:
            item["allowed_versions"] = _normalize_string_list(updates["allowed_versions"])
        if "allowed_countries" in updates:
            item["allowed_countries"] = _normalize_string_list(updates["allowed_countries"])
        if "allowed_periods" in updates:
            item["allowed_periods"] = _normalize_int_list(updates["allowed_periods"])
        save_server_catalog(catalog)
        return item
    return None


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip().lower()
        if text and text not in result:
            result.append(text)
    return result


def _normalize_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in result:
            result.append(number)
    return sorted(result)
