"""
Публикация статистических отчётов в Telegram.

Сами отчёты считает stats, картинки по ним рисует graphs — здесь они
связываются: результат превращается в PNG с подписью и уходит в канал.

Точки входа — функции publish_* по каждому виду отчёта. Периодические
(daily/weekly/monthly) вызываются планировщиком; параметрические
(all_time/league/except_league) — по требованию с нужным аргументом.
"""

# Стандартные библиотеки
import logging
from typing import Optional

# Сторонние библиотеки
from telebot.apihelper import ApiException

# Локальные импорты
from . import config
from . import graphs
from . import stats

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Точки входа: периодические отчёты (для планировщика)
# ════════════════════════════════════════════════════════════════════════════

def publish_daily() -> None:
    """Публикует статистику за прошедшие сутки."""
    _publish(stats.daily(), config.STATS_DAILY_COLORS)


def publish_weekly() -> None:
    """Публикует статистику за прошедшую неделю."""
    _publish(stats.weekly(), config.STATS_WEEKLY_COLORS)


def publish_monthly() -> None:
    """Публикует статистику за прошедший календарный месяц."""
    _publish(stats.monthly(), config.STATS_MONTHLY_COLORS)


# ════════════════════════════════════════════════════════════════════════════
# Точки входа: параметрические отчёты (по требованию)
# ════════════════════════════════════════════════════════════════════════════

def publish_all_time() -> None:
    """Публикует статистику за текущий патч целиком"""
    _publish(stats.all_time(), config.STATS_ALL_TIME_COLORS)


def publish_league(league_id: int) -> None:
    """Публикует статистику по указанной лиге."""
    _publish(stats.league(league_id), config.STATS_LEAGUE_COLORS)


def publish_except_league(league_id: int) -> None:
    """Публикует статистику по всем лигам, кроме указанной."""
    _publish(stats.except_league(league_id), config.STATS_EXCEPT_LEAGUE_COLORS)


# ════════════════════════════════════════════════════════════════════════════
# Публикация
# ════════════════════════════════════════════════════════════════════════════

def _publish(result: Optional[dict], colors: list) -> None:
    """
    Рисует график по результату отчёта и отправляет его с подписью в канал.

    colors — пара цветов из config (STATS_*_COLORS), своя у каждого вида
    отчёта, чтобы они различались визуально.

    result=None означает, что отчёт не посчитан: матчи получить не удалось,
    и stats уже записал причину в лог. Публиковать в этом случае нечего.
    """
    if result is None:
        return

    caption = stats.format_caption(result)
    try:
        image = graphs.render(result, colors)
        config.BOT.send_photo(config.TELEGRAM_CHAT_ID, image, caption=caption)
    except (ApiException, OSError) as exc:
        logger.warning("Не удалось опубликовать отчёт «%s»: %s", result['title'], exc)
        return

    logger.info("Отчёт опубликован: %s", result['title'])
