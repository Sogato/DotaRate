"""
Универсальный модуль загрузки данных для моделей предсказания по составам Dota 2.

Модуль обеспечивает загрузку и предобработку данных из трёх источников
(HDF5 файл, база данных, одиночный матч) и приводит их к единому формату
входных признаков. Один загрузчик обслуживает несколько типов моделей, каждая
из которых решает свою задачу по одним и тем же признакам — составам команд.

Типы целевой переменной (target):
- 'win'   — бинарная классификация: победа Radiant (1) или Dire (0). Метки (N,)
- 'score' — два счёта команд (radiant_score, dire_score). Метки (N, 2)
- 'time'  — длительность матча в секундах (duration). Метки (N,)

Соответствие типа набору полей задаётся словарём TARGET_FIELDS.

Источники данных и их предобработка:
1. HDF5 файлы:
   - Данные уже содержат плотные индексы героев и все целевые поля
   - Признаки и метки читаются напрямую, метки приводятся к float32
   - Возвращает: (features_dict, labels)

2. База данных профессиональных матчей:
   - Запрос матчей с опциональным фильтром по лигам и лимитом
   - Валидация состава и преобразование hero_id → плотные индексы
   - Целевые поля читаются прямо из записи матча (ProMatch)
   - Возвращает: (features_dict, labels)

3. Одиночный матч:
   - Данные содержат исходные hero_id
   - Применяется: hero_id → плотные индексы
   - Возвращает: (features_dict) без меток

Единый формат признаков для всех источников:
{'radiant_heroes': np.ndarray, 'dire_heroes': np.ndarray}
"""

# Стандартные библиотеки
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple, Union

# Сторонние библиотеки
import h5py
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Локальные импорты
from utils.hero_mapper import HeroMapper
from data_bases.pro_matches.models import ProMatch, ProMatchPlayer
from config import (
    DIRE_INDEX,
    EXCLUDED_HERO_IDS,
    PRO_DATABASE_URL,
    RADIANT_INDEX,
    TEAM_SIZE,
)
from utils.console import (
    Colors,
    print_info_line,
    print_subsection_header,
)

