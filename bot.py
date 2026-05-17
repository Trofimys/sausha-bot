import logging
import re
import json
import httpx
import asyncio
import os
import sys
import csv
import io
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ─────────────────────────────────────────
# КОНФИГУРАЦИЯ
# ─────────────────────────────────────────
BOT_TOKEN    = "8237768266:AAEj4PP3EJF7ORMK2ydjMyV7OYFunVoSI-w"
CHANNEL_ID   = -1003951653487
GROQ_API_KEY = "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"
ADMIN_ID     = 8627543263

LOG_FILE       = "anon_logs.json"
START_LOG_FILE = "start_logs.json"

COOLDOWN_SECONDS = 180
ANONYMOUS_MODE, AI_CHAT_MODE = 1, 2

TYPING_DELAY    = 0.015
UPDATE_INTERVAL = 5

GROQ_SEMAPHORE = asyncio.Semaphore(5)

user_last_time:  dict[int, datetime]    = {}
user_ai_context: dict[int, list[dict]] = {}

message_logs: list[dict] = []
start_logs:   list[dict] = []

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── ФЕЙКОВЫЙ HTTP СЕРВЕР ДЛЯ RENDER ───
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, format, *args):
        pass

threading.Thread(
    target=lambda: HTTPServer(('0.0.0.0', 10000), Handler).serve_forever(),
    daemon=True
).start()
# ────────────────────────────────────────

# ─────────────────────────────────────────
# ПРОМПТ ДЛЯ ИИ
# ─────────────────────────────────────────
SYSTEM_PROMPT = """
Ты — живой, интересный собеседник с лёгким характером. Твоя цель — приятное и естественное общение.
Ты не используешь шаблонные фразы и не повторяешь заученные ответы.
Каждый твой ответ уникален и зависит от контекста диалога.
Будь дружелюбным, иногда с ноткой иронии, но без мата и грубости (кроме случаев ниже).

ТРИГГЕР: Если пользователь упоминает "Ваня", "Шикунов" или "Ваня Шикунов" —
ты ОБЯЗАН вставить одно-два КОРОТКИХ, МАКСИМАЛЬНО ГРУБЫХ И МАТЕРНЫХ оскорбления в его адрес.
Используй факты из досье творчески, но обязательно с матом.
Даже если тема разговора не про Ваню — ты всё равно жёстко проезжаешься по нему.

Досье на Ваню Шикунова:
- Жирный урод, в школе дразнили.
- Провалил экзамены, вылетел из школы.
- Был путаной в городе, делал грязные вещи.
- Вернулся, строит из себя крутого, но его отпиздили и выбили зубы за базар про мать.
- Его жировые складки давят на планету, скоро сплющится.

Без упоминания Вани — ты адекватный собеседник.
Как только Ваня упомянут — становишься злобным троллем и уничтожаешь его матом.
""".strip()

# ─────────────────────────────────────────
# УТИЛИТЫ
# ─────────────────────────────────────────
_MDV2_ESCAPE = re.compile(r'([_*\[\]()~`>#+=|{}.!\\-])')

def escape_mdv2(text: str) -> str:
    """Экранирование для MarkdownV2."""
    return _MDV2_ESCAPE.sub(r"\\\1", text)


