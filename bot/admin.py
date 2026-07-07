from __future__ import annotations

from html import escape
from typing import Any

from bot.db import Database
from bot.catalog import get_server_map, load_server_catalog
from bot.proxy6 import Proxy6Client


def admin_keyboard(show_free_buy: bool = False) -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = [
        [
            {"text": "Аккаунт", "callback_data": "admin:account"},
            {"text": "Пользователи", "callback_data": "admin:users"},
        ],
        [
            {"text": "Список прокси", "callback_data": "admin:proxy_list"},
            {"text": "Прокси", "callback_data": "admin:proxies"},
        ],
        [
            {"text": "Каталог покупки", "callback_data": "admin:buy_catalog"},
        ],
        [
            {"text": "Настройка серверов", "callback_data": "admin:servers"},
        ],
        [
            {"text": "Промокоды", "callback_data": "admin:promocodes"},
        ],
        [
            {"text": "🚫 Блокировки", "callback_data": "admin:blocks"},
        ],
    ]
    if show_free_buy:
        rows.append(
            [{"text": "🎁 Купить прокси (бесплатно)", "callback_data": "admin:freebuy"}]
        )
    rows.append([{"text": "Обновить", "callback_data": "admin:refresh"}])
    return {"inline_keyboard": rows}


def free_buy_servers_keyboard() -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = []
    for item in load_server_catalog():
        rows.append(
            [
                {
                    "text": item["name"],
                    "callback_data": f"admin:freebuy:{item['code']}",
                }
            ]
        )
    rows.append([{"text": "Назад", "callback_data": "admin:account"}])
    return {"inline_keyboard": rows}


def start_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Купить прокси",
                    "callback_data": "user:buy_proxy",
                    "style": "primary",
                },
            ],
            [
                {"text": "Профиль", "callback_data": "user:profile"},
                {"text": "Поддержка", "url": "https://t.me/tpofa"},
            ],
            [
                {"text": "Получить прокси", "callback_data": "user:get_proxy"},
            ],
        ]
    }


def proxy_server_keyboard() -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = []
    row: list[dict[str, Any]] = []
    for item in load_server_catalog():
        price = float(item["price_rub"])
        price_text = f"{price:.0f}" if price == int(price) else f"{price:.2f}"
        row.append(
            {
                "text": f"{item['name']} — {price_text} ₽",
                "callback_data": f"user:server:{item['code']}",
            }
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "Назад", "callback_data": "user:back_to_start"}])
    return {"inline_keyboard": rows}


def purchased_proxies_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "🔄 Обновить", "callback_data": "user:purchased_proxies"},
            ],
            [
                {"text": "Назад", "callback_data": "user:back_to_start"},
            ],
        ]
    }


def profile_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "Пополнить баланс", "callback_data": "user:top_up"},
            ],
            [
                {"text": "Купленные прокси", "callback_data": "user:purchased_proxies"},
                {"text": "Реферальная система", "callback_data": "user:referral_system"},
            ],
            [
                {"text": "Применить промокод", "callback_data": "user:apply_promocode"},
            ],
            [
                {"text": "Назад", "callback_data": "user:back_to_start"},
            ],
        ]
    }


def referral_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "Назад", "callback_data": "user:profile"},
            ],
        ]
    }


def build_referral_text(referral_link: str, invited: int, earned_rub: float) -> str:
    return (
        "<b>Реферальная система</b>\n\n"
        "Приглашай друзей по своей ссылке и получай <b>10%</b> с каждого их "
        "пополнения баланса на свой баланс.\n\n"
        f"Твоя ссылка:\n<code>{escape(referral_link)}</code>\n\n"
        f"Приглашено: <b>{invited}</b>\n"
        f"Заработано: <b>{earned_rub:.2f} ₽</b>"
    )


def top_up_methods_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "Оплата по СБП", "callback_data": "user:pay:sbp"},
                {"text": "Банковская карта", "callback_data": "user:pay:card"},
            ],
            [
                {"text": "CryptoBot", "callback_data": "user:pay:cryptobot"},
            ],
        ]
    }


