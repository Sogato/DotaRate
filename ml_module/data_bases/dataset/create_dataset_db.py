"""
Скрипт создания базы данных для датасета матчей Dota 2.
"""

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from models import Base
from config import DATASET_DATABASE_URL
from utils.console import (
    print_section_header,
    print_status_message,
    print_info_line,
    Colors
)


def create_database():
    """
    Создает базу данных и все таблицы согласно определенным моделям.

    Returns:
        bool: True если создание прошло успешно, False при ошибке
    """
    print_section_header("СОЗДАНИЕ БАЗЫ ДАННЫХ ДАТАСЕТА", "🏗️", color=Colors.BRIGHT_BLUE)

    try:
        # Создаем подключение к БД
        print_status_message("Подключение к базе данных...", "info", "🔌")
        engine = create_engine(DATASET_DATABASE_URL)

        # Создаем все таблицы
        print_status_message("Создание таблиц...", "info", "📋")
        Base.metadata.create_all(engine)

        # Выводим информацию о созданных таблицах
        table_names = list(Base.metadata.tables.keys())
        print_info_line("Создано таблиц", str(len(table_names)), "📊")

        for table_name in table_names:
            print_info_line("Таблица", table_name, "🔹", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)

        print_status_message("База данных создана успешно!", "success", "✅")
        return True

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при создании БД: {e}", "error", "❌")
        return False
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
        return False


if __name__ == "__main__":
    create_database()
