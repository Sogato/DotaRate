"""
Публикация статистических отчётов в Telegram.

Сами отчёты считает compute, картинки по ним рисует chart — здесь они
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
from .. import config
from . import chart, compute

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Точки входа: периодические отчёты (для планировщика)
# ────────────────────────────────────────────────────────────────────────────

def publish_daily() -> None:
    """Публикует статистику за прошедшие сутки."""
    _publish(compute.daily())


def publish_weekly() -> None:
    """Публикует статистику за прошедшую неделю."""
    _publish(compute.weekly())


def publish_monthly() -> None:
    """Публикует статистику за прошедший календарный месяц."""
    _publish(compute.monthly())


# ────────────────────────────────────────────────────────────────────────────
# Точки входа: параметрические отчёты (по требованию)
# ────────────────────────────────────────────────────────────────────────────

def publish_all_time() -> None:
    """Публикует статистику за текущий патч целиком."""
    _publish(compute.all_time())


def publish_league(league_id: int) -> None:
    """Публикует статистику по указанной лиге."""
    _publish(compute.league(league_id))


def publish_except_league(league_id: int) -> None:
    """Публикует статистику по всем лигам, кроме указанной."""
    _publish(compute.except_league(league_id))


# ────────────────────────────────────────────────────────────────────────────
# Публикация
# ────────────────────────────────────────────────────────────────────────────

def _publish(result: Optional[dict]) -> None:
    """
    Рисует график по результату отчёта и отправляет его с подписью в канал.

    result=None означает, что отчёт не посчитан: матчи получить не удалось,
    и compute уже записал причину в лог. Публиковать в этом случае нечего.
    """
    if result is None:
        return

    caption = compute.format_caption(result)
    try:
        image = chart.render(result)
        config.BOT.send_photo(config.TELEGRAM_CHAT_ID, image, caption=caption)
    except (ApiException, OSError) as exc:
        logger.warning("Не удалось опубликовать отчёт «%s»: %s", result['title'], exc)
        return

    logger.info("Отчёт опубликован: %s", result['title'])