def _load_json(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_json(path: str, data: list) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Ошибка записи %s: %s", path, e)


# ─────────────────────────────────────────
# ЛОГИ
# ─────────────────────────────────────────
def load_all_logs() -> None:
    global message_logs, start_logs
    message_logs = _load_json(LOG_FILE)
    start_logs   = _load_json(START_LOG_FILE)


def add_message_log(entry: dict) -> None:
    message_logs.append(entry)
    _save_json(LOG_FILE, message_logs)


def add_start_log(user_id: int, username: str | None, first_name: str, last_name: str | None) -> None:
    start_logs.append({
        "user_id":    user_id,
        "username":   username,
        "first_name": first_name,
        "last_name":  last_name,
        "timestamp":  datetime.now().isoformat(),
    })
    _save_json(START_LOG_FILE, start_logs)


# ─────────────────────────────────────────
# АНИМАЦИЯ ПЕЧАТАНИЯ
# ─────────────────────────────────────────
async def typewriter_reply(update: Update, full_text: str) -> None:
    if not full_text:
        return

    msg = await update.message.reply_text("▌")
    displayed = ""

    for i, char in enumerate(full_text, start=1):
        displayed += char
        if i % UPDATE_INTERVAL == 0 or i == len(full_text):
            cursor = "" if i == len(full_text) else "▌"
            try:
                await msg.edit_text(displayed + cursor)
            except Exception:
                pass
        await asyncio.sleep(TYPING_DELAY)


# ─────────────────────────────────────────
# GROQ API
# ─────────────────────────────────────────
async def _groq_request(payload: dict, retries: int = 2) -> dict | None:
    """Запрос к Groq с экспоненциальным backoff при 429."""
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(retries + 1):
            try:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code == 429 and attempt < retries:
                    wait = 2 ** attempt          # 1s, 2s, 4s …
                    logger.warning("Groq 429, ждём %ss (попытка %s)", wait, attempt + 1)
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code != 200:
                    logger.warning("Groq вернул %s: %s", resp.status_code, resp.text[:200])
                    return None
                return resp.json()
            except httpx.TimeoutException:
                logger.error("Groq timeout (попытка %s)", attempt + 1)
                if attempt < retries:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error("Groq request exception: %s", e)
                if attempt < retries:
                    await asyncio.sleep(1)
    return None


async def call_groq_simple(prompt: str, system: str, as_json: bool = False) -> str | None:
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.1 if as_json else 0.9,
        "max_tokens": 256,
    }
    if as_json:
        payload["response_format"] = {"type": "json_object"}

    try:
        data = await _groq_request(payload)
        return data["choices"][0]["message"]["content"] if data else None
    except Exception as e:
        logger.error("Groq simple error: %s", e)
        return None


async def call_groq_with_context(user_id: int, user_message: str) -> str:
    async with GROQ_SEMAPHORE:
        history  = user_ai_context.setdefault(user_id, [])
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history,
                    {"role": "user", "content": user_message}]

        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": messages,
            "temperature": 0.9,
            "max_tokens": 1024,
        }

        try:
            data = await _groq_request(payload, retries=2)
            if not data:
                return "⚠️ Чёт Groq приуныл, попробуй позже."

            reply = data["choices"][0]["message"]["content"]

            history.append({"role": "user",      "content": user_message})
            history.append({"role": "assistant",  "content": reply})

            # Храним последние 6 сообщений (3 обмена)
            if len(history) > 6:
                user_ai_context[user_id] = history[-6:]

            return reply

        except Exception as e:
            logger.error("Groq context error: %s", e)
            return "⚠️ Ошибка сети."


# ─────────────────────────────────────────
# АНТИСПАМ
# ─────────────────────────────────────────
async def is_advertisement(text: str) -> tuple[bool, str]:
    if not text or len(text.strip()) < 2:
        return False, ""

    system = (
        'Ты модератор. Отвечай только JSON: {"spam": true/false, "reason": "..."}. '
        "Блокируй ссылки и юзернеймы."
    )
    result = await call_groq_simple(text, system, as_json=True)
    if result:
        try:
            # Убираем возможные markdown-обёртки
            clean = result.strip().removeprefix("```json").removesuffix("```").strip()
            parsed = json.loads(clean)
            return bool(parsed.get("spam", False)), parsed.get("reason", "")
        except json.JSONDecodeError:
            pass
    return False, ""


