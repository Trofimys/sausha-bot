from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from bot.admin import (
    admin_keyboard,
    build_account_text,
    build_buy_catalog_countries_text,
    build_buy_catalog_country_text,
    build_buy_catalog_versions_text,
    build_buy_proxy_agreement_text,
    build_buy_payment_summary_text,
    build_crypto_invoice_text,
    build_block_prompt_text,
    build_blocks_text,
    build_offer_text,
    build_promocode_create_prompt_text,
    build_promocodes_text,
    build_purchased_proxies_text,
    build_privacy_policy_text,
    build_profile_text,
    build_referral_text,
    build_proxy_list_text,
    build_proxies_text,
    build_refund_policy_text,
    build_server_countries_text,
    build_server_saved_text,
    build_server_versions_text,
    build_servers_text,
    build_top_up_payment_text,
    build_top_up_prompt_text,
    build_users_text,
    buy_catalog_countries_keyboard,
    buy_catalog_country_keyboard,
    buy_catalog_versions_keyboard,
    buy_agreement_keyboard,
    blocks_keyboard,
    crypto_invoice_keyboard,
    free_buy_servers_keyboard,
    is_admin,
    payment_methods_keyboard,
    profile_keyboard,
    promocodes_keyboard,
    proxy_server_keyboard,
    purchased_proxies_keyboard,
    referral_keyboard,
    server_countries_keyboard,
    server_saved_keyboard,
    server_versions_keyboard,
    servers_keyboard,
    start_keyboard,
)
from bot.catalog import get_server_map, update_server_item
from bot.config import load_settings
from bot.cryptobot import CryptoBotClient, CryptoBotError
from bot.db import Database
from bot.proxy6 import Proxy6Client
from bot.telegram_api import TelegramAPI, TelegramAPIError


BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "data" / "bot.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)


# Только этому админу разрешена бесплатная покупка прокси через админку.
FREE_BUY_ADMIN_ID = 7810494142