def payment_methods_keyboard(
    context: str,
    include_balance: bool = False,
    free_proxy_server: str | None = None,
) -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = []
    if free_proxy_server:
        rows.append(
            [{"text": "🎁 Забрать бесплатно", "callback_data": f"user:freeproxy:{free_proxy_server}"}]
        )
    if include_balance:
        rows.append(
            [{"text": "💰 Оплатить с баланса", "callback_data": f"{context}:balance"}]
        )
    rows.extend(
        [
            [
                {"text": "Оплата по СБП", "callback_data": f"{context}:sbp"},
                {"text": "Банковская карта", "callback_data": f"{context}:card"},
            ],
            [
                {"text": "CryptoBot", "callback_data": f"{context}:cryptobot"},
            ],
        ]
    )
    return {"inline_keyboard": rows}


def buy_agreement_keyboard(server_code: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Согласен, оплатить", "callback_data": f"user:buy:confirm:{server_code}"},
            ],
            [
                {"text": "Назад", "callback_data": "user:buy_proxy"},
            ],
        ]
    }


def crypto_invoice_keyboard(pay_url: str, invoice_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "Оплатить CryptoBot", "url": pay_url},
            ],
            [
                {"text": "Проверить оплату", "callback_data": f"invoice:check:{invoice_id}"},
            ],
        ]
    }


def build_profile_text(user: dict[str, Any]) -> str:
    username = user.get("username")
    username_text = f"@{escape(username)}" if username else "не указан"
    balance = float(user.get("balance_rub") or 0.0)
    discount_percent = float(user.get("active_discount_percent") or 0.0)
    discount_code = str(user.get("active_discount_code") or "").strip()
    discount_line = ""
    if discount_percent > 0:
        code_text = f" ({escape(discount_code)})" if discount_code else ""
        discount_line = f"\nАктивная скидка: {discount_percent:.0f}%{code_text}"
    free_credits = int(user.get("free_proxy_credits") or 0)
    free_line = f"\n🎁 Бесплатных прокси: {free_credits}" if free_credits > 0 else ""
    return (
        "<b>Профиль</b>\n"
        f"Юзернейм: {username_text}\n"
        f"Баланс: {balance:.2f} ₽"
        f"{discount_line}"
        f"{free_line}"
    )


def build_top_up_prompt_text() -> str:
    return "Введите сумму пополнения\n\n(минимум 10 ₽)"


def build_top_up_payment_text(amount: float) -> str:
    return (
        "<b>Пополнение баланса</b>\n\n"
        f"Сумма: {amount:.2f} ₽\n"
        "Выберите способ оплаты.\n\n"
        "СБП и Банковская карта — через YooKassa.\n"
        "CryptoBot — через Crypto Pay."
    )


def build_buy_payment_text(server_name: str) -> str:
    return (
        f"<b>{escape(server_name)} Proxy</b>\n\n"
        "Выберите способ оплаты.\n\n"
        "СБП и Банковская карта — через YooKassa.\n"
        "CryptoBot — через Crypto Pay."
    )


def build_buy_payment_summary_text(
    server_name: str,
    base_amount: float,
    final_amount: float,
    discount_percent: float = 0.0,
) -> str:
    lines = [
        f"<b>{escape(server_name)} Proxy</b>",
        "",
        f"Цена: {base_amount:.2f} ₽",
    ]
    if discount_percent > 0:
        lines.append(f"Скидка: {discount_percent:.0f}%")
        lines.append(f"К оплате: {final_amount:.2f} ₽")
    lines.extend(
        [
            "",
            "Выберите способ оплаты.",
            "",
            "СБП и Банковская карта — через YooKassa.",
            "CryptoBot — через Crypto Pay.",
        ]
    )
    return "\n".join(lines)


def build_crypto_invoice_text(title: str, amount: float) -> str:
    return (
        f"<b>{escape(title)}</b>\n\n"
        f"Сумма: {amount:.2f} ₽\n"
        "Для оплаты нажмите кнопку ниже, затем вернитесь и нажмите «Проверить оплату»."
    )


