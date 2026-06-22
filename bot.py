import os
import asyncio
import logging
import random
import re
import json
import httpx
import sys
import csv
import io
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── aiogram (бот 1: анонимные комментарии) ──
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ── python-telegram-bot (бот 2: анонимные сообщения + админка) ──
from telegram import Update, InlineKeyboardButton as PTBInlineKeyboardButton, InlineKeyboardMarkup as PTBInlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application as PTBApplication,
    CommandHandler as PTBCommandHandler,
    MessageHandler as PTBMessageHandler,
    ContextTypes as PTBContextTypes,
    filters as PTBfilters,
    CallbackQueryHandler as PTBCallbackQueryHandler,
)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ═══════════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ (берём из переменных окружения, с fallback на дефолт)
# ═══════════════════════════════════════════════════════════════════

BOT1_TOKEN            = os.environ.get("BOT1_TOKEN", "")
BOT1_CHANNEL_ID       = int(os.environ.get("BOT1_CHANNEL_ID", "-1003854171715"))
BOT1_DISCUSSION_CHAT_ID = int(os.environ.get("BOT1_DISCUSSION_CHAT_ID", "-1003718571364"))

BOT2_TOKEN            = os.environ.get("BOT2_TOKEN", "")
BOT2_CHANNEL_ID       = int(os.environ.get("BOT2_CHANNEL_ID", "-1003854171715"))
BOT2_USERNAME         = os.environ.get("BOT2_USERNAME", "Shkola6_anonchik_bot")  # имя бота 2
BOT2_CHAT_INVITE      = os.environ.get("BOT2_CHAT_INVITE", "https://t.me/+N1hmM9BYc1VkZWQ1")  # ссылка на чат
GROQ_API_KEY          = os.environ.get("GROQ_API_KEY", "")
ADMIN_ID              = int(os.environ.get("ADMIN_ID", "8627543263"))
SE_USER               = "422568370"  # зашито напрямую по просьбе
SE_SECRET             = "bhCjTco48ZpWVtMHftGedNpgyYAWJsvd"  # зашито напрямую по просьбе
SE_MONTH_LIMIT        = int(os.environ.get("SE_MONTH_LIMIT", "2000"))
RENDER_URL            = os.environ.get("RENDER_URL", "https://sausha-bot.onrender.com")

# ── Спецпсевдонимы: для определённых ID везде вместо реального имени
#    показывается заданный текст (в логах, админке, уведомлениях и т.д.) ──
SPECIAL_DISPLAY_NAMES: dict[int, str] = {
    7810494142: "Всевышний Аллах",
}

def get_display_name(uid: int, username: str | None = None,
                      first_name: str | None = None, last_name: str | None = None) -> str:
    """Возвращает отображаемое имя пользователя с учётом спецпсевдонимов."""
    if uid in SPECIAL_DISPLAY_NAMES:
        return SPECIAL_DISPLAY_NAMES[uid]
    if username:
        return f"@{username}"
    full = f"{first_name or ''} {last_name or ''}".strip()
    return full or "—"

if not BOT1_TOKEN or not BOT2_TOKEN:
    raise RuntimeError(
        "Не заданы BOT1_TOKEN / BOT2_TOKEN. "
        "Задайте переменные окружения или создайте файл .env"
    )

# ═══════════════════════════════════════════════════════════════════
# ЛОГИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════════

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# БОТ 1: АНОНИМНЫЕ КОММЕНТАРИИ К ПОСТАМ КАНАЛА
# ═══════════════════════════════════════════════════════════════════

EMOJI_POOL = [
    "🐶","🐱","🐭","🐹","🐰","🦊","🐻","🐼","🐨","🐯",
    "🦁","🐮","🐷","🐸","🐵","🐔","🐧","🐦","🦆","🦅",
    "🦉","🦇","🐺","🐗","🐴","🦄","🐝","🦋","🐌","🐞",
    "🐜","🐢","🐍","🦎","🐙","🦑","🦐","🦀","🐡","🐠",
    "🐟","🐬","🐳","🦈","🐊","🐅","🐆","🦓","🦍","🐘",
    "🦛","🦏","🐪","🦒","🦘","🐃","🦌","🐑","🦙","🐕",
    "🐈","🦃","🦚","🦜","🦢","🦩","🕊","🐇","🦝","🦨",
    "🦡","🦦","🦥","🐁","🐀","🐿","🦔","🌸","🌺","🌻",
    "🍀","🌈","⭐","🌙","☀️","❄️","🔥","💧","🌊","🌿"
]
PSEUDO_LEN = 4

class AnonState(StatesGroup):
    waiting_text = State()

# Хранилища бота 1
bot1_user_pseudos: dict[int, dict[int, str]] = {}
bot1_pending: dict[int, tuple[int, int]] = {}
bot1_post_to_discussion_id: dict[int, int] = {}
bot1_anon_msg_to_user: dict[int, int] = {}
bot1_anon_msg_to_post: dict[int, int] = {}
bot1_reply_msg_to_post: dict[int, int] = {}

bot1 = Bot(token=BOT1_TOKEN)
bot1_dp = Dispatcher(storage=MemoryStorage())
bot1_username_cache: str | None = None

# Состояние админ-панели бота 1 (только для ADMIN_ID, один админ — словаря достаточно)
bot1_admin_state: dict[int, str] = {}


async def bot1_get_username() -> str:
    global bot1_username_cache
    if not bot1_username_cache:
        me = await bot1.get_me()
        bot1_username_cache = me.username
    return bot1_username_cache


def bot1_get_pseudo(user_id: int, post_id: int) -> str:
    if user_id not in bot1_user_pseudos:
        bot1_user_pseudos[user_id] = {}
    if post_id not in bot1_user_pseudos[user_id]:
        rng = random.Random(f"{user_id}:{post_id}:{os.urandom(4).hex()}")
        chosen = rng.sample(EMOJI_POOL, PSEUDO_LEN)
        bot1_user_pseudos[user_id][post_id] = "".join(chosen)
    return bot1_user_pseudos[user_id][post_id]


def bot1_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩  Анонимки",       callback_data="b1_admin_tab_messages"),
         InlineKeyboardButton(text="👥  Пользователи",   callback_data="b1_admin_tab_starts")],
        [InlineKeyboardButton(text="📨  Логи анонимок",  callback_data="b1_admin_anon_msgs")],
        [InlineKeyboardButton(text="📣  Рассылка",      callback_data="b1_admin_broadcast"),
         InlineKeyboardButton(text="🏆  Топ",            callback_data="b1_admin_top")],
        [InlineKeyboardButton(text="➕  Добавить ID",   callback_data="b1_admin_add_ids"),
         InlineKeyboardButton(text="📋  Список ID",     callback_data="b1_admin_list_ids")],
        [InlineKeyboardButton(text="📤  Экспорт CSV",   callback_data="b1_admin_export"),
         InlineKeyboardButton(text="🧹  Удалить >7д",   callback_data="b1_admin_clean_old")],
    ])


def bot1_admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙  Назад в панель", callback_data="b1_admin_back")]
    ])


