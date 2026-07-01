"""
Точка входа Telegram-бота.

Запускается как пакет: `python -m telegram_bot`. Готовит окружение и держит
до остановки процесса два независимых цикла:
    1. Публикация матчей — matches.publisher.run() в главном потоке.
    2. Планировщик статистики — scheduler.start() в фоновом потоке-демоне:
       по расписанию публикует включённые отчёты и раз в день запускает
       очистку БД.

Перед запуском настраивается логирование на весь процесс и в память
загружается справочник имён героев (HeroCache) — без него бот не стартует.
"""

# Стандартные библиотеки
import logging

# Локальные импорты
from dota_core.utils.hero_cache import HeroCache
from telegram_bot import scheduler
from telegram_bot.matches import publisher

logger = logging.getLogger(__name__)


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


def main() -> None:
    """Готовит окружение и запускает оба цикла бота."""
    _configure_logging()
    hero_cache = _load_hero_cache()

    # Планировщик — фоновый поток-демон: завершается вместе с процессом.
    scheduler.start()

    # Публикация матчей — в главном потоке, его и останавливаем по прерыванию.
    try:
        publisher.run(hero_cache)
    except KeyboardInterrupt:
        logger.info("Остановка по прерыванию")


if __name__ == "__main__":
    main()
