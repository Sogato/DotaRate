"""
Ручной запуск статистических отчётов из IDE.

Запускается кнопкой Run или `python telegram_bot/manual_stats.py`. Раскомментируй
нужный вызов ниже, задай id лиги где требуется — отчёт посчитается и уйдёт в канал
картинкой с подписью, ровно как у планировщика.

Требует того же окружения, что и main.py: поднятый backend (иначе матчи не придут
и публикация тихо выйдет — причина будет в логе) и доступный Telegram.
"""

import logging

from telegram_bot import stats_publisher


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Раскомментируй нужный отчёт ──────────────────────────────────────────
    stats_publisher.publish_all_time()
    stats_publisher.publish_daily()
    stats_publisher.publish_weekly()
    stats_publisher.publish_monthly()
    # stats_publisher.publish_league(16935)         # ← id лиги
    # stats_publisher.publish_except_league(16935)  # ← id лиги


if __name__ == "__main__":
    main()