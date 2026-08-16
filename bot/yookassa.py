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
            if response.status_code >= 400:
                raise YooKassaError(f"HTTP {response.status_code}: {response.text}")
            parsed = json.loads(response.text)
            if not isinstance(parsed, dict):
                raise YooKassaError("Unexpected response")
            return parsed
        except requests.RequestException as exc:
            # Fallback к http.client с OP_IGNORE_UNEXPECTED_EOF для совместимости
            # с OpenSSL 3.0 / Python 3.12+ на разных ОС при закрытии TLS соединения.
            try:
                import base64
                import http.client
                import ssl

                ctx = ssl.create_default_context()
                if hasattr(ssl, "OP_IGNORE_UNEXPECTED_EOF"):
                    ctx.options |= ssl.OP_IGNORE_UNEXPECTED_EOF
                conn = http.client.HTTPSConnection("api.yookassa.ru", 443, context=ctx, timeout=30)
                auth_str = base64.b64encode(f"{self.shop_id}:{self.secret_key}".encode()).decode()
                req_headers = {
                    "Authorization": f"Basic {auth_str}",
                    "Content-Type": "application/json",
                    "Connection": "close",
                }
                if idempotence_key:
                    req_headers["Idempotence-Key"] = idempotence_key
                body_data = json.dumps(payload) if payload is not None else None
                conn.request(method, f"/v3{path}", body=body_data, headers=req_headers)
                resp = conn.getresponse()
                resp_text = resp.read().decode("utf-8")
                conn.close()
                if resp.status >= 400:
                    raise YooKassaError(f"HTTP {resp.status}: {resp_text}")
                parsed = json.loads(resp_text)
                if not isinstance(parsed, dict):
                    raise YooKassaError("Unexpected response")
                return parsed
            except YooKassaError:
                raise
            except Exception:
                raise YooKassaError(str(exc)) from exc

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
        if method and method in self.METHOD_TYPES:
            body["payment_method_data"] = {"type": self.METHOD_TYPES[method]}

        return self._request(
            "POST",
            "/payments",
            payload=body,
            idempotence_key=str(uuid.uuid4()),
        )

    def get_payment(self, payment_id: str) -> dict[str, Any] | None:
        result = self._request("GET", f"/payments/{payment_id}")
        return result if result.get("id") else None