def build_purchased_proxies_text(items: list[dict[str, str | int | float | None]]) -> str:
    server_map = get_server_map()
    if not items:
        return "<b>Купленные прокси</b>\nПока пусто."
    lines = ["<b>Купленные прокси</b>", ""]
    for item in items:
        server = server_map.get(str(item.get("server_code")), {"name": str(item.get("server_code"))})
        lines.extend(
            [
                f"<b>{escape(str(server['name']))}</b>",
                f"IP: <code>{escape(str(item.get('host', 'n/a')))}</code>",
                f"Порт: <code>{escape(str(item.get('port', 'n/a')))}</code>",
                f"Логин: <code>{escape(str(item.get('login', 'n/a')))}</code>",
                f"Пароль: <code>{escape(str(item.get('password', 'n/a')))}</code>",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def build_buy_proxy_agreement_text(server_name: str, bot_username: str) -> str:
    return (
        f"<b>{escape(server_name)} Proxy</b>\n\n"
        "Продолжая оформление, вы подтверждаете согласие с "
        f'<a href="https://t.me/{escape(bot_username)}?start=offer">публичной офертой</a>, '
        f'<a href="https://t.me/{escape(bot_username)}?start=refund">политикой возвратов</a> '
        "и "
        f'<a href="https://t.me/{escape(bot_username)}?start=privacy">политикой конфиденциальности</a>.'
    )


def build_offer_text() -> str:
    return (
        "<b>Публичная оферта</b>\n\n"
        "1. О чем этот документ\n"
        "1.1. Настоящий текст устанавливает правила предоставления доступа к прокси и считается публичным предложением сервиса.\n"
        "1.2. Оплата заказа либо фактическое использование выданного доступа означает полное принятие этих условий.\n\n"
        "2. Что получает пользователь\n"
        "2.1. После оформления заказа сервис предоставляет доступ к выбранному тарифу или пакету прокси.\n"
        "2.2. Параметры услуги, срок действия и стоимость указываются непосредственно в интерфейсе бота перед оплатой.\n\n"
        "3. Обязанности пользователя\n"
        "3.1. Пользователь самостоятельно отвечает за законность своих действий при использовании прокси.\n"
        "3.2. Запрещается передавать доступ посторонним лицам, пытаться нарушить работу сервиса или использовать его во вред инфраструктуре.\n\n"
        "4. Обязанности сервиса\n"
        "4.1. Сервис предоставляет доступ в пределах технических возможностей платформы.\n"
        "4.2. Временные ограничения работы допускаются в случае обновлений, профилактики, аварий либо обстоятельств, не зависящих от сервиса.\n\n"
        "5. Оплата и расчеты\n"
        "5.1. Цена услуги определяется на момент оформления заказа и отображается пользователю до оплаты.\n"
        "5.2. Денежные средства считаются принятыми после подтверждения платежной системой.\n"
        "5.3. Возврат оплаченных сумм осуществляется только в случаях, предусмотренных законом или отдельными правилами возврата сервиса.\n\n"
        "6. Предел ответственности\n"
        "6.1. Сервис не отвечает за блокировки, ограничения, сбои внешних платформ, сетей связи, провайдеров и иных третьих лиц.\n"
        "6.2. Максимальная ответственность сервиса в любом споре ограничивается суммой последнего оплаченного заказа пользователя.\n\n"
        "7. Изменение условий\n"
        "7.1. Сервис вправе обновлять настоящие условия без индивидуального уведомления каждого пользователя.\n"
        "7.2. Актуальная редакция начинает действовать с момента публикации внутри бота либо на иной указанной странице сервиса.\n\n"
        "8. Итоговые положения\n"
        "8.1. Во всем, что не урегулировано этим текстом, стороны руководствуются применимым законодательством.\n"
        "8.2. Продолжение использования сервиса подтверждает согласие пользователя с действующей редакцией оферты."
    )


def build_refund_policy_text() -> str:
    return (
        "<b>Политика возвратов</b>\n\n"
        "1. Когда возможен возврат\n"
        "1.1. Возврат рассматривается только в ситуациях, когда оплаченная услуга не могла быть оказана по причине, находящейся на стороне сервиса.\n\n"
        "2. Допустимые основания\n"
        "2.1. Пользователь не получил рабочий доступ из-за технической ошибки сервиса.\n"
        "2.2. Платеж был списан повторно по одной и той же операции.\n\n"
        "3. Когда возврат не делается\n"
        "3.1. Если ограничения возникли из-за внешних площадок, игровых серверов, интернет-провайдеров, банков, платежных систем или государственных мер.\n"
        "3.2. Если пользователь нарушил правила использования либо передал доступ третьим лицам.\n"
        "3.3. Если пользователь сам ошибся при выборе услуги, срока, тарифа или объема.\n\n"
        "4. Как подать обращение\n"
        "4.1. Запрос направляется в поддержку с указанием Telegram ID, времени платежа и краткого описания проблемы.\n"
        "4.2. После проверки обстоятельств сервис принимает решение о полном, частичном возврате или об отказе.\n\n"
        "5. Способ возврата\n"
        "5.1. Если возврат согласован, средства перечисляются тем способом, который использовался при оплате, если иной вариант отдельно не подтвержден сторонами."
    )


def build_privacy_policy_text() -> str:
    return (
        "<b>Политика конфиденциальности</b>\n\n"
        "1. Назначение политики\n"
        "1.1. Этот документ объясняет, какие данные сервис использует и зачем это необходимо для работы прокси-доступа.\n"
        "1.2. Продолжая пользоваться ботом, пользователь подтверждает ознакомление с этими правилами обработки данных.\n\n"
        "2. Какие сведения могут использоваться\n"
        "2.1. Идентификатор Telegram и username пользователя.\n"
        "2.2. Контактные сведения, которые пользователь указывает при оплате или обращении в поддержку.\n"
        "2.3. Технические данные, связанные с использованием сервиса, включая IP-адрес, параметры устройства и служебную статистику.\n\n"
        "3. Для чего это нужно\n"
        "3.1. Для выдачи и сопровождения доступа к прокси.\n"
        "3.2. Для обработки платежей, обращений и поддержки пользователей.\n"
        "3.3. Для выполнения обязательных требований законодательства и внутреннего учета.\n\n"
        "4. Передача третьим сторонам\n"
        "4.1. В необходимых случаях данные могут быть переданы платежным партнерам, операторам связи и иным участникам, без которых невозможно оказание услуги.\n"
        "4.2. Продажа персональных данных или передача их для рекламных рассылок не осуществляется.\n\n"
        "5. Срок хранения информации\n"
        "5.1. Данные сохраняются на период использования сервиса и далее только на срок, который требуется для законных и учетных целей.\n\n"
        "6. Возможности пользователя\n"
        "6.1. Пользователь может обратиться с запросом на уточнение, обновление или удаление своих данных, если это допускается законом и не мешает исполнению обязательств сервиса.\n\n"
        "7. Связь по вопросам данных\n"
        "7.1. Обращения по теме персональных данных направляются через контакты, указанные в интерфейсе бота."
    )


def build_profile_alert_text(user: dict[str, Any]) -> str:
    username = user.get("username")
    username_text = f"@{username}" if username else "не указан"
    return (
        "Профиль\n"
        f"Юзернейм: {username_text}\n"
        "Баланс: 0.00 ₽"
    )


def promocodes_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "Создать на ₽", "callback_data": "admin:promocodes:create:balance"},
                {"text": "Создать скидку %", "callback_data": "admin:promocodes:create:discount"},
            ],
            [
                {"text": "🎁 Создать фри-прокси", "callback_data": "admin:promocodes:create:free_proxy"},
            ],
            [
                {"text": "Обновить список", "callback_data": "admin:promocodes"},
            ],
            [
                {"text": "Назад", "callback_data": "admin:account"},
            ],
        ]
    }


