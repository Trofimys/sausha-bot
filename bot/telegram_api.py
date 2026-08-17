from __future__ import annotations

import json
import logging
import mimetypes
import time
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class TelegramAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        error_code: int | None = None,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.description = description or message
        self.parameters = parameters or {}


class TelegramAPI:
    """Высокопроизводительный клиент Telegram Bot API.

    Использует пул постоянных HTTP Keep-Alive соединений (requests.Session)
    и постоянный SQLite кэш file_id для мгновенного отклика (20-40 мс).
    """

    def __init__(self, token: str, db: Any | None = None) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.db = db
        self._file_id_cache: dict[str, str] = {}

        # Настраиваем постоянный HTTP/1.1 Keep-Alive пул соединений
        self._session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            pool_connections=20,
            pool_maxsize=30,
            max_retries=retries,
            pool_block=False,
        )
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _handle_response(self, response: requests.Response) -> Any:
        try:
            payload = response.json()
        except Exception:
            payload = {}

        if response.status_code == 429:
            parameters = payload.get("parameters") or {}
            retry_after = int(parameters.get("retry_after") or 1)
            logger.warning("Telegram rate limit 429: retry after %s s", retry_after)
            time.sleep(retry_after)
            raise TelegramAPIError(
                f"Telegram rate limit 429: retry after {retry_after}s",
                error_code=429,
                description="Too Many Requests",
                parameters=parameters,
            )

        if not payload.get("ok"):
            description = str(payload.get("description") or response.text)
            error_code = payload.get("error_code") or response.status_code
            parameters = payload.get("parameters") or {}
            raise TelegramAPIError(
                f"Telegram API error {error_code}: {description}",
                error_code=error_code,
                description=description,
                parameters=parameters,
            )

        return payload["result"]

    def call(self, method: str, payload: dict[str, Any] | None = None, timeout: int = 30) -> Any:
        url = f"{self.base_url}/{method}"
        try:
            if payload is not None:
                resp = self._session.post(url, json=payload, timeout=timeout)
            else:
                resp = self._session.get(url, timeout=timeout)
            return self._handle_response(resp)
        except TelegramAPIError:
            raise
        except requests.RequestException as exc:
            raise TelegramAPIError(f"Network error in {method}: {exc}") from exc

    def get_me(self, retries: int = 5, retry_delay: float = 2.0) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                return self.call("getMe", timeout=15)
            except TelegramAPIError as exc:
                last_error = exc
                if attempt < retries:
                    logger.warning("getMe failed (attempt %s/%s): %s", attempt, retries, exc)
                    time.sleep(retry_delay)
        raise last_error or TelegramAPIError("getMe failed")

    def delete_webhook(self, drop_pending_updates: bool = True) -> dict[str, Any]:
        return self.call(
            "deleteWebhook",
            {"drop_pending_updates": drop_pending_updates},
            timeout=15,
        )

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict[str, Any]]:
        query: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": json.dumps(["message", "callback_query"]),
        }
        if offset is not None:
            query["offset"] = offset

        url = f"{self.base_url}/getUpdates"
        try:
            resp = self._session.get(url, params=query, timeout=timeout + 10)
            return self._handle_response(resp)
        except TelegramAPIError:
            raise
        except requests.RequestException as exc:
            logger.warning("getUpdates network error: %s", exc)
            time.sleep(1.0)
            return []

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
        return self.call("answerCallbackQuery", payload, timeout=10)

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
        cache_key = str(media_path.resolve())
        cached_file_id = self._file_id_cache.get(cache_key)
        if cached_file_id is None and self.db is not None:
            cached_file_id = self.db.get_cached_value(f"file_id:{media_path.name}")
            if cached_file_id:
                self._file_id_cache[cache_key] = cached_file_id

        if cached_file_id is not None:
            try:
                return self._edit_media_by_file_id(
                    chat_id, message_id, cached_file_id, caption, reply_markup
                )
            except TelegramAPIError as exc:
                logger.warning("Cached file_id rejected (%s), re-uploading", exc)
                self._file_id_cache.pop(cache_key, None)
                if self.db is not None:
                    self.db.set_cached_value(f"file_id:{media_path.name}", "")

        result = self._upload_media(
            chat_id, message_id, media_path, caption, reply_markup
        )
        file_id = self._extract_file_id(result)
        if file_id is not None:
            self._file_id_cache[cache_key] = file_id
            if self.db is not None:
                self.db.set_cached_value(f"file_id:{media_path.name}", file_id)
        return result

    def _edit_media_by_file_id(
        self,
        chat_id: int,
        message_id: int,
        file_id: str,
        caption: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "media": {
                "type": "photo",
                "media": file_id,
                "caption": caption,
                "parse_mode": "HTML",
            },
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call("editMessageMedia", payload)

    def _upload_media(
        self,
        chat_id: int,
        message_id: int,
        media_path: Path,
        caption: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/editMessageMedia"
        media_data = {
            "type": "photo",
            "media": "attach://photo",
            "caption": caption,
            "parse_mode": "HTML",
        }
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "media": json.dumps(media_data, ensure_ascii=False),
        }
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)

        mime_type = mimetypes.guess_type(media_path.name)[0] or "image/png"
        with open(media_path, "rb") as f:
            files = {"photo": (media_path.name, f.read(), mime_type)}
            resp = self._session.post(url, data=data, files=files, timeout=30)
        return self._handle_response(resp)

    def send_photo(
        self,
        chat_id: int,
        photo_path: Path,
        caption: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cache_key = str(photo_path.resolve())
        cached_file_id = self._file_id_cache.get(cache_key)
        if cached_file_id is None and self.db is not None:
            cached_file_id = self.db.get_cached_value(f"file_id:{photo_path.name}")
            if cached_file_id:
                self._file_id_cache[cache_key] = cached_file_id

        if cached_file_id is not None:
            try:
                return self._send_photo_by_file_id(
                    chat_id, cached_file_id, caption, reply_markup
                )
            except TelegramAPIError as exc:
                logger.warning("Cached file_id rejected (%s), re-uploading", exc)
                self._file_id_cache.pop(cache_key, None)
                if self.db is not None:
                    self.db.set_cached_value(f"file_id:{photo_path.name}", "")

        result = self._upload_photo(chat_id, photo_path, caption, reply_markup)
        file_id = self._extract_file_id(result)
        if file_id is not None:
            self._file_id_cache[cache_key] = file_id
            if self.db is not None:
                self.db.set_cached_value(f"file_id:{photo_path.name}", file_id)
        return result

    def _send_photo_by_file_id(
        self,
        chat_id: int,
        file_id: str,
        caption: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "photo": file_id,
            "caption": caption,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call("sendPhoto", payload)

    @staticmethod
    def _extract_file_id(result: Any) -> str | None:
        if not isinstance(result, dict):
            return None
        photos = result.get("photo")
        if not isinstance(photos, list) or not photos:
            return None
        largest = photos[-1]
        file_id = largest.get("file_id") if isinstance(largest, dict) else None
        return file_id if isinstance(file_id, str) else None

    def _upload_photo(
        self,
        chat_id: int,
        photo_path: Path,
        caption: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/sendPhoto"
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)

        mime_type = mimetypes.guess_type(photo_path.name)[0] or "image/png"
        with open(photo_path, "rb") as f:
            files = {"photo": (photo_path.name, f.read(), mime_type)}
            resp = self._session.post(url, data=data, files=files, timeout=30)
        return self._handle_response(resp)
