"""
Планировщик фоновых задач Telegram-бота.

По расписанию публикует включённые отчёты статистики и раз в день запускает
очистку БД на backend. Какие отчёты публиковать, задаётся флагами
STATS_*_ENABLED в config; время срабатывания всех заданий — STATS_RUN_AT.

Наружу выставлена одна функция start(): она навешивает задания и запускает
цикл проверки расписания в потоке-демоне. Демон завершается вместе с
процессом, отдельная остановка не предусмотрена. start() рассчитан на
однократный вызов из точки входа: библиотека schedule хранит задания в
модульном состоянии, и повторный вызов продублировал бы их.

Каждая задача обёрнута в _safe: упавшее задание пишет ошибку в лог и ждёт
следующего срабатывания по расписанию, не роняя поток и не зацикливаясь.
"""

# Стандартные библиотеки
import logging
import threading
import time
from datetime import date
from typing import Callable

# Сторонние библиотеки
import schedule

# Локальные импорты
from . import api_client, config
from .stats import publisher as stats_publisher

logger = logging.getLogger(__name__)

# Период проверки расписания, секунд.
_CHECK_INTERVAL = 30


# ────────────────────────────────────────────────────────────────────────────
# Точка входа
# ────────────────────────────────────────────────────────────────────────────

def start() -> threading.Thread:
    """
    Навешивает задания и запускает планировщик в фоновом потоке.

    Returns:
        threading.Thread: Запущенный поток-демон планировщика
    """
    _build_schedule()
    thread = threading.Thread(target=_run, name="stats-scheduler", daemon=True)
    thread.start()
    logger.info("Планировщик статистики запущен")
    return thread


# ────────────────────────────────────────────────────────────────────────────
# Цикл и расписание
# ────────────────────────────────────────────────────────────────────────────

def _run() -> None:
    """Цикл потока-демона: выполняет задания по расписанию."""
    while True:
        # Исключения отдельных задач перехватывает _safe; здесь страхуемся от
        # неожиданного сбоя самого планировщика, чтобы поток не остановился.
        try:
            schedule.run_pending()
        except Exception:
            logger.exception("Сбой планировщика статистики")
        time.sleep(_CHECK_INTERVAL)


def _build_schedule() -> None:
    """
    Навешивает задания планировщика.

    Все задания срабатывают в STATS_RUN_AT; какие отчёты публиковать,
    задаётся флагами STATS_*_ENABLED в config.
    """
    at = config.STATS_RUN_AT

    # Ежедневная очистка БД — обслуживание, выполняется всегда, без флага.
    schedule.every().day.at(at).do(_safe(_cleanup_job, "очистка БД"))

    if config.STATS_DAILY_ENABLED:
        schedule.every().day.at(at).do(_safe(stats_publisher.publish_daily, "дневной отчёт"))
    if config.STATS_WEEKLY_ENABLED:
        schedule.every().monday.at(at).do(_safe(stats_publisher.publish_weekly, "недельный отчёт"))
    if config.STATS_MONTHLY_ENABLED:
        # schedule не умеет "раз в месяц": задание ставится ежедневно, а
        # публикацию до первого числа удерживает сам _monthly_job.
        schedule.every().day.at(at).do(_safe(_monthly_job, "месячный отчёт"))


# ────────────────────────────────────────────────────────────────────────────
# Задачи
# ────────────────────────────────────────────────────────────────────────────

def _monthly_job() -> None:
    """Публикует месячный отчёт, но только первого числа месяца."""
    if date.today().day == 1:
        stats_publisher.publish_monthly()


def _cleanup_job() -> None:
    """Запускает очистку БД на backend и записывает её итог в логи."""
    summary = api_client.trigger_cleanup()
    if summary is not None:
        logger.info("Очистка БД: удалено матчей — %s", summary.get("deleted"))


# ────────────────────────────────────────────────────────────────────────────
# Страховка задач
# ────────────────────────────────────────────────────────────────────────────

def _safe(job: Callable[[], None], name: str) -> Callable[[], None]:
    """
    Оборачивает задачу так, чтобы она не пробрасывала исключения наружу.

    Без этого упавшая задача осталась бы "просроченной" в schedule, и он
    запускал бы её на каждой проверке расписания. Перехват превращает повтор
    в штатный — до следующего запланированного срабатывания.
    """
    def wrapper() -> None:
        try:
            job()
        except Exception:
            logger.exception("Задача планировщика «%s» упала", name)

    return wrapper