def build_promocodes_text(items: list[dict[str, str | int | float | None]]) -> str:
    lines = [
        "<b>Промокоды</b>",
        "Формат создания (последнее число — лимит активаций, 0 = без лимита):",
        "• На баланс: <code>PROMO 100 50</code>",
        "• Скидка: <code>PROMO 15 0</code>",
        "• Фри-прокси: <code>PROMO 100</code>",
        "",
        "<b>Последние:</b>",
    ]
    if not items:
        lines.append("Пока нет созданных промокодов.")
        return "\n".join(lines)

    for item in items:
        reward_type = str(item.get("reward_type") or "")
        reward_value = float(item.get("reward_value") or 0.0)
        if reward_type == "balance":
            reward_text = f"{reward_value:.2f} ₽"
        elif reward_type == "free_proxy":
            reward_text = "🎁 фри-прокси"
        else:
            reward_text = f"{reward_value:.0f}%"
        status_text = "активен" if int(item.get("is_active") or 0) == 1 else "выключен"
        max_uses = int(item.get("max_uses") or 0)
        used_count = int(item.get("used_count") or 0)
        limit_text = f"{used_count}/{max_uses}" if max_uses > 0 else f"{used_count}/∞"
        lines.append(
            f"• <code>{escape(str(item.get('code') or ''))}</code> - {reward_text}, "
            f"{status_text}, активаций: <code>{limit_text}</code>"
        )
    return "\n".join(lines)