@bot1_dp.message(Command("admin"))
async def bot1_cmd_admin(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.reply("⛔️ Доступ запрещён.")
        return
    await message.reply(admin_text(), reply_markup=bot1_admin_keyboard())


def bot1_nav_keyboard(page, total, prefix, clear_cb) -> InlineKeyboardMarkup:
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"{prefix}{page - 1}"))
    if page < total - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"{prefix}{page + 1}"))
    rows = []
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🗑  Очистить всё", callback_data=clear_cb)])
    rows.append([InlineKeyboardButton(text="🔙  Назад в панель", callback_data="b1_admin_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def bot1_show_message_logs_page(callback, page):
    if not message_logs:
        await callback.message.edit_text("📭 Нет анонимных сообщений.",
                                          reply_markup=bot1_nav_keyboard(0, 1, "b1_msg_page_", "b1_msg_clear"))
        return
    items, total, page = _paginate(message_logs, page)
    lines = [f"📩 Анонимки — стр. {page + 1}/{total}\n"]
    for i, e in enumerate(items, page * 5 + 1):
        dt = datetime.fromisoformat(e["timestamp"]).strftime("%d.%m.%Y %H:%M")
        name = get_display_name(e["user_id"], e.get("username"), e.get("first_name"), e.get("last_name"))
        snip = (e.get("text") or "")[:60] or "—"
        ico = "🚫" if e.get("blocked") else "✅"
        lines.append(f"{ico} {i}. {dt}\n👤 {e['user_id']} {name}\n📎 {e.get('content_type', 'текст')}: {snip}")
    await callback.message.edit_text("\n\n".join(lines),
                                      reply_markup=bot1_nav_keyboard(page, total, "b1_msg_page_", "b1_msg_clear"))


async def bot1_show_start_logs_page(callback, page):
    if not start_logs:
        await callback.message.edit_text("📭 Нет записей.",
                                          reply_markup=bot1_nav_keyboard(0, 1, "b1_start_page_", "b1_start_clear"))
        return
    items, total, page = _paginate(start_logs, page)
    lines = [f"👥 Пользователи — стр. {page + 1}/{total}\n"]
    for i, e in enumerate(items, page * 5 + 1):
        dt = datetime.fromisoformat(e["timestamp"]).strftime("%d.%m.%Y %H:%M")
        name = get_display_name(e["user_id"], e.get("username"), e.get("first_name"), e.get("last_name"))
        lines.append(f"{i}. {dt}\n👤 {e['user_id']} {name}")
    await callback.message.edit_text("\n\n".join(lines),
                                      reply_markup=bot1_nav_keyboard(page, total, "b1_start_page_", "b1_start_clear"))


async def bot1_show_anon_messages_page(callback, page):
    if not anon_messages_log:
        await callback.message.edit_text("📭 Нет анонимных сообщений в логе.",
                                          reply_markup=bot1_nav_keyboard(0, 1, "b1_anon_page_", "b1_anon_clear"))
        return
    items, total, page = _paginate(anon_messages_log, page)
    lines = [f"📨 Логи анонимных сообщений — стр. {page + 1}/{total}\n"]
    for i, e in enumerate(items, page * 5 + 1):
        dt = datetime.fromisoformat(e["timestamp"]).strftime("%d.%m.%Y %H:%M")
        name = get_display_name(e["user_id"], e.get("username"), e.get("first_name"), e.get("last_name"))
        snip = (e.get("text") or "")[:80] or "—"
        lines.append(f"{i}. {dt}\n👤 {e['user_id']} {name}\n📎 {e.get('content_type', 'текст')}\n💬 {snip}")
    await callback.message.edit_text("\n\n".join(lines),
                                      reply_markup=bot1_nav_keyboard(page, total, "b1_anon_page_", "b1_anon_clear"))


@bot1_dp.callback_query(F.data.startswith("b1_admin"))
async def bot1_admin_callback(callback):
    global message_logs
    uid = callback.from_user.id
    data = callback.data
    if uid != ADMIN_ID:
        await callback.answer("⛔️ Доступ запрещён.", show_alert=True)
        return

    if data == "b1_admin_broadcast":
        bot1_admin_state[uid] = "awaiting_broadcast"
        await callback.message.edit_text(
            "📣 Рассылка\n\nВведи текст — получат все пользователи.\n\n/cancel — отмена",
            reply_markup=bot1_admin_back_keyboard())

    elif data == "b1_admin_add_ids":
        bot1_admin_state[uid] = "awaiting_ids"
        await callback.message.edit_text(
            "➕ Добавление ID\n\nОтправь числовые ID через пробел или запятую.\n\n/cancel — отмена",
            reply_markup=bot1_admin_back_keyboard())

    elif data == "b1_admin_list_ids":
        ids = ", ".join(str(i) for i in manual_ids) if manual_ids else "пусто"
        await callback.message.edit_text(
            f"📋 Список ID:\n\n{ids}", reply_markup=bot1_admin_back_keyboard())

    elif data == "b1_admin_top":
        entries = get_top_entries()
        M = ["🥇", "🥈", "🥉"]
        lines = ["🏆 Топ анонимщиков\n"]
        if not entries:
            lines.append("Пока пусто 😶")
        else:
            for i, e in enumerate(entries[:10]):
                m = M[i] if i < 3 else f"{i + 1}."
                lines.append(f"{m} {e['nick']} — {e['count']} анонимок (ID: {e['user_id']})")
        await callback.message.edit_text("\n".join(lines), reply_markup=bot1_admin_back_keyboard())

    elif data == "b1_admin_export":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["user_id", "username", "first_name", "last_name",
                                            "content_type", "text", "timestamp", "blocked"])
        w.writeheader()
        for row in message_logs:
            w.writerow({k: row.get(k, "") for k in w.fieldnames})
        f = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
        from aiogram.types import BufferedInputFile
        doc = BufferedInputFile(f.getvalue(), filename="logs.csv")
        await callback.message.answer_document(document=doc, caption="📤 Экспорт логов")
        await callback.answer()

    elif data == "b1_admin_clean_old":
        cutoff = datetime.now().timestamp() - 7 * 86400
        before = len(message_logs)
        message_logs = [e for e in message_logs
                        if datetime.fromisoformat(e["timestamp"]).timestamp() > cutoff]
        _save_json(LOG_FILE, message_logs)
        await callback.message.edit_text(
            f"🧹 Удалено {before - len(message_logs)} записей старше 7 дней.",
            reply_markup=bot1_admin_keyboard())

    elif data == "b1_admin_tab_messages":
        await bot1_show_message_logs_page(callback, 0)

    elif data == "b1_admin_tab_starts":
        await bot1_show_start_logs_page(callback, 0)

    elif data == "b1_admin_anon_msgs":
        await bot1_show_anon_messages_page(callback, 0)

    elif data == "b1_admin_back":
        bot1_admin_state.pop(uid, None)
        await callback.message.edit_text(admin_text(), reply_markup=bot1_admin_keyboard())

    await callback.answer()


@bot1_dp.callback_query(F.data.startswith(("b1_msg_", "b1_start_", "b1_anon_")))
async def bot1_logs_pagination_callback(callback):
    global message_logs, start_logs, anon_messages_log
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔️ Доступ запрещён.", show_alert=True)
        return
    data = callback.data

    if data.startswith("b1_msg_page_"):
        await bot1_show_message_logs_page(callback, int(data.rsplit("_", 1)[-1]))
    elif data == "b1_msg_clear":
        message_logs.clear()
        _save_json(LOG_FILE, message_logs)
        await callback.message.edit_text("🧹 Логи анонимок очищены.", reply_markup=bot1_admin_keyboard())

    elif data.startswith("b1_start_page_"):
        await bot1_show_start_logs_page(callback, int(data.rsplit("_", 1)[-1]))
    elif data == "b1_start_clear":
        start_logs.clear()
        _save_json(START_LOG_FILE, start_logs)
        await callback.message.edit_text("🧹 Логи стартов очищены.", reply_markup=bot1_admin_keyboard())

    elif data.startswith("b1_anon_page_"):
        await bot1_show_anon_messages_page(callback, int(data.rsplit("_", 1)[-1]))
    elif data == "b1_anon_clear":
        anon_messages_log.clear()
        _save_json(ANON_MSGS_LOG_FILE, anon_messages_log)
        await callback.message.edit_text("🧹 Логи анонимных сообщений очищены.", reply_markup=bot1_admin_keyboard())

    await callback.answer()


@bot1_dp.message(F.text, lambda m: m.from_user.id == ADMIN_ID and bot1_admin_state.get(m.from_user.id) == "awaiting_broadcast")
async def bot1_admin_broadcast_input(message: Message):
    bot1_admin_state.pop(message.from_user.id, None)
    txt = message.text
    if not txt:
        await message.reply("❌ Сообщение не может быть пустым.")
        return
    all_ids = list(set(e["user_id"] for e in start_logs) | set(manual_ids))
    if not all_ids:
        await message.reply("📭 Нет получателей.")
        return
    await message.reply(f"📣 Рассылка для {len(all_ids)} чел...")
    sent = failed = 0
    for i in all_ids:
        try:
            await bot1.send_message(i, txt)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await message.reply(f"✅ Готово!\n📤 Отправлено: {sent}\n❌ Ошибок: {failed}")


@bot1_dp.message(F.text, lambda m: m.from_user.id == ADMIN_ID and bot1_admin_state.get(m.from_user.id) == "awaiting_ids")
async def bot1_admin_add_ids_input(message: Message):
    bot1_admin_state.pop(message.from_user.id, None)
    raw = re.split(r"[,\s]+", message.text.strip())
    added = []
    for r in raw:
        if not r:
            continue
        try:
            n = int(r)
        except ValueError:
            continue
        if n not in manual_ids:
            manual_ids.append(n)
            added.append(str(n))
    if added:
        save_manual_ids(manual_ids)
        await message.reply(f"✅ Добавлены: {', '.join(added)}")
    else:
        await message.reply("⚠️ Все эти ID уже есть.")


@bot1_dp.message(
    lambda m: (
        m.chat.id == BOT1_DISCUSSION_CHAT_ID
        and m.forward_from_chat is not None
        and m.forward_from_chat.id == BOT1_CHANNEL_ID
    )
)
async def bot1_on_discussion_forward(message: Message):
    channel_post_id = message.forward_from_message_id
    bot1_post_to_discussion_id[channel_post_id] = message.message_id
    logger.info(f"[Bot1] Форвард поста {channel_post_id} -> discussion {message.message_id}")


@bot1_dp.channel_post()
async def bot1_on_channel_post(message: Message):
    if message.chat.id != BOT1_CHANNEL_ID:
        return

    post_id = message.message_id
    logger.info(f"[Bot1] Новый пост: {post_id}")

    # Ждём форвард в дискуссию (до 15 секунд, проверяем каждые 2 сек)
    # Это лучше чем просто sleep(5), т.к. не блокирует надолго при быстром форварде
    for _ in range(8):
        if post_id in bot1_post_to_discussion_id:
            break
        await asyncio.sleep(2)

    username = await bot1_get_username()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="• • •", url=f"https://t.me/{username}?start=post_{post_id}")
    ]])

    discussion_msg_id = bot1_post_to_discussion_id.get(post_id)
    logger.info(f"[Bot1] Отправляю промпт, discussion_msg_id={discussion_msg_id}")

    try:
        sent = await bot1.send_message(
            chat_id=BOT1_DISCUSSION_CHAT_ID,
            text="🤖 Чтобы оставить анонимный комментарий к этому посту, нажмите на кнопку:",
            reply_markup=kb,
            reply_to_message_id=discussion_msg_id
        )
        logger.info(f"[Bot1] Промпт успешно отправлен: {sent.message_id}")
    except Exception as e:
        logger.error(f"[Bot1] Ошибка с reply: {e}, пробуем без reply")
        try:
            sent = await bot1.send_message(
                chat_id=BOT1_DISCUSSION_CHAT_ID,
                text="🤖 Чтобы оставить анонимный комментарий к этому посту, нажмите на кнопку:",
                reply_markup=kb
            )
            logger.info(f"[Bot1] Промпт отправлен без reply: {sent.message_id}")
        except Exception as e2:
            logger.error(f"[Bot1] Совсем не удалось отправить: {e2}")


@bot1_dp.message(
    lambda m: (
        m.chat.id == BOT1_DISCUSSION_CHAT_ID
        and m.reply_to_message is not None
        and m.from_user is not None
        and not m.from_user.is_bot
    )
)
async def bot1_on_discussion_reply(message: Message):
    """Кто-то ответил на сообщение в чате — проверяем, не анонимка ли это."""
    replied_to_id = message.reply_to_message.message_id

    if replied_to_id not in bot1_anon_msg_to_user:
        return

    original_author_id = bot1_anon_msg_to_user[replied_to_id]

    # Не уведомляем если человек ответил сам себе
    if message.from_user.id == original_author_id:
        return

    post_id = bot1_anon_msg_to_post.get(replied_to_id)
    replier_name = message.from_user.full_name

    bot1_reply_msg_to_post[message.message_id] = post_id

    username = await bot1_get_username()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Ответить • • •",
            url=f"https://t.me/{username}?start=reply_{message.message_id}"
        )
    ]])

    try:
        await bot1.send_message(
            chat_id=original_author_id,
            text=f"<b>{replier_name}</b> ответил(а) на ваш анонимный комментарий",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"[Bot1] Не удалось отправить уведомление пользователю {original_author_id}: {e}")


