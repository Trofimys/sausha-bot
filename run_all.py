"""Супервизор для запуска обоих ботов одним процессом на Render.

Каждый бот стартует ОТДЕЛЬНЫМ подпроцессом в своей рабочей папке, чтобы
пакет прокси-бота `bot/` и файл сауша-бота `bot.py` не конфликтовали по имени
`bot` в одном интерпретаторе. Супервизор:

  • держит HTTP-эндпоинт на $PORT (нужно Render Web Service для healthcheck);
  • перезапускает упавший бот с нарастающей задержкой;
  • пробрасывает переменные окружения обоим.

Локально: `python run_all.py`. На Render: команда запуска `python run_all.py`.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SAUSHA_DIR = BASE_DIR / "sausha-bot-main"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | SUPERVISOR | %(levelname)s | %(message)s",
)
logger = logging.getLogger("supervisor")


def load_root_env() -> None:
    """Подхватывает корневой .env в os.environ, чтобы оба подпроцесса его видели.

    На Render переменные заданы в дашборде — файла может не быть, это нормально.
    Существующие переменные окружения имеют приоритет (не перетираем дашборд).
    """
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args: object) -> None:  # тишина в логах
        pass


def start_healthcheck_server() -> None:
    """HTTP на $PORT — Render Web Service считает сервис живым, пока порт слушается."""
    port = int(os.environ.get("PORT", "10000"))
    try:
        server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    except OSError as exc:
        logger.warning("Не удалось открыть healthcheck-порт %s: %s", port, exc)
        return
    logger.info("Healthcheck слушает :%s", port)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def supervise(name: str, args: list[str], cwd: Path) -> None:
    """Держит подпроцесс живым: перезапускает при падении с backoff 5→60 сек."""
    delay = 5
    while True:
        logger.info("[%s] запуск: %s (cwd=%s)", name, " ".join(args), cwd)
        try:
            proc = subprocess.Popen(args, cwd=str(cwd), env=os.environ.copy())
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] не удалось запустить: %s", name, exc)
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue

        code = proc.wait()
        logger.warning("[%s] завершился с кодом %s — перезапуск через %s сек", name, code, delay)
        time.sleep(delay)
        delay = min(delay * 2, 60) if code != 0 else 5


def main() -> None:
    load_root_env()
    start_healthcheck_server()

    bots: list[tuple[str, list[str], Path]] = [
        # Прокси-бот запускается из proxy_bot.py, а НЕ из main.py: сам main.py
        # теперь делегирует запуск сюда, в супервизор. Если бы супервизор звал
        # main.py, он бы рекурсивно запустил самого себя (форк-бомба).
        ("proxy", [sys.executable, "proxy_bot.py"], BASE_DIR),
        ("sausha", [sys.executable, "main.py"], SAUSHA_DIR),
    ]

    threads: list[threading.Thread] = []
    for name, args, cwd in bots:
        script_name = args[-1]
        if not (cwd / script_name).exists():
            logger.error("[%s] пропущен: нет %s/%s", name, cwd, script_name)
            continue
        thread = threading.Thread(target=supervise, args=(name, args, cwd), daemon=True)
        thread.start()
        threads.append(thread)

    if not threads:
        logger.error("Нечего запускать — выходим.")
        return

    # Держим главный поток живым, пока живы супервизоры ботов.
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C.")


if __name__ == "__main__":
    main()
