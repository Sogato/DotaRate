"""
Отображение информации о размере и составе базы данных.

Модуль предоставляет класс DatabaseAnalyzer для получения и вывода информации
о весе и размере данных. Является одним из компонентов валидатора.
"""

# Стандартные библиотеки
from typing import Dict, Any, Tuple

# Сторонние библиотеки
from sqlalchemy.orm import Session
from sqlalchemy import func, text

# Локальные импорты
from data_validation.components.database_config import DatabaseConfig
from dota_core.utils.console import (
    Colors,
    print_section_header,
    print_info_line,
    print_status_message
)


class DatabaseAnalyzer:
    """
    Собирает и отображает информацию о размере и составе базы данных.

    Выполняет SQL-запросы для получения метрик — количества записей
    и физических размеров таблиц и выводит их в консоль в виде оформленного блока.

    Attributes:
        session (Session): Активная сессия SQLAlchemy
        config (DatabaseConfig): Конфигурация активной БД
    """

    def __init__(self, session: Session, config: DatabaseConfig) -> None:
        """
        Инициализирует модуль.

        Args:
            session (Session): Активная сессия SQLAlchemy
            config (DatabaseConfig): Конфигурация активной БД
        """

        self.session = session
        self.config = config

    def collect(self) -> Tuple[int, int]:
        """
        Собирает информацию о БД, отображает её и возвращает счётчики записей.

        Метод служит точкой входа для внешнего кода. Счётчики возвращаются отдельно,
        чтобы вызывающий код мог использовать их без повторных запросов к БД.

        Returns:
            Tuple[int, int]: Пара (matches_count, players_count)
        """

        db_info = self._get_database_info()
        self._display_database_info(db_info)
        return db_info['matches_count'], db_info['players_count']

    def _get_database_info(self) -> Dict[str, Any]:
        """
        Получает детальную информацию о размере и содержимом базы данных.

        Выполняет SQL-запросы для получения имени БД, общего размера,
        количества записей и размеров таблиц. Размеры конвертируются
        в читаемый формат (KB / MB / GB).

        При ошибке SQL-запросов возвращает fallback-словарь с минимальной
        информацией (только счётчики записей, размеры = 'Неизвестно').

        Returns:
            Dict[str, Any]: Словарь с информацией о БД:
                - db_name (str): Имя базы данных
                - total_size (str): Общий размер в читаемом формате
                - matches_count (int): Количество матчей
                - players_count (int): Количество записей игроков
                - matches_table_size (str): Размер таблицы matches
                - players_table_size (str): Размер таблицы players
                - database_type (str): Тип БД (Dataset/Professional)
        """

        try:
            # === ПОЛУЧЕНИЕ ИМЕНИ И РАЗМЕРА БД ===
            db_name = self.session.execute(text("SELECT current_database()")).scalar()
            db_size_bytes = self.session.execute(
                text("SELECT pg_database_size(current_database())")
            ).scalar()
            if db_size_bytes is None:
                db_size_bytes = 0

            # Конвертируем размер БД в читаемый формат
            if db_size_bytes > 1024 ** 3:
                db_size_readable = f"{db_size_bytes / (1024 ** 3):.1f} GB"
            elif db_size_bytes > 1024 ** 2:
                db_size_readable = f"{db_size_bytes / (1024 ** 2):.1f} MB"
            else:
                db_size_readable = f"{db_size_bytes / 1024:.1f} KB"

            # === ПОЛУЧЕНИЕ КОЛИЧЕСТВА ЗАПИСЕЙ ===
            matches_count = self.session.query(
                func.count(self.config.match_model.match_id)
            ).scalar()
            if matches_count is None:
                matches_count = 0

            players_count = self.session.query(
                func.count(self.config.player_model.id)
            ).scalar()
            if players_count is None:
                players_count = 0

            # === ПОЛУЧЕНИЕ РАЗМЕРОВ ТАБЛИЦ ===
            matches_table_size = self.session.execute(
                text(f"SELECT pg_total_relation_size('{self.config.match_table_name}')")
            ).scalar()
            if matches_table_size is None:
                matches_table_size = 0

            players_table_size = self.session.execute(
                text(f"SELECT pg_total_relation_size('{self.config.player_table_name}')")
            ).scalar() or 0
            if players_table_size is None:
                players_table_size = 0

            # Конвертируем размеры таблиц в читаемый формат
            matches_size_readable = (
                f"{matches_table_size / (1024 ** 2):.1f} MB"
                if matches_table_size > 1024 ** 2
                else f"{matches_table_size / 1024:.1f} KB"
            )
            players_size_readable = (
                f"{players_table_size / (1024 ** 2):.1f} MB"
                if players_table_size > 1024 ** 2
                else f"{players_table_size / 1024:.1f} KB"
            )

            return {
                'db_name': db_name,
                'total_size': db_size_readable,
                'matches_count': matches_count,
                'players_count': players_count,
                'matches_table_size': matches_size_readable,
                'players_table_size': players_size_readable,
                'database_type': self.config.database_type
            }

        except Exception as e:
            print_status_message(f"Не удалось получить информацию о размере БД: {e}", "warning")

            # === FALLBACK: МИНИМАЛЬНАЯ ИНФОРМАЦИЯ БЕЗ РАЗМЕРОВ ===
            matches_count = self.session.query(
                func.count(self.config.match_model.match_id)
            ).scalar()
            if matches_count is None:
                matches_count = 0

            players_count = self.session.query(
                func.count(self.config.player_model.id)
            ).scalar()
            if players_count is None:
                players_count = 0

            return {
                'db_name': f'dota_{self.config.database_type.lower()}_database',
                'total_size': 'Неизвестно',
                'matches_count': matches_count,
                'players_count': players_count,
                'matches_table_size': 'Неизвестно',
                'players_table_size': 'Неизвестно',
                'database_type': self.config.database_type
            }

    @staticmethod
    def _display_database_info(db_info: Dict[str, Any]) -> None:
        """
        Отображает информацию о весе и размере данных в консоль.

        Args:
            db_info (Dict[str, Any]): Словарь с информацией о БД из _get_database_info()
        """

        # Формируем заголовок секции с типом БД
        database_type = db_info.get('database_type', 'Unknown')
        header = f"ВЕС И РАЗМЕР ДАННЫХ ({database_type.upper()})"
        print_section_header(header, "⚖️", color=Colors.BRIGHT_CYAN)

        # === ОСНОВНАЯ ИНФОРМАЦИЯ ===
        print_info_line("Тип базы данных", database_type, "🏷️", Colors.BRIGHT_WHITE, Colors.BRIGHT_PURPLE)
        print_info_line("База данных", db_info['db_name'], "🗃️", Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)
        print_info_line("Количество матчей", f"{db_info['matches_count']:,}", "🎮", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_YELLOW)
        print_info_line("Количество записей игроков", f"{db_info['players_count']:,}", "👥", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_CYAN)

        # === ИНФОРМАЦИЯ О РАЗМЕРАХ ===
        print()
        print_info_line("Фактический размер БД", db_info['total_size'], "💾", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)

        # Размеры таблиц выводим только если они доступны
        if db_info['matches_table_size'] != 'Неизвестно':
            print_info_line("Размер таблицы матчей", db_info['matches_table_size'], "📊", Colors.BRIGHT_WHITE,
                            Colors.BRIGHT_PURPLE)
            print_info_line("Размер таблицы игроков", db_info['players_table_size'], "📈", Colors.BRIGHT_WHITE,
                            Colors.BRIGHT_ORANGE)