@bot1_dp.message(Command("start"))
async def bot1_cmd_start(message: Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        param = args[1]

        if param.startswith("reply_"):
            try:
                reply_to_msg_id = int(param.replace("reply_", ""))
                post_id = bot1_reply_msg_to_post.get(reply_to_msg_id)
                if not post_id:
                    await message.answer("❌ Сообщение устарело или не найдено.")
                    return
            except ValueError:
                await message.answer("❌ Неверная ссылка.")
                return

        elif param.startswith("post_"):
            try:
                post_id = int(param.replace("post_", ""))
                reply_to_msg_id = bot1_post_to_discussion_id.get(post_id)
            except ValueError:
                await message.answer("❌ Неверная ссылка.")
                return
        else:
            await message.answer("уебок 👋 Привет! Нажми кнопочку «• • •» своими сардельками под постом в канале, чтобы оставить анонимный комментарий.")
            return

        bot1_pending[message.from_user.id] = (post_id, reply_to_msg_id)
        await state.set_state(AnonState.waiting_text)
        await message.answer(
            "👋 Отправьте сообщение, и я опубликую его анонимно в комментариях.\n"
            "Можно отправлять текст, фото, видео, GIF, стикеры, аудио и файлы.\n\n"
            "/cancel — отменить отправку комментария"
        )
    else:
        await message.answer("уебок 👋 Привет! Нажми кнопочку «• • •» своими сардельками под постом в канале, чтобы оставить анонимный комментарий.")


@bot1_dp.message(Command("cancel"))
async def bot1_cmd_cancel(message: Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID and bot1_admin_state.pop(message.from_user.id, None):
        await message.answer("✅ Отменено.")
        return
    await state.clear()
    bot1_pending.pop(message.from_user.id, None)
    await message.answer("❌ Отправка отменена.")


# ── Уведомление админа о том, кто написал анонимный комментарий (Bot1) ──
async def bot1_notify_admin(message: Message, post_id: int, content_type: str, caption_text: str, pseudo: str):
    u = message.from_user
    ustr = f"@{u.username}" if u.username else "—"
    name = get_display_name(u.id, u.username, u.first_name, u.last_name)
    lines = [
        "🕵️ <b>Новый анонимный комментарий (Bot1)</b>",
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
        f"👤 ID: <code>{u.id}</code>",
        f"🔗 Username: {ustr}",
        f"📛 Имя: {name}",
        f"📝 Псевдоним: {pseudo}",
        f"📌 Пост: {post_id}",
        f"📎 Тип: {content_type}",
    ]
    if caption_text:
        safe_text = caption_text[:300].replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"💬 Текст:\n{safe_text}")
    try:
        await bot1.send_message(ADMIN_ID, "\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.warning(f"[Bot1] Не удалось уведомить админа: {e}")


# ── Общая функция отправки анонимного комментария (любой тип) ──
async def send_anon_comment(
    message: Message,
    state: FSMContext,
    content_type: str,
    file_id: str = None,
    caption_text: str = ""
):
    user_id = message.from_user.id
    data = bot1_pending.get(user_id)

    if not data:
        await state.clear()
        await message.answer("❌ Ошибка. Нажми кнопку под постом снова.")
        return

    post_id, reply_to_msg_id = data
    pseudo = bot1_get_pseudo(user_id, post_id)
    pseudo_block = f"<blockquote>{pseudo}</blockquote>"
    full_caption = f"{pseudo_block}\n{caption_text}" if caption_text else pseudo_block

    try:
        sent = None
        sent_media = None

        if content_type == "text":
            sent = await bot1.send_message(
                chat_id=BOT1_DISCUSSION_CHAT_ID,
                text=full_caption,
                parse_mode="HTML",
                reply_to_message_id=reply_to_msg_id
            )

        elif content_type in ("photo", "video", "animation", "document"):
            method_map = {
                "photo":     bot1.send_photo,
                "video":     bot1.send_video,
                "animation": bot1.send_animation,
                "document":  bot1.send_document,
            }
            kwargs = {
                "chat_id": BOT1_DISCUSSION_CHAT_ID,
                content_type: file_id,
                "caption": full_caption,
                "parse_mode": "HTML",
                "reply_to_message_id": reply_to_msg_id,
            }
            sent = await method_map[content_type](**kwargs)

        elif content_type == "sticker":
            sent_media = await bot1.send_sticker(
                chat_id=BOT1_DISCUSSION_CHAT_ID,
                sticker=file_id,
                reply_to_message_id=reply_to_msg_id
            )
            sent = await bot1.send_message(
                chat_id=BOT1_DISCUSSION_CHAT_ID,
                text=full_caption,
                parse_mode="HTML",
                reply_to_message_id=sent_media.message_id
            )

        elif content_type in ("audio", "voice"):
            send_func = bot1.send_audio if content_type == "audio" else bot1.send_voice
            sent_media = await send_func(
                chat_id=BOT1_DISCUSSION_CHAT_ID,
                **{content_type: file_id},
                reply_to_message_id=reply_to_msg_id
            )
            sent = await bot1.send_message(
                chat_id=BOT1_DISCUSSION_CHAT_ID,
                text=full_caption,
                parse_mode="HTML",
                reply_to_message_id=sent_media.message_id
            )

        else:
            await message.answer("⚠️ Неподдерживаемый тип сообщения.")
            return

        if sent_media:
            bot1_anon_msg_to_user[sent_media.message_id] = user_id
            bot1_anon_msg_to_post[sent_media.message_id] = post_id
        if sent:
            bot1_anon_msg_to_user[sent.message_id] = user_id
            bot1_anon_msg_to_post[sent.message_id] = post_id

        await state.clear()
        bot1_pending.pop(user_id, None)
        await bot1_notify_admin(message, post_id, content_type, caption_text, pseudo)
        await message.answer(
            f"✅ Комментарий успешно опубликован!\n\n"
            f"<b>Ваш псевдоним:</b> {pseudo}\n"
            f"<i>Псевдоним генерируется каждый раз, когда вы комментируете новый пост.</i>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"[Bot1] Ошибка публикации: {e}")
        await state.clear()
        bot1_pending.pop(user_id, None)
        await message.answer("❌ Произошла ошибка. Попробуй позже.")


# ── Хендлеры для разных типов контента ──

@bot1_dp.message(AnonState.waiting_text, F.text)
async def handle_text(message: Message, state: FSMContext):
    await send_anon_comment(message, state, "text", caption_text=message.text)

@bot1_dp.message(AnonState.waiting_text, F.photo)
async def handle_photo(message: Message, state: FSMContext):
    await send_anon_comment(message, state, "photo",
                            file_id=message.photo[-1].file_id,
                            caption_text=message.caption or "")

@bot1_dp.message(AnonState.waiting_text, F.video)
async def handle_video(message: Message, state: FSMContext):
    await send_anon_comment(message, state, "video",
                            file_id=message.video.file_id,
                            caption_text=message.caption or "")

@bot1_dp.message(AnonState.waiting_text, F.animation)
async def handle_animation(message: Message, state: FSMContext):
    await send_anon_comment(message, state, "animation",
                            file_id=message.animation.file_id,
                            caption_text=message.caption or "")

@bot1_dp.message(AnonState.waiting_text, F.audio)
async def handle_audio(message: Message, state: FSMContext):
    await send_anon_comment(message, state, "audio",
                            file_id=message.audio.file_id,
                            caption_text=message.caption or "")

@bot1_dp.message(AnonState.waiting_text, F.voice)
async def handle_voice(message: Message, state: FSMContext):
    await send_anon_comment(message, state, "voice",
                            file_id=message.voice.file_id,
                            caption_text=message.caption or "")

@bot1_dp.message(AnonState.waiting_text, F.document)
async def handle_document(message: Message, state: FSMContext):
    await send_anon_comment(message, state, "document",
                            file_id=message.document.file_id,
                            caption_text=message.caption or "")

@bot1_dp.message(AnonState.waiting_text, F.sticker)
async def handle_sticker(message: Message, state: FSMContext):
    await send_anon_comment(message, state, "sticker",
                            file_id=message.sticker.file_id)


# ═══════════════════════════════════════════════════════════════════
# БОТ 2: АНОНИМНЫЕ СООБЩЕНИЯ В КАНАЛ + АДМИНКА
# ═══════════════════════════════════════════════════════════════════

# ── ВЕБ-СЕРВЕР (keep-alive) ──────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a):
        pass

threading.Thread(
    target=lambda: HTTPServer(("0.0.0.0", 10000), Handler).serve_forever(),
    daemon=True
).start()

def _self_ping():
    while True:
        try:
            urllib.request.urlopen(RENDER_URL, timeout=10)
        except Exception:
            pass
        time.sleep(600)

threading.Thread(target=_self_ping, daemon=True).start()

# ── ПРОМПТЫ ───────────────────────────────
SYSTEM_PROMPT = """
Ты — живой, интересный собеседник с лёгким характером. Твоя цель — приятное и естественное общение.
Ты не используешь шаблонные фразы и не повторяешь заученные ответы.
Каждый твой ответ уникален и зависит от контекста диалога.
Будь дружелюбным, иногда с ноткой иронии, но без мата и грубости.
""".strip()

CONTENT_CHECK_PROMPT = """
Ты — модератор. Оцени текст сообщения:
1) Осмысленность: сообщение должно выражать связную мысль, а не быть случайным набором символов.
2) Не спам: нет ссылок, рекламы, призывов перейти куда-либо.
3) Не содержит явной порнографии или очень грубого контента (мат в умеренном количестве допустим).
Модерация НЕ строгая — блокируй только явно неприемлемое.
Отвечай ТОЛЬКО JSON: {"acceptable": true/false, "reason": "причина если false"}.
""".strip()

# ── ФАЙЛЫ И ДАННЫЕ ────────────────────────
LOG_FILE           = "anon_logs.json"
START_LOG_FILE     = "start_logs.json"
MANUAL_IDS_FILE    = "manual_ids.json"
TOP_FILE           = "top_data.json"
ANON_MSGS_LOG_FILE = "anon_messages_log.json"

COOLDOWN_SECONDS = 180
ANONYMOUS_MODE, AI_CHAT_MODE = 1, 2
TYPING_DELAY = 0.015
UPDATE_INTERVAL = 5
GROQ_SEMAPHORE = asyncio.Semaphore(5)

user_last_time: dict[int, datetime] = {}
user_ai_context: dict[int, list[dict]] = {}
message_logs: list[dict] = []
start_logs: list[dict] = []
manual_ids: list[int] = []
top_data: dict = {}
se_checks_month: dict = {}
anon_messages_log: list[dict] = []

# ── SIGHTENGINE ───────────────────────────
def se_increment(n: int = 1):
    key = datetime.now().strftime("%Y-%m")
    se_checks_month[key] = se_checks_month.get(key, 0) + n
    _save_json("se_checks.json", se_checks_month)

def se_used() -> int:
    key = datetime.now().strftime("%Y-%m")
    return se_checks_month.get(key, 0)

def se_left() -> int:
    return max(0, SE_MONTH_LIMIT - se_used())

# ── УТИЛИТЫ ───────────────────────────────
_MDV2 = re.compile(r'([_*\[\]()~`>#+=|{}.!\\-])')

def escape_mdv2(t: str) -> str:
    return _MDV2.sub(r"\\\1", t)

def _load_json(path) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _load_json_dict(path) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # атомарная замена — защита от порчи файла
    except Exception as e:
        logger.error("Ошибка записи %s: %s", path, e)

# ── ЗАГРУЗКА ДАННЫХ ───────────────────────
def current_week_key() -> str:
    today = datetime.now()
    return (today - timedelta(days=today.weekday())).strftime("%Y-W%V")

def load_manual_ids() -> list[int]:
    if not os.path.exists(MANUAL_IDS_FILE):
        default_ids = [
            1065994703, 1317499381, 1325803980, 1348135622, 1445013145,
            1596705847, 1598141304, 1658111818, 1793536849, 1812163694,
            5012402904, 5058039623, 5093484454, 5222651755, 5244622001,
            5398185223, 5591478632, 5846879986, 5886556924, 5900068784,
            5960908435, 6171031779, 6322668072, 6398253412, 6575282623,
            6647049769, 6677665897, 6716660326, 6762818617, 6811352382,
            6815122910, 6860269336, 6927328893, 7089300064, 7112529527,
            7194633128, 7234303233, 7431729389, 7447312123, 7476200435,
            7691946899, 7810494142, 7824611507, 7854035216, 7927447701,
            7948610168, 7971084218, 8013816191, 8118408450, 8150421121,
            8160648800, 8223293549, 8306392029, 8314930012, 8323205303,
            8340087744, 8366862190, 8475400754, 8484636623, 8534170879,
            8555817128, 8627543263, 8665408669, 8711321595,
        ]
        _save_json(MANUAL_IDS_FILE, default_ids)
        return default_ids
    try:
        return [int(x) for x in _load_json(MANUAL_IDS_FILE)]
    except Exception:
        return []

def save_manual_ids(ids):
    _save_json(MANUAL_IDS_FILE, ids)

def load_all_logs():
    global message_logs, start_logs, manual_ids, top_data, se_checks_month, anon_messages_log
    message_logs     = _load_json(LOG_FILE)
    start_logs       = _load_json(START_LOG_FILE)
    manual_ids       = load_manual_ids()
    raw              = _load_json(TOP_FILE)
    top_data         = raw if isinstance(raw, dict) else {}
    se_checks_month  = _load_json_dict("se_checks.json")
    anon_messages_log = _load_json(ANON_MSGS_LOG_FILE)

def add_message_log(entry):
    message_logs.append(entry)
    _save_json(LOG_FILE, message_logs)

def add_anon_message_log(entry):
    anon_messages_log.append(entry)
    _save_json(ANON_MSGS_LOG_FILE, anon_messages_log)

def add_start_log(uid, uname, fn, ln):
    # Не добавляем дубли в start_logs, но manual_ids проверяем всегда,
    # чтобы пользователь не потерялся как получатель рассылки.
    is_new = True
    for e in start_logs:
        if e.get("user_id") == uid:
            is_new = False
            break
    if is_new:
        start_logs.append({
            "user_id": uid, "username": uname, "first_name": fn,
            "last_name": ln, "timestamp": datetime.now().isoformat()
        })
        _save_json(START_LOG_FILE, start_logs)

    if uid not in manual_ids:
        manual_ids.append(uid)
        save_manual_ids(manual_ids)

# ── ТОП ───────────────────────────────────
def get_top_entries() -> list[dict]:
    week = current_week_key()
    res = [{"user_id": int(k), "nick": v.get("nick", "Аноним"), "count": v.get("count", 0)}
           for k, v in top_data.items() if v.get("week") == week]
    return sorted(res, key=lambda x: x["count"], reverse=True)

def increment_top(uid: int):
    k = str(uid)
    week = current_week_key()
    if k not in top_data:
        return
    e = top_data[k]
    if e.get("week") != week:
        e["count"] = 0
        e["week"] = week
    e["count"] = e.get("count", 0) + 1
    _save_json(TOP_FILE, top_data)

def join_top(uid: int, nick: str):
    k = str(uid)
    week = current_week_key()
    if k in top_data:
        if top_data[k].get("week") == week:
            top_data[k]["nick"] = nick
        else:
            count = _count_user_anons_this_week(uid)
            top_data[k] = {"nick": nick, "count": count, "week": week}
    else:
        count = _count_user_anons_this_week(uid)
        top_data[k] = {"nick": nick, "count": count, "week": week}
    _save_json(TOP_FILE, top_data)

def _count_user_anons_this_week(uid: int) -> int:
    week_start = datetime.now() - timedelta(days=datetime.now().weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    count = 0
    for entry in message_logs:
        if entry.get("user_id") != uid or entry.get("blocked"):
            continue
        try:
            if datetime.fromisoformat(entry["timestamp"]) >= week_start:
                count += 1
        except Exception:
            pass
    return count

def leave_top(uid: int):
    k = str(uid)
    if k in top_data:
        del top_data[k]
        _save_json(TOP_FILE, top_data)

def is_in_top(uid: int) -> bool:
    k = str(uid)
    return k in top_data and top_data[k].get("week") == current_week_key()

def build_top_text() -> str:
    entries = get_top_entries()
    today = datetime.now()
    days_until_monday = (7 - today.weekday()) % 7 or 7
    reset = (today + timedelta(days=days_until_monday)).strftime("%d.%m")
    week = current_week_key()
    MEDALS = ["🥇", "🥈", "🥉"]
    PLACES = ["4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = [
        "🏆 ТОП АНОНИМЩИКОВ НЕДЕЛИ",
        "▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔",
        f"📅 Неделя: {week}",
        f"🔄 Сброс рейтинга: {reset}",
        "",
    ]
    if not entries:
        lines += ["😶 Пока никого нет в рейтинге", "", "💡 Нажми «Вступить в топ» чтобы", "   участвовать в соревновании!"]
    else:
        max_count = max(e["count"] for e in entries) or 1
        for i, e in enumerate(entries[:10]):
            medal = MEDALS[i] if i < 3 else PLACES[i - 3] if i < 10 else f"{i + 1}."
            filled = round(e["count"] / max_count * 8)
            bar = "█" * filled + "░" * (8 - filled)
            nick = e["nick"][:20]
            lines.append(f"{medal}  {nick}")
            lines.append(f"    ▏{bar}▏  {e['count']} анонимок")
            lines.append("")
    lines.append("▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔")
    return "\n".join(lines)

# ── GROQ API ──────────────────────────────
async def _groq_request(payload, retries=2):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(retries + 1):
            try:
                r = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers, json=payload
                )
                if r.status_code == 429 and attempt < retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return r.json() if r.status_code == 200 else None
            except httpx.TimeoutException:
                if attempt < retries:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error("Groq: %s", e)
                if attempt < retries:
                    await asyncio.sleep(1)
    return None

async def call_groq_simple(prompt, system, as_json=False):
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "temperature": 0.1 if as_json else 0.9,
        "max_tokens": 256,
    }
    if as_json:
        payload["response_format"] = {"type": "json_object"}
    try:
        d = await _groq_request(payload)
        return d["choices"][0]["message"]["content"] if d else None
    except Exception as e:
        logger.error("Groq simple: %s", e)
        return None

async def call_groq_with_context(uid: int, user_msg: str) -> str:
    async with GROQ_SEMAPHORE:
        history = user_ai_context.setdefault(uid, [])
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}, *history,
                {"role": "user", "content": user_msg}]
        try:
            d = await _groq_request({
                "model": "llama-3.1-8b-instant",
                "messages": msgs,
                "temperature": 0.9,
                "max_tokens": 1024
            }, retries=2)
            if not d:
                return "⚠️ ИИ временно недоступен, попробуй позже."
            reply = d["choices"][0]["message"]["content"]
            history += [{"role": "user", "content": user_msg}, {"role": "assistant", "content": reply}]
            # Держим не больше 6 сообщений в истории (3 обмена)
            user_ai_context[uid] = history[-6:]
            return reply
        except Exception as e:
            logger.error("Groq ctx: %s", e)
            return "⚠️ Ошибка сети."

