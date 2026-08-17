from __future__ import annotations

import math
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator


@dataclass(slots=True)
class UserStats:
    total_users: int
    new_today: int
    last_users: list[dict[str, str | int | None]]


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.execute("PRAGMA busy_timeout = 5000;")
        try:
            yield connection
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL;")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    is_bot INTEGER NOT NULL DEFAULT 0,
                    balance_rub REAL NOT NULL DEFAULT 0,
                    active_discount_percent REAL NOT NULL DEFAULT 0,
                    active_discount_code TEXT,
                    referred_by INTEGER,
                    referral_earned_rub REAL NOT NULL DEFAULT 0,
                    free_proxy_credits INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS invoices (
                    invoice_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    purpose TEXT NOT NULL,
                    amount_rub REAL NOT NULL,
                    status TEXT NOT NULL,
                    server_code TEXT,
                    proxy_id TEXT,
                    proxy_version TEXT,
                    proxy_country TEXT,
                    proxy_period INTEGER,
                    pay_url TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS purchased_proxies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    server_code TEXT NOT NULL,
                    proxy_id TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port TEXT NOT NULL,
                    login TEXT NOT NULL,
                    password TEXT NOT NULL,
                    purchased_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS promocodes (
                    code TEXT PRIMARY KEY,
                    reward_type TEXT NOT NULL,
                    reward_value REAL NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    max_uses INTEGER NOT NULL DEFAULT 0,
                    used_count INTEGER NOT NULL DEFAULT 0,
                    created_by INTEGER,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS promocode_redemptions (
                    code TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    redeemed_at TEXT NOT NULL,
                    PRIMARY KEY (code, user_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS blocked_users (
                    user_id INTEGER PRIMARY KEY,
                    reason TEXT,
                    blocked_by INTEGER,
                    blocked_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS proxy_pool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_code TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port TEXT NOT NULL,
                    login TEXT NOT NULL DEFAULT '',
                    password TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'available',
                    assigned_to INTEGER,
                    assigned_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS system_cache (
                    cache_key TEXT PRIMARY KEY,
                    cache_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(connection, "users", "balance_rub", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(connection, "users", "active_discount_percent", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(connection, "users", "active_discount_code", "TEXT")
            self._ensure_column(connection, "users", "referred_by", "INTEGER")
            self._ensure_column(connection, "users", "referral_earned_rub", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(connection, "users", "free_proxy_credits", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "invoices", "proxy_version", "TEXT")
            self._ensure_column(connection, "invoices", "proxy_country", "TEXT")
            self._ensure_column(connection, "invoices", "proxy_period", "INTEGER")
            self._ensure_column(
                connection, "invoices", "provider", "TEXT NOT NULL DEFAULT 'cryptobot'"
            )
            self._ensure_column(connection, "promocodes", "max_uses", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "promocodes", "used_count", "INTEGER NOT NULL DEFAULT 0")

            # Indexes for performance and quick lookups
            connection.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by);")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at);")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_invoices_user_id ON invoices(user_id);")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_purchased_proxies_user_id ON purchased_proxies(user_id);")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_blocked_users_blocked_at ON blocked_users(blocked_at);")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_promocodes_created_at ON promocodes(created_at);")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_proxy_pool_server_status ON proxy_pool(server_code, status);")

            connection.commit()

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        allowed_tables = {
            "users",
            "invoices",
            "purchased_proxies",
            "promocodes",
            "promocode_redemptions",
            "blocked_users",
            "proxy_pool",
        }
        if table_name not in allowed_tables:
            return
        columns = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing = {row["name"] for row in columns}
        if column_name not in existing:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def upsert_user(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        is_bot: bool = False,
    ) -> None:
        if not isinstance(user_id, int):
            try:
                user_id = int(user_id)
            except (ValueError, TypeError):
                return
        username_clean = str(username)[:64] if username is not None else None
        first_name_clean = str(first_name)[:64] if first_name is not None else None
        last_name_clean = str(last_name)[:64] if last_name is not None else None
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO users (
                    user_id, username, first_name, last_name, is_bot, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    is_bot = excluded.is_bot,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    username_clean,
                    first_name_clean,
                    last_name_clean,
                    int(is_bot),
                    now,
                    now,
                ),
            )
            connection.commit()

    def get_user_stats(self) -> UserStats:
        today_prefix = datetime.now(timezone.utc).date().isoformat()
        with self._connect() as connection:
            total_users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            new_today = connection.execute(
                "SELECT COUNT(*) FROM users WHERE substr(created_at, 1, 10) = ?",
                (today_prefix,),
            ).fetchone()[0]
            rows = connection.execute(
                """
                SELECT user_id, username, first_name, last_name, created_at
                FROM users
                ORDER BY created_at DESC
                LIMIT 10
                """
            ).fetchall()

        last_users = [
            {
                "user_id": row["user_id"],
                "username": row["username"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        return UserStats(
            total_users=total_users,
            new_today=new_today,
            last_users=last_users,
        )

    def get_user_balance(self, user_id: int) -> float:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT balance_rub FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return 0.0
        return round(float(row["balance_rub"] or 0.0), 2)

    def get_user(self, user_id: int) -> dict[str, str | int | float | None] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def add_user_balance(self, user_id: int, amount_rub: float) -> bool:
        if not isinstance(amount_rub, (int, float)) or not math.isfinite(amount_rub) or amount_rub <= 0:
            return False
        clean_amount = round(float(amount_rub), 2)
        if clean_amount <= 0:
            return False
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE users SET balance_rub = round(balance_rub + ?, 2), updated_at = ? WHERE user_id = ?",
                (clean_amount, datetime.now(timezone.utc).isoformat(), user_id),
            )
            connection.commit()
            return cursor.rowcount > 0

    def set_user_discount(self, user_id: int, discount_percent: float, promo_code: str | None) -> None:
        clean_percent = 0.0
        if isinstance(discount_percent, (int, float)) and math.isfinite(discount_percent):
            clean_percent = max(0.0, min(100.0, float(discount_percent)))
        promo_code_clean = str(promo_code)[:64] if promo_code is not None else None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE users
                SET active_discount_percent = ?, active_discount_code = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (
                    clean_percent,
                    promo_code_clean,
                    datetime.now(timezone.utc).isoformat(),
                    user_id,
                ),
            )
            connection.commit()

    def clear_user_discount(self, user_id: int) -> None:
        self.set_user_discount(user_id, 0.0, None)

    def subtract_user_balance(self, user_id: int, amount_rub: float) -> bool:
        """Атомарно списывает баланс пользователя.
        
        Защищает от состояния гонки (Race Condition / Double Spending)
        за счёт проверки `balance_rub >= ?` прямо в атомарном SQL UPDATE.
        """
        if not isinstance(amount_rub, (int, float)) or not math.isfinite(amount_rub) or amount_rub <= 0:
            return False
        clean_amount = round(float(amount_rub), 2)
        if clean_amount <= 0:
            return False
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE users
                SET balance_rub = round(balance_rub - ?, 2), updated_at = ?
                WHERE user_id = ? AND balance_rub >= ?
                """,
                (clean_amount, datetime.now(timezone.utc).isoformat(), user_id, clean_amount),
            )
            connection.commit()
            return cursor.rowcount > 0

    # --- Реферальная система ---

    def set_referrer(self, user_id: int, referrer_id: int) -> bool:
        """Привязывает реферера к пользователю атомарно один раз.

        Возвращает True, если привязка выполнена. Нельзя пригласить самого себя,
        нельзя перепривязать уже приглашённого, реферер должен существовать.
        """
        if user_id == referrer_id:
            return False
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE users
                SET referred_by = ?, updated_at = ?
                WHERE user_id = ?
                  AND referred_by IS NULL
                  AND EXISTS (SELECT 1 FROM users WHERE user_id = ?)
                """,
                (referrer_id, datetime.now(timezone.utc).isoformat(), user_id, referrer_id),
            )
            connection.commit()
            return cursor.rowcount > 0

    def get_referrer_id(self, user_id: int) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT referred_by FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None or row["referred_by"] is None:
            return None
        return int(row["referred_by"])

    def add_referral_earning(self, referrer_id: int, amount_rub: float) -> bool:
        """Начисляет рефереру бонус на баланс и копит суммарный заработок."""
        if not isinstance(amount_rub, (int, float)) or not math.isfinite(amount_rub) or amount_rub <= 0:
            return False
        clean_amount = round(float(amount_rub), 2)
        if clean_amount <= 0:
            return False
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE users
                SET balance_rub = round(balance_rub + ?, 2),
                    referral_earned_rub = round(referral_earned_rub + ?, 2),
                    updated_at = ?
                WHERE user_id = ?
                """,
                (clean_amount, clean_amount, datetime.now(timezone.utc).isoformat(), referrer_id),
            )
            connection.commit()
            return cursor.rowcount > 0

    def get_referral_stats(self, user_id: int) -> dict[str, float | int]:
        with self._connect() as connection:
            invited = connection.execute(
                "SELECT COUNT(*) FROM users WHERE referred_by = ?",
                (user_id,),
            ).fetchone()[0]
            row = connection.execute(
                "SELECT referral_earned_rub FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        earned = round(float(row["referral_earned_rub"] or 0.0), 2) if row else 0.0
        return {"invited": int(invited), "earned_rub": earned}

    # --- Фри-прокси кредиты ---

    def get_free_proxy_credits(self, user_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT free_proxy_credits FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return 0
        return max(0, int(row["free_proxy_credits"] or 0))

    def add_free_proxy_credits(self, user_id: int, amount: int) -> bool:
        if not isinstance(amount, int) or amount <= 0:
            try:
                amount = int(amount)
                if amount <= 0:
                    return False
            except (ValueError, TypeError):
                return False
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE users SET free_proxy_credits = free_proxy_credits + ?, updated_at = ? WHERE user_id = ?",
                (amount, datetime.now(timezone.utc).isoformat(), user_id),
            )
            connection.commit()
            return cursor.rowcount > 0

    def use_free_proxy_credit(self, user_id: int) -> bool:
        """Списывает один фри-прокси кредит атомарно. True при успехе."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE users
                SET free_proxy_credits = free_proxy_credits - 1, updated_at = ?
                WHERE user_id = ? AND free_proxy_credits > 0
                """,
                (datetime.now(timezone.utc).isoformat(), user_id),
            )
            connection.commit()
            return cursor.rowcount > 0

    # --- Блокировки пользователей ---

    def block_user(self, user_id: int, reason: str | None, blocked_by: int | None) -> None:
        reason_clean = str(reason)[:256] if reason is not None else None
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO blocked_users (user_id, reason, blocked_by, blocked_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    reason = excluded.reason,
                    blocked_by = excluded.blocked_by,
                    blocked_at = excluded.blocked_at
                """,
                (user_id, reason_clean, blocked_by, datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()

    def unblock_user(self, user_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM blocked_users WHERE user_id = ?",
                (user_id,),
            )
            connection.commit()
            return cursor.rowcount > 0

    def is_blocked(self, user_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM blocked_users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row is not None

    def list_blocked_users(self, limit: int = 50) -> list[dict[str, str | int | None]]:
        limit = max(1, min(500, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT b.user_id, b.reason, b.blocked_at, u.username, u.first_name, u.last_name
                FROM blocked_users b
                LEFT JOIN users u ON u.user_id = b.user_id
                ORDER BY b.blocked_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def find_user_id_by_username(self, username: str) -> int | None:
        """Ищет user_id по @username среди тех, кто уже писал боту."""
        normalized = username.lstrip("@").strip().lower()
        if not normalized:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id FROM users WHERE lower(username) = ?",
                (normalized,),
            ).fetchone()
        return int(row["user_id"]) if row else None

    def get_all_active_user_ids(self) -> list[int]:
        """Возвращает список всех незаблокированных пользователей (для рассылки)."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id FROM users
                WHERE user_id NOT IN (SELECT user_id FROM blocked_users)
                  AND is_bot = 0
                ORDER BY user_id ASC
                """
            ).fetchall()
        return [int(row["user_id"]) for row in rows]

    def save_invoice(
        self,
        invoice_id: str,
        user_id: int,
        purpose: str,
        amount_rub: float,
        status: str,
        server_code: str | None,
        proxy_id: str | None,
        proxy_version: str | None,
        proxy_country: str | None,
        proxy_period: int | None,
        pay_url: str | None,
        provider: str = "cryptobot",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        clean_amount = round(float(amount_rub), 2) if isinstance(amount_rub, (int, float)) and math.isfinite(amount_rub) else 0.0
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO invoices (
                    invoice_id, user_id, purpose, amount_rub, status, server_code, proxy_id,
                    proxy_version, proxy_country, proxy_period, pay_url, provider,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(invoice_id) DO UPDATE SET
                    status = excluded.status,
                    pay_url = excluded.pay_url,
                    provider = excluded.provider,
                    updated_at = excluded.updated_at
                """,
                (
                    str(invoice_id)[:128],
                    int(user_id),
                    str(purpose)[:64],
                    clean_amount,
                    str(status)[:64],
                    str(server_code)[:64] if server_code else None,
                    str(proxy_id)[:64] if proxy_id else None,
                    str(proxy_version)[:64] if proxy_version else None,
                    str(proxy_country)[:64] if proxy_country else None,
                    int(proxy_period) if proxy_period is not None else None,
                    str(pay_url)[:512] if pay_url else None,
                    str(provider)[:64],
                    now,
                    now,
                ),
            )
            connection.commit()

    def get_invoice(self, invoice_id: str) -> dict[str, str | int | float | None] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM invoices WHERE invoice_id = ?",
                (str(invoice_id),),
            ).fetchone()
        return dict(row) if row else None

    def update_invoice_status(self, invoice_id: str, status: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE invoices SET status = ?, updated_at = ? WHERE invoice_id = ?",
                (str(status)[:64], datetime.now(timezone.utc).isoformat(), str(invoice_id)),
            )
            connection.commit()
            return cursor.rowcount > 0

    def mark_invoice_paid(self, invoice_id: str) -> bool:
        """Атомарно переводит счёт в статус 'paid', если он ещё не был оплачен.

        Возвращает True только при первом успешном переводе в 'paid'.
        Защищает от дублирования начислений и выдачи прокси при спам-кликах.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE invoices SET status = 'paid', updated_at = ? WHERE invoice_id = ? AND status != 'paid'",
                (datetime.now(timezone.utc).isoformat(), str(invoice_id)),
            )
            connection.commit()
            return cursor.rowcount > 0

    def add_purchased_proxy(
        self,
        user_id: int,
        server_code: str,
        proxy_id: str,
        host: str,
        port: str,
        login: str,
        password: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO purchased_proxies (
                    user_id, server_code, proxy_id, host, port, login, password, purchased_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    str(server_code)[:64],
                    str(proxy_id)[:64],
                    str(host)[:128],
                    str(port)[:32],
                    str(login)[:128],
                    str(password)[:128],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()

    def get_purchased_proxies(self, user_id: int) -> list[dict[str, str | int | float | None]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id, server_code, proxy_id, host, port, login, password, purchased_at
                FROM purchased_proxies
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (int(user_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    # --- Промокоды ---

    def create_promocode(
        self,
        code: str,
        reward_type: str,
        reward_value: float,
        created_by: int | None,
        max_uses: int = 0,
    ) -> bool:
        normalized_code = code.strip().upper()
        if not normalized_code or len(normalized_code) > 64:
            return False
        if not re.match(r"^[A-Z0-9_-]+$", normalized_code):
            return False
        if reward_type not in {"balance", "discount", "free_proxy"}:
            return False
        if not isinstance(reward_value, (int, float)) or not math.isfinite(reward_value) or reward_value <= 0:
            return False
        clean_reward = round(float(reward_value), 2)
        if reward_type == "discount" and clean_reward >= 100:
            return False
        clean_max_uses = max(0, int(max_uses)) if isinstance(max_uses, (int, float)) and math.isfinite(max_uses) else 0

        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO promocodes (
                        code, reward_type, reward_value, is_active, max_uses, used_count, created_by, created_at
                    )
                    VALUES (?, ?, ?, 1, ?, 0, ?, ?)
                    """,
                    (normalized_code, reward_type, clean_reward, clean_max_uses, created_by, now),
                )
                connection.commit()
        except sqlite3.IntegrityError:
            return False
        return True

    def get_promocode(self, code: str) -> dict[str, str | int | float | None] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM promocodes WHERE code = ?",
                (code.strip().upper(),),
            ).fetchone()
        return dict(row) if row else None

    def get_recent_promocodes(self, limit: int = 10) -> list[dict[str, str | int | float | None]]:
        limit = max(1, min(100, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    p.code,
                    p.reward_type,
                    p.reward_value,
                    p.is_active,
                    p.max_uses,
                    p.used_count,
                    p.created_at,
                    COUNT(r.user_id) AS redemptions
                FROM promocodes p
                LEFT JOIN promocode_redemptions r ON r.code = p.code
                GROUP BY p.code, p.reward_type, p.reward_value, p.is_active, p.max_uses, p.used_count, p.created_at
                ORDER BY p.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def has_user_redeemed_promocode(self, user_id: int, code: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM promocode_redemptions WHERE code = ? AND user_id = ?",
                (code.strip().upper(), int(user_id)),
            ).fetchone()
        return row is not None

    def redeem_promocode(self, user_id: int, code: str) -> tuple[bool, str, dict[str, str | int | float | None] | None]:
        """Атомарно применяет промокод к пользователю.
        
        Полностью защищено от гонок (Race condition):
        1. Проверка и атомарное инкрементирование `used_count` при соблюдении `max_uses`.
        2. Защита от повторного использования через PRIMARY KEY (code, user_id).
        3. Откат транзакции при любых ошибках.
        """
        normalized_code = code.strip().upper()
        if not normalized_code:
            return False, "Промокод не указан.", None

        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as connection:
            # 1. Проверяем существование пользователя
            user_row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if user_row is None:
                return False, "Пользователь не найден.", None

            # 2. Проверяем, не активировал ли уже этот пользователь данный промокод
            already_redeemed = connection.execute(
                "SELECT 1 FROM promocode_redemptions WHERE code = ? AND user_id = ?",
                (normalized_code, user_id),
            ).fetchone()
            if already_redeemed is not None:
                promo = connection.execute("SELECT * FROM promocodes WHERE code = ?", (normalized_code,)).fetchone()
                return False, "Вы уже использовали этот промокод.", dict(promo) if promo else None

            # 3. Атомарно инкрементируем счётчик использования только если промокод активен и лимит не исчерпан
            cursor = connection.execute(
                """
                UPDATE promocodes
                SET used_count = used_count + 1,
                    is_active = CASE WHEN max_uses > 0 AND used_count + 1 >= max_uses THEN 0 ELSE is_active END
                WHERE code = ?
                  AND is_active = 1
                  AND (max_uses = 0 OR used_count < max_uses)
                """,
                (normalized_code,),
            )
            if cursor.rowcount == 0:
                promo = connection.execute("SELECT * FROM promocodes WHERE code = ?", (normalized_code,)).fetchone()
                if promo is None:
                    return False, "Промокод не найден.", None
                if int(promo["is_active"] or 0) != 1:
                    return False, "Промокод отключен.", dict(promo)
                return False, "Лимит активаций промокода исчерпан.", dict(promo)

            # Получаем актуальные данные промокода
            promo = connection.execute("SELECT * FROM promocodes WHERE code = ?", (normalized_code,)).fetchone()
            promo_dict = dict(promo)
            reward_type = str(promo_dict["reward_type"] or "")
            reward_value = float(promo_dict["reward_value"] or 0.0)

            # 4. Проверяем бизнес-правила применения награды
            if reward_type == "discount":
                current_discount = float(user_row["active_discount_percent"] or 0.0)
                if current_discount > 0:
                    connection.rollback()
                    return False, "Сначала используйте уже активированную скидку.", promo_dict

            # 5. Фиксируем погашение промокода
            try:
                connection.execute(
                    """
                    INSERT INTO promocode_redemptions (code, user_id, redeemed_at)
                    VALUES (?, ?, ?)
                    """,
                    (normalized_code, user_id, now),
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                return False, "Вы уже использовали этот промокод.", promo_dict

            # 6. Начисляем награду пользователю
            if reward_type == "balance":
                connection.execute(
                    """
                    UPDATE users
                    SET balance_rub = round(balance_rub + ?, 2), updated_at = ?
                    WHERE user_id = ?
                    """,
                    (reward_value, now, user_id),
                )
            elif reward_type == "discount":
                connection.execute(
                    """
                    UPDATE users
                    SET active_discount_percent = ?, active_discount_code = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (reward_value, normalized_code, now, user_id),
                )
            elif reward_type == "free_proxy":
                credits = max(1, int(reward_value)) if reward_value else 1
                connection.execute(
                    """
                    UPDATE users
                    SET free_proxy_credits = free_proxy_credits + ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (credits, now, user_id),
                )
            else:
                connection.rollback()
                return False, "У промокода неверный тип.", promo_dict

            connection.commit()

        return True, "ok", promo_dict

    # --- Пул прокси (Локальный пул для ручного добавления и выдачи) ---

    def add_proxy_to_pool(
        self,
        server_code: str,
        host: str,
        port: str,
        login: str = "",
        password: str = "",
    ) -> int | None:
        """Добавляет единичный прокси в локальный пул."""
        server_code_clean = str(server_code).strip().lower()[:64]
        host_clean = str(host).strip()[:128]
        port_clean = str(port).strip()[:32]
        login_clean = str(login).strip()[:128]
        password_clean = str(password).strip()[:128]

        if not host_clean or not port_clean:
            return None

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO proxy_pool (
                    server_code, host, port, login, password, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, 'available', ?)
                """,
                (server_code_clean, host_clean, port_clean, login_clean, password_clean, now),
            )
            connection.commit()
            return cursor.lastrowid

    def add_proxies_bulk(self, server_code: str, proxy_lines: list[str]) -> tuple[int, int]:
        """Пакетное добавление прокси из строк формата host:port:user:pass или host:port."""
        added = 0
        failed = 0
        now = datetime.now(timezone.utc).isoformat()
        server_code_clean = str(server_code).strip().lower()[:64]

        records: list[tuple[str, str, str, str, str, str]] = []
        for raw_line in proxy_lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) >= 4:
                host, port, user, pwd = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
            elif len(parts) >= 2:
                host, port, user, pwd = parts[0].strip(), parts[1].strip(), "", ""
            else:
                failed += 1
                continue

            if not host or not port:
                failed += 1
                continue
            records.append((server_code_clean, host[:128], port[:32], user[:128], pwd[:128], now))

        if records:
            with self._connect() as connection:
                cursor = connection.executemany(
                    """
                    INSERT INTO proxy_pool (
                        server_code, host, port, login, password, status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'available', ?)
                    """,
                    records,
                )
                connection.commit()
                added = cursor.rowcount if cursor.rowcount > 0 else len(records)

        return added, failed

    def remove_proxy_from_pool(self, proxy_id: int) -> bool:
        """Удаляет прокси из пула по ID."""
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM proxy_pool WHERE id = ?",
                (int(proxy_id),),
            )
            connection.commit()
            return cursor.rowcount > 0

    def get_available_proxy_from_pool(self, server_code: str) -> dict[str, Any] | None:
        """Возвращает один свободный прокси из пула для сервера."""
        server_code_clean = str(server_code).strip().lower()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM proxy_pool
                WHERE server_code = ? AND status = 'available'
                ORDER BY id ASC
                LIMIT 1
                """,
                (server_code_clean,),
            ).fetchone()
        return dict(row) if row else None

    def assign_pool_proxy(self, proxy_id: int, user_id: int) -> bool:
        """Атомарно переводит прокси из статуса available в assigned на user_id."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE proxy_pool
                SET status = 'assigned', assigned_to = ?, assigned_at = ?
                WHERE id = ? AND status = 'available'
                """,
                (int(user_id), now, int(proxy_id)),
            )
            connection.commit()
            return cursor.rowcount > 0

    def list_pool_proxies(
        self,
        server_code: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        query = "SELECT * FROM proxy_pool WHERE 1=1"
        params: list[Any] = []
        if server_code:
            query += " AND server_code = ?"
            params.append(str(server_code).strip().lower())
        if status:
            query += " AND status = ?"
            params.append(str(status).strip())
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def get_pool_stats(self) -> dict[str, Any]:
        """Возвращает общую статистику пула прокси."""
        with self._connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM proxy_pool").fetchone()[0]
            available = connection.execute("SELECT COUNT(*) FROM proxy_pool WHERE status = 'available'").fetchone()[0]
            assigned = connection.execute("SELECT COUNT(*) FROM proxy_pool WHERE status = 'assigned'").fetchone()[0]
            by_server_rows = connection.execute(
                """
                SELECT server_code,
                       COUNT(*) as total,
                       SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) as available
                FROM proxy_pool
                GROUP BY server_code
                """
            ).fetchall()

        by_server = {
            str(row["server_code"]): {
                "total": int(row["total"]),
                "available": int(row["available"] or 0),
            }
            for row in by_server_rows
        }
        return {
            "total": int(total),
            "available": int(available),
            "assigned": int(assigned),
            "by_server": by_server,
        }

    def get_cached_value(self, key: str) -> str | None:
        """Получает закэшированное строковое значение (например file_id фото)."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cache_value FROM system_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
        return str(row["cache_value"]) if row else None

    def set_cached_value(self, key: str, value: str) -> None:
        """Сохраняет значение в постоянный системный кэш SQLite."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO system_cache (cache_key, cache_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    cache_value = excluded.cache_value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
            connection.commit()
