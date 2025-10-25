"""
Модуль загрузки данных для модели Win v1 предсказания исходов матчей Dota 2.

Модуль обеспечивает загрузку и предобработку данных для трёх источников:
HDF5 файлов, базы данных и одиночных матчей. Преобразует составы команд
в единый формат для входного слоя модели Win v1.

Задача модели Win v1:
- Бинарная классификация: победа Radiant (1) или Dire (0)
- Входные признаки: составы команд по 5 героев (плотные индексы)
- Целевая переменная: radiant_win (наличие зависит от источника)

Источники данных и их предобработка:
1. HDF5 файлы:
   - Данные уже содержат плотные индексы
   - Применяется сортировка (опционально)
   - Возвращает: (features_dict, labels)

2. База данных (множество матчей):
   - Данные содержат исходные hero_id
   - Применяется: hero_id → плотные индексы → сортировка (опционально)
   - Возвращает: (features_dict, labels)

3. Одиночный матч:
   - Данные содержат исходные hero_id
   - Применяется: hero_id → плотные индексы → сортировка (опционально)
   - Возвращает: (features_dict) без labels

Все методы возвращают единый формат Dict для признаков:
{'radiant_heroes': np.ndarray, 'dire_heroes': np.ndarray}
"""
from pathlib import Path

import h5py
import numpy as np
from typing import Dict, List, Union, Tuple, Set

from config import EXCLUDED_HERO_IDS
from utils.hero_mapper import HeroMapper
from utils.console import print_info_line, Colors

# === КОНСТАНТЫ ДЛЯ МОДЕЛИ WIN V1 ===
SORT_TEAM_HEROES = True  # Флаг сортировки составов команд