# ── SIGHTENGINE МОДЕРАЦИЯ ─────────────────
async def _get_tg_file_url(bot, file_id: str) -> str | None:
    try:
        tg_file = await bot.get_file(file_id)
        if tg_file.file_path.startswith("http"):
            return tg_file.file_path
        return f"https://api.telegram.org/file/bot{BOT2_TOKEN}/{tg_file.file_path}"
    except Exception as e:
        logger.error("Ошибка получения URL файла: %s", e)
        return None

async def _sightengine_check_bytes(image_bytes: bytes) -> tuple[bool, str]:
    if not SE_USER or not SE_SECRET:
        return True, ""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.sightengine.com/1.0/check.json",
                data={
                    "models": "nudity-2.1,offensive",
                    "api_user": SE_USER,
                    "api_secret": SE_SECRET,
                },
                files={"media": ("image.jpg", image_bytes, "image/jpeg")}
            )
        if r.status_code != 200:
            logger.error("Sightengine error: %s", r.text)
            return True, ""
        data = r.json()
        nudity = data.get("nudity", {})
        sexual_score = max(
            nudity.get("sexual_activity", 0),
            nudity.get("sexual_display", 0),
            nudity.get("erotica", 0),
            nudity.get("very_suggestive", 0),
            nudity.get("suggestive", 0),
            nudity.get("mildly_suggestive", 0),
        )
        offensive = data.get("offensive", {}).get("prob", 0)
        logger.info("Sightengine: sexual=%.2f offensive=%.2f", sexual_score, offensive)
        se_increment(1)
        if sexual_score > 0.2:
            return False, f"сексуальный контент ({int(sexual_score * 100)}%)"
        if offensive > 0.7:
            return False, f"оскорбительный контент ({int(offensive * 100)}%)"
        return True, ""
    except Exception as e:
        logger.error("Sightengine bytes check error: %s", e)
        return True, ""

