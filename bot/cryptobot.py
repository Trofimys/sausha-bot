from __future__ import annotations

import json
from typing import Any

import requests


class CryptoBotError(RuntimeError):
    pass


class CryptoBotClient:
    BASE_URL = "https://pay.crypt.bot/api"

    def __init__(self, api_token: str) -> None:
        self.api_token = api_token.strip()

    def is_configured(self) -> bool:
        return bool(self.api_token)

    def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        if not self.api_token:
            raise CryptoBotError("CRYPTO_PAY_TOKEN is not set")

        headers = {"Crypto-Pay-API-Token": self.api_token}
        try:
            response = requests.post(
                url=f"{self.BASE_URL}/{method}",
                headers=headers,
                json=payload or {},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise CryptoBotError(str(exc)) from exc

        if response.status_code >= 400:
            raise CryptoBotError(f"HTTP {response.status_code}: {response.text}")

        parsed = json.loads(response.text)
        if not parsed.get("ok"):
            raise CryptoBotError(str(parsed))
        return parsed.get("result")

    def create_invoice(self, amount_rub: float, description: str, payload: str) -> dict[str, Any]:
        result = self._call(
            "createInvoice",
            {
                "currency_type": "fiat",
                "fiat": "RUB",
                "amount": f"{amount_rub:.2f}",
                "description": description,
                "payload": payload,
                "allow_comments": False,
                "allow_anonymous": False,
            },
        )
        if not isinstance(result, dict):
            raise CryptoBotError("Unexpected createInvoice response")
        return result

    def get_invoice(self, invoice_id: str) -> dict[str, Any] | None:
        result = self._call("getInvoices", {"invoice_ids": invoice_id})
        if not isinstance(result, dict):
            return None
        items = result.get("items")
        if not isinstance(items, list) or not items:
            return None
        first = items[0]
        return first if isinstance(first, dict) else None
