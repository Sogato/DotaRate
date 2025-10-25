"""
Модуль преобразования разреженных ID героев в плотные последовательные индексы.

Решает проблему пропусков в нумерации hero_id (1,2,3,5,7...145) для эффективного
использования в embedding слоях нейронных сетей. Преобразует в плотный диапазон
индексов (0,1,2,3,4...) без пропусков.

Основные возможности:
- Двусторонний маппинг: hero_id ↔ плотный индекс
- Опциональное исключение нежелательных героев
- Ленивая инициализация (маппинги строятся при первом обращении)
- Кеширование результатов для повторных обращений
"""

from typing import Dict, Set
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import HEROES_DATABASE_URL
from data_bases.heroes.models import Hero


class HeroMapper:
    """
    Маппер для преобразования hero_id в плотные последовательные индексы.

    Создает двусторонний маппинг между разреженными hero_id из базы данных
    и плотными индексами для embedding слоев. Поддерживает исключение героев.

    Маппинг строится лениво при первом обращении к свойствам и кешируется.

    Attributes:
        excluded_hero_ids (Set[int]): Множество исключенных hero_id
        engine: SQLAlchemy движок для подключения к БД
        session_maker: Фабрика сессий SQLAlchemy
    """

    def __init__(self, excluded_hero_ids: Set[int] = None):
        """
        Инициализирует маппер с с настройкой подключения к БД и опциональным исключением героев.

        Args:
            excluded_hero_ids (Set[int], optional): Множество hero_id для исключения
            из маппинга (например, новые или проблемные герои)
        """
        self.excluded_hero_ids = excluded_hero_ids or set()
        self.engine = create_engine(HEROES_DATABASE_URL)
        self.session_maker = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        # Кеш для маппингов (заполняется лениво)
        self._hero_to_index = None
        self._index_to_hero = None
        self._is_built = False

    def _load_hero_ids_from_db(self):
        """
        Загружает все hero_id из базы данных Heroes.

        Returns:
            List[int]: Список всех hero_id из БД, отсортированный по возрастанию
        """
        with self.session_maker() as session:
            hero_ids = session.query(Hero.id).order_by(Hero.id).all()
            return [hero_id[0] for hero_id in hero_ids]

    def _build_mappings(self):
        """
        Строит двусторонний маппинг между hero_id и плотными индексами.

        Выполняется только один раз при первом обращении к свойствам.
        Результат кешируется для последующих обращений.

        Этапы:
        1. Загрузка всех hero_id из БД
        2. Фильтрация исключенных героев
        3. Создание маппинга hero_to_index
        4. Создание обратного маппинга index_to_hero
        5. Установка флага _is_built
        """
        if self._is_built:
            return

        # Загрузка hero_id из БД
        all_hero_ids = self._load_hero_ids_from_db()

        # Исключение нежелательных героев
        filtered_hero_ids = [
            hero_id for hero_id in all_hero_ids
            if hero_id not in self.excluded_hero_ids
        ]

        # Создание прямого маппинга
        self._hero_to_index = {
            hero_id: index
            for index, hero_id in enumerate(filtered_hero_ids)
        }

        # Создание обратного маппинга
        self._index_to_hero = {
            index: hero_id
            for hero_id, index in self._hero_to_index.items()
        }

        self._is_built = True

    @property
    def hero_to_index(self) -> Dict[int, int]:
        """
        Маппинг hero_id в плотные индексы.

        Returns:
            Dict[int, int]: {hero_id: плотный_индекс}
        """
        self._build_mappings()
        return self._hero_to_index

    @property
    def index_to_hero(self) -> Dict[int, int]:
        """
        Обратный маппинг плотных индексов в hero_id.

        Returns:
            Dict[int, int]: {плотный_индекс: hero_id}
        """
        self._build_mappings()
        return self._index_to_hero

    @property
    def total_heroes(self) -> int:
        """
        Общее количество героев в маппинге (с учётом исключений).

        Returns:
            int: Размерность пространства героев для embedding слоев
        """
        self._build_mappings()
        return len(self._hero_to_index)

    def get_mapping_info(self) -> Dict[str, any]:
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
        """
        self._build_mappings()

        hero_ids = list(self._hero_to_index.keys())

        return {
            'total_heroes': self.total_heroes,
            'excluded_heroes_count': len(self.excluded_hero_ids),
            'excluded_hero_ids': sorted(self.excluded_hero_ids),
            'min_hero_id': min(hero_ids) if hero_ids else None,
            'max_hero_id': max(hero_ids) if hero_ids else None,
            'index_range': f"0-{self.total_heroes - 1}" if self.total_heroes > 0 else "empty"
        }

    def cleanup(self):
        """Освобождает ресурсы подключения к базе данных."""
        if hasattr(self, 'engine'):
            self.engine.dispose()
