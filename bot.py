import logging
import re
import json
import httpx
import asyncio
import os
import sys
import csv
import io
from datetime import datetime, timedelta
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
import time
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

BOT_TOKEN      = "8237768266:AAEj4PP3EJF7ORMK2ydjMyV7OYFunVoSI-w"
CHANNEL_ID     = -1003854171715
LINKED_CHAT_ID = -1003718571364
GROQ_API_KEY   = "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"
ADMIN_ID       = 8627543263

LOG_FILE         = "anon_logs.json"
START_LOG_FILE   = "start_logs.json"
MANUAL_IDS_FILE  = "manual_ids.json"
TOP_FILE         = "top_data.json"
COMMENTS_FILE    = "anon_comments.json"

COOLDOWN_SECONDS  = 180
COMMENT_COOLDOWN  = 60
ANONYMOUS_MODE, AI_CHAT_MODE, COMMENT_MODE = 1, 2, 3
TYPING_DELAY      = 0.015
UPDATE_INTERVAL   = 5
GROQ_SEMAPHORE    = asyncio.Semaphore(5)

ANIMAL_EMOJIS = [
    "🐶","🐱","🐭","🐹","🐰","🦊","🐻","🐼","🐨","🐯",
    "🦁","🐮","🐷","🐸","🐵","🐔","🐧","🐦","🦆","🦅",
    "🦉","🦇","🐺","🐗","🐴","🦄","🐝","🐛","🦋","🐌",
    "🐞","🐜","🦟","🦗","🕷","🦂","🐢","🐍","🦎","🦖",
    "🦕","🐙","🦑","🦐","🦀","🐡","🐠","🐟","🐬","🐳",
    "🦈","🐊","🐅","🐆","🦓","🦍","🦧","🦣","🐘","🦛",
    "🦏","🐪","🐫","🦒","🦘","🦬","🐃","🐂","🐄","🐎",
    "🐖","🐏","🐑","🦙","🐐","🦌","🐕","🐩","🦮","🐈",
]

user_last_time:     dict[int, datetime]   = {}
user_comment_time:  dict[int, datetime]   = {}
user_ai_context:    dict[int, list[dict]] = {}
message_logs:  list[dict] = []
start_logs:    list[dict] = []
manual_ids:    list[int]  = []
top_data:      dict       = {}
anon_comments: dict       = {}

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── ВЕБ-СЕРВЕР (keep-alive) ───────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass

threading.Thread(
    target=lambda: HTTPServer(("0.0.0.0", 10000), Handler).serve_forever(),
    daemon=True
).start()

