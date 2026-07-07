from __future__ import annotations

import json
import logging
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class TelegramAPIError(RuntimeError):
    pass


class TelegramAPI:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"

    def _read_json(self, request: Request, timeout: int) -> Any:
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise TelegramAPIError(f"Network error: {exc}") from exc

        payload = json.loads(raw)
        if not payload.get("ok"):
            raise TelegramAPIError(str(payload))
        return payload["result"]

    def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(
            url=f"{self.base_url}/{method}",
            data=data,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )
        return self._read_json(request, timeout=30)

    def get_me(self, retries: int = 5, retry_delay: float = 3.0) -> dict[str, Any]:
        """getMe с ретраями: старт не должен падать от одного сетевого блипа."""
        last_error: TelegramAPIError | None = None
        for attempt in range(1, retries + 1):
            try:
                return self.call("getMe")
            except TelegramAPIError as exc:
                last_error = exc
                if attempt < retries:
                    logging.warning(
                        "getMe failed (attempt %s/%s): %s — retrying in %.0fs",
                        attempt,
                        retries,
                        exc,
                        retry_delay,
                    )
                    time.sleep(retry_delay)
        assert last_error is not None
        raise last_error

    def delete_webhook(self, drop_pending_updates: bool = True) -> dict[str, Any]:
        return self.call(
            "deleteWebhook",
            {"drop_pending_updates": drop_pending_updates},
        )

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            query["offset"] = offset
        query["allowed_updates"] = json.dumps(["message", "callback_query"])
        request = Request(
            url=f"{self.base_url}/getUpdates?{urlencode(query)}",
            method="GET",
        )
        return self._read_json(request, timeout=timeout + 10)

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call("sendMessage", payload)

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text is not None:
            payload["text"] = text
        return self.call("answerCallbackQuery", payload)

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call("editMessageText", payload)

    def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: dict[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup,
        }
        return self.call("editMessageReplyMarkup", payload)

    def edit_message_caption(
        self,
        chat_id: int,
        message_id: int,
        caption: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": caption,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call("editMessageCaption", payload)

    def edit_message_media(
        self,
        chat_id: int,
        message_id: int,
        media_path: Path,
        caption: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        boundary = f"----CodexBoundary{uuid.uuid4().hex}"
        mime_type = mimetypes.guess_type(media_path.name)[0] or "application/octet-stream"
        media_bytes = media_path.read_bytes()
        parts: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )

        add_field("chat_id", str(chat_id))
        add_field("message_id", str(message_id))
        add_field(
            "media",
            json.dumps(
                {
                    "type": "photo",
                    "media": "attach://photo",
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                ensure_ascii=False,
            ),
        )
        if reply_markup is not None:
            add_field("reply_markup", json.dumps(reply_markup, ensure_ascii=False))

        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="photo"; filename="{media_path.name}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(media_bytes)
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

        request = Request(
            url=f"{self.base_url}/editMessageMedia",
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        return self._read_json(request, timeout=30)

    def send_photo(
        self,
        chat_id: int,
        photo_path: Path,
        caption: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        boundary = f"----CodexBoundary{uuid.uuid4().hex}"
        mime_type = mimetypes.guess_type(photo_path.name)[0] or "application/octet-stream"
        photo_bytes = photo_path.read_bytes()
        parts: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )

        add_field("chat_id", str(chat_id))
        add_field("caption", caption)
        add_field("parse_mode", "HTML")
        if reply_markup is not None:
            add_field("reply_markup", json.dumps(reply_markup, ensure_ascii=False))

        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="photo"; filename="{photo_path.name}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(photo_bytes)
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

        request = Request(
            url=f"{self.base_url}/sendPhoto",
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        return self._read_json(request, timeout=30)