async def _convert_to_jpg_bytes(input_bytes: bytes, suffix: str) -> bytes | None:
    input_path = None
    output_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(input_bytes)
            input_path = f.name
        output_path = input_path + "_out.jpg"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", input_path,
            "-vframes", "1", "-q:v", "2", output_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.communicate(), timeout=20)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            with open(output_path, "rb") as f:
                return f.read()
        return None
    except Exception as e:
        logger.error("ffmpeg конвертация: %s", e)
        return None
    finally:
        for p in [input_path, output_path]:
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass

async def is_sticker_acceptable(bot, sticker) -> tuple[bool, str]:
    if not SE_USER or not SE_SECRET:
        return True, ""
    try:
        tg_file = await bot.get_file(sticker.file_id)
        file_bytes = bytes(await tg_file.download_as_bytearray())
        if sticker.is_animated:
            jpg = await _convert_to_jpg_bytes(file_bytes, ".tgs")
        elif sticker.is_video:
            jpg = await _convert_to_jpg_bytes(file_bytes, ".webm")
        else:
            jpg = await _convert_to_jpg_bytes(file_bytes, ".webp")
        if not jpg:
            logger.warning("Не удалось конвертировать стикер — пропускаем")
            return True, ""
        return await _sightengine_check_bytes(jpg)
    except Exception as e:
        logger.error("Ошибка проверки стикера: %s", e)
        return True, ""

async def is_image_acceptable(bot, file_id: str) -> tuple[bool, str]:
    if not SE_USER or not SE_SECRET:
        return True, ""
    try:
        url = await _get_tg_file_url(bot, file_id)
        if not url:
            return True, ""
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                "https://api.sightengine.com/1.0/check.json",
                params={
                    "url": url,
                    "models": "nudity-2.1,offensive",
                    "api_user": SE_USER,
                    "api_secret": SE_SECRET,
                }
            )
        if r.status_code != 200:
            logger.error("Sightengine error: %s", r.text)
            return True, ""
        data = r.json()
        nudity = data.get("nudity", {})
        sexual_score = max(
            nudity.get("sexual_activity", 0),
            nudity.get("sexual_display", 0),
            nudity.get("erotica", 0),
            nudity.get("very_suggestive", 0),
            nudity.get("suggestive", 0),
            nudity.get("mildly_suggestive", 0),
        )
        offensive = data.get("offensive", {}).get("prob", 0)
        logger.info("Sightengine фото: sexual=%.2f offensive=%.2f", sexual_score, offensive)
        se_increment(1)
        if sexual_score > 0.2:
            return False, f"сексуальный контент (уверенность {int(sexual_score * 100)}%)"
        if offensive > 0.7:
            return False, f"оскорбительный контент (уверенность {int(offensive * 100)}%)"
        return True, ""
    except Exception as e:
        logger.error("Ошибка проверки изображения Sightengine: %s", e)
        return True, ""

async def is_video_acceptable(bot, file_id: str) -> tuple[bool, str]:
    if not SE_USER or not SE_SECRET:
        return True, ""
    video_path = None
    try:
        tg_file = await bot.get_file(file_id)
        video_bytes = await tg_file.download_as_bytearray()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as vf:
            vf.write(bytes(video_bytes))
            video_path = vf.name

        for sec in [1, 3, 6]:
            frame_path = f"{video_path}_frame_{sec}.jpg"
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", video_path,
                "-ss", f"00:00:0{sec}",
                "-vframes", "1",
                "-q:v", "2",
                frame_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=15)
            except asyncio.TimeoutError:
                continue

            if not os.path.exists(frame_path) or os.path.getsize(frame_path) == 0:
                continue

            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    with open(frame_path, "rb") as f:
                        r = await client.post(
                            "https://api.sightengine.com/1.0/check.json",
                            data={
                                "models": "nudity-2.1,offensive",
                                "api_user": SE_USER,
                                "api_secret": SE_SECRET,
                            },
                            files={"media": f}
                        )
                if r.status_code == 200:
                    data = r.json()
                    nudity = data.get("nudity", {})
                    sexual_score = max(
                        nudity.get("sexual_activity", 0),
                        nudity.get("sexual_display", 0),
                        nudity.get("erotica", 0),
                        nudity.get("very_suggestive", 0),
                        nudity.get("suggestive", 0),
                        nudity.get("mildly_suggestive", 0),
                    )
                    offensive = data.get("offensive", {}).get("prob", 0)
                    logger.info("Видео кадр %d: sexual=%.2f offensive=%.2f", sec, sexual_score, offensive)
                    se_increment(1)
                    if sexual_score > 0.2:
                        return False, f"сексуальный контент на {sec}-й секунде ({int(sexual_score * 100)}%)"
                    if offensive > 0.7:
                        return False, f"оскорбительный контент на {sec}-й секунде ({int(offensive * 100)}%)"
            except Exception as e:
                logger.error("Ошибка Sightengine кадр %d: %s", sec, e)
            finally:
                if os.path.exists(frame_path):
                    os.unlink(frame_path)

        return True, ""
    except Exception as e:
        logger.error("Ошибка проверки видео: %s", e)
        return True, ""
    finally:
        if video_path and os.path.exists(video_path):
            os.unlink(video_path)

# ── МОДЕРАЦИЯ ТЕКСТА ──────────────────────
async def is_content_acceptable(text: str) -> tuple[bool, str]:
    if not text or len(text.strip()) < 2:
        return False, "слишком короткое"
    if not GROQ_API_KEY:
        return True, ""
    res = await call_groq_simple(text, CONTENT_CHECK_PROMPT, as_json=True)
    if not res:
        return True, ""
    try:
        cleaned = res.strip().removeprefix("```json").removesuffix("```").strip()
        p = json.loads(cleaned)
        ok = bool(p.get("acceptable", True))
        return ok, ("" if ok else p.get("reason", ""))
    except Exception:
        return True, ""

# ── АНИМАЦИЯ ПЕЧАТАНИЯ ────────────────────
async def typewriter_reply(update: Update, full_text: str):
    if not full_text:
        return
    msg = await update.message.reply_text("▌")
    displayed = ""
    for i, ch in enumerate(full_text, 1):
        displayed += ch
        if i % UPDATE_INTERVAL == 0 or i == len(full_text):
            cursor = "" if i == len(full_text) else "▌"
            try:
                await msg.edit_text(displayed + cursor)
            except Exception:
                pass
        await asyncio.sleep(TYPING_DELAY)

# ── ОТПРАВКА В КАНАЛ ──────────────────────
async def send_to_channel(context, update, text) -> int | None:
    """
    Одно сообщение в канал:
      📩 Анонимное сообщение
      ✉️ Отправить анонимку (ссылка на бота, без превью)

      Само анон сообщение

      вейп барахолка и по совместительству чат шiкунчиков (ссылка на чат, без превью)
    """
    msg = update.message
    bot = context.bot

    bot_link = f"https://t.me/{BOT2_USERNAME}"
    safe = escape_mdv2(text) if text else ""
    # chat_link НЕ экранируем — ссылка должна остаться рабочей внутри []()
    chat_link = BOT2_CHAT_INVITE

    # > в начале строки = цитированный блок (зелёная полоска), ссылка внутри кликабельна
    header = (
        f"*📩 Анонимное сообщение*\n"
        f">[✉️ Отправить анонимку]({bot_link})"
    )
    footer = f">[вейп барахолка и по совместительству чат шiкунчиков]({chat_link})"

    full = f"{header}\n\n{safe}\n\n{footer}" if safe else f"{header}\n\n{footer}"

    try:
        s = None
        if msg.photo:
            s = await bot.send_photo(BOT2_CHANNEL_ID, msg.photo[-1].file_id,
                                     caption=full, parse_mode="MarkdownV2")
        elif msg.video:
            s = await bot.send_video(BOT2_CHANNEL_ID, msg.video.file_id,
                                     caption=full, parse_mode="MarkdownV2")
        elif msg.animation:
            s = await bot.send_animation(BOT2_CHANNEL_ID, msg.animation.file_id,
                                         caption=full, parse_mode="MarkdownV2")
        elif msg.audio:
            s = await bot.send_audio(BOT2_CHANNEL_ID, msg.audio.file_id,
                                     caption=full, parse_mode="MarkdownV2")
        elif msg.voice:
            s = await bot.send_voice(BOT2_CHANNEL_ID, msg.voice.file_id,
                                     caption=full, parse_mode="MarkdownV2")
        elif msg.document:
            s = await bot.send_document(BOT2_CHANNEL_ID, msg.document.file_id,
                                        caption=full, parse_mode="MarkdownV2")
        elif msg.sticker:
            s = await bot.send_sticker(BOT2_CHANNEL_ID, msg.sticker.file_id)
            await bot.send_message(BOT2_CHANNEL_ID, f"{header}\n\n{footer}",
                                   parse_mode="MarkdownV2", disable_web_page_preview=True)
        elif msg.text:
            s = await bot.send_message(BOT2_CHANNEL_ID, full,
                                       parse_mode="MarkdownV2", disable_web_page_preview=True)
        else:
            return None
        return s.message_id if s else None
    except Exception as e:
        logger.error("send_to_channel: %s", e)
        return None