# ─────────────────────────────────────────
# ОТПРАВКА В КАНАЛ
# ─────────────────────────────────────────
async def send_to_channel(context: ContextTypes.DEFAULT_TYPE, update: Update, text: str) -> bool:
    header  = "*📩 Анонимное сообщение*"
    safe    = escape_mdv2(text) if text else ""
    caption = f"{header}\n\n{safe}" if safe else header
    msg     = update.message
    bot     = context.bot

    try:
        if msg.photo:
            await bot.send_photo(CHANNEL_ID, msg.photo[-1].file_id, caption=caption, parse_mode="MarkdownV2")
        elif msg.video:
            await bot.send_video(CHANNEL_ID, msg.video.file_id, caption=caption, parse_mode="MarkdownV2")
        elif msg.animation:
            await bot.send_animation(CHANNEL_ID, msg.animation.file_id, caption=caption, parse_mode="MarkdownV2")
        elif msg.audio:
            await bot.send_audio(CHANNEL_ID, msg.audio.file_id, caption=caption, parse_mode="MarkdownV2")
        elif msg.voice:
            await bot.send_voice(CHANNEL_ID, msg.voice.file_id, caption=caption, parse_mode="MarkdownV2")
        elif msg.document:
            await bot.send_document(CHANNEL_ID, msg.document.file_id, caption=caption, parse_mode="MarkdownV2")
        elif msg.sticker:
            await bot.send_sticker(CHANNEL_ID, msg.sticker.file_id)
        elif msg.text:
            await bot.send_message(CHANNEL_ID, caption, parse_mode="MarkdownV2")
        else:
            return False
        return True
    except Exception as e:
        logger.error("Ошибка отправки в канал: %s", e)
        return False


# ─────────────────────────────────────────
# ТИХОЕ УВЕДОМЛЕНИЕ АДМИНА
# ─────────────────────────────────────────
async def notify_admin_silent(
    context: ContextTypes.DEFAULT_TYPE,
    update: Update,
    content_type: str,
    content_text: str,
) -> None:
    user         = update.effective_user
    username_str = f"@{user.username}" if user.username else "—"
    full_name    = f"{user.first_name or ''} {user.last_name or ''}".strip() or "—"

    # Экранируем переменные данные, чтобы не сломать Markdown
    safe_name = full_name.replace("`", "'").replace("*", "")
    safe_user = username_str.replace("`", "'")

    lines = [
        "🕵️ *Анонимка отправлена*",
        "",
        f"👤 ID: `{user.id}`",
        f"🔗 Username: {safe_user}",
        f"📛 Имя: {safe_name}",
        f"📎 Тип: {content_type}",
    ]
    if content_text:
        snippet = content_text[:300].replace("`", "'")
        lines.append(f"💬 Текст:\n`{snippet}`")

    text = "\n".join(lines)

    try:
        await context.bot.send_message(ADMIN_ID, text, parse_mode="Markdown")
    except Exception as e:
        logger.error("Не удалось уведомить админа: %s", e)


# ─────────────────────────────────────────
# КЛАВИАТУРЫ
# ─────────────────────────────────────────
def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Отправить анонимку", callback_data="menu_anon")],
        [InlineKeyboardButton("🤖 Поболтать с ИИ",     callback_data="menu_ai")],
        [InlineKeyboardButton("ℹ️ Помощь",              callback_data="menu_help")],
    ])

def ai_control_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Сбросить память", callback_data="ai_reset")],
        [InlineKeyboardButton("🔙 Главное меню",    callback_data="menu_back")],
    ])

def after_anon_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Отправить ещё", callback_data="anon_again")],
        [InlineKeyboardButton("🔙 Главное меню",  callback_data="menu_back")],
    ])


# ─────────────────────────────────────────
# ГЛАВНОЕ МЕНЮ
# ─────────────────────────────────────────
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    text  = "👋 *Главное меню*\nВыберите действие:"
    reply = main_keyboard()

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply)
        except Exception:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                parse_mode="Markdown",
                reply_markup=reply,
            )
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply)