class DataLoaderWinV1:
    """
    Загрузчик данных для модели Win v1 предсказания исходов матчей Dota 2.

    Класс преобразует данные матчей из различных источников в единый формат:
    плотные индексы героев, готовые для embedding слоев.

    Этапы предобработки:
    1. Преобразование hero_id в плотные индексы (если требуется)
    2. Сортировка индексов героев (если SORT_TEAM_HEROES=True)
    3. Формирование numpy массивов в Dict формате

    Attributes:
        hero_mapper (HeroMapper): Маппер для преобразования hero_id в плотные индексы
    """

    def __init__(self, excluded_hero_ids: Set[int] = EXCLUDED_HERO_IDS):
        """
        Инициализирует загрузчик данных с настройкой HeroMapper.

        Args:
            excluded_hero_ids (Set[int], optional): Множество ID героев для исключения
        """
        self.hero_mapper = HeroMapper(excluded_hero_ids=excluded_hero_ids)

    def _process_team_heroes(self, heroes: Union[List[int], np.ndarray], from_ids: bool = True) -> np.ndarray:
        """
        Преобразует состав команды в плотные индексы с опциональной сортировкой.

        Args:
            heroes (Union[List[int], np.ndarray]): Состав команды (5 героев)
            from_ids (bool, optional):
                True - преобразовать hero_id в индексы,
                False - данные уже плотные индексы

        Returns:
            np.ndarray: Массив индексов (отсортированный если SORT_TEAM_HEROES=True), shape (5,), dtype int32

        Raises:
            ValueError: Если hero_id не найден в маппере
        """
        if isinstance(heroes, np.ndarray):
            heroes = heroes.tolist()

        if from_ids:
            indices = []
            for hero_id in heroes:
                if hero_id not in self.hero_mapper.hero_to_index:
                    raise ValueError(f"Герой с ID {hero_id} отсутствует в модели или исключён")
                indices.append(self.hero_mapper.hero_to_index[hero_id])
        else:
            indices = heroes

        # Опциональная сортировка
        if SORT_TEAM_HEROES:
            indices = sorted(indices)

        return np.array(indices, dtype=np.int32)

    def load_data_from_hdf5(self, hdf5_path: str) -> Tuple[Dict[str, np.ndarray], np.ndarray]:
        """
        Загружает данные из HDF5 файла.

        HDF5 файлы предполагает содержание предобработанных плотных индексов.

        Args:
            hdf5_path (str): Путь к HDF5 файлу с данными

        Returns:
            Tuple[Dict[str, np.ndarray], np.ndarray]:
                - features: {'radiant_heroes': (N, 5), 'dire_heroes': (N, 5)}, dtype int32
                - labels: shape (N,), dtype float32
        """
        print_info_line("Полный путь", hdf5_path, "📂", value_color=Colors.BRIGHT_BLUE)
        print_info_line("Название файла", Path(hdf5_path).name, "🗃️", value_color=Colors.BRIGHT_CYAN)

        with h5py.File(hdf5_path, 'r') as h5_file:
            radiant_all = h5_file['radiant_heroes'][:].astype(np.int32)
            dire_all = h5_file['dire_heroes'][:].astype(np.int32)
            labels_all = h5_file['radiant_win'][:].astype(np.float32)

        total_matches = radiant_all.shape[0]

        # Обработка составов (сортировка если включена)
        for match_idx in range(total_matches):
            radiant_all[match_idx] = self._process_team_heroes(radiant_all[match_idx], from_ids=False)
            dire_all[match_idx] = self._process_team_heroes(dire_all[match_idx], from_ids=False)

        # Статистика
        radiant_wins = np.sum(labels_all)
        dire_wins = total_matches - radiant_wins
        radiant_win_rate = (radiant_wins / total_matches) * 100

        print_info_line("Всего матчей", f"{total_matches:,}", "🎮", value_color=Colors.BRIGHT_GOLD)
        print_info_line("Побед Radiant", f"{int(radiant_wins):,} ({radiant_win_rate:.1f}%)", "🌞",
                        value_color=Colors.BRIGHT_YELLOW)
        print_info_line("Побед Dire", f"{int(dire_wins):,} ({100 - radiant_win_rate:.1f}%)", "🌑",
                        value_color=Colors.BRIGHT_PURPLE)
        print_info_line("Форма Radiant", f"{radiant_all.shape}", "📐", value_color=Colors.BRIGHT_BLUE)
        print_info_line("Форма Dire", f"{dire_all.shape}", "📐", value_color=Colors.BRIGHT_BLUE)

        # Формирование Dict формата
        features = {
            'radiant_heroes': radiant_all,
            'dire_heroes': dire_all
        }

        return features, labels_all

    def get_data_from_db(self, matches_data: List[Dict]) -> Tuple[Dict[str, np.ndarray], np.ndarray]:
        """
        Преобразует множество матчей из базы данных в numpy массивы.

        Выполняет полную предобработку: hero_id → плотные индексы → сортировка (опционально).

        Args:
            matches_data (List[Dict]): Список матчей, каждый содержит:
                - 'radiant_heroes': список hero_id (5 элементов)
                - 'dire_heroes': список hero_id (5 элементов)
                - 'radiant_win': булев результат

        Returns:
            Tuple[Dict[str, np.ndarray], np.ndarray]:
                - features: {'radiant_heroes': (N, 5), 'dire_heroes': (N, 5)}, dtype int32
                - labels: (N,), dtype float32

        Raises:
            ValueError: При пустых данных
        """
        if not matches_data:
            raise ValueError("Нет матчей для обработки")

        radiant_features = []
        dire_features = []
        labels = []

        for match in matches_data:
            radiant_indices = self._process_team_heroes(match['radiant_heroes'], from_ids=True)
            dire_indices = self._process_team_heroes(match['dire_heroes'], from_ids=True)

            radiant_features.append(radiant_indices)
            dire_features.append(dire_indices)
            labels.append(float(match['radiant_win']))

        print_info_line("Подготовлено матчей", f"{len(labels)}", "🎮", value_color=Colors.BRIGHT_CYAN)

        features = {
            'radiant_heroes': np.array(radiant_features, dtype=np.int32),
            'dire_heroes': np.array(dire_features, dtype=np.int32)
        }
        labels_array = np.array(labels, dtype=np.float32)

        return features, labels_array

    def get_single_match_input(self, radiant_hero_ids: List[int], dire_hero_ids: List[int]) -> Dict[str, np.ndarray]:
        """
        Преобразует составы одного матча в numpy массивы.

        Выполняет полную предобработку: hero_id → плотные индексы → сортировка (опционально).

        Args:
            radiant_hero_ids (List[int]): 5 hero_id команды Radiant
            dire_hero_ids (List[int]): 5 hero_id команды Dire

        Returns:
            Dict[str, np.ndarray]: {'radiant_heroes': (1, 5), 'dire_heroes': (1, 5)}, dtype int32
        """
        radiant_indices = self._process_team_heroes(radiant_hero_ids, from_ids=True)
        dire_indices = self._process_team_heroes(dire_hero_ids, from_ids=True)

        return {
            'radiant_heroes': np.array([radiant_indices], dtype=np.int32),
            'dire_heroes': np.array([dire_indices], dtype=np.int32)
        }