def _self_ping():
    while True:
        try: urllib.request.urlopen("https://sausha-bot.onrender.com")
        except: pass
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
Ты — строгий модератор. Оцени текст сообщения по двум критериям:
1) Осмысленность: сообщение должно выражать связную мысль (вопрос, шутку, эмоцию, приветствие, комментарий), а не быть случайным набором букв/цифр/эмодзи.
2) Не спам: в нём не должно быть ссылок, рекламы, призывов перейти куда-либо.
Отвечай ТОЛЬКО JSON: {"acceptable": true/false, "reason": "причина если false"}.
""".strip()

# ── УТИЛИТЫ ───────────────────────────────
_MDV2 = re.compile(r'([_*\[\]()~`>#+=|{}.!\\-])')
def escape_mdv2(t: str) -> str:
    return _MDV2.sub(r"\\\1", t)

def _load_json(path):
    if not os.path.exists(path): return []
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except: return []

def _load_json_dict(path):
    if not os.path.exists(path): return {}
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except: return {}

def _save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e: logger.error("Ошибка записи %s: %s", path, e)

# ── ЗАГРУЗКА ДАННЫХ ───────────────────────
def current_week_key() -> str:
    today = datetime.now()
    return (today - timedelta(days=today.weekday())).strftime("%Y-W%V")

def load_manual_ids() -> list[int]:
    if not os.path.exists(MANUAL_IDS_FILE):
        default_ids = [
            1065994703,1317499381,1325803980,1348135622,1445013145,
            1596705847,1598141304,1658111818,1793536849,1812163694,
            5012402904,5058039623,5093484454,5222651755,5244622001,
            5398185223,5591478632,5846879986,5886556924,5900068784,
            5960908435,6171031779,6322668072,6398253412,6575282623,
            6647049769,6677665897,6716660326,6762818617,6811352382,
            6815122910,6860269336,6927328893,7089300064,7112529527,
            7194633128,7234303233,7431729389,7447312123,7476200435,
            7691946899,7810494142,7824611507,7854035216,7927447701,
            7948610168,7971084218,8013816191,8118408450,8150421121,
            8160648800,8223293549,8306392029,8314930012,8323205303,
            8340087744,8366862190,8475400754,8484636623,8534170879,
            8555817128,8627543263,8665408669,8711321595,
        ]
        _save_json(MANUAL_IDS_FILE, default_ids)
        return default_ids
    try: return [int(x) for x in _load_json(MANUAL_IDS_FILE)]
    except: return []

def save_manual_ids(ids): _save_json(MANUAL_IDS_FILE, ids)

def load_all_logs():
    global message_logs, start_logs, manual_ids, top_data, anon_comments
    message_logs   = _load_json(LOG_FILE)
    start_logs     = _load_json(START_LOG_FILE)
    manual_ids     = load_manual_ids()
    raw            = _load_json(TOP_FILE)
    top_data       = raw if isinstance(raw, dict) else {}
    raw_c          = _load_json_dict(COMMENTS_FILE)
    anon_comments  = raw_c if isinstance(raw_c, dict) else {}

def add_message_log(entry):
    message_logs.append(entry)
    _save_json(LOG_FILE, message_logs)

def add_start_log(uid, uname, fn, ln):
    start_logs.append({
        "user_id": uid, "username": uname, "first_name": fn,
        "last_name": ln, "timestamp": datetime.now().isoformat()
    })
    _save_json(START_LOG_FILE, start_logs)

# ── ТОП ───────────────────────────────────
def get_top_entries() -> list[dict]:
    week = current_week_key()
    res = [{"user_id": int(k), "nick": v.get("nick","Аноним"), "count": v.get("count",0)}
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
        e["week"]  = week
    e["count"] = e.get("count", 0) + 1
    _save_json(TOP_FILE, top_data)

def join_top(uid: int, nick: str):
    k    = str(uid)
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
        except: pass
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
    today   = datetime.now()
    days_until_monday = (7 - today.weekday()) % 7 or 7
    reset   = (today + timedelta(days=days_until_monday)).strftime("%d.%m")
    week    = current_week_key()
    MEDALS  = ["🥇","🥈","🥉"]
    PLACES  = ["4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    lines   = [
        "🏆 ТОП АНОНИМЩИКОВ НЕДЕЛИ",
        "▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔",
        f"📅 Неделя: {week}",
        f"🔄 Сброс рейтинга: {reset}",
        "",
    ]
    if not entries:
        lines += ["😶 Пока никого нет в рейтинге","","💡 Нажми «Вступить в топ» чтобы","   участвовать в соревновании!"]
    else:
        max_count = max(e["count"] for e in entries) or 1
        for i, e in enumerate(entries[:10]):
            medal  = MEDALS[i] if i < 3 else PLACES[i-3] if i < 10 else f"{i+1}."
            filled = round(e["count"] / max_count * 8)
            bar    = "█" * filled + "░" * (8 - filled)
            nick   = e["nick"][:20]
            lines.append(f"{medal}  {nick}")
            lines.append(f"    ▏{bar}▏  {e['count']} анонимок")
            lines.append("")
    lines.append("▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔")
    return "\n".join(lines)

# ── АНОНИМНЫЕ КОММЕНТАРИИ ─────────────────
def get_animal_alias(user_id: int, post_msg_id: int) -> str:
    import hashlib
    seed_str = f"{user_id}:{post_msg_id}:alias_v1"
    h = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    n = len(ANIMAL_EMOJIS)
    a = ANIMAL_EMOJIS[h % n]
    b = ANIMAL_EMOJIS[(h // n) % n]
    c = ANIMAL_EMOJIS[(h // n // n) % n]
    return f"{a}{b}{c}"

def save_comments(): _save_json(COMMENTS_FILE, anon_comments)

def register_comment(post_msg_id: int, user_id: int, text: str) -> str:
    key   = str(post_msg_id)
    alias = get_animal_alias(user_id, post_msg_id)
    if key not in anon_comments:
        anon_comments[key] = {}
    uid_str = str(user_id)
    if uid_str not in anon_comments[key]:
        anon_comments[key][uid_str] = {"alias": alias, "messages": []}
    anon_comments[key][uid_str]["messages"].append({
        "text": text,
        "timestamp": datetime.now().isoformat(),
    })
    save_comments()
    return alias

def get_comments_for_post(post_msg_id: int) -> list[dict]:
    key = str(post_msg_id)
    if key not in anon_comments:
        return []
    result = []
    for uid_str, data in anon_comments[key].items():
        alias = data["alias"]
        for msg in data["messages"]:
            result.append({"alias": alias, "text": msg["text"], "timestamp": msg["timestamp"]})
    result.sort(key=lambda x: x["timestamp"])
    return result

# ── GROQ API ──────────────────────────────
async def _groq_request(payload, retries=2):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(retries + 1):
            try:
                r = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers, json=payload
                )
                if r.status_code == 429 and attempt < retries:
                    await asyncio.sleep(2 ** attempt); continue
                return r.json() if r.status_code == 200 else None
            except httpx.TimeoutException:
                if attempt < retries: await asyncio.sleep(1)
            except Exception as e:
                logger.error("Groq: %s", e)
                if attempt < retries: await asyncio.sleep(1)
    return None

async def call_groq_simple(prompt, system, as_json=False):
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role":"system","content":system},{"role":"user","content":prompt}],
        "temperature": 0.1 if as_json else 0.9,
        "max_tokens": 256,
    }
    if as_json: payload["response_format"] = {"type":"json_object"}
    try:
        d = await _groq_request(payload)
        return d["choices"][0]["message"]["content"] if d else None
    except Exception as e: logger.error("Groq simple: %s", e); return None

async def call_groq_with_context(uid: int, user_msg: str) -> str:
    async with GROQ_SEMAPHORE:
        history = user_ai_context.setdefault(uid, [])
        msgs = [{"role":"system","content":SYSTEM_PROMPT}, *history,
                {"role":"user","content":user_msg}]
        try:
            d = await _groq_request({"model":"llama-3.1-8b-instant","messages":msgs,
                                     "temperature":0.9,"max_tokens":1024}, retries=2)
            if not d: return "⚠️ ИИ временно недоступен, попробуй позже."
            reply = d["choices"][0]["message"]["content"]
            history += [{"role":"user","content":user_msg},{"role":"assistant","content":reply}]
            if len(history) > 6: user_ai_context[uid] = history[-6:]
            return reply
        except Exception as e:
            logger.error("Groq ctx: %s", e)
            return "⚠️ Ошибка сети."

# ── ПРОВЕРКА КОНТЕНТА ─────────────────────
async def is_content_acceptable(text: str) -> tuple[bool, str]:
    if not text or len(text.strip()) < 2: return False, "слишком короткое"
    res = await call_groq_simple(text, CONTENT_CHECK_PROMPT, as_json=True)
    if not res: return True, ""
    try:
        p  = json.loads(res.strip().removeprefix("```json").removesuffix("```").strip())
        ok = bool(p.get("acceptable", False))
        return ok, ("" if ok else p.get("reason",""))
    except: return True, ""

# ── АНИМАЦИЯ ПЕЧАТАНИЯ ────────────────────
async def typewriter_reply(update: Update, full_text: str):
    if not full_text: return
    msg       = await update.message.reply_text("▌")
    displayed = ""
    for i, ch in enumerate(full_text, 1):
        displayed += ch
        if i % UPDATE_INTERVAL == 0 or i == len(full_text):
            cursor = "" if i == len(full_text) else "▌"
            try: await msg.edit_text(displayed + cursor)
            except: pass
        await asyncio.sleep(TYPING_DELAY)

# ── ОТПРАВКА В КАНАЛ ──────────────────────
async def send_to_channel(context, update, text) -> int | None:
    """
    БАГ-ФИКС: Убрали footer из caption при отправке медиа — он вызывал
    ошибки из-за вложенных MarkdownV2 ссылок внутри caption у медиа.
    Footer теперь отправляется отдельным сообщением только для медиа.
    Для текстовых сообщений footer по-прежнему встроен.
    """
    header  = "*📩 Анонимное сообщение*"
    safe    = escape_mdv2(text) if text else ""
    footer  = ">  [✉️ Отправить анонимку](https://t.me/Shkola6_anonchik_bot)"
    caption = f"{header}\n\n{safe}" if safe else header

    msg = update.message
    bot = context.bot

    try:
        s = None
        if msg.photo:
            s = await bot.send_photo(
                CHANNEL_ID, msg.photo[-1].file_id,
                caption=f"{caption}\n\n{footer}", parse_mode="MarkdownV2"
            )
        elif msg.video:
            s = await bot.send_video(
                CHANNEL_ID, msg.video.file_id,
                caption=f"{caption}\n\n{footer}", parse_mode="MarkdownV2"
            )
        elif msg.animation:
            s = await bot.send_animation(
                CHANNEL_ID, msg.animation.file_id,
                caption=f"{caption}\n\n{footer}", parse_mode="MarkdownV2"
            )
        elif msg.audio:
            s = await bot.send_audio(
                CHANNEL_ID, msg.audio.file_id,
                caption=f"{caption}\n\n{footer}", parse_mode="MarkdownV2"
            )
        elif msg.voice:
            s = await bot.send_voice(
                CHANNEL_ID, msg.voice.file_id,
                caption=f"{caption}\n\n{footer}", parse_mode="MarkdownV2"
            )
        elif msg.document:
            s = await bot.send_document(
                CHANNEL_ID, msg.document.file_id,
                caption=f"{caption}\n\n{footer}", parse_mode="MarkdownV2"
            )
        elif msg.sticker:
            # БАГ-ФИКС: стикеры не поддерживают caption — footer отдельным сообщением
            s = await bot.send_sticker(CHANNEL_ID, msg.sticker.file_id)
            if s:
                await bot.send_message(CHANNEL_ID, footer, parse_mode="MarkdownV2")
        elif msg.text:
            s = await bot.send_message(
                CHANNEL_ID, f"{caption}\n\n{footer}", parse_mode="MarkdownV2"
            )
        else:
            return None
        return s.message_id if s else None
    except Exception as e:
        logger.error("send_to_channel error: %s", e)
        return None


# ── ПОСТ КНОПКИ АНОНИМНОГО КОММЕНТАРИЯ В ЧАТ ──
async def post_comment_invite(context, channel_msg_id: int):
    """
    Отправляет кнопку анонимного комментария в связанный чат.
    Стратегия попыток:
      1. reply_to_message_id=channel_msg_id  — ответом на авто-форвард поста
      2. message_thread_id=channel_msg_id    — в тред (если форвард уже стал тредом)
      3. Без привязки                        — просто в чат
    """
    bot_link = f"https://t.me/Shkola6_anonchik_bot?start=comment_{channel_msg_id}"
    text = "🤖 Чтобы оставить анонимный комментарий к этому посту, нажми на кнопку:"
    kb   = InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Написать анонимно", url=bot_link)
    ]])

    # Попытка 1: ответ на авто-форвард поста в чате (самый правильный способ)
    try:
        await context.bot.send_message(
            LINKED_CHAT_ID,
            text,
            reply_markup=kb,
            reply_to_message_id=channel_msg_id,
        )
        logger.info("post_comment_invite OK reply: channel_msg_id=%s", channel_msg_id)
        return
    except Exception as e:
        logger.warning("post_comment_invite reply failed (%s): %s", channel_msg_id, e)

    # Попытка 2: через message_thread_id (треды включены)
    try:
        await context.bot.send_message(
            LINKED_CHAT_ID,
            text,
            reply_markup=kb,
            message_thread_id=channel_msg_id,
        )
        logger.info("post_comment_invite OK thread: channel_msg_id=%s", channel_msg_id)
        return
    except Exception as e:
        logger.warning("post_comment_invite thread failed (%s): %s", channel_msg_id, e)

    # Попытка 3: просто в чат без привязки
    try:
        await context.bot.send_message(LINKED_CHAT_ID, text, reply_markup=kb)
        logger.info("post_comment_invite OK plain: channel_msg_id=%s", channel_msg_id)
    except Exception as e:
        logger.error("post_comment_invite totally failed (%s): %s", channel_msg_id, e)


# ── УВЕДОМЛЕНИЕ АВТОРА АНОНИМКИ ──────────
async def notify_anon_author(context, post_msg_id: int, commenter_uid: int):
    original_author_id = None
    for entry in reversed(message_logs):
        if entry.get("channel_msg_id") == post_msg_id and not entry.get("blocked"):
            original_author_id = entry.get("user_id")
            break
    if not original_author_id or original_author_id == commenter_uid:
        return
    ch = str(CHANNEL_ID).replace("-100", "")
    post_link = f"https://t.me/c/{ch}/{post_msg_id}"
    try:
        await context.bot.send_message(
            original_author_id,
            "💬 *Кто-то ответил на твою анонимку!*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("👀 Посмотреть", url=post_link)
            ]])
        )
    except Exception as e:
        logger.error("notify_anon_author: %s", e)


# ── УВЕДОМЛЕНИЕ АДМИНА ────────────────────
async def notify_admin_silent(context, update, ctype, ctext):
    u    = update.effective_user
    ustr = f"@{u.username}" if u.username else "—"
    name = f"{u.first_name or ''} {u.last_name or ''}".strip() or "—"
    safe_ustr = ustr.replace("_","\_").replace("*","\*").replace("`","\`")
    safe_name = name.replace("_","\_").replace("*","\*").replace("`","\`")
    lines = [
        "🕵️ *Новая анонимка*",
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
        f"👤 ID: `{u.id}`",
        f"🔗 Username: {safe_ustr}",
        f"📛 Имя: {safe_name}",
        f"📎 Тип: {ctype}",
    ]
    if ctext:
        safe_text = ctext[:300].replace("`", "'")
        lines.append(f"💬 Текст:\n`{safe_text}`")
    try:
        await context.bot.send_message(ADMIN_ID, "\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error("Админ-уведомление: %s", e)


# ── КЛАВИАТУРЫ ────────────────────────────
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✉️  Отправить анонимку",    callback_data="menu_anon")],
        [InlineKeyboardButton("🏆  Топ анонимщиков",       callback_data="menu_top")],
        [InlineKeyboardButton("🤖  Поболтать с ИИ",        callback_data="menu_ai")],
        [InlineKeyboardButton("❓  Помощь",                 callback_data="menu_help")],
    ])

def top_keyboard(uid):
    rows = []
    if is_in_top(uid):
        rows.append([
            InlineKeyboardButton("✏️  Сменить ник",  callback_data="top_join"),
            InlineKeyboardButton("🚪  Покинуть топ", callback_data="top_leave"),
        ])
    else:
        rows.append([InlineKeyboardButton("🏅  Вступить в топ", callback_data="top_join")])
    rows.append([
        InlineKeyboardButton("🔄  Обновить", callback_data="top_refresh"),
        InlineKeyboardButton("🔙  Назад",    callback_data="menu_back"),
    ])
    return InlineKeyboardMarkup(rows)

def ai_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠  Сбросить память", callback_data="ai_reset"),
         InlineKeyboardButton("🔙  Назад",           callback_data="menu_back")],
    ])

def after_anon_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄  Отправить ещё",  callback_data="anon_again"),
         InlineKeyboardButton("🏆  Мой топ",        callback_data="menu_top")],
        [InlineKeyboardButton("🏠  Главное меню",   callback_data="menu_back")],
    ])

def back_keyboard(cb="menu_back"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙  Назад", callback_data=cb)]])


# ── ГЛАВНОЕ МЕНЮ ──────────────────────────
MENU_TEXT = (
    "👋 Привет!\n\n"
    "Это бот анонимных сообщений школы 🏫\n"
    "Здесь ты можешь написать что угодно — никто не узнает что это ты 🤫\n\n"
    "Выбери действие 👇"
)

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    kb = main_keyboard()
    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(MENU_TEXT, reply_markup=kb)
        except:
            await context.bot.send_message(update.effective_chat.id, MENU_TEXT, reply_markup=kb)
    else:
        await update.message.reply_text(MENU_TEXT, reply_markup=kb)


# ── КОМАНДЫ ───────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u    = update.effective_user
    args = context.args

    add_start_log(u.id, u.username, u.first_name, u.last_name)
    context.user_data.clear()

    # Обработка deeplink для анонимных комментариев
    if args and args[0].startswith("comment_"):
        try:
            post_msg_id = int(args[0].split("_", 1)[1])
            context.user_data["state"]           = COMMENT_MODE
            context.user_data["comment_post_id"] = post_msg_id
            alias = get_animal_alias(u.id, post_msg_id)
            await update.message.reply_text(
                f"💬 *Анонимный комментарий*\n\n"
                f"Твой псевдоним для этого поста: *{alias}*\n\n"
                f"Отправь текст, фото, видео, голосовое, GIF или стикер 👇\n"
                f"(появится в чате анонимно)",
                parse_mode="Markdown",
                reply_markup=back_keyboard("menu_back"),
            )
            return
        except (ValueError, IndexError):
            pass

    await update.message.reply_text(
        "🌟 *Добро пожаловать!*\n\n"
        "Этот бот позволяет тебе:\n\n"
        "✉️  Отправлять анонимки в канал\n"
        "💬  Оставлять анонимные комментарии\n"
        "🏆  Участвовать в топе анонимщиков\n"
        "🤖  Общаться с ИИ\n\n"
        "Всё анонимно — никто не узнает 🔒",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    popped = (context.user_data.pop("awaiting_broadcast", None)
              or context.user_data.pop("awaiting_ids", None))
    await update.message.reply_text("✅ Отменено." if popped else "Нечего отменять.")


# ── АДМИН-ПАНЕЛЬ ──────────────────────────
def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📩  Анонимки",         callback_data="admin_tab_messages"),
         InlineKeyboardButton("💬  Комментарии",      callback_data="admin_tab_comments")],
        [InlineKeyboardButton("👥  Пользователи",     callback_data="admin_tab_starts"),
         InlineKeyboardButton("🏆  Топ",              callback_data="admin_view_top")],
        [InlineKeyboardButton("📣  Рассылка",         callback_data="admin_broadcast"),
         InlineKeyboardButton("➕  Добавить ID",      callback_data="admin_add_ids")],
        [InlineKeyboardButton("📋  Список ID",        callback_data="admin_list_ids"),
         InlineKeyboardButton("📤  Экспорт CSV",      callback_data="admin_export")],
        [InlineKeyboardButton("🧹  Удалить >7д",      callback_data="admin_clean_old")],
    ])

def admin_text():
    total_comments = sum(
        sum(len(u["messages"]) for u in post.values())
        for post in anon_comments.values()
    )
    return (
        "👑 АДМИН-ПАНЕЛЬ\n"
        "▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔\n"
        f"📩  Всего анонимок:      {len(message_logs)}\n"
        f"💬  Всего комментариев:  {total_comments}\n"
        f"👥  Всего пользователей: {len(start_logs)}\n"
        f"🏆  В топе сейчас:       {len(get_top_entries())}\n"
        f"📅  Текущая неделя:      {current_week_key()}\n"
        "▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔"
    )

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔️ Доступ запрещён.")
        return
    await update.message.reply_text(admin_text(), reply_markup=admin_keyboard())

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data  = query.data
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
        M       = ["🥇","🥈","🥉"]
        lines   = ["🏆 *Топ анонимщиков*\n"]
        if not entries:
            lines.append("Пока пусто 😶")
        else:
            for i, e in enumerate(entries[:10]):
                m = M[i] if i < 3 else f"{i+1}\\."
                lines.append(f"{m} *{e['nick']}* — {e['count']} анонимок\n`ID: {e['user_id']}`")
        await query.edit_message_text(
            "\n".join(lines), parse_mode="Markdown",
            reply_markup=back_keyboard("admin_back"))

    elif data == "admin_tab_messages":
        await show_message_logs_page(query, 0)

    elif data == "admin_tab_starts":
        await show_start_logs_page(query, 0)

    elif data == "admin_tab_comments":
        await show_comments_page(query, 0)

    elif data == "admin_export":
        await export_logs_csv(query)

    elif data == "admin_clean_old":
        await clean_old_logs(query)

    elif data == "admin_back":
        await query.edit_message_text(admin_text(), reply_markup=admin_keyboard())

    elif data.startswith("msg_page_"):
        await show_message_logs_page(query, int(data.rsplit("_",1)[-1]))

    elif data.startswith("start_page_"):
        await show_start_logs_page(query, int(data.rsplit("_",1)[-1]))

    elif data.startswith("comm_page_"):
        await show_comments_page(query, int(data.rsplit("_",1)[-1]))

    elif data == "msg_clear":
        message_logs.clear()
        _save_json(LOG_FILE, message_logs)
        await query.edit_message_text("🧹 Логи анонимок очищены.", reply_markup=admin_keyboard())

    elif data == "start_clear":
        start_logs.clear()
        _save_json(START_LOG_FILE, start_logs)
        await query.edit_message_text("🧹 Логи стартов очищены.", reply_markup=admin_keyboard())

    elif data == "comm_clear":
        anon_comments.clear()
        save_comments()
        await query.edit_message_text("🧹 Комментарии очищены.", reply_markup=admin_keyboard())

    else:
        await query.answer("Неизвестная команда.", show_alert=True)


# ── ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ ─────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # БАГ-ФИКС: игнорируем сообщения из связанного чата и канала
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id in (LINKED_CHAT_ID, CHANNEL_ID):
        return

    # БАГ-ФИКС: проверяем наличие update.message (edited_message и др. дают None)
    if not update.message:
        return

    # БАГ-ФИКС: игнорируем сообщения без пользователя (форварды от каналов и т.д.)
    if not update.effective_user:
        return

    uid = update.effective_user.id

    # --- ожидаем ник для топа ---
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
            f"📊 Твои анонимки за эту неделю уже засчитаны: *{count}*\n\n"
            f"{build_top_text()}",
            parse_mode="Markdown",
            reply_markup=top_keyboard(uid))
        return

    # --- добавление ID (админ) ---
    if context.user_data.get("awaiting_ids") and uid == ADMIN_ID:
        text    = (update.message.text or "").strip()
        new_ids = [int(x) for x in re.findall(r'\b\d+\b', text)]
        if not new_ids:
            await update.message.reply_text("Не найдено числовых ID. Попробуй снова.")
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
            await update.message.reply_text("Сообщение не может быть пустым.")
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
            except:
                failed += 1
        await update.message.reply_text(f"✅ Готово!\n📤 Отправлено: {sent}\n❌ Ошибок: {failed}")
        return

    state = context.user_data.get("state")
    if state == ANONYMOUS_MODE:
        await handle_anonymous(update, context)
    elif state == AI_CHAT_MODE:
        await handle_ai_chat(update, context)
    elif state == COMMENT_MODE:
        await handle_comment(update, context)
    else:
        await main_menu(update, context)


# ── АНОНИМКА ──────────────────────────────
async def handle_anonymous(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = update.message.text or update.message.caption or ""
    now  = datetime.now()

    last = user_last_time.get(uid)
    if last and (now - last).total_seconds() < COOLDOWN_SECONDS:
        rem  = int(COOLDOWN_SECONDS - (now - last).total_seconds())
        m, s = divmod(rem, 60)
        await update.message.reply_text(f"⏳ Подожди ещё {m}:{s:02d} перед следующей отправкой.")
        return

    # Проверяем контент только если есть текст
    if text.strip():
        ok, reason = await is_content_acceptable(text)
        if not ok:
            add_message_log({
                "user_id": uid, "username": update.effective_user.username,
                "first_name": update.effective_user.first_name,
                "last_name":  update.effective_user.last_name,
                "content_type": "текст", "text": text,
                "timestamp": now.isoformat(), "blocked": reason,
            })
            await update.message.reply_text(
                f"🚫 *Сообщение не принято*\n\nПричина: {reason}",
                parse_mode="Markdown")
            return

    mid = await send_to_channel(context, update, text)
    if mid is None:
        await update.message.reply_text("❌ Не удалось отправить. Попробуй позже.")
        return

    user_last_time[uid] = now
    msg   = update.message
    ctype = ("фото"      if msg.photo     else
             "видео"     if msg.video     else
             "GIF"       if msg.animation else
             "аудио"     if msg.audio     else
             "голосовое" if msg.voice     else
             "документ"  if msg.document  else
             "стикер"    if msg.sticker   else "текст")

    add_message_log({
        "user_id": uid, "username": update.effective_user.username,
        "first_name": update.effective_user.first_name,
        "last_name":  update.effective_user.last_name,
        "content_type": ctype, "text": text,
        "timestamp": now.isoformat(), "channel_msg_id": mid,
    })

    increment_top(uid)
    await notify_admin_silent(context, update, ctype, text)

    await post_comment_invite(context, mid)

    await update.message.reply_text(
        "✅ *Анонимка отправлена!*\n\n"
        "Твоё сообщение опубликовано в канале 🎉\n"
        "Никто не знает что это ты 🔒",
        parse_mode="Markdown",
        reply_markup=after_anon_keyboard())


# ── АНОНИМНЫЙ КОММЕНТАРИЙ ─────────────────
async def handle_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid         = update.effective_user.id
    post_msg_id = context.user_data.get("comment_post_id")
    msg         = update.message
    text        = (msg.text or msg.caption or "").strip()
    now         = datetime.now()

    if not post_msg_id:
        await msg.reply_text("❌ Пост не найден. Вернись через ссылку из чата.")
        context.user_data.pop("state", None)
        return

    has_media = bool(msg.photo or msg.video or msg.animation or
                     msg.audio or msg.voice or msg.document or msg.sticker)
    if not text and not has_media:
        await msg.reply_text("⚠️ Отправь текст, фото, видео, голосовое, GIF или стикер.")
        return

    last = user_comment_time.get(uid)
    if last and (now - last).total_seconds() < COMMENT_COOLDOWN:
        rem = int(COMMENT_COOLDOWN - (now - last).total_seconds())
        await msg.reply_text(f"⏳ Подожди ещё {rem} сек. перед следующим комментарием.")
        return

    # Проверяем контент только если есть текст
    if text:
        ok, reason = await is_content_acceptable(text)
        if not ok:
            await msg.reply_text(
                f"🚫 *Комментарий не принят*\n\nПричина: {reason}",
                parse_mode="Markdown")
            return

    alias = register_comment(post_msg_id, uid, text)
    user_comment_time[uid] = now

    alias_escaped = escape_mdv2(alias)
    text_escaped  = escape_mdv2(text) if text else ""
    if text_escaped:
        caption_mdv2  = f">{alias_escaped}\n{text_escaped}"
        caption_plain = f"{alias}\n{text}"
    else:
        caption_mdv2  = f">{alias_escaped}"
        caption_plain = alias

    bot      = context.bot
    sent_ok  = False

    # БАГ-ФИКС: пробуем с тредом, если не получилось — без треда
    async def _send_comment_to_chat(thread_id=None):
        """Отправляет комментарий в связанный чат. thread_id=None → без треда."""
        nonlocal sent_ok
        kw = {}
        if thread_id is not None:
            kw["message_thread_id"] = thread_id

        try:
            if msg.photo:
                await bot.send_photo(LINKED_CHAT_ID, msg.photo[-1].file_id,
                                     caption=caption_mdv2, parse_mode="MarkdownV2", **kw)
            elif msg.video:
                await bot.send_video(LINKED_CHAT_ID, msg.video.file_id,
                                     caption=caption_mdv2, parse_mode="MarkdownV2", **kw)
            elif msg.animation:
                await bot.send_animation(LINKED_CHAT_ID, msg.animation.file_id,
                                         caption=caption_mdv2, parse_mode="MarkdownV2", **kw)
            elif msg.audio:
                await bot.send_audio(LINKED_CHAT_ID, msg.audio.file_id,
                                     caption=caption_mdv2, parse_mode="MarkdownV2", **kw)
            elif msg.voice:
                await bot.send_voice(LINKED_CHAT_ID, msg.voice.file_id,
                                     caption=caption_mdv2, parse_mode="MarkdownV2", **kw)
            elif msg.document:
                await bot.send_document(LINKED_CHAT_ID, msg.document.file_id,
                                        caption=caption_mdv2, parse_mode="MarkdownV2", **kw)
            elif msg.sticker:
                await bot.send_sticker(LINKED_CHAT_ID, msg.sticker.file_id, **kw)
                await bot.send_message(LINKED_CHAT_ID, caption_plain, **kw)
            else:
                await bot.send_message(LINKED_CHAT_ID, caption_mdv2,
                                       parse_mode="MarkdownV2", **kw)
            sent_ok = True
        except Exception as e:
            raise e

    # Попытка 1: в тред поста
    try:
        await _send_comment_to_chat(thread_id=post_msg_id)
    except Exception as e:
        logger.warning("Комментарий в тред не удался (%s): %s — пробую без треда", post_msg_id, e)
        # Попытка 2: без треда
        try:
            await _send_comment_to_chat(thread_id=None)
        except Exception as e2:
            logger.error("Комментарий без треда тоже не удался: %s", e2)

    if not sent_ok:
        await msg.reply_text("❌ Не удалось опубликовать комментарий. Попробуй позже.")
        return

    # Уведомляем автора исходной анонимки
    await notify_anon_author(context, post_msg_id, uid)

    ctype = ("фото"      if msg.photo     else
             "видео"     if msg.video     else
             "GIF"       if msg.animation else
             "аудио"     if msg.audio     else
             "голосовое" if msg.voice     else
             "документ"  if msg.document  else
             "стикер"    if msg.sticker   else "текст")

    await msg.reply_text(
        f"✅ *Комментарий опубликован!*\n\n"
        f"Тип: {ctype}\n"
        f"Твой псевдоним: *{alias}*\n"
        f"Никто не знает что это ты 🔒\n\n"
        f"Хочешь написать ещё?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Ещё комментарий", callback_data="comment_again")],
            [InlineKeyboardButton("🏠 Главное меню",    callback_data="menu_back")],
        ]))


# ── ИИ-ЧАТ ───────────────────────────────
async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    inp = update.message.text
    if not inp:
        await update.message.reply_text("В режиме ИИ принимается только текст.")
        return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    res = await call_groq_with_context(update.effective_user.id, inp)
    await typewriter_reply(update, res or "⚠️ ИИ временно недоступен.")


# ── КНОПКИ (основной обработчик) ──────────
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    # БАГ-ФИКС: answer() в самом начале — предотвращает "часики" на кнопке
    await query.answer()

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id in (LINKED_CHAT_ID, CHANNEL_ID):
        return

    data = query.data
    uid  = update.effective_user.id

    # Делегируем админские коллбэки
    if (data.startswith("admin_") or data.startswith("msg_page_")
            or data.startswith("start_page_") or data.startswith("comm_page_")
            or data in ("msg_clear","start_clear","comm_clear")):
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
        except: pass

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
            "💬 *Анонимные комментарии*\n"
            "Под каждым постом в чате появляется кнопка.\n"
            "Нажми её — получишь рандомный псевдоним из эмодзи-животных.\n"
            "Можно отправить текст, фото, видео, голосовое, GIF или стикер.\n"
            "Псевдоним уникален для каждого поста!\n\n"
            "🏆 *Топ анонимщиков*\n"
            "Еженедельный рейтинг. Каждая анонимка = +1 к счёту.\n\n"
            "🤖 *ИИ-чат*\n"
            "Общайся с искусственным интеллектом на любую тему.\n\n"
            "/start — вернуться в главное меню",
            parse_mode="Markdown",
            reply_markup=back_keyboard("menu_back"))

    elif data == "menu_back":
        context.user_data.pop("state", None)
        context.user_data.pop("comment_post_id", None)
        await main_menu(update, context, edit=True)

    elif data == "ai_reset":
        user_ai_context.pop(uid, None)
        await query.edit_message_text(
            "🧠 Память сброшена. Начинаем с чистого листа!",
            reply_markup=ai_keyboard())

    elif data == "anon_again":
        context.user_data["state"] = ANONYMOUS_MODE
        await query.edit_message_text("✉️ Режим анонимки.\n\nОтправь следующее сообщение 👇")

    elif data == "comment_again":
        post_msg_id = context.user_data.get("comment_post_id")
        alias       = get_animal_alias(uid, post_msg_id) if post_msg_id else "?"
        await query.edit_message_text(
            f"💬 *Режим комментариев*\n\n"
            f"Твой псевдоним: *{alias}*\n\n"
            f"Напиши следующий комментарий 👇",
            parse_mode="Markdown",
            reply_markup=back_keyboard("menu_back"))

    else:
        await query.answer("Неизвестная команда. Используй /start.", show_alert=True)


# ── ЛОГИ / ПАГИНАЦИЯ ─────────────────────
def _paginate(items, page, per=5):
    total = max(1, (len(items) + per - 1) // per)
    page  = max(0, min(page, total - 1))
    return items[page*per:(page+1)*per], total

def _nav(page, total, prefix, clear_cb, end=False):
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"{prefix}{page-1}"))
    if page < total - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"{prefix}{page+1}"))
    if end and total > 1 and page < total - 1:
        nav.append(InlineKeyboardButton("⏭", callback_data=f"{prefix}{total-1}"))
    rows = []
    if nav: rows.append(nav)
    rows.append([InlineKeyboardButton("🗑  Очистить всё",   callback_data=clear_cb)])
    rows.append([InlineKeyboardButton("🔙  Назад в панель", callback_data="admin_back")])
    return InlineKeyboardMarkup(rows)

async def show_message_logs_page(query, page):
    if not message_logs:
        await query.edit_message_text(
            "📭 Нет анонимных сообщений.",
            reply_markup=_nav(0, 1, "msg_page_", "msg_clear"))
        return
    items, total = _paginate(message_logs, page)
    lines = [f"📩 Анонимки — стр. {page+1}/{total}\n▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔\n"]
    for i, e in enumerate(items, page*5+1):
        dt   = datetime.fromisoformat(e["timestamp"]).strftime("%d.%m.%Y %H:%M")
        uname = e.get("username") or ""
        fname = (e.get("first_name") or "")
        lname = (e.get("last_name") or "")
        if uname:
            ustr = f"@{uname}"
        elif fname or lname:
            ustr = f"{fname} {lname}".strip()
        else:
            ustr = "—"
        snip = (e.get("text") or "")[:60] or "—"
        blk  = e.get("blocked")
        ico  = "🚫" if blk else "✅"
        mid  = e.get("channel_msg_id")
        if mid and not blk:
            ch   = str(CHANNEL_ID).replace("-100","")
            link = f"[🔗 открыть](https://t.me/c/{ch}/{mid})"
        else:
            link = f"🚫 {blk}" if blk else "—"
        safe_ustr = ustr.replace("_", "\\_").replace("*","\\*").replace("[","\\[")
        lines.append(
            f"{ico} *{i}.* {dt}\n"
            f"👤 `{e['user_id']}` {safe_ustr}\n"
            f"📎 {e.get('content_type','текст')}: {snip}\n"
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
    items, total = _paginate(start_logs, page)
    lines = [f"👥 Пользователи — стр. {page+1}/{total}\n▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔\n"]
    for i, e in enumerate(items, page*5+1):
        dt    = datetime.fromisoformat(e["timestamp"]).strftime("%d.%m.%Y %H:%M")
        uname = e.get("username") or ""
        fname = (e.get("first_name") or "")
        lname = (e.get("last_name") or "")
        if uname:
            ustr = f"@{uname}"
        elif fname or lname:
            ustr = f"{fname} {lname}".strip()
        else:
            ustr = "—"
        safe_ustr = ustr.replace("_", "\\_").replace("*","\\*").replace("[","\\[")
        lines.append(f"*{i}.* {dt}\n👤 `{e['user_id']}` {safe_ustr}")
    await query.edit_message_text(
        "\n\n".join(lines),
        parse_mode="Markdown",
        reply_markup=_nav(page, total, "start_page_", "start_clear"))

async def show_comments_page(query, page):
    all_comments = []
    for post_id, users in anon_comments.items():
        for uid_str, data in users.items():
            alias = data["alias"]
            for msg in data["messages"]:
                all_comments.append({
                    "post_id": post_id,
                    "alias": alias,
                    "text": msg["text"],
                    "timestamp": msg["timestamp"],
                })
    all_comments.sort(key=lambda x: x["timestamp"], reverse=True)

    if not all_comments:
        await query.edit_message_text(
            "📭 Нет анонимных комментариев.",
            reply_markup=_nav(0, 1, "comm_page_", "comm_clear"))
        return

    items, total = _paginate(all_comments, page)
    lines = [f"💬 Комментарии — стр. {page+1}/{total}\n▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔\n"]
    ch = str(CHANNEL_ID).replace("-100","")
    for i, e in enumerate(items, page*5+1):
        dt   = datetime.fromisoformat(e["timestamp"]).strftime("%d.%m.%Y %H:%M")
        snip = e["text"][:80]
        link = f"[пост](https://t.me/c/{ch}/{e['post_id']})"
        lines.append(f"*{i}.* {dt}\n{e['alias']} → {link}\n💬 {snip}")
    await query.edit_message_text(
        "\n\n".join(lines),
        parse_mode="Markdown",
        reply_markup=_nav(page, total, "comm_page_", "comm_clear"),
        disable_web_page_preview=True)

async def export_logs_csv(query):
    buf = io.StringIO()
    w   = csv.DictWriter(buf, fieldnames=["user_id","username","first_name","last_name",
                                          "content_type","text","timestamp"])
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


# ── ОБРАБОТЧИК ПОСТОВ ИЗ КАНАЛА ──────────
# БАГ-ФИКС: этот хендлер срабатывает на ВСЕ посты канала,
# в т.ч. опубликованные вручную — кнопка комментария появится под каждым.
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post:
        return
    if post.chat.id != CHANNEL_ID:
        return
    channel_msg_id = post.message_id
    logger.info("Новый пост в канале: msg_id=%s", channel_msg_id)
    await post_comment_invite(context, channel_msg_id)


# ── ТОЧКА ВХОДА ───────────────────────────
def main():
    load_all_logs()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("admin",  cmd_admin))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(button_callback))

    # БАГ-ФИКС: хендлер постов канала должен идти ДО общего handle_message
    # чтобы посты из канала не попадали в handle_message
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))

    # Обрабатываем только личные сообщения (не из канала и не из группы)
    # БАГ-ФИКС: добавлен фильтр ~filters.ChatType.CHANNEL & ~filters.UpdateType.CHANNEL_POST
    app.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND & ~filters.ChatType.CHANNEL,
        handle_message
    ))

    logger.info("✅ Бот запущен!")
    # allowed_updates=Update.ALL_TYPES нужен чтобы получать channel_post апдейты
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
