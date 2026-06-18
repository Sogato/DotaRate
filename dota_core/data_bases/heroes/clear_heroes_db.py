"""
Скрипт очистки базы данных героев Dota 2.

Модуль предоставляет функционал для полной очистки всех таблиц базы данных
heroes с сохранением структуры схемы. Используется TRUNCATE для удаления
всех записей и сброса AUTO_INCREMENT счетчиков.

ВАЖНО: Операция необратима. Все данные будут удалены без возможности восстановления.
"""
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from config import HEROES_DATABASE_URL
from models import Hero
from dota_core.utils.console import (
    print_section_header,
    print_subsection_header,
    print_status_message,
    print_info_line,
    Colors
)

engine = create_engine(HEROES_DATABASE_URL)  # Engine для работы напрямую с БД


def clear_database() -> None:
    """
    Полностью очищает все таблицы базы данных heroes.

    Выполняет TRUNCATE для всех таблиц с сохранением структуры схемы
    и сбросом AUTO_INCREMENT счетчиков. Операция является транзакционной
    и выполняется атомарно - либо очищаются все таблицы, либо ни одна.

    Последовательность действий:
    1. Получение имени таблицы из модели SQLAlchemy
    2. Открытие транзакции с автоматическим commit
    3. TRUNCATE таблицы heroes
    4. Автоматический commit при успешном выполнении

    Raises:
        SQLAlchemyError: При ошибках подключения или выполнения SQL
        Exception: При других непредвиденных ошибках

    Note:
        При ошибке скрипт завершается с кодом 1 (sys.exit(1))
    """
    print_section_header("ОЧИСТКА БАЗЫ ДАННЫХ ГЕРОЕВ", "🗑️", color=Colors.BRIGHT_RED)

    try:
        # Получаем имя таблицы из модели
        hero_table = Hero.__tablename__

        print_status_message("Очистка таблицы героев...", "warning", "⚠️")

        with engine.begin() as conn:
            print_info_line("Очистка таблицы", hero_table, "🔄",
                            Colors.BLUE_3, Colors.BRIGHT_YELLOW)
            conn.execute(text(f'TRUNCATE TABLE "{hero_table}" RESTART IDENTITY CASCADE'))

        print_status_message("Данные о героях успешно очищены!", "success", "✅")

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при очистке БД героев: {e}", "error", "❌")
        sys.exit(1)
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
        sys.exit(1)


def check_if_empty() -> None:
    """
    Проверяет, пуста ли база данных героев после очистки.

    Выполняет подсчет записей в таблице heroes и выводит детальную
    статистику. Используется для верификации успешности операции очистки.

    Raises:
        SQLAlchemyError: При ошибках подключения или выполнения SQL
        Exception: При других непредвиденных ошибках

    Note:
        При ошибке скрипт завершается с кодом 1 (sys.exit(1))
    """
    print()
    print_subsection_header("Проверка состояния БД героев", "🔍", Colors.BRIGHT_YELLOW)

    try:
        hero_table = Hero.__tablename__

        with engine.connect() as conn:
            # Подсчитываем количество героев в БД
            hero_count = conn.execute(text(f'SELECT COUNT(*) FROM "{hero_table}"')).scalar()

        # Выводим статистику
        print_info_line("Героев в БД", f"{hero_count:,}", "⚔️",
                        Colors.BLUE_3, Colors.BRIGHT_CYAN)

        # Определяем и выводим результат проверки
        if hero_count == 0:
            print_status_message("База данных героев пуста", "success", "✅")
        else:
            print_status_message("База данных героев содержит данные", "warning", "⚠️")

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при проверке БД героев: {e}", "error", "❌")
        sys.exit(1)
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
        sys.exit(1)


def main() -> None:
    """
    Главная функция для выполнения полной очистки и проверки БД heroes.
    """
    clear_database()
    check_if_empty()


if __name__ == "__main__":
    main()