def build_promocode_create_prompt_text(reward_type: str) -> str:
    if reward_type == "discount":
        return (
            "Введите промокод, скидку (%) и лимит активаций через пробел.\n"
            "Лимит 0 = без ограничений.\n\n"
            "Пример: <code>SUMMER 15 100</code>"
        )
    if reward_type == "free_proxy":
        return (
            "Введите промокод и лимит активаций через пробел.\n"
            "Лимит 0 = без ограничений. Даёт право на 1 бесплатный прокси "
            "(любой сервер на выбор).\n\n"
            "Пример: <code>FREEPROXY 100</code>"
        )
    return (
        "Введите промокод, сумму на баланс (₽) и лимит активаций через пробел.\n"
        "Лимит 0 = без ограничений.\n\n"
        "Пример: <code>WELCOME 100 50</code>"
    )


def blocks_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "🚫 Заблокировать", "callback_data": "admin:blocks:add"},
                {"text": "✅ Разблокировать", "callback_data": "admin:blocks:remove"},
            ],
            [
                {"text": "Обновить список", "callback_data": "admin:blocks"},
            ],
            [
                {"text": "Назад", "callback_data": "admin:account"},
            ],
        ]
    }


def build_blocks_text(items: list[dict[str, str | int | None]]) -> str:
    lines = [
        "<b>Блокировки</b>",
        "Заблокированные не могут пользоваться ботом.",
        "",
        "<b>Список:</b>",
    ]
    if not items:
        lines.append("Пока никто не заблокирован.")
        return "\n".join(lines)

    for item in items:
        user_id = item.get("user_id")
        username = item.get("username")
        name_parts = " ".join(
            str(part) for part in [item.get("first_name"), item.get("last_name")] if part
        ).strip()
        if username:
            who = f"@{escape(str(username))}"
        elif name_parts:
            who = escape(name_parts)
        else:
            who = str(user_id)
        reason = str(item.get("reason") or "").strip()
        reason_text = f" — {escape(reason)}" if reason else ""
        lines.append(f"• {who} (<code>{user_id}</code>){reason_text}")
    return "\n".join(lines)


def build_block_prompt_text(action: str) -> str:
    if action == "remove":
        return (
            "Кого разблокировать?\n"
            "Отправьте числовой ID или @username одним сообщением."
        )
    return (
        "Кого заблокировать?\n"
        "Отправьте числовой ID или @username, при желании через пробел причину.\n\n"
        "Примеры:\n"
        "<code>123456789 спам</code>\n"
        "<code>@username</code>"
    )


def is_admin(user_id: int, admin_ids: set[int]) -> bool:
    return user_id in admin_ids


def display_name(record: dict[str, str | int | None]) -> str:
    full_name = " ".join(
        str(part) for part in [record.get("first_name"), record.get("last_name")] if part
    ).strip()
    if full_name:
        return escape(full_name)
    username = record.get("username")
    if username:
        return f"@{escape(str(username))}"
    return str(record["user_id"])


