"""Точка входа для Render.

Раньше здесь жил сам прокси-бот, и `python main.py` поднимал ТОЛЬКО его.
Теперь код прокси-бота переехал в `proxy_bot.py`, а этот файл делегирует
запуск супервизору `run_all.py`, который поднимает все три бота:

  • прокси-бот            (proxy_bot.py)
  • анонимные комментарии (sausha-bot-main, Bot1)
  • анонимные сообщения   (sausha-bot-main, Bot2)

Благодаря этому неважно, что стоит в Start Command на Render — и
`python main.py`, и `python run_all.py` запускают всех троих.
"""
from __future__ import annotations

import run_all

if __name__ == "__main__":
    run_all.main()