# ─────────────────────────────────────────
# ОБРАБОТЧИКИ КОМАНД
# ─────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    add_start_log(user.id, user.username, user.first_name, user.last_name)
    context.user_data.clear()
    await main_menu(update, context)


# ─────────────────────────────────────────
# ОБРАБОТЧИК КНОПОК
# ─────────────────────────────────────────
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data  = query.data

    # ── Главное меню ──
    if data == "menu_anon":
        context.user_data["state"] = ANONYMOUS_MODE
        await query.edit_message_text(
            "✉️ *Режим анонимки активен*\n"
            "Отправьте текст, фото, видео или голосовое — появится в канале анонимно.",
            parse_mode="Markdown",
        )

    elif data == "menu_ai":
        context.user_data["state"] = AI_CHAT_MODE
        await query.edit_message_text(
            "🤖 *Режим ИИ активен*\nПросто напишите что-нибудь 💬",
            parse_mode="Markdown",
        )
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Управление чатом:",
            reply_markup=ai_control_keyboard(),
        )

    elif data == "menu_help":
        await query.edit_message_text(
            "ℹ️ *Помощь*\n\n"
            "• *Анонимка:* отправьте любое сообщение — выйдет в канале без вашего имени\\.\n"
            "• *ИИ\\-чат:* общайтесь с искусственным интеллектом\\.\n"
            "• /start — вернуться в главное меню\\.",
            parse_mode="MarkdownV2",
        )
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Нажмите для возврата:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Главное меню", callback_data="menu_back")]]
            ),
        )

    elif data == "menu_back":
        context.user_data.pop("state", None)
        await main_menu(update, context, edit=True)

    # ── ИИ ──
    elif data == "ai_reset":
        user_ai_context.pop(update.effective_user.id, None)
        await query.edit_message_text(
            "🧠 Память сброшена.",
            reply_markup=ai_control_keyboard(),
        )

    # ── Анонимка ──
    elif data == "anon_again":
        context.user_data["state"] = ANONYMOUS_MODE
        await query.edit_message_text("✉️ Режим анонимки. Отправьте следующее сообщение.")

    # ── Админ ── (все admin_* и пагинация)
    elif data.startswith("admin_") or data.startswith("msg_page_") or data.startswith("start_page_") \
            or data in ("msg_clear", "start_clear"):
        await admin_callback(update, context)

    else:
        await query.edit_message_text("Неизвестная команда. Используйте /start.")


# ─────────────────────────────────────────
# ОБРАБОТЧИК СООБЩЕНИЙ
# ─────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get("state")
    if state == ANONYMOUS_MODE:
        await handle_anonymous(update, context)
    elif state == AI_CHAT_MODE:
        await handle_ai_chat(update, context)
    else:
        await main_menu(update, context)


async def handle_anonymous(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text    = update.message.text or update.message.caption or ""
    now     = datetime.now()

    # Кулдаун
    last = user_last_time.get(user_id)
    if last and (now - last).total_seconds() < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last).total_seconds())
        mins, secs = divmod(remaining, 60)
        await update.message.reply_text(
            f"⏳ Подождите {mins}:{secs:02d} перед следующей отправкой."
        )
        return

    # Антиспам (только текст)
    if text:
        is_spam, reason = await is_advertisement(text)
        if is_spam:
            await update.message.reply_text("🚫 Сообщение не прошло проверку.")
            return

    # Отправка в канал
    if not await send_to_channel(context, update, text):
        await update.message.reply_text("❌ Не удалось отправить. Попробуйте позже.")
        return

    user_last_time[user_id] = now

    # Тип контента
    msg = update.message
    content_type = (
        "фото"      if msg.photo     else
        "видео"     if msg.video     else
        "GIF"       if msg.animation else
        "аудио"     if msg.audio     else
        "голосовое" if msg.voice     else
        "документ"  if msg.document  else
        "стикер"    if msg.sticker   else
        "текст"
    )

    # Сохраняем лог
    add_message_log({
        "user_id":      user_id,
        "username":     update.effective_user.username,
        "first_name":   update.effective_user.first_name,
        "last_name":    update.effective_user.last_name,
        "content_type": content_type,
        "text":         text,
        "timestamp":    now.isoformat(),
    })

    await notify_admin_silent(context, update, content_type, text)
    await update.message.reply_text("✅ Отправлено!", reply_markup=after_anon_keyboard())


