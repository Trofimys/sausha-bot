from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class UserStats:
    total_users: int
    new_today: int
    last_users: list[dict[str, str | int | None]]


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
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
            connection.commit()

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        columns = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing = {row["name"] for row in columns}
        if column_name not in existing:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def upsert_user(
        self,
        user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        is_bot: bool,
    ) -> None:
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
                    username,
                    first_name,
                    last_name,
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
        return float(row["balance_rub"] or 0.0)

    def get_user(self, user_id: int) -> dict[str, str | int | float | None] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def add_user_balance(self, user_id: int, amount_rub: float) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET balance_rub = balance_rub + ?, updated_at = ? WHERE user_id = ?",
                (amount_rub, datetime.now(timezone.utc).isoformat(), user_id),
            )
            connection.commit()

    def set_user_discount(self, user_id: int, discount_percent: float, promo_code: str | None) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE users
                SET active_discount_percent = ?, active_discount_code = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (
                    max(0.0, float(discount_percent)),
                    promo_code,
                    datetime.now(timezone.utc).isoformat(),
                    user_id,
                ),
            )
            connection.commit()

    def clear_user_discount(self, user_id: int) -> None:
        self.set_user_discount(user_id, 0.0, None)

    def subtract_user_balance(self, user_id: int, amount_rub: float) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT balance_rub FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            current_balance = float(row["balance_rub"] or 0.0) if row else 0.0
            if current_balance < amount_rub:
                return False
            connection.execute(
                "UPDATE users SET balance_rub = balance_rub - ?, updated_at = ? WHERE user_id = ?",
                (amount_rub, datetime.now(timezone.utc).isoformat(), user_id),
            )
            connection.commit()
            return True

    # --- Реферальная система ---

    def set_referrer(self, user_id: int, referrer_id: int) -> bool:
        """Привязывает реферера к пользователю один раз.

        Возвращает True, если привязка выполнена. Нельзя пригласить самого себя,
        нельзя перепривязать уже приглашённого, реферер должен существовать.
        """
        if user_id == referrer_id:
            return False
        with self._connect() as connection:
            user_row = connection.execute(
                "SELECT referred_by FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if user_row is None or user_row["referred_by"] is not None:
                return False
            referrer_row = connection.execute(
                "SELECT 1 FROM users WHERE user_id = ?",
                (referrer_id,),
            ).fetchone()
            if referrer_row is None:
                return False
            connection.execute(
                "UPDATE users SET referred_by = ?, updated_at = ? WHERE user_id = ?",
                (referrer_id, datetime.now(timezone.utc).isoformat(), user_id),
            )
            connection.commit()
        return True

    def get_referrer_id(self, user_id: int) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT referred_by FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None or row["referred_by"] is None:
            return None
        return int(row["referred_by"])

    def add_referral_earning(self, referrer_id: int, amount_rub: float) -> None:
        """Начисляет рефереру бонус на баланс и копит суммарный заработок."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE users
                SET balance_rub = balance_rub + ?,
                    referral_earned_rub = referral_earned_rub + ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (amount_rub, amount_rub, datetime.now(timezone.utc).isoformat(), referrer_id),
            )
            connection.commit()

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
        earned = float(row["referral_earned_rub"] or 0.0) if row else 0.0
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
        return int(row["free_proxy_credits"] or 0)

    def add_free_proxy_credits(self, user_id: int, amount: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET free_proxy_credits = free_proxy_credits + ?, updated_at = ? WHERE user_id = ?",
                (int(amount), datetime.now(timezone.utc).isoformat(), user_id),
            )
            connection.commit()

    def use_free_proxy_credit(self, user_id: int) -> bool:
        """Списывает один фри-прокси кредит атомарно. True при успехе."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT free_proxy_credits FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            credits = int(row["free_proxy_credits"] or 0) if row else 0
            if credits <= 0:
                return False
            connection.execute(
                "UPDATE users SET free_proxy_credits = free_proxy_credits - 1, updated_at = ? WHERE user_id = ?",
                (datetime.now(timezone.utc).isoformat(), user_id),
            )
            connection.commit()
        return True

    # --- Блокировки пользователей ---

    def block_user(self, user_id: int, reason: str | None, blocked_by: int | None) -> None:
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
                (user_id, reason, blocked_by, datetime.now(timezone.utc).isoformat()),
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
                    invoice_id,
                    user_id,
                    purpose,
                    amount_rub,
                    status,
                    server_code,
                    proxy_id,
                    proxy_version,
                    proxy_country,
                    proxy_period,
                    pay_url,
                    provider,
                    now,
                    now,
                ),
            )
            connection.commit()

    def get_invoice(self, invoice_id: str) -> dict[str, str | int | float | None] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM invoices WHERE invoice_id = ?",
                (invoice_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_invoice_status(self, invoice_id: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE invoices SET status = ?, updated_at = ? WHERE invoice_id = ?",
                (status, datetime.now(timezone.utc).isoformat(), invoice_id),
            )
            connection.commit()

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
                    user_id,
                    server_code,
                    proxy_id,
                    host,
                    port,
                    login,
                    password,
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
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_promocode(
        self,
        code: str,
        reward_type: str,
        reward_value: float,
        created_by: int | None,
        max_uses: int = 0,
    ) -> bool:
        normalized_code = code.strip().upper()
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
                    (normalized_code, reward_type, reward_value, max(0, int(max_uses)), created_by, now),
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
                (code.strip().upper(), user_id),
            ).fetchone()
        return row is not None

    def redeem_promocode(self, user_id: int, code: str) -> tuple[bool, str, dict[str, str | int | float | None] | None]:
        normalized_code = code.strip().upper()
        promo = self.get_promocode(normalized_code)
        if promo is None:
            return False, "Промокод не найден.", None
        if int(promo["is_active"] or 0) != 1:
            return False, "Промокод отключен.", promo
        max_uses = int(promo["max_uses"] or 0)
        used_count = int(promo["used_count"] or 0)
        if max_uses > 0 and used_count >= max_uses:
            return False, "Лимит активаций промокода исчерпан.", promo
        if self.has_user_redeemed_promocode(user_id, normalized_code):
            return False, "Вы уже использовали этот промокод.", promo

        user = self.get_user(user_id)
        if user is None:
            return False, "Пользователь не найден.", promo

        reward_type = str(promo["reward_type"] or "")
        reward_value = float(promo["reward_value"] or 0.0)
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as connection:
            if reward_type == "balance":
                connection.execute(
                    """
                    UPDATE users
                    SET balance_rub = balance_rub + ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (reward_value, now, user_id),
                )
            elif reward_type == "discount":
                current_discount = float(user["active_discount_percent"] or 0.0)
                if current_discount > 0:
                    return False, "Сначала используйте уже активированную скидку.", promo
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
                return False, "У промокода неверный тип.", promo

            connection.execute(
                """
                INSERT INTO promocode_redemptions (code, user_id, redeemed_at)
                VALUES (?, ?, ?)
                """,
                (normalized_code, user_id, now),
            )
            connection.execute(
                "UPDATE promocodes SET used_count = used_count + 1 WHERE code = ?",
                (normalized_code,),
            )
            connection.execute(
                """
                UPDATE promocodes SET is_active = 0
                WHERE code = ? AND max_uses > 0 AND used_count >= max_uses
                """,
                (normalized_code,),
            )
            connection.commit()

        return True, "ok", promo