# === ПОДКЛЮЧЕНИЕ К БД ПРОФЕССИОНАЛЬНЫХ МАТЧЕЙ ===
engine = create_engine(PRO_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# === КОНСТАНТЫ ЗАГРУЗКИ ИЗ БД ===
DB_PLAYERS_FETCH_CHUNK_SIZE = 1000  # Размер чанка match_id для батчевого запроса игроков

# === СООТВЕТСТВИЕ ТИПА МОДЕЛИ ЦЕЛЕВЫМ ПОЛЯМ ===
TARGET_FIELDS: Dict[str, Tuple[str, ...]] = {
    'win': ('radiant_win',),
    'score': ('radiant_score', 'dire_score'),
    'time': ('duration',),
}


class DataLoaderV1:
    """
    Универсальный загрузчик данных V1 для моделей предсказания по составам Dota 2.

    Класс преобразует данные матчей из различных источников в единый формат:
    плотные индексы героев. Тип целевой переменной выбирается параметром target
    и определяет, какие поля читаются как метки.

    На признаки тип модели не влияет.

    Этапы предобработки признаков:
    1. Преобразование hero_id в плотные индексы (если данные ещё не индексы)
    2. Формирование numpy массивов в Dict формате

    Примечание о порядке героев:
    Состав команды агрегируется в модели через permutation-invariant pooling
    (average pooling по 5 героям), поэтому порядок индексов внутри команды
    не влияет на результат.

    Сортировка составов не выполняется.

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

    @staticmethod
    def _resolve_target_fields(target: str) -> Tuple[str, ...]:
        """
        Возвращает набор целевых полей для указанного типа модели.

        Служит единой точкой проверки параметра target для всех источников:
        неизвестное значение приводит к ошибке вместо обращения к
        несуществующему датасету или атрибуту.

        Args:
            target (str): Тип целевой переменной ('win', 'score' или 'time')

        Returns:
            Tuple[str, ...]: Имена полей, читаемых как метки (в порядке столбцов)

        Raises:
            ValueError: Если target отсутствует в TARGET_FIELDS
        """

        try:
            return TARGET_FIELDS[target]
        except KeyError:
            available = ", ".join(TARGET_FIELDS)
            raise ValueError(f"Неизвестный target '{target}'. Доступные: {available}")

    def _process_team_heroes(self, heroes: Union[List[int], np.ndarray], from_ids: bool = True) -> np.ndarray:
        """
        Преобразует состав команды в плотные индексы.

        Args:
            heroes (Union[List[int], np.ndarray]): Состав команды (5 героев)
            from_ids (bool, optional):
                True - преобразовать hero_id в индексы,
                False - данные уже плотные индексы

        Returns:
            np.ndarray: Массив индексов, shape (5,), dtype int32

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

        return np.array(indices, dtype=np.int32)

    @staticmethod
    def _print_target_statistics(labels_all: np.ndarray, target: str) -> None:
        """
        Выводит статистику меток в зависимости от типа целевой переменной.

        Для 'win' показывает баланс классов (доля побед каждой стороны).
        Для регрессионных типов ('score', 'time') показывает распределение каждого
        целевого поля: среднее ± стандартное отклонение, медиану и диапазон
        (мин–макс). Этого достаточно, чтобы оценить и масштаб таргета, и его
        разброс. Для 'time' значения переводятся из секунд в Ч:ММ:СС / ММ:СС,
        чтобы длительности читались осмысленно.

        Args:
            labels_all (np.ndarray): Метки выборки, (N,) или (N, число_полей)
            target (str): Тип целевой переменной
        """

        total = labels_all.shape[0]
        print_info_line("Всего матчей", f"{total:,}", "🎮",
                        Colors.BLUE_3, Colors.BRIGHT_WHITE)

        # Бинарная классификация: показываем баланс классов
        if target == 'win':
            radiant_wins = int(np.sum(labels_all))
            dire_wins = total - radiant_wins
            radiant_win_rate = (radiant_wins / total) * 100 if total else 0.0
            print_info_line("Побед Radiant", f"{radiant_wins:,} ({radiant_win_rate:.1f}%)", "🌞",
                            Colors.BLUE_3, Colors.GOLD_3)
            print_info_line("Побед Dire", f"{dire_wins:,} ({100 - radiant_win_rate:.1f}%)", "🌑",
                            Colors.BLUE_3, Colors.BRIGHT_PURPLE)
            return

        # Регрессия: распределение по каждому целевому полю
        # reshape к 2D даёт единый перебор столбцов и для (N,), и для (N, K)
        fields = TARGET_FIELDS[target]
        columns = labels_all.reshape(total, -1)

        # Для 'time' значения — секунды, поэтому переводим их в Ч:ММ:СС / ММ:СС.
        # Для остальных таргетов оставляем числа как есть.
        is_time = target == 'time'

        def fmt(value: float, decimals: int = 1) -> str:
            if is_time:
                total_sec = int(round(float(value)))
                hours, rem = divmod(total_sec, 3600)
                minutes, seconds = divmod(rem, 60)
                if hours:
                    return f"{hours}:{minutes:02d}:{seconds:02d}"
                return f"{minutes}:{seconds:02d}"
            return f"{value:.{decimals}f}"

        for col_idx, field in enumerate(fields):
            col = columns[:, col_idx]

            # Эмодзи и цвет под поле, в палитре баланса побед:
            if field == 'radiant_score':
                emoji, value_color = "🌞", Colors.GOLD_3
            elif field == 'dire_score':
                emoji, value_color = "🌑", Colors.BRIGHT_PURPLE
            elif field == 'duration':
                emoji, value_color = "⏱️", Colors.BRIGHT_GREEN
            else:
                emoji, value_color = "📊", Colors.BRIGHT_CYAN

            stats = (f"{fmt(col.mean())} ± {fmt(col.std())} | "
                     f"медиана {fmt(np.median(col), decimals=0)} | "
                     f"от {fmt(col.min(), decimals=0)} до {fmt(col.max(), decimals=0)}")
            print_info_line(field, stats, emoji,
                            Colors.BLUE_3, value_color)

    def load_data_from_hdf5(self,
                            hdf5_path: str,
                            target: str) -> Tuple[Dict[str, np.ndarray], np.ndarray]:
        """
        Загружает данные из HDF5 файла под выбранный тип модели.

        Предполагается, что HDF5 файл содержит предобработанные плотные индексы
        героев и все целевые поля. Признаки читаются всегда одни и те же, а метки
        собираются из полей, заданных типом target.

        Args:
            hdf5_path (str): Путь к HDF5 файлу с данными
            target (str): Тип целевой переменной ('win', 'score' или 'time')

        Returns:
            Tuple[Dict[str, np.ndarray], np.ndarray]:
                - features: {'radiant_heroes': (N, 5), 'dire_heroes': (N, 5)}, dtype int32
                - labels: (N,) для одного поля или (N, K) для нескольких, dtype float32

        Raises:
            ValueError: Если target неизвестен или HDF5 файл не содержит данных
            FileNotFoundError: Если HDF5 файл не найден
            OSError: Если не удаётся получить доступ к файлу
        """

        fields = self._resolve_target_fields(target)

        print_subsection_header("Загрузка данных из HDF5", "📂", Colors.BLUE_2)
        print_info_line("Полный путь", hdf5_path, "📂",
                        Colors.BLUE_3, Colors.AMBER_4)
        print_info_line("Название файла", Path(hdf5_path).name, "🗃️",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("Тип модели", target, "🎯",
                        Colors.BLUE_3, Colors.BRIGHT_YELLOW)

        try:
            with h5py.File(hdf5_path, 'r') as h5_file:
                radiant_all = h5_file['radiant_heroes'][:].astype(np.int32)
                dire_all = h5_file['dire_heroes'][:].astype(np.int32)
                # Каждое целевое поле читается отдельным столбцом
                target_columns = [h5_file[field][:].astype(np.float32) for field in fields]
        except FileNotFoundError:
            raise FileNotFoundError(f"HDF5 файл не найден: {hdf5_path}")
        except OSError as e:
            raise OSError(f"Ошибка доступа к HDF5 файлу: {e}")

        total_matches = radiant_all.shape[0]

        # Проверка на пустой датасет
        if total_matches == 0:
            raise ValueError("HDF5 файл не содержит данных")

        # Один столбец → (N,), несколько → (N, K)
        labels_all = target_columns[0] if len(target_columns) == 1 else np.stack(target_columns, axis=1)

        # Статистика меток и размерности признаков
        self._print_target_statistics(labels_all, target)
        print_info_line("Размерность Radiant", f"{radiant_all.shape}", "📐",
                        Colors.BLUE_3, Colors.BLUE_4)
        print_info_line("Размерность Dire", f"{dire_all.shape}", "📐",
                        Colors.BLUE_3, Colors.BLUE_4)

        features = {
            'radiant_heroes': radiant_all,
            'dire_heroes': dire_all
        }

        return features, labels_all

    @staticmethod
    def _fetch_players_by_match(session, match_ids: List[int]) -> Dict[int, List[ProMatchPlayer]]:
        """
        Батчево загружает игроков для всех матчей одним проходом.

        Все match_id режутся на чанки и выбираются через IN (...), после чего игроки
        группируются по match_id в словарь.

        Чанкование нужно, чтобы не упереться в лимит числа параметров запроса у
        драйвера БД при IN на десятки тысяч ID.

        Args:
            session: Активная сессия SQLAlchemy
            match_ids (List[int]): ID матчей, для которых нужны игроки

        Returns:
            Dict[int, List[ProMatchPlayer]]: Игроки, сгруппированные по match_id.
                Матчи без игроков в словарь не попадают (вызывающий код трактует
                отсутствие как неполный состав и пропускает матч).
        """

        players_by_match: Dict[int, List[ProMatchPlayer]] = defaultdict(list)

        # Поэтапная выборка чанками match_id, чтобы IN (...) не разрастался безгранично
        for chunk_start in range(0, len(match_ids), DB_PLAYERS_FETCH_CHUNK_SIZE):
            chunk_ids = match_ids[chunk_start:chunk_start + DB_PLAYERS_FETCH_CHUNK_SIZE]
            chunk_players = session.query(ProMatchPlayer).filter(
                ProMatchPlayer.match_id.in_(chunk_ids)
            ).all()
            for player in chunk_players:
                players_by_match[player.match_id].append(player)

        return players_by_match

    def load_data_from_db(self,
                          target: str,
                          limit: Optional[int] = None,
                          league_ids: Optional[Set[int]] = None) -> Tuple[Dict[str, np.ndarray], np.ndarray]:
        """
        Загружает данные из БД профессиональных матчей под выбранный тип модели.

        Сначала выбираются сами матчи (с опциональным фильтром по лигам и лимитом),
        затем игроки всех матчей загружаются единым батчевым запросом через
        _fetch_players_by_match, и валидный состав каждого матча конвертируется
        hero_id → плотные индексы. Целевые поля читаются прямо из записи матча по
        именам из TARGET_FIELDS.

        Args:
            limit (Optional[int]): Максимум матчей в выборке (None = все).
                Из-за отсева невалидных матчей загружено может быть чуть меньше.
            league_ids (Optional[Set[int]]): Фильтр по лигам (пустое/None = все).
            target (str): Тип целевой переменной ('win', 'score' или 'time')

        Returns:
            Tuple[Dict[str, np.ndarray], np.ndarray]:
                - features: {'radiant_heroes': (N, 5), 'dire_heroes': (N, 5)}, dtype int32
                - labels: (N,) для одного поля или (N, K) для нескольких, dtype float32

        Raises:
            ValueError: Если target неизвестен или из БД не получено ни одного
                валидного матча.

        Note:
            Матч пропускается со счётчиком, если в нём не 10 игроков, состав не
            бьётся по TEAM_SIZE на команду, либо встречается hero_id вне маппера
            (исключённый/новый герой). Ошибки БД (SQLAlchemyError) пробрасываются
            наверх — их ловит общий обработчик у вызывающего кода.
        """

        fields = self._resolve_target_fields(target)

        print_subsection_header("Загрузка данных из БД", "📂", Colors.BLUE_2)
        print_info_line("Тип цели", target, "🎯",
                        Colors.BLUE_3, Colors.BRIGHT_YELLOW)

        session = SessionLocal()
        try:
            # Запрос к матчам с опциональным фильтром по лигам
            query = session.query(ProMatch)
            if league_ids:
                query = query.filter(ProMatch.leagueid.in_(league_ids))
                leagues_str = ", ".join(map(str, sorted(league_ids)))
                print_info_line("Фильтр по лигам", f"ID [{leagues_str}]", "🏆",
                                Colors.BLUE_3, Colors.BRIGHT_PURPLE)
            if limit:
                query = query.limit(limit)

            matches = query.all()
            print_info_line("Найдено матчей в БД", f"{len(matches):,}", "📊",
                            Colors.BLUE_3, Colors.BRIGHT_CYAN)

            # Батчевая загрузка игроков всех матчей разом
            match_ids = [match.match_id for match in matches]
            players_by_match = self._fetch_players_by_match(session, match_ids)

            # Накопители сразу в виде индексов — без промежуточного List[Dict]
            radiant_features = []
            dire_features = []
            labels = []
            skipped_count = 0

            for match in matches:
                players = players_by_match.get(match.match_id, [])

                # Отсев неполного состава
                if len(players) != 10:
                    skipped_count += 1
                    continue

                radiant_heroes = [p.hero_id for p in players if p.team_number == RADIANT_INDEX]
                dire_heroes = [p.hero_id for p in players if p.team_number == DIRE_INDEX]

                # Отсев некорректного распределения по командам
                if len(radiant_heroes) != TEAM_SIZE or len(dire_heroes) != TEAM_SIZE:
                    skipped_count += 1
                    continue

                # hero_id → плотные индексы; матч с неизвестным героем пропускаем
                try:
                    radiant_indices = self._process_team_heroes(radiant_heroes, from_ids=True)
                    dire_indices = self._process_team_heroes(dire_heroes, from_ids=True)
                except ValueError:
                    skipped_count += 1
                    continue

                # Целевые поля берём прямо из записи матча по именам из TARGET_FIELDS.
                # Один источник имён для БД и HDF5 исключает рассинхрон полей.
                target_values = [float(getattr(match, field)) for field in fields]

                radiant_features.append(radiant_indices)
                dire_features.append(dire_indices)
                labels.append(target_values[0] if len(fields) == 1 else target_values)
        finally:
            session.close()

        total_matches = len(labels)
        if total_matches == 0:
            raise ValueError("БД не вернула ни одного валидного матча")

        radiant_all = np.array(radiant_features, dtype=np.int32)
        dire_all = np.array(dire_features, dtype=np.int32)
        # np.array сам даёт (N,) для скаляров и (N, K) для списков значений
        labels_all = np.array(labels, dtype=np.float32)

        if skipped_count > 0:
            print_info_line("Пропущено матчей", f"{skipped_count:,}", "⚠️",
                            Colors.BLUE_3, Colors.BRIGHT_ORANGE)

        # Статистика меток и размерности признаков
        self._print_target_statistics(labels_all, target)
        print_info_line("Размерность Radiant", f"{radiant_all.shape}", "📐",
                        Colors.BLUE_3, Colors.BLUE_4)
        print_info_line("Размерность Dire", f"{dire_all.shape}", "📐",
                        Colors.BLUE_3, Colors.BLUE_4)

        features = {
            'radiant_heroes': radiant_all,
            'dire_heroes': dire_all
        }

        return features, labels_all

    def get_single_match_input(self, radiant_hero_ids: List[int], dire_hero_ids: List[int]) -> Dict[str, np.ndarray]:
        """
        Преобразует составы одного матча в numpy массивы.

        Выполняет полную предобработку: hero_id → плотные индексы.

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