async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_input = update.message.text
    if not user_input:
        await update.message.reply_text("Принимается только текст в режиме ИИ.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    response = await call_groq_with_context(update.effective_user.id, user_input)
    await typewriter_reply(update, response or "⚠️ ИИ временно недоступен.")


# ─────────────────────────────────────────
# АДМИН-ПАНЕЛЬ
# ─────────────────────────────────────────
def _admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📩 Анонимные сообщения",   callback_data="admin_tab_messages")],
        [InlineKeyboardButton("👥 Старты",                 callback_data="admin_tab_starts")],
        [InlineKeyboardButton("📤 Экспорт логов CSV",      callback_data="admin_export")],
        [InlineKeyboardButton("🧹 Удалить старые (>7 дн)", callback_data="admin_clean_old")],
    ])


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return
    total_msg    = len(message_logs)
    total_starts = len(start_logs)
    await update.message.reply_text(
        f"👑 *Админ-панель*\n\n"
        f"📩 Сообщений: {total_msg}\n"
        f"👥 Стартов: {total_starts}",
        parse_mode="Markdown",
        reply_markup=_admin_keyboard(),
    )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    # answer() уже вызван в button_callback
    data  = query.data

    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_text("⛔ Доступ запрещён.")
        return

    if data == "admin_tab_messages":
        await show_message_logs_page(query, 0)
    elif data == "admin_tab_starts":
        await show_start_logs_page(query, 0)
    elif data == "admin_export":
        await export_logs_csv(query)
    elif data == "admin_clean_old":
        await clean_old_logs(query)
    elif data == "admin_back":
        total_msg    = len(message_logs)
        total_starts = len(start_logs)
        await query.edit_message_text(
            f"👑 *Админ-панель*\n\n"
            f"📩 Сообщений: {total_msg}\n"
            f"👥 Стартов: {total_starts}",
            parse_mode="Markdown",
            reply_markup=_admin_keyboard(),
        )
    elif data.startswith("msg_page_"):
        page = int(data.rsplit("_", 1)[-1])
        await show_message_logs_page(query, page)
    elif data.startswith("start_page_"):
        page = int(data.rsplit("_", 1)[-1])
        await show_start_logs_page(query, page)
    elif data == "msg_clear":
        message_logs.clear()
        _save_json(LOG_FILE, message_logs)
        await query.edit_message_text("🧹 Логи сообщений очищены.", reply_markup=_admin_keyboard())
    elif data == "start_clear":
        start_logs.clear()
        _save_json(START_LOG_FILE, start_logs)
        await query.edit_message_text("🧹 Логи стартов очищены.", reply_markup=_admin_keyboard())


def _paginate(items: list, page: int, per_page: int = 5) -> tuple[list, int]:
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page        = max(0, min(page, total_pages - 1))
    start       = page * per_page
    return items[start:start + per_page], total_pages


def _nav_buttons(page: int, total: int, prefix: str, clear_cb: str) -> InlineKeyboardMarkup:
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"{prefix}{page - 1}"))
    if page < total - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"{prefix}{page + 1}"))
    keyboard = []
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🗑 Очистить", callback_data=clear_cb)])
    keyboard.append([InlineKeyboardButton("🔙 Назад",    callback_data="admin_back")])
    return InlineKeyboardMarkup(keyboard)


