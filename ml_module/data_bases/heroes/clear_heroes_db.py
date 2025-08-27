"""
Скрипт очистки базы данных героев Dota 2.

Удаляет все данные из таблицы heroes с сохранением структуры.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from config import HEROES_DATABASE_URL
from models import Hero
from utils.console import (
    print_section_header,
    print_subsection_header,
    print_status_message,
    print_info_line,
    Colors
)

# Создаем подключение к БД героев
engine = create_engine(HEROES_DATABASE_URL)


def clear_heroes_database():
    """
    Полностью очищает таблицу героев.

    Использует TRUNCATE для быстрого удаления всех записей героев.

    Returns:
        bool: True если очистка прошла успешно, False при ошибке
    """
    print_section_header("ОЧИСТКА БАЗЫ ДАННЫХ ГЕРОЕВ", "🗑️", color=Colors.BRIGHT_RED)

    try:
        # Получаем имя таблицы из модели
        hero_table = Hero.__tablename__

        print_status_message("Очистка таблицы героев...", "warning", "⚠️")

        with engine.begin() as conn:
            print_info_line("Очистка таблицы", hero_table, "🔄")
            conn.execute(text(f'TRUNCATE TABLE "{hero_table}" RESTART IDENTITY CASCADE'))

        print_status_message("Данные о героях успешно очищены!", "success", "✅")
        return True

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при очистке БД героев: {e}", "error", "❌")
        return False
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
        return False


def check_if_heroes_empty():
    """
    Проверяет, пуста ли база данных героев после очистки.
    Выполняет подсчет записей в таблице heroes и выводит статистику.

    Returns:
        bool: True если БД пуста, False если содержит данные
    """
    print_subsection_header("Проверка состояния БД героев", "🔍", Colors.BRIGHT_YELLOW)

    try:
        hero_table = Hero.__tablename__

        with engine.connect() as conn:
            # Подсчитываем количество героев в БД
            hero_count = conn.execute(text(f'SELECT COUNT(*) FROM "{hero_table}"')).scalar()

        # Выводим статистику
        print_info_line("Героев в БД", f"{hero_count:,}", "⚔️")

        # Определяем и выводим результат проверки
        if hero_count == 0:
            print_status_message("База данных героев пуста", "success", "✅")
            return True
        else:
            print_status_message("База данных героев содержит данные", "warning", "⚠️")
            return False

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при проверке БД героев: {e}", "error", "❌")
        return False
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
        return False


def main():
    """
    Главная функция для выполнения полной очистки и проверки БД героев.
    """
    # Выполняем очистку
    if clear_heroes_database():
        print()  # Добавляем отступ

        # Проверяем результат
        check_if_heroes_empty()
    else:
        print_status_message("Очистка не выполнена из-за ошибки", "error", "❌")


if __name__ == "__main__":
    main()