# ── УВЕДОМЛЕНИЕ АДМИНА ────────────────────
async def notify_admin_silent(context, update, ctype, ctext, blocked_reason=None):
    u = update.effective_user
    ustr = f"@{u.username}" if u.username else "—"
    name = get_display_name(u.id, u.username, u.first_name, u.last_name)
    # Используем Markdown (v1) для уведомлений — проще и надёжнее
    ico = "🚫" if blocked_reason else "🕵️"
    lines = [
        f"{ico} *{'ЗАБЛОКИРОВАНО' if blocked_reason else 'Новая анонимка'}*",
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
        f"👤 ID: `{u.id}`",
        f"🔗 Username: {ustr}",
        f"📛 Имя: {name}",
        f"📎 Тип: {ctype}",
    ]
    if blocked_reason:
        lines.append(f"❌ Причина блока: {blocked_reason}")
    if ctext:
        safe_text = ctext[:300].replace("`", "'")
        lines.append(f"💬 Текст:\n`{safe_text}`")

    caption = "\n".join(lines)
    msg = update.message
    bot = context.bot
    try:
        if blocked_reason:
            if msg.photo:
                await bot.send_photo(ADMIN_ID, msg.photo[-1].file_id,
                                     caption=caption, parse_mode="Markdown")
            elif msg.video:
                await bot.send_video(ADMIN_ID, msg.video.file_id,
                                     caption=caption, parse_mode="Markdown")
            elif msg.animation:
                await bot.send_animation(ADMIN_ID, msg.animation.file_id,
                                         caption=caption, parse_mode="Markdown")
            elif msg.sticker:
                await bot.send_message(ADMIN_ID, caption, parse_mode="Markdown")
                await bot.send_sticker(ADMIN_ID, msg.sticker.file_id)
            else:
                await bot.send_message(ADMIN_ID, caption, parse_mode="Markdown")
        else:
            await bot.send_message(ADMIN_ID, caption, parse_mode="Markdown")
    except Exception as e:
        logger.error("Админ-уведомление: %s", e)

# ── КЛАВИАТУРЫ ────────────────────────────
def main_keyboard():
    return PTBInlineKeyboardMarkup([
        [PTBInlineKeyboardButton("✉️  Отправить анонимку", callback_data="menu_anon")],
        [PTBInlineKeyboardButton("🏆  Топ анонимщиков",   callback_data="menu_top")],
        [PTBInlineKeyboardButton("🤖  Поболтать с ИИ",    callback_data="menu_ai")],
        [PTBInlineKeyboardButton("❓  Помощь",             callback_data="menu_help")],
    ])

def top_keyboard(uid):
    rows = []
    if is_in_top(uid):
        rows.append([
            PTBInlineKeyboardButton("✏️  Сменить ник",  callback_data="top_join"),
            PTBInlineKeyboardButton("🚪  Покинуть топ", callback_data="top_leave"),
        ])
    else:
        rows.append([PTBInlineKeyboardButton("🏅  Вступить в топ", callback_data="top_join")])
    rows.append([
        PTBInlineKeyboardButton("🔄  Обновить", callback_data="top_refresh"),
        PTBInlineKeyboardButton("🔙  Назад",    callback_data="menu_back"),
    ])
    return PTBInlineKeyboardMarkup(rows)

def ai_keyboard():
    return PTBInlineKeyboardMarkup([
        [PTBInlineKeyboardButton("🧠  Сбросить память", callback_data="ai_reset"),
         PTBInlineKeyboardButton("🔙  Назад",           callback_data="menu_back")],
    ])

def after_anon_keyboard():
    return PTBInlineKeyboardMarkup([
        [PTBInlineKeyboardButton("🔄  Отправить ещё", callback_data="anon_again"),
         PTBInlineKeyboardButton("🏆  Мой топ",       callback_data="menu_top")],
        [PTBInlineKeyboardButton("🏠  Главное меню",  callback_data="menu_back")],
    ])

def back_keyboard(cb="menu_back"):
    return PTBInlineKeyboardMarkup([[PTBInlineKeyboardButton("🔙  Назад", callback_data=cb)]])

# ── ГЛАВНОЕ МЕНЮ ──────────────────────────
MENU_TEXT = (
    "👋 Привет!\n\n"
    "Это бот анонимных сообщений школы 🏫\n"
    "Здесь ты можешь написать что угодно — никто не узнает что это ты 🤫\n\n"
    "Выбери действие 👇"
)

async def main_menu(update: Update, context: PTBContextTypes.DEFAULT_TYPE, edit=False):
    kb = main_keyboard()
    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(MENU_TEXT, reply_markup=kb)
        except Exception:
            await context.bot.send_message(update.effective_chat.id, MENU_TEXT, reply_markup=kb)
    else:
        await update.message.reply_text(MENU_TEXT, reply_markup=kb)