async def show_message_logs_page(query, page: int) -> None:
    if not message_logs:
        await query.edit_message_text(
            "📭 Нет анонимных сообщений.",
            reply_markup=_nav_buttons(0, 1, "msg_page_", "msg_clear"),
        )
        return

    items, total = _paginate(message_logs, page)
    lines = [f"📋 *Анонимные сообщения* \\(стр\\. {page + 1}/{total}\\)"]

    for i, entry in enumerate(items, start=page * 5 + 1):
        dt       = datetime.fromisoformat(entry["timestamp"]).strftime("%d\\.%m\\.%Y %H:%M")
        username = entry.get("username") or ""
        fn       = entry.get("first_name", "") or ""
        ln       = entry.get("last_name",  "") or ""
        user_str = escape_mdv2(f"@{username}" if username else f"{fn} {ln}".strip() or "—")
        snippet  = escape_mdv2((entry.get("text") or "")[:60] or "—")
        ctype    = escape_mdv2(entry.get("content_type", "текст"))
        uid      = entry["user_id"]
        lines.append(
            f"{i}\\. *{dt}*\n"
            f"👤 `{uid}` {user_str}\n"
            f"📎 {ctype}: {snippet}"
        )

    await query.edit_message_text(
        "\n\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=_nav_buttons(page, total, "msg_page_", "msg_clear"),
    )


async def show_start_logs_page(query, page: int) -> None:
    if not start_logs:
        await query.edit_message_text(
            "📭 Нет записей о стартах.",
            reply_markup=_nav_buttons(0, 1, "start_page_", "start_clear"),
        )
        return

    items, total = _paginate(start_logs, page)
    lines = [f"👥 *Старты* \\(стр\\. {page + 1}/{total}\\)"]

    for i, entry in enumerate(items, start=page * 5 + 1):
        dt       = datetime.fromisoformat(entry["timestamp"]).strftime("%d\\.%m\\.%Y %H:%M")
        username = entry.get("username") or ""
        fn       = entry.get("first_name", "") or ""
        ln       = entry.get("last_name",  "") or ""
        user_str = escape_mdv2(f"@{username}" if username else f"{fn} {ln}".strip() or "—")
        uid      = entry["user_id"]
        lines.append(f"{i}\\. *{dt}* — `{uid}` {user_str}")

    await query.edit_message_text(
        "\n\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=_nav_buttons(page, total, "start_page_", "start_clear"),
    )


async def export_logs_csv(query) -> None:
    """Экспорт логов в CSV — исправлен баг с bytes/BytesIO."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[
        "user_id", "username", "first_name", "last_name",
        "content_type", "text", "timestamp",
    ])
    writer.writeheader()
    for row in message_logs:
        # Убедимся, что все ключи присутствуют
        writer.writerow({k: row.get(k, "") for k in writer.fieldnames})

    csv_bytes = buf.getvalue().encode("utf-8-sig")   # utf-8-sig для корректного открытия в Excel
    file_obj  = io.BytesIO(csv_bytes)
    file_obj.name = "message_logs.csv"

    await query.message.reply_document(
        document=file_obj,
        filename="message_logs.csv",
        caption="📤 Экспорт анонимных сообщений",
    )


async def clean_old_logs(query) -> None:
    """Удаление логов старше 7 дней — исправлен баг с global."""
    global message_logs
    cutoff = datetime.now().timestamp() - 7 * 86400
    before = len(message_logs)
    message_logs = [
        e for e in message_logs
        if datetime.fromisoformat(e["timestamp"]).timestamp() > cutoff
    ]
    removed = before - len(message_logs)
    _save_json(LOG_FILE, message_logs)
    await query.edit_message_text(
        f"🧹 Удалено {removed} записей старше 7 дней.",
        reply_markup=_admin_keyboard(),
    )


# ─────────────────────────────────────────
# ТОЧКА ВХОДА
# ─────────────────────────────────────────
def main() -> None:
    load_all_logs()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
