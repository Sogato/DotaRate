"""
Модуль кэширования справочных данных о лигах Dota 2.

Модуль предоставляет класс LeagueCache для единовременной загрузки информации
о лигах из справочной базы данных и быстрого O(1) доступа к названиям лиг
по их ID.

Справочная база данных leagues содержит статическую информацию о всех
лигах Dota 2, полученную из OpenDota API. Эта БД не изменяется во время
работы приложения и служит единственным источником истины для названий
лиг.

Основные этапы работы:
1. Создание экземпляра LeagueCache (данные не загружаются)
2. Вызов initialize() — подключение к БД, загрузка всех записей League,
   построение in-memory словаря {leagueid: name}
3. Обращение к get_league_name(leagueid) — O(1) поиск в словаре

Особенности:
- Данные загружаются только при явном вызове initialize()
- Повторные вызовы безопасны и не вызывают повторной загрузки
- get_league_name() никогда не падает, возвращает строку-заглушку для любых невалидных или отсутствующих ID
"""

# Стандартные библиотеки
from typing import Dict

# Сторонние библиотеки
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Локальные импорты
from dota_core.data_bases.leagues.models import League
from dota_core.config import LEAGUES_DATABASE_URL
from dota_core.utils.console import (
    Colors,
    print_subsection_header,
    print_info_line,
)


class LeagueCache:
    """
    Кэш справочной информации о лигах Dota 2.

    Загружает данные из БД один раз при вызове initialize() и сохраняет их
    в in-memory словаре на всё время работы скрипта. Повторные вызовы
    initialize() безопасны — повторная загрузка не выполняется.

    Attributes:
        _cache (Dict[int, str]): Словарь маппинга {leagueid: name}
        _initialized (bool): Флаг успешной инициализации кэша

    Example:
        cache = LeagueCache()
        cache.initialize()
        print(cache.get_league_name(65006)) # "The International 2013"
    """

    def __init__(self) -> None:
        """
        Создаёт пустой экземпляр кэша.

        Для загрузки данных вызовите initialize().
        """

        # Словарь маппинга {leagueid: name}, заполняется при initialize()
        self._cache: Dict[int, str] = {}

        # Флаг успешной инициализации, защищает от повторной загрузки
        self._initialized: bool = False

    def initialize(self) -> bool:
        """
        Загружает все лиги из справочной БД и строит in-memory словарь.

        При повторном вызове возвращает True без повторной загрузки.

        Returns:
            bool: True если кэш загружен и готов к работе,
                  False если БД пуста или произошла ошибка
        """

        # Защита от повторной инициализации
        if self._initialized:
            return True

        # Подключение к справочной БД
        engine = create_engine(LEAGUES_DATABASE_URL)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session: Session = SessionLocal()

        try:
            print_subsection_header("Инициализация кэша лиг", "📚", Colors.BRIGHT_MAGENTA)

            # Загружаем все записи League из справочной таблицы
            leagues = session.query(League).all()

            # Проверка на пустую БД
            if not leagues:
                print_info_line("Записей в БД", "0", "📊", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
                print_info_line("Статус", "База данных лиг пуста", "⚠️", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
                print_info_line("Результат", "Инициализация не выполнена", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
                print()
                return False

            # Построение словаря {leagueid: name}
            for league in leagues:
                self._cache[league.leagueid] = league.name

            # Подтверждение успешной загрузки
            print_info_line("Загружено лиг", f"{len(self._cache)}", "📊", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
            print_info_line("Структура данных", "{leagueid: name}", "💾", Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)
            print_info_line("Статус", "Кэш инициализирован успешно", "✅", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
            print()

            self._initialized = True
            return True

        except Exception as e:
            # Логируем ошибку и возвращаем False без пробрасывания исключения
            print_info_line("Ошибка", str(e), "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
            print_info_line("Статус", "Инициализация не выполнена", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
            print()
            return False

        finally:
            # Закрываем сессию в любом случае
            session.close()

    def get_league_name(self, league_id: int) -> str:
        """
        Возвращает название лиги по её ID.

        Метод безопасен для вызова с любыми значениями — для отсутствующих
        ID возвращает строку-заглушку.

        Args:
            league_id (int): Уникальный идентификатор лиги из OpenDota API

        Returns:
            str: Название лиги (например, "The International 2013") или
                 "Unknown League (ID: X)" для отсутствующих ID
        """

        return self._cache.get(league_id, f"Unknown League (ID: {league_id})")

    def __len__(self) -> int:
        """
        Возвращает количество лиг в кэше.

        Returns:
            int: Количество загруженных записей в словаре _cache
        """

        return len(self._cache)

    @property
    def is_initialized(self) -> bool:
        """
        Проверяет статус инициализации кэша.

        Returns:
            bool: True если initialize() завершился успешно, False иначе
        """

        return self._initialized
