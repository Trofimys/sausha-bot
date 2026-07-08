from __future__ import annotations

import json
import uuid
from typing import Any

import requests


class YooKassaError(RuntimeError):
    pass


class YooKassaClient:
    """Тонкий клиент к API ЮKassa (https://api.yookassa.ru/v3).

    Оплата подтверждается опросом: создаём платёж (статус ``pending``),
    отдаём пользователю ``confirmation_url``, а по кнопке «Проверить оплату»
    запрашиваем статус платежа — ``succeeded`` означает успешную оплату.
    Это тот же polling-подход, что и у CryptoBot, поэтому webhook не нужен.
    """

    BASE_URL = "https://api.yookassa.ru/v3"

    # Тип метода оплаты в терминах ЮKassa.
    METHOD_TYPES = {"sbp": "sbp", "card": "bank_card"}

    def __init__(self, shop_id: str, secret_key: str) -> None:
        self.shop_id = shop_id.strip()
        self.secret_key = secret_key.strip()

    def is_configured(self) -> bool:
        return bool(self.shop_id and self.secret_key)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        idempotence_key: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured():
            raise YooKassaError("YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY is not set")

        headers = {"Content-Type": "application/json"}
        if idempotence_key:
            headers["Idempotence-Key"] = idempotence_key

        try:
            response = requests.request(
                method=method,
                url=f"{self.BASE_URL}{path}",
                headers=headers,
                auth=(self.shop_id, self.secret_key),
                json=payload,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise YooKassaError(str(exc)) from exc

        if response.status_code >= 400:
            raise YooKassaError(f"HTTP {response.status_code}: {response.text}")

        parsed = json.loads(response.text)
        if not isinstance(parsed, dict):
            raise YooKassaError("Unexpected response")
        return parsed

    def create_payment(
        self,
        amount_rub: float,
        description: str,
        payload: str,
        return_url: str,
        method: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": description[:128],
            "metadata": {"payload": payload},
        }
        method_type = self.METHOD_TYPES.get(method or "")
        if method_type:
            body["payment_method_data"] = {"type": method_type}

        return self._request(
            "POST",
            "/payments",
            payload=body,
            idempotence_key=str(uuid.uuid4()),
        )

    def get_payment(self, payment_id: str) -> dict[str, Any] | None:
        result = self._request("GET", f"/payments/{payment_id}")
        return result if result.get("id") else None
