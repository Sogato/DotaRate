"""
Скрипт очистки базы данных лиг Dota 2.

Модуль предоставляет функционал для полной очистки всех таблиц базы данных
leagues с сохранением структуры схемы. Используется TRUNCATE для удаления
всех записей и сброса AUTO_INCREMENT счетчиков.

ВАЖНО: Операция необратима. Все данные будут удалены без возможности восстановления.
"""
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from dota_core.data_bases.leagues.models import League
from dota_core.config import LEAGUES_DATABASE_URL
from dota_core.utils.console import (
    print_section_header,
    print_subsection_header,
    print_status_message,
    print_info_line,
    Colors
)

engine = create_engine(LEAGUES_DATABASE_URL)  # Engine для работы напрямую с БД


def clear_database() -> None:
    """
    Полностью очищает все таблицы базы данных leagues.

    Выполняет TRUNCATE для всех таблиц с сохранением структуры схемы
    и сбросом AUTO_INCREMENT счетчиков. Операция является транзакционной
    и выполняется атомарно - либо очищаются все таблицы, либо ни одна.

    Raises:
        SQLAlchemyError: При ошибках подключения или выполнения SQL
        Exception: При других непредвиденных ошибках

    Note:
        При ошибке скрипт завершается с кодом 1 (sys.exit(1))
    """
    print_section_header("ОЧИСТКА БАЗЫ ДАННЫХ ЛИГ", "🗑️", color=Colors.BRIGHT_RED)

    try:
        # Получаем имя таблицы из модели
        league_table = League.__tablename__

        print_status_message("Очистка таблицы лиг...", "warning", "⚠️")

        with engine.begin() as conn:
            print_info_line("Очистка таблицы", league_table, "🔄",
                            Colors.BLUE_3, Colors.BRIGHT_YELLOW)
            conn.execute(text(f'TRUNCATE TABLE "{league_table}" RESTART IDENTITY CASCADE'))

        print_status_message("Данные о лигах успешно очищены!", "success", "✅")

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при очистке БД лиг: {e}", "error", "❌")
        sys.exit(1)
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
        sys.exit(1)


def check_if_empty() -> None:
    """
    Проверяет, пуста ли база данных лиг после очистки.

    Выполняет подсчет записей в таблице leagues и выводит детальную
    статистику. Используется для верификации успешности операции очистки.

    Raises:
        SQLAlchemyError: При ошибках подключения или выполнения SQL
        Exception: При других непредвиденных ошибках

    Note:
        При ошибке скрипт завершается с кодом 1 (sys.exit(1))
    """
    print()
    print_subsection_header("Проверка состояния БД лиг", "🔍", Colors.BRIGHT_YELLOW)

    try:
        league_table = League.__tablename__

        with engine.connect() as conn:
            # Подсчитываем количество лиг в БД
            league_count = conn.execute(text(f'SELECT COUNT(*) FROM "{league_table}"')).scalar()

        # Выводим статистику
        print_info_line("Лиг в БД", f"{league_count:,}", "🏆",
                        Colors.BLUE_3, Colors.BRIGHT_CYAN)

        # Определяем и выводим результат проверки
        if league_count == 0:
            print_status_message("База данных лиг пуста", "success", "✅")
        else:
            print_status_message("База данных лиг содержит данные", "warning", "⚠️")

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при проверке БД лиг: {e}", "error", "❌")
        sys.exit(1)
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
        sys.exit(1)


def main() -> None:
    """
    Главная функция для выполнения полной очистки и проверки БД leagues.
    """
    clear_database()
    check_if_empty()


if __name__ == "__main__":
    main()
