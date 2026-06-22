"""
Скрипт создания базы данных для лиг Dota 2.

Модуль отвечает за инициализацию структуры базы данных leagues,
создавая все необходимые таблицы на основе определенных SQLAlchemy моделей.

Основное назначение:
- Первичная инициализация БД leagues при развертывании проекта
- Восстановление структуры после полного удаления БД

Ключевые особенности:
- Скрипт НЕ удаляет существующие данные
- Если таблицы уже существуют, они не будут изменены
"""
import sys
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from dota_core.data_bases.leagues.models import Base
from dota_core.config import LEAGUES_DATABASE_URL
from dota_core.utils.console import (
    print_section_header,
    print_status_message,
    print_info_line,
    Colors
)


def create_database() -> None:
    """
    Создает базу данных leagues и все таблицы согласно определенным моделям.

    Последовательность действий:
    1. Установка подключения к БД через SQLAlchemy Engine
    2. Вызов Base.metadata.create_all() для генерации и выполнения DDL
    3. Извлечение списка созданных таблиц из metadata
    4. Вывод детальной информации о каждой таблице
    5. Вывод итогового статуса операции

    Raises:
        SQLAlchemyError: При ошибках подключения, создания таблиц или нарушении constraints
        Exception: При других непредвиденных ошибках (права доступа, дисковое пространство)

    Note:
        При ошибке скрипт завершается с кодом 1 (sys.exit(1))
    """
    print_section_header("СОЗДАНИЕ БАЗЫ ДАННЫХ ЛИГ", "🏆", color=Colors.BRIGHT_PURPLE)

    try:
        # Создаем подключение к БД
        print_status_message("Подключение к базе данных лиг...", "info", "🔌")
        engine = create_engine(LEAGUES_DATABASE_URL)

        # Создаем таблицы
        print_status_message("Создание таблиц...", "info", "📋")
        Base.metadata.create_all(engine)

        # Выводим информацию о созданных таблицах
        table_names = list(Base.metadata.tables.keys())
        print_info_line("Создано таблиц", str(len(table_names)), "📊",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)

        for table_name in table_names:
            print_info_line("Таблица", table_name, "🔹",
                            Colors.BLUE_3, Colors.BRIGHT_CYAN)

        print_status_message("База данных лиг создана успешно!", "success", "✅")

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при создании БД лиг: {e}", "error", "❌")
        sys.exit(1)
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
        sys.exit(1)


def main() -> None:
    """
    Главная функция для создания БД leagues.
    """
    create_database()


if __name__ == "__main__":
    main()