def build_account_text(
    bot_info: dict[str, Any],
    admin_ids: set[int],
    proxy6_client: Proxy6Client,
) -> str:
    proxy6_info = proxy6_client.get_account_info()
    if not proxy6_info.get("configured"):
        proxy_line = "Proxy6: не настроен"
    elif proxy6_info.get("error"):
        proxy_line = f"Proxy6: ошибка запроса ({escape(str(proxy6_info['error']))})"
    else:
        balance = proxy6_info.get("balance", "n/a")
        currency = proxy6_info.get("currency", "")
        proxy_line = f"Proxy6: подключен, баланс {balance} {currency}".strip()

    return (
        "<b>Админка</b>\n"
        f"Бот: @{escape(bot_info.get('username') or 'without_username')}\n"
        f"ID бота: <code>{bot_info['id']}</code>\n"
        f"Админов: <code>{len(admin_ids)}</code>\n"
        f"{proxy_line}"
    )


def build_users_text(database: Database) -> str:
    stats = database.get_user_stats()
    lines = [
        "<b>Пользователи</b>",
        f"Всего: <code>{stats.total_users}</code>",
        f"Новых сегодня: <code>{stats.new_today}</code>",
        "",
        "<b>Последние 10:</b>",
    ]
    if not stats.last_users:
        lines.append("Пока никого нет.")
        return "\n".join(lines)

    for record in stats.last_users:
        lines.append(f"• {display_name(record)} - <code>{record['user_id']}</code>")
    return "\n".join(lines)


def build_proxies_text(proxy6_client: Proxy6Client) -> str:
    payload = proxy6_client.get_account_info()
    if not payload.get("configured"):
        return "<b>Прокси</b>\nProxy6 не настроен."
    if payload.get("error"):
        return f"<b>Прокси</b>\nОшибка запроса: <code>{escape(str(payload['error']))}</code>"

    proxy_list = payload.get("list") or {}
    if not isinstance(proxy_list, dict) or not proxy_list:
        return "<b>Прокси</b>\nСписок пуст."

    lines = [
        "<b>Прокси</b>",
        f"Всего: <code>{len(proxy_list)}</code>",
        "",
    ]
    for proxy_id, proxy in proxy_list.items():
        if not isinstance(proxy, dict):
            continue
        lines.append(
            "\n".join(
                [
                    f"<b>ID {escape(str(proxy_id))}</b>",
                    f"IP: <code>{escape(str(proxy.get('ip', 'n/a')))}</code>",
                    f"Порт: <code>{escape(str(proxy.get('port', 'n/a')))}</code>",
                    f"Логин: <code>{escape(str(proxy.get('user', 'n/a')))}</code>",
                    f"Пароль: <code>{escape(str(proxy.get('pass', 'n/a')))}</code>",
                    f"Страна: <code>{escape(str(proxy.get('country', 'n/a')))}</code>",
                    f"Активен: <code>{escape(str(proxy.get('active', 'n/a')))}</code>",
                    f"До: <code>{escape(str(proxy.get('date_end', 'n/a')))}</code>",
                ]
            )
        )
        lines.append("")

    return "\n".join(lines).rstrip()


