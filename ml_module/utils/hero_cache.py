"""
Модуль кэширования справочных данных о героях Dota 2.

Модуль предоставляет класс HeroCache для единовременной загрузки информации
о героях из справочной базы данных и быстрого O(1) доступа к локализованным
именам по их ID.

Справочная база данных heroes.db содержит статическую информацию о всех
героях Dota 2, полученную из официального API. Эта БД не изменяется во время
работы приложения и служит единственным источником истины для имён и
метаданных героев.

Основные этапы работы:
1. Создание экземпляра HeroCache (данные не загружаются)
2. Вызов initialize() — подключение к БД, загрузка всех записей Hero,
   построение in-memory словаря {hero_id: localized_name}
3. Обращение к get_hero_name(hero_id) — O(1) поиск в словаре

Особенности:
- Данные загружаются только при явном вызове initialize()
- Повторные вызовы безопасны и не вызывают повторной загрузки
- get_hero_name() никогда не падает, возвращает строку-заглушку для любых невалидных или отсутствующих ID
"""

# Стандартные библиотеки
from typing import Dict

# Сторонние библиотеки
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Локальные импорты
from data_bases.heroes.models import Hero
from config import HEROES_DATABASE_URL
from utils.console import (
    Colors,
    print_subsection_header,
    print_info_line,
)


class HeroCache:
    """
    Кэш справочной информации о героях Dota 2.

    Загружает данные из БД один раз при вызове initialize() и сохраняет их
    в in-memory словаре на всё время работы скрипта. Повторные вызовы
    initialize() безопасны — повторная загрузка не выполняется.

    Attributes:
        _cache (Dict[int, str]): Словарь маппинга {hero_id: localized_name}
        _initialized (bool): Флаг успешной инициализации кэша

    Example:
        cache = HeroCache()
        cache.initialize()
        print(cache.get_hero_name(1)) # "Anti-Mage"
    """

    def __init__(self) -> None:
        """
        Создаёт пустой экземпляр кэша.

        Для загрузки данных вызовите initialize().
        """

        # Словарь маппинга {hero_id: localized_name}, заполняется при initialize()
        self._cache: Dict[int, str] = {}

        # Флаг успешной инициализации, защищает от повторной загрузки
        self._initialized: bool = False

    def initialize(self) -> bool:
        """
        Загружает всех героев из справочной БД и строит in-memory словарь.

        При повторном вызове возвращает True без повторной загрузки.

        Returns:
            bool: True если кэш загружен и готов к работе,
                  False если БД пуста или произошла ошибка
        """

        # Защита от повторной инициализации
        if self._initialized:
            return True

        # Подключение к справочной БД
        engine = create_engine(HEROES_DATABASE_URL)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session: Session = SessionLocal()

        try:
            print_subsection_header("Инициализация кэша героев", "📚", Colors.BRIGHT_MAGENTA)

            # Загружаем все записи Hero из справочной таблицы
            heroes = session.query(Hero).all()

            # Проверка на пустую БД
            if not heroes:
                print_info_line("Записей в БД", "0", "📊", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
                print_info_line("Статус", "База данных героев пуста", "⚠️", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
                print_info_line("Результат", "Инициализация не выполнена", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
                print()
                return False

            # Построение словаря {hero_id: localized_name}
            for hero in heroes:
                self._cache[hero.id] = hero.localized_name

            # Подтверждение успешной загрузки
            print_info_line("Загружено героев", f"{len(self._cache)}", "📊", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
            print_info_line("Структура данных", "{hero_id: localized_name}", "💾", Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)
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

    def get_hero_name(self, hero_id: int) -> str:
        """
        Возвращает локализованное имя героя по его ID.

        Метод безопасен для вызова с любыми значениями — для отсутствующих
        ID возвращает строку-заглушку.

        Args:
            hero_id (int): Уникальный идентификатор героя из Dota 2 API

        Returns:
            str: Локализованное название героя (например, "Anti-Mage") или
                 "Unknown Hero (ID: X)" для отсутствующих ID
        """

        return self._cache.get(hero_id, f"Unknown Hero (ID: {hero_id})")

    def __len__(self) -> int:
        """
        Возвращает количество героев в кэше.

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
