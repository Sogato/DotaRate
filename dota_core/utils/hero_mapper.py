"""
Модуль преобразования разреженных ID героев в плотные последовательные индексы.

Решает проблему пропусков в нумерации hero_id (1,2,3,5,7...145) для эффективного
использования в embedding слоях нейронных сетей. Преобразует в плотный диапазон
индексов (0,1,2,3,4...) без пропусков.

Основные возможности:
- Двусторонний маппинг: hero_id ↔ плотный индекс
- Опциональное исключение нежелательных героев через excluded_hero_ids

Основные этапы работы:
1. Создание экземпляра HeroMapper (данные не загружаются)
2. Вызов initialize() — подключение к БД, чтение всех hero_id, построение словарей hero_to_index и index_to_hero
3. Обращение к hero_to_index / index_to_hero — O(1) поиск в словарях

Особенности:
- Данные загружаются только при явном вызове initialize()
- Повторные вызовы initialize() безопасны и не вызывают повторной загрузки
- Соединение с БД открывается и закрывается внутри initialize()
- Обращение к маппингам до успешной инициализации вызывает RuntimeError
"""

# Стандартные библиотеки
from typing import Dict, Set, Any

# Сторонние библиотеки
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Локальные импорты
from dota_core.data_bases.heroes.models import Hero
from dota_core.config import HEROES_DATABASE_URL
from dota_core.utils.memory import dict_memory_bytes, format_memory
from dota_core.utils.console import (
    Colors,
    print_subsection_header,
    print_info_line,
)