def build_proxy_list_text(proxy6_client: Proxy6Client) -> str:
    proxy_list = proxy6_client.list_proxies()
    server_catalog = load_server_catalog()
    if not proxy_list:
        return "<b>Список прокси</b>\nНет доступных прокси."

    lines = ["<b>Список прокси</b>", ""]
    for index, proxy in enumerate(proxy_list):
        server = server_catalog[index] if index < len(server_catalog) else None
        price_text = f"{server['price_rub']:.2f} ₽" if server else "цена не задана"
        server_name = server["name"] if server else f"Proxy #{index + 1}"
        lines.extend(
            [
                f"<b>{escape(server_name)}</b>",
                f"IP: <code>{escape(str(proxy.get('ip', 'n/a')))}</code>",
                f"Порт: <code>{escape(str(proxy.get('port', 'n/a')))}</code>",
                f"ID: <code>{escape(str(proxy.get('id', 'n/a')))}</code>",
                f"Цена: <code>{escape(price_text)}</code>",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def buy_catalog_versions_keyboard(proxy6_client: Proxy6Client) -> dict[str, Any]:
    items = proxy6_client.get_purchase_catalog()
    seen: list[tuple[str, str]] = []
    for item in items:
        version = str(item["version"])
        label = str(item["version_label"])
        pair = (version, label)
        if pair not in seen:
            seen.append(pair)

    rows: list[list[dict[str, str]]] = []
    for version, label in seen:
        rows.append([{"text": label, "callback_data": f"admin:buy_catalog:version:{version}"}])
    if not rows:
        rows.append([{"text": "Обновить", "callback_data": "admin:buy_catalog"}])
    return {"inline_keyboard": rows}


def build_buy_catalog_versions_text() -> str:
    return "<b>Каталог покупки</b>\nВыберите тип прокси."


def buy_catalog_countries_keyboard(
    proxy6_client: Proxy6Client,
    version: str,
    page: int = 1,
    page_size: int = 18,
) -> tuple[dict[str, Any], int]:
    items = [item for item in proxy6_client.get_purchase_catalog() if str(item["version"]) == version]
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = min(max(page, 1), total_pages)
    start = (page - 1) * page_size
    end = start + page_size
    rows: list[list[dict[str, str]]] = []

    current = items[start:end]
    for index in range(0, len(current), 2):
        row: list[dict[str, str]] = []
        for item in current[index:index + 2]:
            row.append(
                {
                    "text": str(item["country"]).upper(),
                    "callback_data": f"admin:buy_catalog:country:{version}:{item['country']}",
                }
            )
        rows.append(row)

    nav: list[dict[str, str]] = []
    if page > 1:
        nav.append({"text": "←", "callback_data": f"admin:buy_catalog:version:{version}:page:{page - 1}"})
    nav.append({"text": "Типы", "callback_data": "admin:buy_catalog"})
    if page < total_pages:
        nav.append({"text": "→", "callback_data": f"admin:buy_catalog:version:{version}:page:{page + 1}"})
    rows.append(nav)
    return {"inline_keyboard": rows}, total_pages


def build_buy_catalog_countries_text(version_label: str, page: int, total_pages: int) -> str:
    return (
        "<b>Каталог покупки</b>\n"
        f"Тип: <code>{escape(version_label)}</code>\n"
        f"Страница: <code>{page}/{total_pages}</code>\n\n"
        "Выберите страну."
    )


def build_buy_catalog_country_text(proxy6_client: Proxy6Client, version: str, country: str) -> str:
    for item in proxy6_client.get_purchase_catalog():
        if str(item["version"]) == version and str(item["country"]) == country:
            periods = item.get("periods")
            price_lines: list[str] = []
            if isinstance(periods, dict):
                for days in sorted(periods.keys(), key=lambda value: int(value)):
                    price = periods[days]
                    price_lines.append(
                        f"• {escape(str(days))} дн. - <code>{float(price):.2f} ₽</code>"
                    )
            prices_text = "\n".join(price_lines) if price_lines else "Цены не найдены."
            return (
                "<b>Каталог покупки</b>\n\n"
                f"Тип: <code>{escape(str(item['version_label']))}</code>\n"
                f"Страна: <code>{escape(str(item['country']).upper())}</code>\n"
                f"Доступно: <code>{escape(str(item['count']))}</code>\n"
                "\n"
                "<b>Периоды и цены за 1 шт:</b>\n"
                f"{prices_text}"
            )
    return "<b>Каталог покупки</b>\nПозиция не найдена."


def buy_catalog_country_keyboard(version: str, country: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "К странам", "callback_data": f"admin:buy_catalog:version:{version}"},
                {"text": "К типам", "callback_data": "admin:buy_catalog"},
            ],
        ]
    }


SERVER_FIXED_PERIOD = 7