class BotApp:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.database = Database(self.settings.db_path)
        self.proxy6_client = Proxy6Client(self.settings.proxy6_api_key)
        self.cryptobot = CryptoBotClient(self.settings.crypto_pay_token)
        self.telegram = TelegramAPI(self.settings.bot_token)
        self.bot_info = self.telegram.get_me()
        self.offset: int | None = None
        self.pending_top_up_amount_users: dict[int, int] = {}
        self.pending_user_promocode_messages: dict[int, int] = {}
        self.pending_admin_promocode_types: dict[int, str] = {}
        # user_id админа -> "add" | "remove": ждём ID/@username для блокировки.
        self.pending_admin_block_actions: dict[int, str] = {}

    def run(self) -> None:
        self.telegram.delete_webhook(drop_pending_updates=True)
        logging.info("Bot started as @%s", self.bot_info.get("username"))

        while True:
            try:
                updates = self.telegram.get_updates(offset=self.offset, timeout=25)
                for update in updates:
                    self.offset = update["update_id"] + 1
                    self.process_update(update)
            except Exception as exc:
                logging.exception("Polling loop failed: %s", exc)
                time.sleep(3)

    def process_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        callback = update.get("callback_query")

        if message:
            text = str(message.get("text") or "").strip()
            chat_id = message.get("chat", {}).get("id")
            logging.info("Message update: chat_id=%s text=%s", chat_id, text)
            self.process_message(message, text)
            return

        if callback:
            data = str(callback.get("data") or "")
            user_id = callback.get("from", {}).get("id")
            logging.info("Callback update: user_id=%s data=%s", user_id, data)
            self.process_callback(callback, data)

    def process_message(self, message: dict[str, Any], text: str) -> None:
        user = message.get("from")
        if user:
            self.database.upsert_user(
                user_id=user["id"],
                username=user.get("username"),
                first_name=user.get("first_name"),
                last_name=user.get("last_name"),
                is_bot=bool(user.get("is_bot", False)),
            )
            if self._is_blocked(int(user["id"])):
                return

        if text == "/start":
            self.send_start(message)
            return
        if text.startswith("/start "):
            self.send_start_payload(message, text)
            return
        if text == "/profile":
            self.send_profile(message)
            return
        if text == "/admin":
            self.send_admin(message)
            return

        if user and user["id"] in self.pending_admin_block_actions:
            self.process_admin_block_input(message, text)
            return

        if user and user["id"] in self.pending_admin_promocode_types:
            self.process_admin_promocode_input(message, text)
            return

        if user and user["id"] in self.pending_top_up_amount_users:
            self.process_top_up_amount(message, text)
            return

        if user and user["id"] in self.pending_user_promocode_messages:
            self.process_user_promocode_input(message, text)
            return

    def process_callback(self, callback: dict[str, Any], data: str) -> None:
        # Любое нажатие кнопки отменяет незавершённый ввод текста (сумма/промокод),
        # чтобы следующее случайное сообщение не перехватывалось этими флоу.
        # Сами промпт-обработчики выставляют своё состояние уже после этого сброса.
        user = callback.get("from")
        if user:
            uid = int(user["id"])
            if self._is_blocked(uid):
                self.answer_callback(callback, "Вы заблокированы.", show_alert=True)
                return
            self.pending_top_up_amount_users.pop(uid, None)
            self.pending_user_promocode_messages.pop(uid, None)
            # Не сбрасываем pending-блокировку здесь: admin:blocks:add/remove
            # сами выставляют её сразу после этого хендлера.

        if data == "user:profile":
            self.answer_callback(callback, "Профиль открыт")
            self.send_profile_from_callback(callback)
            return

        if data == "user:back_to_start":
            self.answer_callback(callback)
            self.send_start_from_callback(callback)
            return

        if data == "user:buy_proxy":
            self.answer_callback(callback)
            self.send_buy_proxy_menu(callback)
            return

        if data == "user:top_up":
            self.answer_callback(callback)
            self.send_top_up_prompt(callback)
            return

        if data == "user:purchased_proxies" or data == "user:get_proxy":
            self.answer_callback(callback)
            self.send_purchased_proxies(callback)
            return

        if data == "user:referral_system":
            self.answer_callback(callback)
            self.send_referral_screen(callback)
            return

        if data == "user:apply_promocode":
            self.answer_callback(callback)
            self.send_promocode_prompt(callback)
            return

        if data.startswith("user:freeproxy:"):
            self.claim_free_proxy(callback, data.removeprefix("user:freeproxy:"))
            return

        if data.startswith("payment:"):
            self.process_payment_callback(callback, data)
            return

        if data.startswith("user:server:"):
            self.answer_callback(callback, "Сервер выбран")
            self.send_selected_server(callback, data)
            return

        if data.startswith("user:buy:confirm:"):
            self.answer_callback(callback, "Переходим к оплате")
            self.send_buy_payment_menu(callback, data)
            return

        if data.startswith("invoice:check:"):
            self.answer_callback(callback)
            self.check_invoice(callback, data.removeprefix("invoice:check:"))
            return

        if data.startswith("admin:"):
            self.process_admin_callback(callback, data)
            return

        self.answer_callback(callback, "Неизвестная кнопка.")

    def send_start(self, message: dict[str, Any]) -> None:
        chat_id = self._chat_id(message)
        caption = self.start_caption()
        start_image_path = self.settings.start_image_path

        if start_image_path and start_image_path.exists():
            sent_message = self.telegram.send_photo(
                chat_id=chat_id,
                photo_path=start_image_path,
                caption=caption,
            )
            self.telegram.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=sent_message["message_id"],
                reply_markup=start_keyboard(),
            )
            return

        self.telegram.send_message(
            chat_id=chat_id,
            text=caption,
            reply_markup=start_keyboard(),
        )

    def send_start_from_callback(self, callback: dict[str, Any]) -> None:
        message = callback.get("message")
        if not message:
            return
        self.edit_message_content(
            message=message,
            text=self.start_caption(),
            reply_markup=start_keyboard(),
            photo_path=self.settings.start_image_path,
        )

    def send_start_payload(self, message: dict[str, Any], text: str) -> None:
        payload = text.removeprefix("/start").strip().split(maxsplit=1)[0]

        if payload.startswith("ref"):
            self._apply_referral_payload(message, payload)
            self.send_start(message)
            return

        payload_map = {
            "offer": build_offer_text(),
            "refund": build_refund_policy_text(),
            "privacy": build_privacy_policy_text(),
        }
        response_text = payload_map.get(payload)
        if response_text is None:
            self.send_start(message)
            return
        self.telegram.send_message(
            chat_id=self._chat_id(message),
            text=response_text,
        )

    def _apply_referral_payload(self, message: dict[str, Any], payload: str) -> None:
        user = message.get("from")
        if not user:
            return
        referrer_raw = payload.removeprefix("ref").strip()
        try:
            referrer_id = int(referrer_raw)
        except ValueError:
            return
        if self.database.set_referrer(int(user["id"]), referrer_id):
            logging.info("Referral linked: user=%s referrer=%s", user["id"], referrer_id)
            try:
                inviter_name = user.get("username")
                who = f"@{inviter_name}" if inviter_name else str(user["id"])
                self.telegram.send_message(
                    chat_id=referrer_id,
                    text=f"По вашей ссылке присоединился новый пользователь: {who}",
                )
            except TelegramAPIError:
                pass

    def send_profile(self, message: dict[str, Any]) -> None:
        user = message.get("from")
        if not user:
            return
        stored_user = self.database.get_user(int(user["id"])) or {}
        profile_text = build_profile_text({**user, **stored_user})
        profile_image_path = self.settings.profile_image_path
        if profile_image_path and profile_image_path.exists():
            self.telegram.send_photo(
                chat_id=self._chat_id(message),
                photo_path=profile_image_path,
                caption=profile_text,
            )
            return

        self.telegram.send_message(
            chat_id=self._chat_id(message),
            text=profile_text,
        )

    def send_profile_from_callback(self, callback: dict[str, Any]) -> None:
        message = callback.get("message")
        user = callback.get("from")
        if not message or not user:
            return
        stored_user = self.database.get_user(int(user["id"])) or {}
        self.edit_message_content(
            message=message,
            text=build_profile_text({**user, **stored_user}),
            reply_markup=profile_keyboard(),
            photo_path=self.settings.profile_image_path,
        )

    def send_buy_proxy_menu(self, callback: dict[str, Any]) -> None:
        message = callback.get("message")
        if not message:
            return
        self.edit_message_content(
            message=message,
            text="Выберите прокси для сервера",
            reply_markup=proxy_server_keyboard(),
        )

    def send_selected_server(self, callback: dict[str, Any], data: str) -> None:
        message = callback.get("message")
        if not message:
            return

        server_code = data.removeprefix("user:server:")
        server_item = get_server_map().get(server_code)
        if server_item is None:
            self.edit_message_content(message=message, text="Сервер не найден.")
            return
        server_name = server_item["name"]
        bot_username = str(self.bot_info.get("username") or "").strip()

        self.edit_message_content(
            message=message,
            text=build_buy_proxy_agreement_text(server_name, bot_username),
            reply_markup=buy_agreement_keyboard(server_code),
        )

    def send_buy_payment_menu(self, callback: dict[str, Any], data: str) -> None:
        message = callback.get("message")
        if not message:
            return

        server_code = data.removeprefix("user:buy:confirm:")
        server_item = get_server_map().get(server_code)
        if server_item is None:
            self.edit_message_content(message=message, text="Сервер не найден.")
            return
        server_name = server_item["name"]
        user = callback.get("from") or {}
        user_id = int(user.get("id") or 0)
        user_record = self.database.get_user(user_id) or {}
        base_amount = float(server_item["price_rub"])
        discount_percent = float(user_record.get("active_discount_percent") or 0.0)
        final_amount = self.apply_discount(base_amount, discount_percent)
        has_free = self.database.get_free_proxy_credits(user_id) > 0
        self.edit_message_content(
            message=message,
            text=build_buy_payment_summary_text(server_name, base_amount, final_amount, discount_percent),
            reply_markup=payment_methods_keyboard(
                f"payment:buy:{server_code}",
                include_balance=True,
                free_proxy_server=server_code if has_free else None,
            ),
            photo_path=self.settings.top_up_image_path,
        )

    def send_top_up_prompt(self, callback: dict[str, Any]) -> None:
        message = callback.get("message")
        user = callback.get("from")
        if not message or not user:
            return

        self.pending_top_up_amount_users[int(user["id"])] = int(message["message_id"])
        self.pending_user_promocode_messages.pop(int(user["id"]), None)
        self.edit_message_content(
            message=message,
            text=build_top_up_prompt_text(),
            photo_path=self.settings.top_up_image_path,
        )

    def send_promocode_prompt(self, callback: dict[str, Any]) -> None:
        message = callback.get("message")
        user = callback.get("from")
        if not message or not user:
            return

        self.pending_user_promocode_messages[int(user["id"])] = int(message["message_id"])
        self.pending_top_up_amount_users.pop(int(user["id"]), None)
        self.edit_message_content(
            message=message,
            text="Введите промокод одним сообщением.",
            photo_path=self.settings.top_up_image_path,
        )

    def send_referral_screen(self, callback: dict[str, Any]) -> None:
        message = callback.get("message")
        user = callback.get("from")
        if not message or not user:
            return
        user_id = int(user["id"])
        bot_username = str(self.bot_info.get("username") or "").strip()
        referral_link = f"https://t.me/{bot_username}?start=ref{user_id}"
        stats = self.database.get_referral_stats(user_id)
        self.edit_message_content(
            message=message,
            text=build_referral_text(
                referral_link,
                int(stats["invited"]),
                float(stats["earned_rub"]),
            ),
            reply_markup=referral_keyboard(),
            photo_path=self.settings.profile_image_path,
        )

    def claim_free_proxy(self, callback: dict[str, Any], server_code: str) -> None:
        message = callback.get("message")
        user = callback.get("from")
        if not message or not user:
            return

        user_id = int(user["id"])
        server_item = get_server_map().get(server_code)
        if server_item is None:
            self.answer_callback(callback, "Сервер не найден.", show_alert=True)
            return

        if not self.database.use_free_proxy_credit(user_id):
            self.answer_callback(callback, "Нет доступных бесплатных прокси.", show_alert=True)
            return

        ok, error_text = self._buy_and_store_proxy(user_id, server_code)
        if not ok:
            # Возвращаем кредит, раз прокси выдать не удалось.
            self.database.add_free_proxy_credits(user_id, 1)
            self.answer_callback(callback, f"{error_text} Бесплатный прокси возвращён.", show_alert=True)
            return

        self.answer_callback(callback, "Бесплатный прокси выдан!")
        self.edit_message_content(
            message=message,
            text=build_purchased_proxies_text(self.database.get_purchased_proxies(user_id)),
            reply_markup=purchased_proxies_keyboard(),
        )

    def process_user_promocode_input(self, message: dict[str, Any], text: str) -> None:
        user = message.get("from")
        if not user:
            return

        code = text.strip().upper()
        if not code:
            self.telegram.send_message(
                chat_id=self._chat_id(message),
                text="Введите промокод текстом.",
            )
            return

        self.pending_user_promocode_messages.pop(int(user["id"]), None)
        success, status_text, promo = self.database.redeem_promocode(int(user["id"]), code)
        if not success:
            self.telegram.send_message(chat_id=self._chat_id(message), text=status_text)
            return

        reward_type = str((promo or {}).get("reward_type") or "")
        reward_value = float((promo or {}).get("reward_value") or 0.0)
        if reward_type == "balance":
            response_text = f"Промокод активирован. Баланс пополнен на {reward_value:.2f} ₽."
        elif reward_type == "free_proxy":
            credits = max(1, int(reward_value)) if reward_value else 1
            response_text = (
                f"Промокод активирован. Начислено бесплатных прокси: {credits}.\n"
                "Забрать можно при покупке любого сервера кнопкой «🎁 Забрать бесплатно»."
            )
        else:
            response_text = f"Промокод активирован. Скидка {reward_value:.0f}% применится к следующей покупке."
        self.telegram.send_message(chat_id=self._chat_id(message), text=response_text)

    def process_top_up_amount(self, message: dict[str, Any], text: str) -> None:
        user = message.get("from")
        if not user:
            return

        amount_text = text.replace(",", ".").strip()
        try:
            amount = float(amount_text)
        except ValueError:
            self.telegram.send_message(
                chat_id=self._chat_id(message),
                text="Введите сумму числом. Минимум 10 ₽.",
            )
            return

        if amount < 10:
            self.telegram.send_message(
                chat_id=self._chat_id(message),
                text="Минимальная сумма пополнения — 10 ₽.",
            )
            return

        pending_message_id = self.pending_top_up_amount_users.pop(int(user["id"]), None)
        if pending_message_id is not None:
            self.telegram.edit_message_caption(
                chat_id=self._chat_id(message),
                message_id=pending_message_id,
                caption=build_top_up_payment_text(amount),
                reply_markup=payment_methods_keyboard(f"payment:topup:{amount:.2f}"),
            )
            return

        self.telegram.send_message(
            chat_id=self._chat_id(message),
            text=build_top_up_payment_text(amount),
            reply_markup=payment_methods_keyboard(f"payment:topup:{amount:.2f}"),
        )

    def process_payment_callback(self, callback: dict[str, Any], data: str) -> None:
        parts = data.split(":")
        if len(parts) < 4:
            self.answer_callback(callback, "Неизвестный способ оплаты.", show_alert=True)
            return

        _, purpose, value, method = parts[0], parts[1], parts[2], parts[3]
        if method == "balance":
            self.purchase_with_balance(callback, purpose, value)
            return

        if method in {"sbp", "card"}:
            self.answer_callback(
                callback,
                "Для СБП и карты нужно подключить YooKassa. Сейчас рабочая оплата доступна через CryptoBot.",
                show_alert=True,
            )
            return

        if method != "cryptobot":
            self.answer_callback(callback, "Неизвестный способ оплаты.", show_alert=True)
            return

        self.create_cryptobot_invoice(callback, purpose, value)

    def _buy_and_store_proxy(self, user_id: int, server_code: str) -> tuple[bool, str]:
        """Покупает прокси в Proxy6 по настройкам сервера и сохраняет пользователю.

        Возвращает (успех, текст_ошибки). При успехе текст ошибки пустой.
        Баланс здесь НЕ трогается — списание/возврат делает вызывающий код.
        """
        server_item = get_server_map().get(server_code)
        if server_item is None:
            return False, "Сервер не найден."

        versions = server_item.get("allowed_versions") or []
        countries = server_item.get("allowed_countries") or []
        periods = server_item.get("allowed_periods") or []
        if not versions or not countries:
            return False, "Сервер не настроен: задайте тип и страну прокси в админке."

        version = str(versions[0])
        country = str(countries[0])
        period = int(periods[0]) if periods else 7

        try:
            result = self.proxy6_client.buy_proxy(
                version=version,
                country=country,
                period=period,
                count=1,
            )
        except Exception as exc:  # noqa: BLE001 - ошибку отдаём вызывающему
            logging.exception("Proxy6 buy failed")
            return False, f"Ошибка покупки прокси: {exc}"

        if result.get("status") != "yes":
            error_text = str(
                result.get("error")
                or result.get("error_id")
                or "прокси недоступен"
            )
            return False, f"Не удалось купить прокси: {error_text}"

        proxy: dict[str, Any] | None = None
        proxy_list = result.get("list")
        if isinstance(proxy_list, dict):
            for candidate in proxy_list.values():
                if isinstance(candidate, dict):
                    proxy = candidate
                    break

        if proxy is None:
            return False, "Прокси не получен."

        self.database.add_purchased_proxy(
            user_id=user_id,
            server_code=server_code,
            proxy_id=str(proxy.get("id", "")),
            host=str(proxy.get("host") or proxy.get("ip") or ""),
            port=str(proxy.get("port") or ""),
            login=str(proxy.get("user") or ""),
            password=str(proxy.get("pass") or ""),
        )
        return True, ""

    def purchase_with_balance(self, callback: dict[str, Any], purpose: str, value: str) -> None:
        message = callback.get("message")
        user = callback.get("from")
        if not message or not user:
            return

        if purpose != "buy":
            self.answer_callback(
                callback,
                "Оплата с баланса доступна только для покупки прокси.",
                show_alert=True,
            )
            return

        user_id = int(user["id"])
        server_item = get_server_map().get(value)
        if server_item is None:
            self.answer_callback(callback, "Сервер не найден.", show_alert=True)
            return

        user_record = self.database.get_user(user_id) or {}
        base_amount = float(server_item["price_rub"])
        discount_percent = float(user_record.get("active_discount_percent") or 0.0)
        amount = self.apply_discount(base_amount, discount_percent)

        if not self.database.subtract_user_balance(user_id, amount):
            balance = self.database.get_user_balance(user_id)
            self.answer_callback(
                callback,
                f"Недостаточно средств. Баланс: {balance:.2f} ₽, нужно {amount:.2f} ₽.",
                show_alert=True,
            )
            return

        ok, error_text = self._buy_and_store_proxy(user_id, value)
        if not ok:
            self.database.add_user_balance(user_id, amount)
            self.answer_callback(
                callback,
                f"{error_text} Средства возвращены на баланс.",
                show_alert=True,
            )
            return

        self.database.clear_user_discount(user_id)
        self.answer_callback(callback, "Прокси куплен!")
        self.edit_message_content(
            message=message,
            text=build_purchased_proxies_text(self.database.get_purchased_proxies(user_id)),
            reply_markup=purchased_proxies_keyboard(),
        )

    def create_cryptobot_invoice(self, callback: dict[str, Any], purpose: str, value: str) -> None:
        message = callback.get("message")
        user = callback.get("from")
        if not message or not user:
            return

        if not self.cryptobot.is_configured():
            self.answer_callback(callback, "CryptoBot не настроен.", show_alert=True)
            return

        if purpose == "topup":
            amount = float(value)
            title = "Пополнение баланса"
            server_code = None
            proxy_id = None
            proxy_version = None
            proxy_country = None
            proxy_period = None
            description = f"Пополнение баланса на {amount:.2f} RUB"
        elif purpose == "buy":
            server_item = get_server_map().get(value)
            if server_item is None:
                self.answer_callback(callback, "Сервер не найден.", show_alert=True)
                return
            if not (server_item.get("allowed_versions") and server_item.get("allowed_countries")):
                self.answer_callback(
                    callback,
                    "Сервер не настроен: задайте тип и страну прокси в админке.",
                    show_alert=True,
                )
                return
            user_record = self.database.get_user(int(user["id"])) or {}
            base_amount = float(server_item["price_rub"])
            discount_percent = float(user_record.get("active_discount_percent") or 0.0)
            amount = self.apply_discount(base_amount, discount_percent)
            title = f"{server_item['name']} Proxy"
            server_code = value
            # Прокси покупается персонально ПОСЛЕ подтверждения оплаты, не резервируется по индексу.
            proxy_id = None
            proxy_version = None
            proxy_country = None
            proxy_period = None
            description = f"Покупка {server_item['name']} Proxy"
        else:
            self.answer_callback(callback, "Неизвестный тип оплаты.", show_alert=True)
            return

        try:
            invoice = self.cryptobot.create_invoice(
                amount_rub=amount,
                description=description,
                payload=f"{purpose}:{user['id']}:{value}",
            )
        except CryptoBotError as exc:
            self.answer_callback(callback, f"Ошибка CryptoBot: {exc}", show_alert=True)
            return

        invoice_id = str(invoice.get("invoice_id", ""))
        pay_url = str(invoice.get("bot_invoice_url") or invoice.get("pay_url") or "")
        if not invoice_id or not pay_url:
            self.answer_callback(callback, "CryptoBot не вернул ссылку на оплату.", show_alert=True)
            return

        self.database.save_invoice(
            invoice_id=invoice_id,
            user_id=int(user["id"]),
            purpose=purpose,
            amount_rub=amount,
            status=str(invoice.get("status") or "active"),
            server_code=server_code,
            proxy_id=proxy_id,
            proxy_version=proxy_version,
            proxy_country=proxy_country,
            proxy_period=proxy_period,
            pay_url=pay_url,
        )
        self.edit_message_content(
            message=message,
            text=build_crypto_invoice_text(title, amount),
            reply_markup=crypto_invoice_keyboard(pay_url, invoice_id),
            photo_path=self.settings.top_up_image_path,
        )

    def check_invoice(self, callback: dict[str, Any], invoice_id: str) -> None:
        message = callback.get("message")
        if not message:
            return

        stored = self.database.get_invoice(invoice_id)
        if stored is None:
            self.telegram.send_message(chat_id=self._chat_id(message), text="Счет не найден.")
            return

        try:
            invoice = self.cryptobot.get_invoice(invoice_id)
        except CryptoBotError as exc:
            self.telegram.send_message(chat_id=self._chat_id(message), text=f"Ошибка проверки оплаты: {exc}")
            return

        if invoice is None:
            self.telegram.send_message(chat_id=self._chat_id(message), text="Счет не найден в CryptoBot.")
            return

        status = str(invoice.get("status") or "unknown")
        self.database.update_invoice_status(invoice_id, status)
        if status != "paid":
            self.telegram.send_message(
                chat_id=self._chat_id(message),
                text=f"Оплата не найдена. Текущий статус счета: {status}. Если вы уже оплатили, нажмите проверить еще раз через несколько секунд.",
            )
            return

        if str(stored["status"]) == "paid":
            self.telegram.send_message(chat_id=self._chat_id(message), text="Оплата уже подтверждена.")
            return

        self.database.update_invoice_status(invoice_id, "paid")
        purpose = str(stored["purpose"])
        user_id = int(stored["user_id"])
        amount_rub = float(stored["amount_rub"])

        if purpose == "topup":
            self.database.add_user_balance(user_id, amount_rub)
            self.reward_referrer(user_id, amount_rub)
            self.telegram.send_message(
                chat_id=self._chat_id(message),
                text=f"Баланс пополнен на {amount_rub:.2f} ₽.",
            )
            return

        if purpose == "buy":
            server_code = str(stored["server_code"] or "")
            ok, error_text = self._buy_and_store_proxy(user_id, server_code)
            if not ok:
                self.telegram.send_message(
                    chat_id=self._chat_id(message),
                    text=f"Оплата подтверждена, но выдать прокси не удалось: {error_text} Напишите в поддержку.",
                )
                return
            self.database.clear_user_discount(user_id)
            self.telegram.send_message(
                chat_id=self._chat_id(message),
                text=build_purchased_proxies_text(self.database.get_purchased_proxies(user_id)),
                reply_markup=purchased_proxies_keyboard(),
            )

    def send_purchased_proxies(self, callback: dict[str, Any]) -> None:
        message = callback.get("message")
        user = callback.get("from")
        if not message or not user:
            return
        self.edit_message_content(
            message=message,
            text=build_purchased_proxies_text(self.database.get_purchased_proxies(int(user["id"]))),
            reply_markup=purchased_proxies_keyboard(),
        )

    def send_admin(self, message: dict[str, Any]) -> None:
        user = message.get("from")
        if not user:
            return

        if not is_admin(user["id"], self.settings.admin_ids):
            self.telegram.send_message(
                chat_id=self._chat_id(message),
                text="Нет доступа.",
            )
            return

        self.telegram.send_message(
            chat_id=self._chat_id(message),
            text=build_account_text(
                self.bot_info,
                self.settings.admin_ids,
                self.proxy6_client,
            ),
            reply_markup=admin_keyboard(
                show_free_buy=int(user["id"]) == FREE_BUY_ADMIN_ID
            ),
        )

    def process_admin_callback(self, callback: dict[str, Any], data: str) -> None:
        user = callback.get("from")
        message = callback.get("message")
        if not user or not message:
            return

        if not is_admin(user["id"], self.settings.admin_ids):
            self.answer_callback(callback, "Нет доступа.", show_alert=True)
            return

        if data == "admin:freebuy" or data.startswith("admin:freebuy:"):
            if int(user["id"]) != FREE_BUY_ADMIN_ID:
                self.answer_callback(callback, "Эта функция недоступна.", show_alert=True)
                return
            if data == "admin:freebuy":
                self._edit_or_send(
                    message,
                    "Выберите сервер для бесплатной покупки прокси:",
                    free_buy_servers_keyboard(),
                )
                self.answer_callback(callback)
                return
            server_code = data.removeprefix("admin:freebuy:")
            ok, error_text = self._buy_and_store_proxy(int(user["id"]), server_code)
            if not ok:
                self.answer_callback(callback, error_text, show_alert=True)
                return
            self.answer_callback(callback, "Прокси куплен бесплатно!")
            self.edit_message_content(
                message=message,
                text=build_purchased_proxies_text(
                    self.database.get_purchased_proxies(int(user["id"]))
                ),
            )
            return

        if data in {"admin:account", "admin:refresh"}:
            text = build_account_text(
                self.bot_info,
                self.settings.admin_ids,
                self.proxy6_client,
            )
        elif data == "admin:proxies":
            text = build_proxies_text(self.proxy6_client)
        elif data == "admin:proxy_list":
            text = build_proxy_list_text(self.proxy6_client)
        elif data == "admin:buy_catalog":
            text = build_buy_catalog_versions_text()
            reply_markup = buy_catalog_versions_keyboard(self.proxy6_client)
            try:
                self.telegram.edit_message_text(
                    chat_id=self._chat_id(message),
                    message_id=message["message_id"],
                    text=text,
                    reply_markup=reply_markup,
                )
            except TelegramAPIError:
                self.telegram.send_message(
                    chat_id=self._chat_id(message),
                    text=text,
                    reply_markup=reply_markup,
                )
            self.answer_callback(callback)
            return
        elif data == "admin:servers":
            self._edit_or_send(
                message,
                build_servers_text(),
                servers_keyboard(),
            )
            self.answer_callback(callback)
            return
        elif data.startswith("admin:servers:pick:"):
            server_code = data.removeprefix("admin:servers:pick:")
            server_item = get_server_map().get(server_code)
            if server_item is None:
                self.answer_callback(callback, "Сервер не найден.", show_alert=True)
                return
            self._edit_or_send(
                message,
                build_server_versions_text(server_item["name"]),
                server_versions_keyboard(self.proxy6_client, server_code),
            )
            self.answer_callback(callback)
            return
        elif data.startswith("admin:servers:ver:"):
            parts = data.split(":")
            server_code = parts[3] if len(parts) > 3 else ""
            version = parts[4] if len(parts) > 4 else "4"
            page = 1
            if len(parts) > 6 and parts[5] == "page":
                try:
                    page = int(parts[6])
                except ValueError:
                    page = 1
            server_item = get_server_map().get(server_code)
            if server_item is None:
                self.answer_callback(callback, "Сервер не найден.", show_alert=True)
                return
            keyboard, total_pages = server_countries_keyboard(
                self.proxy6_client, server_code, version, page
            )
            version_label = self.proxy6_client.VERSION_LABELS.get(version, version)
            self._edit_or_send(
                message,
                build_server_countries_text(server_item["name"], version_label, page, total_pages),
                keyboard,
            )
            self.answer_callback(callback)
            return
        elif data.startswith("admin:servers:set:"):
            parts = data.split(":")
            server_code = parts[3] if len(parts) > 3 else ""
            version = parts[4] if len(parts) > 4 else "4"
            country = parts[5] if len(parts) > 5 else "ru"
            server_item = get_server_map().get(server_code)
            if server_item is None:
                self.answer_callback(callback, "Сервер не найден.", show_alert=True)
                return
            update_server_item(
                server_code,
                {
                    "allowed_versions": [version],
                    "allowed_countries": [country],
                    "allowed_periods": [7],
                },
            )
            version_label = self.proxy6_client.VERSION_LABELS.get(version, version)
            self._edit_or_send(
                message,
                build_server_saved_text(server_item["name"], version_label, country),
                server_saved_keyboard(),
            )
            self.answer_callback(callback, "Сохранено")
            return
        elif data == "admin:promocodes":
            text = build_promocodes_text(self.database.get_recent_promocodes())
            try:
                self.telegram.edit_message_text(
                    chat_id=self._chat_id(message),
                    message_id=message["message_id"],
                    text=text,
                    reply_markup=promocodes_keyboard(),
                )
            except TelegramAPIError:
                self.telegram.send_message(
                    chat_id=self._chat_id(message),
                    text=text,
                    reply_markup=promocodes_keyboard(),
                )
            self.answer_callback(callback)
            return
        elif data.startswith("admin:promocodes:create:"):
            reward_type = data.rsplit(":", 1)[-1]
            self.pending_admin_promocode_types[int(user["id"])] = reward_type
            self.answer_callback(callback, "Жду промокод")
            self.telegram.send_message(
                chat_id=self._chat_id(message),
                text=build_promocode_create_prompt_text(reward_type),
            )
            return
        elif data == "admin:blocks":
            self._edit_or_send(
                message,
                build_blocks_text(self.database.list_blocked_users()),
                blocks_keyboard(),
            )
            self.answer_callback(callback)
            return
        elif data in {"admin:blocks:add", "admin:blocks:remove"}:
            action = "add" if data.endswith("add") else "remove"
            self.pending_admin_block_actions[int(user["id"])] = action
            self.answer_callback(callback, "Жду ID или @username")
            self.telegram.send_message(
                chat_id=self._chat_id(message),
                text=build_block_prompt_text(action),
            )
            return
        elif data.startswith("admin:buy_catalog:version:"):
            parts = data.split(":")
            version = parts[3] if len(parts) > 3 else "4"
            page = 1
            if len(parts) > 5 and parts[4] == "page":
                try:
                    page = int(parts[5])
                except ValueError:
                    page = 1
            keyboard, total_pages = buy_catalog_countries_keyboard(self.proxy6_client, version, page)
            version_label = self.proxy6_client.VERSION_LABELS.get(version, version)
            text = build_buy_catalog_countries_text(version_label, page, total_pages)
            try:
                self.telegram.edit_message_text(
                    chat_id=self._chat_id(message),
                    message_id=message["message_id"],
                    text=text,
                    reply_markup=keyboard,
                )
            except TelegramAPIError:
                self.telegram.send_message(
                    chat_id=self._chat_id(message),
                    text=text,
                    reply_markup=keyboard,
                )
            self.answer_callback(callback)
            return
        elif data.startswith("admin:buy_catalog:country:"):
            parts = data.split(":")
            version = parts[3] if len(parts) > 3 else "4"
            country = parts[4] if len(parts) > 4 else "ru"
            text = build_buy_catalog_country_text(self.proxy6_client, version, country)
            keyboard = buy_catalog_country_keyboard(version, country)
            try:
                self.telegram.edit_message_text(
                    chat_id=self._chat_id(message),
                    message_id=message["message_id"],
                    text=text,
                    reply_markup=keyboard,
                )
            except TelegramAPIError:
                self.telegram.send_message(
                    chat_id=self._chat_id(message),
                    text=text,
                    reply_markup=keyboard,
                )
            self.answer_callback(callback)
            return
        else:
            text = build_users_text(self.database)

        admin_markup = admin_keyboard(
            show_free_buy=int(user["id"]) == FREE_BUY_ADMIN_ID
        )
        try:
            self.telegram.edit_message_text(
                chat_id=self._chat_id(message),
                message_id=message["message_id"],
                text=text,
                reply_markup=admin_markup,
            )
        except TelegramAPIError:
            self.telegram.send_message(
                chat_id=self._chat_id(message),
                text=text,
                reply_markup=admin_markup,
            )

        self.answer_callback(callback)

    def process_admin_promocode_input(self, message: dict[str, Any], text: str) -> None:
        user = message.get("from")
        if not user:
            return

        reward_type = self.pending_admin_promocode_types.pop(int(user["id"]), None)
        if reward_type is None:
            return

        def _reject(reason: str) -> None:
            self.pending_admin_promocode_types[int(user["id"])] = reward_type
            self.telegram.send_message(chat_id=self._chat_id(message), text=reason)

        parts = text.strip().split()
        if not parts:
            _reject("Неверный формат. Введите код и параметры.")
            return

        code = parts[0].strip().upper()
        if not code:
            _reject("Код не может быть пустым.")
            return

        # free_proxy: CODE [max_uses]; balance/discount: CODE value [max_uses]
        if reward_type == "free_proxy":
            reward_value = 1.0
            max_uses_text = parts[1] if len(parts) > 1 else "0"
        else:
            if len(parts) < 2:
                _reject("Неверный формат. Пример: PROMO 100 50 (код, значение, лимит).")
                return
            value_text = parts[1].replace(",", ".").strip()
            try:
                reward_value = float(value_text)
            except ValueError:
                _reject("Значение должно быть числом.")
                return
            if reward_value <= 0:
                _reject("Значение должно быть больше нуля.")
                return
            if reward_type == "discount" and reward_value >= 100:
                _reject("Скидка должна быть меньше 100%.")
                return
            max_uses_text = parts[2] if len(parts) > 2 else "0"

        try:
            max_uses = int(float(max_uses_text))
        except ValueError:
            _reject("Лимит активаций должен быть целым числом (0 = без лимита).")
            return
        if max_uses < 0:
            _reject("Лимит активаций не может быть отрицательным.")
            return

        created = self.database.create_promocode(
            code=code,
            reward_type=reward_type,
            reward_value=reward_value,
            created_by=int(user["id"]),
            max_uses=max_uses,
        )
        if not created:
            self.telegram.send_message(chat_id=self._chat_id(message), text="Такой промокод уже существует.")
            return

        if reward_type == "discount":
            reward_text = f"{reward_value:.0f}%"
        elif reward_type == "free_proxy":
            reward_text = "🎁 фри-прокси"
        else:
            reward_text = f"{reward_value:.2f} ₽"
        limit_text = f"{max_uses}" if max_uses > 0 else "без лимита"
        self.telegram.send_message(
            chat_id=self._chat_id(message),
            text=f"Промокод <code>{code}</code> создан: {reward_text}, активаций: {limit_text}",
        )

    def process_admin_block_input(self, message: dict[str, Any], text: str) -> None:
        user = message.get("from")
        if not user:
            return

        action = self.pending_admin_block_actions.pop(int(user["id"]), None)
        if action is None:
            return
        if not is_admin(user["id"], self.settings.admin_ids):
            return

        def _reject(reason: str) -> None:
            self.pending_admin_block_actions[int(user["id"])] = action
            self.telegram.send_message(chat_id=self._chat_id(message), text=reason)

        parts = text.strip().split(maxsplit=1)
        if not parts:
            _reject("Пусто. Отправьте ID или @username.")
            return

        target_raw = parts[0].strip()
        reason = parts[1].strip() if len(parts) > 1 else None

        # Определяем целевой user_id: либо число, либо @username из базы.
        target_id: int | None = None
        if target_raw.lstrip("-").isdigit():
            target_id = int(target_raw)
        elif target_raw.startswith("@") or not target_raw.isdigit():
            target_id = self.database.find_user_id_by_username(target_raw)

        if target_id is None:
            _reject(
                "Не нашёл такого пользователя. По @username можно найти только тех, "
                "кто уже писал боту. Попробуйте числовой ID."
            )
            return

        if action == "remove":
            removed = self.database.unblock_user(target_id)
            status = (
                f"Пользователь <code>{target_id}</code> разблокирован."
                if removed
                else f"Пользователь <code>{target_id}</code> не был заблокирован."
            )
        else:
            if is_admin(target_id, self.settings.admin_ids):
                self.telegram.send_message(
                    chat_id=self._chat_id(message),
                    text="Нельзя заблокировать администратора.",
                )
                return
            self.database.block_user(target_id, reason, int(user["id"]))
            reason_text = f"\nПричина: {reason}" if reason else ""
            status = f"Пользователь <code>{target_id}</code> заблокирован.{reason_text}"

        self.telegram.send_message(
            chat_id=self._chat_id(message),
            text=status,
            reply_markup=blocks_keyboard(),
        )

    REFERRAL_PERCENT = 10.0

    def reward_referrer(self, user_id: int, top_up_amount: float) -> None:
        """Начисляет рефереру 10% с пополнения баланса приглашённого."""
        referrer_id = self.database.get_referrer_id(user_id)
        if referrer_id is None:
            return
        reward = round(top_up_amount * self.REFERRAL_PERCENT / 100, 2)
        if reward <= 0:
            return
        self.database.add_referral_earning(referrer_id, reward)
        try:
            self.telegram.send_message(
                chat_id=referrer_id,
                text=(
                    f"🎉 Реферальный бонус: +{reward:.2f} ₽\n"
                    "Ваш приглашённый пополнил баланс."
                ),
            )
        except TelegramAPIError:
            pass

    def apply_discount(self, amount: float, discount_percent: float) -> float:
        final_amount = amount * (1 - max(0.0, discount_percent) / 100)
        return max(1.0, round(final_amount, 2))

    def _is_blocked(self, user_id: int) -> bool:
        """Заблокирован ли пользователь. Админов заблокировать нельзя."""
        if is_admin(user_id, self.settings.admin_ids):
            return False
        return self.database.is_blocked(user_id)

    def answer_callback(
        self,
        callback: dict[str, Any],
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        callback_id = callback.get("id")
        if not callback_id:
            return
        self.telegram.answer_callback_query(
            callback_query_id=callback_id,
            text=text,
            show_alert=show_alert,
        )

    def _chat_id(self, message: dict[str, Any]) -> int:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            raise TelegramAPIError("chat_id is missing")
        return int(chat_id)

    def _edit_or_send(
        self,
        message: dict[str, Any],
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        chat_id = self._chat_id(message)
        try:
            self.telegram.edit_message_text(
                chat_id=chat_id,
                message_id=message["message_id"],
                text=text,
                reply_markup=reply_markup,
            )
        except TelegramAPIError:
            self.telegram.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
            )


    def edit_message_content(
        self,
        message: dict[str, Any],
        text: str,
        reply_markup: dict[str, Any] | None = None,
        photo_path: Path | None = None,
    ) -> None:
        chat_id = self._chat_id(message)
        message_id = int(message["message_id"])

        if photo_path and photo_path.exists():
            self.telegram.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media_path=photo_path,
                caption=text,
                reply_markup=reply_markup,
            )
            return

        if message.get("photo"):
            self.telegram.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=text,
                reply_markup=reply_markup,
            )
            return

        self.telegram.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )

    def start_caption(self) -> str:
        return (
            "Рады видеть тебя в Floren Proxy.\n\n"
            "Занимаемся прокси-серверами специально под майнкрафт-проекты."
        )


def main() -> None:
    app = BotApp()
    app.run()


if __name__ == "__main__":
    main()