# ── КОМАНДЫ БОТА 2 ────────────────────────
async def bot2_cmd_start(update: Update, context: PTBContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    add_start_log(u.id, u.username, u.first_name, u.last_name)
    context.user_data.clear()
    await update.message.reply_text(
        "🌟 *Добро пожаловать!*\n\n"
        "Этот бот позволяет тебе:\n\n"
        "✉️  Отправлять анонимки в канал\n"
        "🏆  Участвовать в топе анонимщиков\n"
        "🤖  Общаться с ИИ\n\n"
        "Всё анонимно — никто не узнает 🔒",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )

async def bot2_cmd_cancel(update: Update, context: PTBContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    popped = (context.user_data.pop("awaiting_broadcast", None)
              or context.user_data.pop("awaiting_ids", None)
              or context.user_data.pop("awaiting_test_media", None))
    await update.message.reply_text("✅ Отменено." if popped else "Нечего отменять.")

# ── АДМИН-ПАНЕЛЬ ──────────────────────────
def admin_keyboard():
    return PTBInlineKeyboardMarkup([
        [PTBInlineKeyboardButton("📩  Анонимки",         callback_data="admin_tab_messages"),
         PTBInlineKeyboardButton("👥  Пользователи",     callback_data="admin_tab_starts")],
        [PTBInlineKeyboardButton("🏆  Топ",              callback_data="admin_view_top"),
         PTBInlineKeyboardButton("📣  Рассылка",         callback_data="admin_broadcast")],
        [PTBInlineKeyboardButton("➕  Добавить ID",      callback_data="admin_add_ids"),
         PTBInlineKeyboardButton("📋  Список ID",        callback_data="admin_list_ids")],
        [PTBInlineKeyboardButton("📤  Экспорт CSV",      callback_data="admin_export"),
         PTBInlineKeyboardButton("🧹  Удалить >7д",      callback_data="admin_clean_old")],
        [PTBInlineKeyboardButton("🧪  Тест ИИ модерации", callback_data="admin_test_ai")],
        [PTBInlineKeyboardButton("📨  Логи анонимок",    callback_data="admin_anon_msgs")],
    ])

def admin_text():
    return (
        "👑 АДМИН-ПАНЕЛЬ\n"
        "▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔\n"
        f"📩  Всего анонимок:      {len(message_logs)}\n"
        f"👥  Всего пользователей: {len(start_logs)}\n"
        f"🏆  В топе сейчас:       {len(get_top_entries())}\n"
        f"📅  Текущая неделя:      {current_week_key()}\n"
        "▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔\n"
        f"🔍  Проверок SE в месяц: {se_used()} / {SE_MONTH_LIMIT}\n"
        f"✅  Осталось проверок:   {se_left()}\n"
        "▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔"
    )

async def bot2_cmd_admin(update: Update, context: PTBContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔️ Доступ запрещён.")
        return
    await update.message.reply_text(admin_text(), reply_markup=admin_keyboard())

async def admin_callback(update: Update, context: PTBContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if update.effective_user.id != ADMIN_ID:
        await query.answer("⛔️ Доступ запрещён.", show_alert=True)
        return

    if data == "admin_broadcast":
        context.user_data["awaiting_broadcast"] = True
        await query.edit_message_text(
            "📣 *Рассылка*\n\nВведи текст — получат все пользователи.\n\n/cancel — отмена",
            parse_mode="Markdown")

    elif data == "admin_add_ids":
        context.user_data["awaiting_ids"] = True
        await query.edit_message_text(
            "➕ *Добавление ID*\n\nОтправь числовые ID через пробел или запятую.\n\n/cancel — отмена",
            parse_mode="Markdown")

    elif data == "admin_list_ids":
        ids = ", ".join(str(i) for i in manual_ids) if manual_ids else "пусто"
        await query.edit_message_text(
            f"📋 *Список ID:*\n\n{ids}",
            parse_mode="Markdown",
            reply_markup=back_keyboard("admin_back"))

    elif data == "admin_view_top":
        entries = get_top_entries()
        M = ["🥇", "🥈", "🥉"]
        lines = ["🏆 *Топ анонимщиков*\n"]
        if not entries:
            lines.append("Пока пусто 😶")
        else:
            for i, e in enumerate(entries[:10]):
                m = M[i] if i < 3 else f"{i + 1}."
                nick = e['nick'].replace('*', '\\*').replace('_', '\\_')
                lines.append(f"{m} *{nick}* — {e['count']} анонимок\n`ID: {e['user_id']}`")
        await query.edit_message_text(
            "\n".join(lines), parse_mode="Markdown",
            reply_markup=back_keyboard("admin_back"))

    elif data == "admin_tab_messages":
        await show_message_logs_page(query, 0)

    elif data == "admin_tab_starts":
        await show_start_logs_page(query, 0)

    elif data == "admin_export":
        await export_logs_csv(query)

    elif data == "admin_clean_old":
        await clean_old_logs(query)

    elif data == "admin_test_ai":
        context.user_data["awaiting_test_media"] = True
        await query.edit_message_text(
            "🧪 *Тест ИИ модерации*\n\n"
            "Отправь фото, видео, GIF или стикер — я проверю через Sightengine и скажу:\n"
            "✅ пропустил бы в канал или 🚫 заблокировал бы\n\n"
            "_(в канал ничего не отправляется)_\n\n"
            "/cancel — отмена",
            parse_mode="Markdown",
            reply_markup=back_keyboard("admin_back"))

    elif data == "admin_anon_msgs":
        await show_anon_messages_page(query, 0)

    elif data == "admin_back":
        await query.edit_message_text(admin_text(), reply_markup=admin_keyboard())

    elif data.startswith("msg_page_"):
        await show_message_logs_page(query, int(data.rsplit("_", 1)[-1]))

    elif data.startswith("start_page_"):
        await show_start_logs_page(query, int(data.rsplit("_", 1)[-1]))

    elif data.startswith("anon_msg_page_"):
        await show_anon_messages_page(query, int(data.rsplit("_", 1)[-1]))

    elif data == "msg_clear":
        message_logs.clear()
        _save_json(LOG_FILE, message_logs)
        await query.edit_message_text("🧹 Логи анонимок очищены.", reply_markup=admin_keyboard())

    elif data == "start_clear":
        start_logs.clear()
        _save_json(START_LOG_FILE, start_logs)
        await query.edit_message_text("🧹 Логи стартов очищены.", reply_markup=admin_keyboard())

    elif data == "anon_msg_clear":
        anon_messages_log.clear()
        _save_json(ANON_MSGS_LOG_FILE, anon_messages_log)
        await query.edit_message_text("🧹 Логи анонимных сообщений очищены.", reply_markup=admin_keyboard())

    else:
        await query.answer("Неизвестная команда.", show_alert=True)

# ── ОБРАБОТЧИК СООБЩЕНИЙ БОТА 2 ──────────
async def bot2_handle_message(update: Update, context: PTBContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    uid = update.effective_user.id

    # --- тест модерации (админ) ---
    if context.user_data.get("awaiting_test_media") and uid == ADMIN_ID:
        msg = update.message
        if msg.text and not msg.photo and not msg.video and not msg.animation and not msg.sticker:
            await msg.reply_text("⚠️ Отправь фото, видео, GIF или стикер для теста.")
            return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        ok, reason = True, ""
        ctype = "неизвестно"
        try:
            if msg.photo:
                ctype = "фото"
                ok, reason = await is_image_acceptable(context.bot, msg.photo[-1].file_id)
            elif msg.sticker:
                ctype = "стикер"
                ok, reason = await is_sticker_acceptable(context.bot, msg.sticker)
            elif msg.video:
                ctype = "видео"
                ok, reason = await is_video_acceptable(context.bot, msg.video.file_id)
            elif msg.animation:
                ctype = "GIF"
                ok, reason = await is_video_acceptable(context.bot, msg.animation.file_id)
            else:
                await msg.reply_text("⚠️ Поддерживается только фото, видео, GIF и стикер.")
                return
        except Exception as e:
            await msg.reply_text(f"❌ Ошибка при проверке: {e}")
            context.user_data.pop("awaiting_test_media", None)
            return

        context.user_data.pop("awaiting_test_media", None)
        if ok:
            await msg.reply_text(
                f"✅ *Sightengine пропустил бы это {ctype} в канал*\n\nКонтент признан приемлемым.",
                parse_mode="Markdown", reply_markup=admin_keyboard())
        else:
            await msg.reply_text(
                f"🚫 *Sightengine заблокировал бы это {ctype}*\n\nПричина: {reason}",
                parse_mode="Markdown", reply_markup=admin_keyboard())
        return

    # --- ник для топа ---
    if context.user_data.get("awaiting_top_nick"):
        nick = (update.message.text or "").strip()
        if not nick or len(nick) > 32:
            await update.message.reply_text("⚠️ Ник: 1–32 символа. Попробуй ещё раз:")
            return
        join_top(uid, nick)
        context.user_data.pop("awaiting_top_nick", None)
        count = top_data.get(str(uid), {}).get("count", 0)
        await update.message.reply_text(
            f"✅ Ты в топе под ником *{nick}*!\n\n"
            f"📊 Твои анонимки за эту неделю: *{count}*\n\n"
            f"{build_top_text()}",
            parse_mode="Markdown",
            reply_markup=top_keyboard(uid))
        return

    # --- добавление ID (админ) ---
    if context.user_data.get("awaiting_ids") and uid == ADMIN_ID:
        text = (update.message.text or "").strip()
        new_ids = [int(x) for x in re.findall(r'\b\d+\b', text)]
        if not new_ids:
            await update.message.reply_text("❌ Не найдено числовых ID. Попробуй снова.")
            return
        added = []
        for n in new_ids:
            if n not in manual_ids:
                manual_ids.append(n)
                added.append(str(n))
        if added:
            save_manual_ids(manual_ids)
            await update.message.reply_text(f"✅ Добавлены: {', '.join(added)}")
        else:
            await update.message.reply_text("⚠️ Все эти ID уже есть.")
        context.user_data.pop("awaiting_ids", None)
        return

    # --- рассылка (админ) ---
    if context.user_data.get("awaiting_broadcast") and uid == ADMIN_ID:
        txt = update.message.text
        if not txt:
            await update.message.reply_text("❌ Сообщение не может быть пустым.")
            return
        context.user_data.pop("awaiting_broadcast", None)
        all_ids = list(set(e["user_id"] for e in start_logs) | set(manual_ids))
        if not all_ids:
            await update.message.reply_text("📭 Нет получателей.")
            return
        await update.message.reply_text(f"📣 Рассылка для {len(all_ids)} чел...")
        sent = failed = 0
        for i in all_ids:
            try:
                await context.bot.send_message(i, txt)
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1
        await update.message.reply_text(f"✅ Готово!\n📤 Отправлено: {sent}\n❌ Ошибок: {failed}")
        return

    state = context.user_data.get("state")
    if state == ANONYMOUS_MODE:
        await handle_anonymous(update, context)
    elif state == AI_CHAT_MODE:
        await handle_ai_chat(update, context)
    else:
        await main_menu(update, context)

# ── АНОНИМКА БОТА 2 ───────────────────────
async def handle_anonymous(update: Update, context: PTBContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message
    text = msg.text or msg.caption or ""
    now = datetime.now()

    last = user_last_time.get(uid)
    if last and (now - last).total_seconds() < COOLDOWN_SECONDS:
        rem = int(COOLDOWN_SECONDS - (now - last).total_seconds())
        m, s = divmod(rem, 60)
        await msg.reply_text(f"⏳ Подожди ещё {m}:{s:02d} перед следующей отправкой.")
        return

    ctype = ("фото"      if msg.photo     else
             "видео"     if msg.video     else
             "GIF"       if msg.animation else
             "аудио"     if msg.audio     else
             "голосовое" if msg.voice     else
             "документ"  if msg.document  else
             "стикер"    if msg.sticker   else "текст")

    # ── Модерация ──
    if msg.photo:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        ok, reason = await is_image_acceptable(context.bot, msg.photo[-1].file_id)
        if not ok:
            _log_blocked(uid, update, ctype, text, reason, now)
            await notify_admin_silent(context, update, ctype, text, blocked_reason=reason)
            await msg.reply_text(f"🚫 *Изображение не принято*\n\nПричина: {reason}", parse_mode="Markdown")
            return

    elif msg.sticker:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        ok, reason = await is_sticker_acceptable(context.bot, msg.sticker)
        if not ok:
            _log_blocked(uid, update, ctype, text, reason, now)
            await notify_admin_silent(context, update, ctype, text, blocked_reason=reason)
            await msg.reply_text(f"🚫 *Стикер не принят*\n\nПричина: {reason}", parse_mode="Markdown")
            return

    elif msg.video or msg.animation:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        file_id = msg.video.file_id if msg.video else msg.animation.file_id
        ok, reason = await is_video_acceptable(context.bot, file_id)
        if not ok:
            _log_blocked(uid, update, ctype, text, reason, now)
            await notify_admin_silent(context, update, ctype, text, blocked_reason=reason)
            await msg.reply_text(f"🚫 *Видео не принято*\n\nПричина: {reason}", parse_mode="Markdown")
            return

    elif msg.text:
        ok, reason = await is_content_acceptable(msg.text)
        if not ok:
            _log_blocked(uid, update, ctype, text, reason, now)
            await notify_admin_silent(context, update, ctype, text, blocked_reason=reason)
            await msg.reply_text(f"🚫 *Сообщение не принято*\n\nПричина: {reason}", parse_mode="Markdown")
            return

    # ── Отправка в канал ──
    mid = await send_to_channel(context, update, text)
    if mid is None:
        await msg.reply_text("❌ Не удалось отправить. Попробуй позже.")
        return

    user_last_time[uid] = now
    log_entry = {
        "user_id": uid,
        "username": update.effective_user.username,
        "first_name": update.effective_user.first_name,
        "last_name": update.effective_user.last_name,
        "content_type": ctype,
        "text": text,
        "timestamp": now.isoformat(),
        "channel_msg_id": mid,
    }
    add_message_log(log_entry)
    add_anon_message_log(log_entry)
    increment_top(uid)
    await notify_admin_silent(context, update, ctype, text)
    await msg.reply_text(
        "✅ *Анонимка отправлена!*\n\n"
        "Твоё сообщение опубликовано в канале 🎉\n"
        "Никто не знает что это ты 🔒",
        parse_mode="Markdown",
        reply_markup=after_anon_keyboard())


def _log_blocked(uid, update, ctype, text, reason, now):
    """Вспомогательная: записать заблокированное сообщение"""
    add_message_log({
        "user_id": uid,
        "username": update.effective_user.username,
        "first_name": update.effective_user.first_name,
        "last_name": update.effective_user.last_name,
        "content_type": ctype,
        "text": text,
        "timestamp": now.isoformat(),
        "blocked": reason,
    })

# ── ИИ-ЧАТ БОТА 2 ────────────────────────
async def handle_ai_chat(update: Update, context: PTBContextTypes.DEFAULT_TYPE):
    inp = update.message.text
    if not inp:
        await update.message.reply_text("В режиме ИИ принимается только текст.")
        return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    res = await call_groq_with_context(update.effective_user.id, inp)
    await typewriter_reply(update, res or "⚠️ ИИ временно недоступен.")

# ── КНОПКИ БОТА 2 ────────────────────────
async def bot2_button_callback(update: Update, context: PTBContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = update.effective_user.id

    # Делегируем всё admin_callback, если это adminские данные
    if (data.startswith("admin_") or data.startswith("msg_page_")
            or data.startswith("start_page_") or data.startswith("anon_msg_page_")
            or data in ("msg_clear", "start_clear", "anon_msg_clear")):
        await admin_callback(update, context)
        return

    if data == "menu_anon":
        context.user_data["state"] = ANONYMOUS_MODE
        await query.edit_message_text(
            "✉️ *Режим анонимки*\n\n"
            "Отправь текст, фото, видео, голосовое, GIF или стикер.\n"
            "Всё появится в канале без твоего имени 🔒\n\n"
            "⏳ Между отправками: 3 минуты\n\n"
            "👇 Жду твоё сообщение...",
            parse_mode="Markdown")

    elif data == "menu_top":
        await query.edit_message_text(build_top_text(), reply_markup=top_keyboard(uid))

    elif data == "top_refresh":
        try:
            await query.edit_message_text(build_top_text(), reply_markup=top_keyboard(uid))
        except Exception:
            pass

    elif data == "top_join":
        context.user_data["awaiting_top_nick"] = True
        already = is_in_top(uid)
        hint = ("Введи *новый ник* чтобы обновить:" if already
                else "Введи *ник* — он будет виден всем в рейтинге:")
        await query.edit_message_text(
            f"✍️ *Ник для топа*\n\n{hint}\n\n"
            "Это может быть прозвище, псевдоним — что угодно 😎\n"
            "Максимум 32 символа",
            parse_mode="Markdown")

    elif data == "top_leave":
        leave_top(uid)
        await query.edit_message_text(
            "👋 Ты вышел из топа.\n\nВозвращайся в любой момент!",
            reply_markup=top_keyboard(uid))

    elif data == "menu_ai":
        context.user_data["state"] = AI_CHAT_MODE
        await query.edit_message_text(
            "🤖 *ИИ-чат активен*\n\n"
            "Просто напиши что-нибудь и я отвечу 💬\n\n"
            "Я запоминаю последние 3 сообщения диалога.",
            parse_mode="Markdown",
            reply_markup=ai_keyboard())

    elif data == "menu_help":
        await query.edit_message_text(
            "❓ *Помощь*\n\n"
            "✉️ *Анонимка*\n"
            "Отправь любое сообщение — оно выйдет в канале без твоего имени.\n"
            "Поддерживаются: текст, фото, видео, голос, GIF, стикер.\n\n"
            "🏆 *Топ анонимщиков*\n"
            "Еженедельный рейтинг. Каждая анонимка = +1 к счёту.\n\n"
            "🤖 *ИИ-чат*\n"
            "Общайся с искусственным интеллектом на любую тему.\n\n"
            "/start — вернуться в главное меню",
            parse_mode="Markdown",
            reply_markup=back_keyboard("menu_back"))

    elif data == "menu_back":
        context.user_data.pop("state", None)
        await main_menu(update, context, edit=True)

    elif data == "ai_reset":
        user_ai_context.pop(uid, None)
        await query.edit_message_text(
            "🧠 Память сброшена. Начинаем с чистого листа!",
            reply_markup=ai_keyboard())

    elif data == "anon_again":
        context.user_data["state"] = ANONYMOUS_MODE
        await query.edit_message_text("✉️ Режим анонимки.\n\nОтправь следующее сообщение 👇")

    else:
        await query.answer("Неизвестная команда. Используй /start.", show_alert=True)

# ── ЛОГИ / ПАГИНАЦИЯ ─────────────────────
def _paginate(items, page, per=5):
    total = max(1, (len(items) + per - 1) // per)
    page = max(0, min(page, total - 1))
    return items[page * per:(page + 1) * per], total, page  # возвращаем скорректированный page

def _nav(page, total, prefix, clear_cb, end=False):
    nav = []
    if page > 0:
        nav.append(PTBInlineKeyboardButton("◀️", callback_data=f"{prefix}{page - 1}"))
    if page < total - 1:
        nav.append(PTBInlineKeyboardButton("▶️", callback_data=f"{prefix}{page + 1}"))
    if end and total > 1 and page < total - 1:
        nav.append(PTBInlineKeyboardButton("⏭", callback_data=f"{prefix}{total - 1}"))
    rows = []
    if nav:
        rows.append(nav)
    rows.append([PTBInlineKeyboardButton("🗑  Очистить всё", callback_data=clear_cb)])
    rows.append([PTBInlineKeyboardButton("🔙  Назад в панель", callback_data="admin_back")])
    return PTBInlineKeyboardMarkup(rows)


def _safe(s: str) -> str:
    """Экранирует спецсимволы Markdown v1 в тексте для логов"""
    return s.replace("*", "\\*").replace("_", "\\_").replace("`", "\\`").replace("[", "\\[")


async def show_message_logs_page(query, page):
    if not message_logs:
        await query.edit_message_text(
            "📭 Нет анонимных сообщений.",
            reply_markup=_nav(0, 1, "msg_page_", "msg_clear"))
        return
    items, total, page = _paginate(message_logs, page)
    lines = [f"📩 Анонимки — стр. {page + 1}/{total}\n▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔\n"]
    for i, e in enumerate(items, page * 5 + 1):
        dt = datetime.fromisoformat(e["timestamp"]).strftime("%d.%m.%Y %H:%M")
        uname = e.get("username") or ""
        fname = e.get("first_name") or ""
        lname = e.get("last_name") or ""
        ustr = get_display_name(e["user_id"], uname, fname, lname)
        snip = (e.get("text") or "")[:60] or "—"
        blk = e.get("blocked")
        ico = "🚫" if blk else "✅"
        mid = e.get("channel_msg_id")
        if mid and not blk:
            ch = str(BOT2_CHANNEL_ID).replace("-100", "")
            link = f"[🔗 открыть](https://t.me/c/{ch}/{mid})"
        else:
            link = f"🚫 {blk}" if blk else "—"
        lines.append(
            f"{ico} *{i}.* {dt}\n"
            f"👤 `{e['user_id']}` {_safe(ustr)}\n"
            f"📎 {e.get('content_type', 'текст')}: {_safe(snip)}\n"
            f"{link}"
        )
    await query.edit_message_text(
        "\n\n".join(lines),
        parse_mode="Markdown",
        reply_markup=_nav(page, total, "msg_page_", "msg_clear", end=True),
        disable_web_page_preview=True)


async def show_start_logs_page(query, page):
    if not start_logs:
        await query.edit_message_text(
            "📭 Нет записей.",
            reply_markup=_nav(0, 1, "start_page_", "start_clear"))
        return
    items, total, page = _paginate(start_logs, page)
    lines = [f"👥 Пользователи — стр. {page + 1}/{total}\n▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔\n"]
    for i, e in enumerate(items, page * 5 + 1):
        dt = datetime.fromisoformat(e["timestamp"]).strftime("%d.%m.%Y %H:%M")
        uname = e.get("username") or ""
        fname = e.get("first_name") or ""
        lname = e.get("last_name") or ""
        ustr = get_display_name(e["user_id"], uname, fname, lname)
        lines.append(f"*{i}.* {dt}\n👤 `{e['user_id']}` {_safe(ustr)}")
    await query.edit_message_text(
        "\n\n".join(lines),
        parse_mode="Markdown",
        reply_markup=_nav(page, total, "start_page_", "start_clear"))


async def show_anon_messages_page(query, page):
    if not anon_messages_log:
        await query.edit_message_text(
            "📭 Нет анонимных сообщений в логе.",
            reply_markup=_nav(0, 1, "anon_msg_page_", "anon_msg_clear"))
        return
    items, total, page = _paginate(anon_messages_log, page)
    lines = [f"📨 Логи анонимных сообщений — стр. {page + 1}/{total}\n▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔\n"]
    for i, e in enumerate(items, page * 5 + 1):
        dt = datetime.fromisoformat(e["timestamp"]).strftime("%d.%m.%Y %H:%M")
        uname = e.get("username") or ""
        fname = e.get("first_name") or ""
        lname = e.get("last_name") or ""
        ustr = get_display_name(e["user_id"], uname, fname, lname)
        snip = (e.get("text") or "")[:80] or "—"
        ctype = e.get("content_type", "текст")
        mid = e.get("channel_msg_id")
        if mid:
            ch = str(BOT2_CHANNEL_ID).replace("-100", "")
            link = f"[🔗 открыть](https://t.me/c/{ch}/{mid})"
        else:
            link = "—"
        lines.append(
            f"*{i}.* {dt}\n"
            f"👤 `{e['user_id']}` {_safe(ustr)}\n"
            f"📎 Тип: {ctype}\n"
            f"💬 {_safe(snip)}\n"
            f"{link}"
        )
    await query.edit_message_text(
        "\n\n".join(lines),
        parse_mode="Markdown",
        reply_markup=_nav(page, total, "anon_msg_page_", "anon_msg_clear", end=True),
        disable_web_page_preview=True)


async def export_logs_csv(query):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["user_id", "username", "first_name", "last_name",
                                        "content_type", "text", "timestamp", "blocked"])
    w.writeheader()
    for row in message_logs:
        w.writerow({k: row.get(k, "") for k in w.fieldnames})
    f = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    f.name = "logs.csv"
    await query.message.reply_document(document=f, filename="logs.csv", caption="📤 Экспорт логов")


async def clean_old_logs(query):
    global message_logs
    cutoff = datetime.now().timestamp() - 7 * 86400
    before = len(message_logs)
    message_logs = [e for e in message_logs
                    if datetime.fromisoformat(e["timestamp"]).timestamp() > cutoff]
    _save_json(LOG_FILE, message_logs)
    await query.edit_message_text(
        f"🧹 Удалено {before - len(message_logs)} записей старше 7 дней.",
        reply_markup=admin_keyboard())


# ═══════════════════════════════════════════════════════════════════
# ЗАПУСК ОБОИХ БОТОВ
# ═══════════════════════════════════════════════════════════════════

async def run_bot1():
    await bot1.delete_webhook(drop_pending_updates=True)
    logger.info("[Bot1] Анонимные комментарии запущены!")
    await bot1_dp.start_polling(bot1)


def run_bot2():
    load_all_logs()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        app = PTBApplication.builder().token(BOT2_TOKEN).build()
        app.add_handler(PTBCommandHandler("start", bot2_cmd_start))
        app.add_handler(PTBCommandHandler("admin", bot2_cmd_admin))
        app.add_handler(PTBCommandHandler("cancel", bot2_cmd_cancel))
        app.add_handler(PTBCallbackQueryHandler(bot2_button_callback))
        app.add_handler(PTBMessageHandler(
            PTBfilters.ALL & ~PTBfilters.COMMAND & ~PTBfilters.ChatType.CHANNEL,
            bot2_handle_message
        ))
        logger.info("[Bot2] Анонимные сообщения + админка запущены!")
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES, stop_signals=None)
    finally:
        loop.close()


async def main():
    bot2_thread = threading.Thread(target=run_bot2, daemon=True)
    bot2_thread.start()
    await run_bot1()


if __name__ == "__main__":
    asyncio.run(main())