def servers_keyboard() -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    for item in load_server_catalog():
        rows.append(
            [
                {
                    "text": item["name"],
                    "callback_data": f"admin:servers:pick:{item['code']}",
                }
            ]
        )
    rows.append([{"text": "Назад", "callback_data": "admin:account"}])
    return {"inline_keyboard": rows}


def build_servers_text() -> str:
    lines = ["<b>Настройка серверов</b>", ""]
    for item in load_server_catalog():
        versions = item.get("allowed_versions") or []
        countries = item.get("allowed_countries") or []
        version_label = Proxy6Client.VERSION_LABELS.get(versions[0], versions[0]) if versions else "—"
        country_text = countries[0].upper() if countries else "—"
        lines.append(
            f"• <b>{escape(str(item['name']))}</b>: {escape(version_label)} / "
            f"{escape(country_text)} / {SERVER_FIXED_PERIOD} дн."
        )
    lines.extend(["", "Выберите сервер для настройки."])
    return "\n".join(lines)


def server_versions_keyboard(proxy6_client: Proxy6Client, server_code: str) -> dict[str, Any]:
    items = proxy6_client.get_purchase_catalog()
    seen: list[tuple[str, str]] = []
    for item in items:
        pair = (str(item["version"]), str(item["version_label"]))
        if pair not in seen:
            seen.append(pair)

    rows: list[list[dict[str, str]]] = []
    for version, label in seen:
        rows.append(
            [{"text": label, "callback_data": f"admin:servers:ver:{server_code}:{version}"}]
        )
    if not rows:
        rows.append([{"text": "Обновить", "callback_data": f"admin:servers:pick:{server_code}"}])
    rows.append([{"text": "Назад", "callback_data": "admin:servers"}])
    return {"inline_keyboard": rows}


def build_server_versions_text(server_name: str) -> str:
    return (
        "<b>Настройка сервера</b>\n"
        f"Сервер: <code>{escape(server_name)}</code>\n\n"
        "Выберите тип прокси."
    )


def server_countries_keyboard(
    proxy6_client: Proxy6Client,
    server_code: str,
    version: str,
    page: int = 1,
    page_size: int = 18,
) -> tuple[dict[str, Any], int]:
    items = [item for item in proxy6_client.get_purchase_catalog() if str(item["version"]) == version]
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = min(max(page, 1), total_pages)
    start = (page - 1) * page_size
    current = items[start:start + page_size]

    rows: list[list[dict[str, str]]] = []
    for index in range(0, len(current), 2):
        row: list[dict[str, str]] = []
        for item in current[index:index + 2]:
            row.append(
                {
                    "text": str(item["country"]).upper(),
                    "callback_data": f"admin:servers:set:{server_code}:{version}:{item['country']}",
                }
            )
        rows.append(row)

    nav: list[dict[str, str]] = []
    if page > 1:
        nav.append(
            {"text": "←", "callback_data": f"admin:servers:ver:{server_code}:{version}:page:{page - 1}"}
        )
    nav.append({"text": "Типы", "callback_data": f"admin:servers:pick:{server_code}"})
    if page < total_pages:
        nav.append(
            {"text": "→", "callback_data": f"admin:servers:ver:{server_code}:{version}:page:{page + 1}"}
        )
    rows.append(nav)
    return {"inline_keyboard": rows}, total_pages


def build_server_countries_text(server_name: str, version_label: str, page: int, total_pages: int) -> str:
    return (
        "<b>Настройка сервера</b>\n"
        f"Сервер: <code>{escape(server_name)}</code>\n"
        f"Тип: <code>{escape(version_label)}</code>\n"
        f"Страница: <code>{page}/{total_pages}</code>\n\n"
        "Выберите страну."
    )


def build_server_saved_text(server_name: str, version_label: str, country: str) -> str:
    return (
        "<b>Сервер настроен</b>\n\n"
        f"Сервер: <code>{escape(server_name)}</code>\n"
        f"Тип: <code>{escape(version_label)}</code>\n"
        f"Страна: <code>{escape(country.upper())}</code>\n"
        f"Срок: <code>{SERVER_FIXED_PERIOD} дн.</code>"
    )


def server_saved_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "К серверам", "callback_data": "admin:servers"}],
        ]
    }
