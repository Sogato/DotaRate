"""
Точка входа Telegram-бота.

Запускается как пакет: `python -m telegram_bot`. Готовит окружение и держит
до остановки процесса два независимых цикла:
    1. Публикация матчей — matches.publisher.run() в главном потоке.
    2. Планировщик статистики — scheduler.start() в фоновом потоке-демоне:
       по расписанию публикует включённые отчёты и раз в день запускает
       очистку БД.
"""

# Стандартные библиотеки
import logging

# Локальные импорты
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


def main() -> None:
    """Готовит окружение и запускает оба цикла бота."""
    _configure_logging()

    # Планировщик — фоновый поток-демон: завершается вместе с процессом.
    scheduler.start()

    # Публикация матчей — в главном потоке, его и останавливаем по прерыванию.
    try:
        publisher.run()
    except KeyboardInterrupt:
        logger.info("Остановка по прерыванию")


if __name__ == "__main__":
    main()
