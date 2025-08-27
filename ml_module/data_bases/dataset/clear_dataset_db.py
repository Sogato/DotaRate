"""
Скрипт очистки базы данных датасета матчей Dota 2.

Удаляет все данные из таблиц с сохранением структуры.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from config import DATASET_DATABASE_URL
from models import Match, MatchPlayer
from utils.console import (
    print_section_header,
    print_subsection_header,
    print_status_message,
    print_info_line,
    Colors
)

# Создаем подключение к БД
engine = create_engine(DATASET_DATABASE_URL)


def clear_database():
    """
    Полностью очищает все таблицы базы данных.

    Использует TRUNCATE для быстрого удаления всех записей
    с сохранением структуры таблиц и сбросом AUTO_INCREMENT.

    Returns:
        bool: True если очистка прошла успешно, False при ошибке
    """
    print_section_header("ОЧИСТКА БАЗЫ ДАННЫХ", "🗑️", color=Colors.BRIGHT_RED)

    try:
        # Получаем имена таблиц из моделей
        match_table = Match.__tablename__
        match_player_table = MatchPlayer.__tablename__

        print_status_message("Очистка таблиц...", "warning", "⚠️")

        with engine.begin() as conn:
            # ВАЖНО: Очищаем сначала дочернюю таблицу из-за внешних ключей
            print_info_line("Очистка таблицы", match_player_table, "🔄")
            conn.execute(text(f'TRUNCATE TABLE "{match_player_table}" RESTART IDENTITY CASCADE'))

            print_info_line("Очистка таблицы", match_table, "🔄")
            conn.execute(text(f'TRUNCATE TABLE "{match_table}" RESTART IDENTITY CASCADE'))

        print_status_message("Данные успешно очищены!", "success", "✅")
        return True

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при очистке БД: {e}", "error", "❌")
        return False
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
        return False


def check_if_empty():
    """
    Проверяет, пуста ли база данных после очистки.

    Выполняет подсчет записей во всех основных таблицах
    и выводит детальную статистику.

    Returns:
        bool: True если БД пуста, False если содержит данные
    """
    print_subsection_header("Проверка состояния БД", "🔍", Colors.BRIGHT_YELLOW)

    try:
        match_table = Match.__tablename__
        match_player_table = MatchPlayer.__tablename__

        with engine.connect() as conn:
            # Подсчитываем записи в каждой таблице
            match_count = conn.execute(text(f'SELECT COUNT(*) FROM "{match_table}"')).scalar()
            player_count = conn.execute(text(f'SELECT COUNT(*) FROM "{match_player_table}"')).scalar()

        # Выводим статистику
        print_info_line("Матчей в БД", f"{match_count:,}", "🎮")
        print_info_line("Записей игроков", f"{player_count:,}", "👥")

        # Определяем и выводим результат проверки
        if match_count == 0 and player_count == 0:
            print_status_message("База данных пуста", "success", "✅")
            return True
        else:
            print_status_message("База данных содержит данные", "warning", "⚠️")
            return False

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при проверке БД: {e}", "error", "❌")
        return False
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
        return False


def main():
    """
    Главная функция для выполнения полной очистки и проверки БД.
    """
    # Выполняем очистку
    if clear_database():
        print()  # Добавляем отступ

        # Проверяем результат
        check_if_empty()
    else:
        print_status_message("Очистка не выполнена из-за ошибки", "error", "❌")


if __name__ == "__main__":
    main()
