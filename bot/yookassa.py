from __future__ import annotations

import base64
import http.client
import json
import logging
import ssl
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class YooKassaError(RuntimeError):
    pass


class YooKassaClient:
    """Клиент к API ЮKassa (https://api.yookassa.ru/v3).

    Использует прямой HTTPS-клиент с поддержкой TLS 1.3 и ssl.OP_IGNORE_UNEXPECTED_EOF
    для мгновенных ответов (< 300мс) без таймаутов на Linux/Render и Windows.
    """

    HOST = "api.yookassa.ru"
    PORT = 443

    # Типы методов оплаты в ЮKassa
    METHOD_TYPES = {
        "sbp": "sbp",
        "card": "bank_card",
        "tinkoff": "tinkoff_bank",
        "sberbank": "sberbank",
    }

    def __init__(self, shop_id: str, secret_key: str) -> None:
        self.shop_id = shop_id.strip()
        self.secret_key = secret_key.strip()
        self._auth_header = (
            f"Basic {base64.b64encode(f'{self.shop_id}:{self.secret_key}'.encode()).decode()}"
            if self.shop_id and self.secret_key
            else ""
        )
        self._ssl_ctx = ssl.create_default_context()
        if hasattr(ssl, "OP_IGNORE_UNEXPECTED_EOF"):
            self._ssl_ctx.options |= ssl.OP_IGNORE_UNEXPECTED_EOF

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
            raise YooKassaError("YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY не настроены")

        headers = {
            "Authorization": self._auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Connection": "close",
        }
        if idempotence_key:
            headers["Idempotence-Key"] = idempotence_key

        body_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None

        try:
            conn = http.client.HTTPSConnection(
                self.HOST,
                self.PORT,
                context=self._ssl_ctx,
                timeout=15,
            )
            conn.request(method, f"/v3{path}", body=body_bytes, headers=headers)
            response = conn.getresponse()
            raw_data = response.read().decode("utf-8", errors="replace")
            conn.close()

            if response.status >= 400:
                logger.error("YooKassa API HTTP %s: %s", response.status, raw_data)
                try:
                    err_json = json.loads(raw_data)
                    err_desc = err_json.get("description") or raw_data
                except Exception:
                    err_desc = raw_data
                raise YooKassaError(f"HTTP {response.status}: {err_desc}")

            parsed = json.loads(raw_data)
            if not isinstance(parsed, dict):
                raise YooKassaError("Некорректный ответ от YooKassa API")
            return parsed

        except YooKassaError:
            raise
        except Exception as exc:
            logger.exception("YooKassa connection error: %s", exc)
            raise YooKassaError(f"Ошибка соединения с YooKassa: {exc}") from exc

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
        try:
            result = self._request("GET", f"/payments/{payment_id}")
            return result if result.get("id") else None
        except YooKassaError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise
