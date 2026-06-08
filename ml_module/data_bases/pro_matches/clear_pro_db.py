"""
Скрипт очистки базы данных профессиональных матчей Dota 2.

Модуль предоставляет функционал для полной очистки всех таблиц базы данных PRO матчей
с сохранением структуры схемы. Используется TRUNCATE для удаления
всех записей и сброса AUTO_INCREMENT счетчиков.

ВАЖНО: Операция необратима. Все данные будут удалены без возможности восстановления.
"""
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from config import PRO_DATABASE_URL
from models import ProMatch, ProMatchPlayer
from utils.console import (
    print_section_header,
    print_subsection_header,
    print_status_message,
    print_info_line,
    Colors
)

engine = create_engine(PRO_DATABASE_URL)  # Engine для работы напрямую с БД


def clear_database() -> None:
    """
    Полностью очищает все таблицы базы данных PRO матчей.

    Выполняет TRUNCATE для всех таблиц с сохранением структуры схемы
    и сбросом AUTO_INCREMENT счетчиков. Операция является транзакционной
    и выполняется атомарно - либо очищаются все таблицы, либо ни одна.

    Последовательность действий:
    1. Получение имен таблиц из моделей SQLAlchemy
    2. Открытие транзакции с автоматическим commit
    3. TRUNCATE дочерней таблицы pro_match_players (из-за FK constraints)
    4. TRUNCATE родительской таблицы pro_matches
    5. Автоматический commit при успешном выполнении

    Raises:
        SQLAlchemyError: При ошибках подключения или выполнения SQL
        Exception: При других непредвиденных ошибках

    Note:
        При ошибке скрипт завершается с кодом 1 (sys.exit(1))
    """
    print_section_header("ОЧИСТКА БД ПРОФЕССИОНАЛЬНЫХ МАТЧЕЙ", "🗑️", color=Colors.BRIGHT_RED)

    try:
        # Получаем имена таблиц из моделей
        match_table = ProMatch.__tablename__
        match_player_table = ProMatchPlayer.__tablename__

        print_status_message("Очистка таблиц...", "warning", "⚠️")

        with engine.begin() as conn:
            # ВАЖНО: Очищаем сначала дочернюю таблицу из-за внешних ключей
            print_info_line("Очистка таблицы", match_player_table, "🔄",
                            Colors.BLUE_3, Colors.BRIGHT_YELLOW)
            conn.execute(text(f'TRUNCATE TABLE "{match_player_table}" RESTART IDENTITY CASCADE'))

            print_info_line("Очистка таблицы", match_table, "🔄",
                            Colors.BLUE_3, Colors.BRIGHT_YELLOW)
            conn.execute(text(f'TRUNCATE TABLE "{match_table}" RESTART IDENTITY CASCADE'))

        print_status_message("Данные профессиональных матчей успешно очищены!", "success", "✅")

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при очистке БД: {e}", "error", "❌")
        sys.exit(1)
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
        sys.exit(1)


def check_if_empty() -> None:
    """
    Проверяет, пуста ли база данных профессиональных матчей после очистки.

    Выполняет подсчет записей во всех основных таблицах PRO матчей
    и выводит детальную статистику. Используется для верификации
    успешности операции очистки.

    Raises:
        SQLAlchemyError: При ошибках подключения или выполнения SQL
        Exception: При других непредвиденных ошибках

    Note:
        При ошибке скрипт завершается с кодом 1 (sys.exit(1))
    """
    print()
    print_subsection_header("Проверка состояния БД", "🔍", Colors.BRIGHT_YELLOW)

    try:
        match_table = ProMatch.__tablename__
        match_player_table = ProMatchPlayer.__tablename__

        with engine.connect() as conn:
            # Подсчитываем записи в каждой таблице
            match_count = conn.execute(text(f'SELECT COUNT(*) FROM "{match_table}"')).scalar()
            player_count = conn.execute(text(f'SELECT COUNT(*) FROM "{match_player_table}"')).scalar()

        # Выводим статистику
        print_info_line("Профессиональных матчей в БД", f"{match_count:,}", "🏆",
                        Colors.BLUE_3, Colors.BRIGHT_CYAN)
        print_info_line("Записей игроков", f"{player_count:,}", "👥",
                        Colors.BLUE_3, Colors.BRIGHT_CYAN)

        # Определяем и выводим результат проверки
        if match_count == 0 and player_count == 0:
            print_status_message("База данных профессиональных матчей пуста", "success", "✅")
        else:
            print_status_message("База данных содержит данные", "warning", "⚠️")

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при проверке БД: {e}", "error", "❌")
        sys.exit(1)
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
        sys.exit(1)


def main() -> None:
    """
    Главная функция для выполнения полной очистки и проверки БД pro.
    """
    clear_database()
    check_if_empty()


if __name__ == "__main__":
    main()
