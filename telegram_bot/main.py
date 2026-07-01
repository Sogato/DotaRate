"""
Точка входа Telegram-бота.

Запускается напрямую — кнопкой Run в IDE или `python telegram_bot/main.py`.
Поднимает два независимых цикла и держит их до остановки процесса:
    1. Публикация матчей — runner.run() в главном потоке.
    2. Планировщик статистики — фоновый поток: по расписанию публикует
       включённые отчёты и раз в день запускает очистку БД.

Перед запуском настраивается логирование на весь процесс и в память
загружается справочник имён героев (HeroCache) — без него бот не стартует.

Импорты пакета здесь абсолютные (from telegram_bot ...): файл исполняется как
__main__, и при прямом запуске относительный импорт пакета был бы невозможен.
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
from dota_core.utils.hero_cache import HeroCache
from telegram_bot import api_client, config, runner, stats_publisher

logger = logging.getLogger(__name__)

# Период проверки расписания, секунд.
_SCHEDULER_INTERVAL = 30


def _configure_logging() -> None:
    """Настраивает формат и уровень логов в журнал на весь процесс."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _load_hero_cache() -> HeroCache:
    """
    Загружает справочник имён героев в память.

    Returns:
        HeroCache: Готовый к работе справочник имён

    Raises:
        RuntimeError: Справочник героев пуст или недоступен
    """
    hero_cache = HeroCache()
    if not hero_cache.initialize():
        raise RuntimeError(
            "HeroCache: справочник героев пуст или недоступен. Бот не запущен."
        )
    return hero_cache


# ════════════════════════════════════════════════════════════════════════════
# Планировщик статистики
# ════════════════════════════════════════════════════════════════════════════

def _run_scheduler() -> None:
    """Цикл потока-демона: навешивает задания и выполняет их по расписанию."""
    _build_schedule()
    logger.info("Планировщик статистики запущен")
    while True:
        # Исключения отдельных задач перехватывает _safe; здесь страхуемся от
        # неожиданного сбоя самого планировщика, чтобы поток не остановился.
        try:
            schedule.run_pending()
        except Exception:
            logger.exception("Сбой планировщика статистики")
        time.sleep(_SCHEDULER_INTERVAL)


def _build_schedule() -> None:
    """
    Навешивает задания планировщика.

    Все задания срабатывают в STATS_RUN_AT; какие отчёты публиковать, задаётся флагами STATS_*_ENABLED в config.
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


def _monthly_job() -> None:
    """Публикует месячный отчёт, но только первого числа месяца."""
    if date.today().day == 1:
        stats_publisher.publish_monthly()


def _cleanup_job() -> None:
    """Запускает очистку БД на backend и записывает её итог в логи."""
    summary = api_client.trigger_cleanup()
    if summary is not None:
        logger.info("Очистка БД: удалено матчей — %s", summary.get("deleted"))


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


# ════════════════════════════════════════════════════════════════════════════
# Запуск
# ════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Готовит окружение и запускает оба цикла бота."""
    _configure_logging()
    hero_cache = _load_hero_cache()

    # Планировщик статистики — фоновый поток-демон: завершается вместе с процессом.
    scheduler_thread = threading.Thread(
        target=_run_scheduler, name="stats-scheduler", daemon=True
    )
    scheduler_thread.start()

    # Публикация матчей — в главном потоке, её и останавливаем по прерыванию.
    try:
        runner.run(hero_cache)
    except KeyboardInterrupt:
        logger.info("Остановка по прерыванию")


if __name__ == "__main__":
    main()
