from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


class Proxy6Client:
    BASE_URL = "https://px6.link/api"
    CACHE_TTL_MINUTES = 10
    VERSION_LABELS = {
        "3": "IPv4 Shared",
        "4": "IPv4",
        "5": "MTproto",
        "6": "IPv6",
    }

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.cache_path = Path("data/proxy6_buy_catalog.json")

    def get_account_info(self) -> dict[str, Any]:
        if not self.api_key:
            return {"configured": False}

        payload = self._call("getproxy")
        payload["configured"] = True
        return payload

    def buy_proxy(
        self,
        version: str,
        country: str,
        period: int,
        count: int = 1,
        proxy_type: str = "http",
    ) -> dict[str, Any]:
        """Покупает прокси на Proxy6 и возвращает ответ API.

        При успехе status == "yes" и в "list" лежат купленные прокси.
        """
        if not self.api_key:
            return {"configured": False, "status": "no", "error": "Proxy6 API key is not set"}

        params = {
            "count": str(max(1, int(count))),
            "period": str(max(1, int(period))),
            "country": country,
            "version": str(version),
            "type": proxy_type,
        }
        payload = self._call("buy", params)
        payload.setdefault("configured", True)
        return payload

    def buy_proxy_list(
        self,
        version: str,
        country: str,
        period: int,
        count: int = 1,
        proxy_type: str = "http",
    ) -> list[dict[str, Any]]:
        payload = self.buy_proxy(version, country, period, count, proxy_type)
        if payload.get("status") != "yes":
            return []
        proxy_list = payload.get("list")
        if not isinstance(proxy_list, dict):
            return []
        return [proxy for proxy in proxy_list.values() if isinstance(proxy, dict)]

    def _call(self, method: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        if not self.api_key:
            return {"configured": False}

        query = ""
        if params:
            query = f"?{urlencode(params)}"
        url = f"{self.BASE_URL}/{self.api_key}/{method}{query}"
        try:
            with urlopen(url, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            return {
                "configured": True,
                "error": str(exc),
            }

        return payload

    def list_proxies(self) -> list[dict[str, Any]]:
        payload = self.get_account_info()
        proxy_list = payload.get("list") if isinstance(payload, dict) else None
        if not isinstance(proxy_list, dict):
            return []
        return [
            proxy
            for proxy in proxy_list.values()
            if isinstance(proxy, dict)
        ]

    def get_purchase_catalog(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        if not force_refresh:
            cached = self._read_purchase_catalog_cache()
            if cached is not None:
                return cached

        items: list[dict[str, Any]] = []
        price_matrix = self._call("getprice").get("data")
        if not isinstance(price_matrix, dict):
            price_matrix = {}

        for version in ("4", "3", "5", "6"):
            countries_payload = self._call("getcountry", {"version": version})
            countries = countries_payload.get("list")
            if not isinstance(countries, list):
                continue

            version_prices_raw = price_matrix.get(version)
            periods: dict[str, float] = {}
            if isinstance(version_prices_raw, dict):
                for days, price in version_prices_raw.items():
                    try:
                        periods[str(days)] = float(price)
                    except (TypeError, ValueError):
                        continue

            for country in countries:
                country_code = str(country)
                count_payload = self._call(
                    "getcount",
                    {"country": country_code, "version": version},
                )
                if count_payload.get("status") != "yes":
                    continue
                count = int(count_payload.get("count") or 0)
                if count <= 0:
                    continue

                items.append(
                    {
                        "version": version,
                        "version_label": self.VERSION_LABELS.get(version, version),
                        "country": country_code,
                        "count": count,
                        "periods": periods,
                    }
                )

        self._write_purchase_catalog_cache(items)
        return items

    def _read_purchase_catalog_cache(self) -> list[dict[str, Any]] | None:
        if not self.cache_path.exists():
            return None
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        fetched_at_raw = payload.get("fetched_at")
        items = payload.get("items")
        if not isinstance(fetched_at_raw, str) or not isinstance(items, list):
            return None
        try:
            fetched_at = datetime.fromisoformat(fetched_at_raw)
        except ValueError:
            return None
        if datetime.now(timezone.utc) - fetched_at > timedelta(minutes=self.CACHE_TTL_MINUTES):
            return None
        return [item for item in items if isinstance(item, dict)]

    def _write_purchase_catalog_cache(self, items: list[dict[str, Any]]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "items": items,
        }
        self.cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