class HeroMapper:
    """
    Маппер для преобразования hero_id в плотные последовательные индексы.

    Создает двусторонний маппинг между разреженными hero_id из базы данных
    и плотными индексами для embedding слоев. Поддерживает исключение героев.

    Данные загружаются из БД один раз при вызове initialize() и сохраняются
    в in-memory словарях на всё время работы. Соединение с БД открывается
    и закрывается внутри initialize(). Повторные вызовы initialize()
    безопасны — повторная загрузка не выполняется.

    Attributes:
        excluded_hero_ids (Set[int]): Множество исключенных hero_id
        _hero_to_index (Dict[int, int]): Словарь {hero_id: плотный_индекс}
        _index_to_hero (Dict[int, int]): Словарь {плотный_индекс: hero_id}
        _initialized (bool): Флаг успешной инициализации

    Example:
        mapper = HeroMapper(excluded_hero_ids={130})
        mapper.initialize()
        print(mapper.hero_to_index[1])  # 0
        print(mapper.total_heroes)      # 124
    """

    def __init__(self, excluded_hero_ids: Set[int] | None = None) -> None:
        """
        Создаёт пустой экземпляр маппера.

        К БД не подключается, данные не загружает — только сохраняет конфигурацию.
        Для загрузки данных вызовите initialize().

        Args:
            excluded_hero_ids (Set[int], optional): Множество hero_id для исключения из маппинга
        """

        # Конфигурация: какие hero_id не включать в маппинг
        self.excluded_hero_ids: Set[int] = excluded_hero_ids or set()

        # Словари маппинга, заполняются при initialize()
        self._hero_to_index: Dict[int, int] = {}
        self._index_to_hero: Dict[int, int] = {}

        # Флаг успешной инициализации, защищает от повторной загрузки
        self._initialized: bool = False

    def initialize(self) -> bool:
        """
        Загружает hero_id из справочной БД и строит оба словаря маппинга.

        Соединение с БД открывается и закрывается внутри этого метода.
        При повторном вызове возвращает True без повторной загрузки.

        Returns:
            bool: True если маппинг построен и готов к работе,
                  False если БД пуста или произошла ошибка
        """

        # Защита от повторной инициализации
        if self._initialized:
            return True

        # Подключение к справочной БД (живёт только внутри этого метода)
        engine = create_engine(HEROES_DATABASE_URL)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session: Session = SessionLocal()

        try:
            print_subsection_header("Инициализация маппера героев", "🗺️", Colors.BRIGHT_PURPLE)

            # Загружаем все hero_id из справочной таблицы, по возрастанию
            rows = session.query(Hero.id).order_by(Hero.id).all()
            all_hero_ids = [row[0] for row in rows]

            # Проверка на пустую БД
            if not all_hero_ids:
                print_info_line("Записей в БД", "0", "📊", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
                print_info_line("Статус", "База данных героев пуста", "⚠️", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
                print_info_line("Результат", "Инициализация не выполнена", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
                print()
                return False

            # Исключение нежелательных героев
            filtered_hero_ids = [
                hero_id for hero_id in all_hero_ids
                if hero_id not in self.excluded_hero_ids
            ]

            # Создание прямого маппинга {hero_id: плотный_индекс}
            self._hero_to_index = {
                hero_id: index
                for index, hero_id in enumerate(filtered_hero_ids)
            }

            # Создание обратного маппинга {плотный_индекс: hero_id}
            self._index_to_hero = {
                index: hero_id
                for hero_id, index in self._hero_to_index.items()
            }

            # Подтверждение успешной загрузки
            print_info_line("Загружено героев", f"{len(self._hero_to_index)}", "🧙‍♂️", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
            print_info_line("Исключено героев", f"{len(self.excluded_hero_ids)}", "🚫", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
            print_info_line("Структура данных", "{hero_id: index} + {index: hero_id}", "💾", Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)
            print_info_line("Задействовано памяти", format_memory(self.memory_usage_bytes()), "🧠", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)
            print_info_line("Статус", "Маппер инициализирован успешно", "✅", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
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
            # Полностью освобождаем соединение с БД в любом случае
            session.close()
            engine.dispose()

    def _ensure_initialized(self) -> None:
        """
        Проверяет, что маппинг построен, и даёт понятную ошибку вместо
        пустого словаря.

        Raises:
            RuntimeError: Если initialize() ещё не вызывался или завершился
                неудачно
        """

        if not self._initialized:
            raise RuntimeError(
                "HeroMapper не инициализирован. Вызовите initialize() перед использованием."
            )

    @property
    def hero_to_index(self) -> Dict[int, int]:
        """
        Маппинг hero_id в плотные индексы.

        Returns:
            Dict[int, int]: {hero_id: плотный_индекс}

        Raises:
            RuntimeError: Если маппер не инициализирован
        """

        self._ensure_initialized()
        return self._hero_to_index

    @property
    def index_to_hero(self) -> Dict[int, int]:
        """
        Обратный маппинг плотных индексов в hero_id.

        Returns:
            Dict[int, int]: {плотный_индекс: hero_id}

        Raises:
            RuntimeError: Если маппер не инициализирован
        """

        self._ensure_initialized()
        return self._index_to_hero

    @property
    def total_heroes(self) -> int:
        """
        Общее количество героев в маппинге (с учётом исключений).

        Returns:
            int: Размерность пространства героев для embedding слоев

        Raises:
            RuntimeError: Если маппер не инициализирован
        """

        self._ensure_initialized()
        return len(self._hero_to_index)

    def get_mapping_info(self) -> Dict[str, Any]:
        """
        Возвращает статистику и метаданные о маппинге.

        Returns:
            Dict: Информация о маппинге:
                - total_heroes: общее количество героев
                - excluded_heroes_count: количество исключенных героев
                - excluded_hero_ids: список исключенных hero_id
                - min_hero_id: минимальный hero_id в маппинге
                - max_hero_id: максимальный hero_id в маппинге
                - index_range: диапазон индексов
                - memory_usage: занимаемая память в читаемом виде

        Raises:
            RuntimeError: Если маппер не инициализирован
        """

        self._ensure_initialized()

        hero_ids = list(self._hero_to_index.keys())

        return {
            'total_heroes': len(self._hero_to_index),
            'excluded_heroes_count': len(self.excluded_hero_ids),
            'excluded_hero_ids': sorted(self.excluded_hero_ids),
            'min_hero_id': min(hero_ids) if hero_ids else None,
            'max_hero_id': max(hero_ids) if hero_ids else None,
            'index_range': f"0-{len(self._hero_to_index) - 1}" if self._hero_to_index else "empty",
            'memory_usage': format_memory(self.memory_usage_bytes()),
        }

    def memory_usage_bytes(self) -> int:
        """
        Возвращает память, занимаемую данными маппера (оба словаря).

        Returns:
            int: Суммарный размер словарей маппинга в байтах
        """

        return dict_memory_bytes(self._hero_to_index, self._index_to_hero)

    def __len__(self) -> int:
        """
        Возвращает количество героев в маппинге.

        Returns:
            int: Количество записей в словаре hero_to_index
        """

        return len(self._hero_to_index)

    @property
    def is_initialized(self) -> bool:
        """
        Проверяет статус инициализации маппера.

        Returns:
            bool: True если initialize() завершился успешно, False иначе
        """

        return self._initialized
