"""
УСТАРЕВШИЙ КОД, НЕ ИСПОЛЬЗУЕТСЯ В АКТУАЛЬНОЙ РЕАЛИЗАЦИИ

Универсальный препроцессор для создания TFRecord файлов из данных матчей Dota 2.

Модуль предоставляет функциональность для преобразования данных матчей из базы данных
в формат TFRecord, подходящий для обучения различных моделей машинного обучения.

Основные возможности:
- Загрузка данных матчей из PostgreSQL базы данных с пагинацией
- Преобразование hero_id в плотные индексы через HeroMapper для оптимального представления
- Фильтрация и исключение определенных героев из обработки
- Нормализация и сортировка составов команд для независимости от порядка
- Автоматическое разделение данных на тестовую и основную выборки
- Включение дополнительных полей матча и игроков для расширенного анализа
- Валидация созданных TFRecord файлов с детальной статистикой

Структура выходных данных:
- match_id: Уникальный идентификатор матча
- radiant_heroes/dire_heroes: Составы команд (плотные индексы)
- radiant_win: Результат матча (0/1)
- duration: Длительность матча в секундах
- radiant_score/dire_score: Счет команд (убийства)
- tower_status_*: Статус башен команд
- barracks_status_*: Статус казарм команд
- *_hero_variants: Варианты героев (аспекты)
- *_roles: Роли игроков (core=0, support=1)

Технические особенности:
- Использует keyset пагинацию для эффективной обработки больших датасетов
- Применяет reservoir sampling для равномерного распределения тестовой выборки
- Разбиение train/test фиксируется сидом RANDOM_SEED
- Обеспечивает детерминированную сортировку героев внутри команд
- Поддерживает исключение героев через конфигурируемый список
- Создает два отдельных TFRecord файла для различных сценариев использования
"""

import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '1'

# Стандартные библиотеки
import random
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Сторонние библиотеки
import numpy as np
import tensorflow as tf
from sqlalchemy import and_, create_engine, func, select
from sqlalchemy.orm import sessionmaker

# Локальные импорты
from config import (
    DATASET_DATABASE_URL,
    DIRE_INDEX,
    DOTA_VERSION,
    EXCLUDED_HERO_IDS,
    RADIANT_INDEX,
    ROLE_MAPPING,
    TEAM_SIZE,
)
from data_bases.dataset.models import Match, MatchPlayer
from utils.console import (
    Colors,
    print_info_line,
    print_progress_bar,
    print_section_header,
    print_status_message,
    print_subsection_header,
)
from utils.hero_mapper import HeroMapper

# === НАСТРОЙКИ TFRECORD ПРЕПРОЦЕССОРА ===
TFRECORD_FILE_NAME = "TFRecord_dataset"     # Базовое имя выходных файлов
CHUNK_SIZE = 100_000                        # Размер чанка для потоковой обработки
TEST_DATASET_SIZE = 10_000                  # Размер тестовой выборки
VALIDATION_SAMPLE_SIZE = 10_000             # Количество записей для валидации
RANDOM_SEED = 42                            # Сид разбиения train/test


class TFRecordPreprocessor:
    """
    Универсальный препроцессор для создания TFRecord файлов из данных матчей Dota 2.

    Класс обеспечивает полный цикл обработки данных: от загрузки из базы данных
    до создания оптимизированных TFRecord файлов для машинного обучения.
    Поддерживает разделение данных на тестовую и основную выборки с использованием
    статистически корректного reservoir sampling алгоритма.

    Основные этапы обработки:
    1. Подключение к базе данных и инициализация маппера героев
    2. Потоковая загрузка данных матчей с фильтрацией
    3. Преобразование hero_id в плотные индексы
    4. Нормализация составов команд (сортировка по индексам)
    5. Случайное разделение на тестовую и основную выборки (фиксируется RANDOM_SEED)
    6. Сериализация в TFRecord формат
    7. Валидация созданных файлов

    Attributes:
        test_output_path (str): Путь для сохранения тестового TFRecord файла
        main_output_path (str): Путь для сохранения основного TFRecord файла
        engine: SQLAlchemy движок для подключения к базе данных
        session_maker: Фабрика сессий SQLAlchemy для работы с БД
        hero_mapper (HeroMapper): Маппер для преобразования hero_id в плотные индексы
    """

    def __init__(self, base_output_path: str, database_url: str = DATASET_DATABASE_URL):
        """
        Инициализирует препроцессор с настройкой подключений и создание выходных путей.

        Создает два выходных файла: основной для обучения и тестовый для валидации.
        Инициализирует подключение к базе данных и маппер героев с исключениями.

        Args:
            base_output_path (str): Базовый путь для формирования имен выходных файлов
            database_url (str, optional): URL подключения к базе данных.
                По умолчанию используется DATASET_DATABASE_URL из конфигурации

        Raises:
            Exception: При ошибках подключения к базе данных или инициализации маппера
        """
        # Формируем пути для тестового и основного файлов
        base_name = os.path.splitext(base_output_path)[0]
        self.test_output_path = f"{base_name}_test.tfrecord"
        self.main_output_path = f"{base_name}_main.tfrecord"

        print_info_line("Основной файл", os.path.basename(self.main_output_path), "📚", value_color=Colors.BRIGHT_GREEN)
        print_info_line("Тестовый файл", os.path.basename(self.test_output_path), "🧪", value_color=Colors.BRIGHT_BLUE)

        # Инициализация подключения к базе данных
        try:
            self.engine = create_engine(database_url)
            self.session_maker = sessionmaker(bind=self.engine)
        except Exception as e:
            print_status_message(f"Ошибка подключения к базе данных: {e}", "error", "❌")
            raise

        # Инициализация маппера героев с выводом статистики
        print_subsection_header("Инициализация маппера героев", "🗺️", Colors.BRIGHT_PURPLE)
        self.hero_mapper = HeroMapper(excluded_hero_ids=EXCLUDED_HERO_IDS)
        mapper_info = self.hero_mapper.get_mapping_info()
        print_info_line("Диапазон индексов", f"{mapper_info['index_range']}", "🔢")
        print_info_line("Исключено героев", f"{mapper_info['excluded_heroes_count']}", "❌",
                        value_color=Colors.BRIGHT_RED)
        print_info_line("Итого героев", f"{mapper_info['total_heroes']:,}", "🧙‍♂️")

    def _get_total_matches_count(self) -> int:
        """
        Получает общее количество матчей в базе данных для планирования обработки.

        Returns:
            int: Общее количество записей в таблице Match

        Note:
            Используется для расчета количества чанков и прогресс-индикации
        """
        with self.session_maker() as session:
            count = session.scalar(select(func.count(Match.match_id)))
        return count

    def _validate_team_composition(self, radiant_heroes: List[int], dire_heroes: List[int]) -> bool:
        """
        Проверяет корректность состава команд для включения в датасет.

        Валидирует что все герои присутствуют в маппере (не исключены из обработки).

        Args:
            radiant_heroes (List[int]): Список hero_id команды Radiant
            dire_heroes (List[int]): Список hero_id команды Dire

        Returns:
            bool: True если состав корректен и может быть включен в датасет

        Note:
            Функция проверяет только базовые требования валидности.
            Дополнительные бизнес-правила применяются на уровне SQL запросов.
        """

        # Проверка, что все герои есть в маппере (не исключены)
        all_heroes = radiant_heroes + dire_heroes
        for hero_id in all_heroes:
            if hero_id not in self.hero_mapper.hero_to_index:
                return False
        return True

    def _convert_team_to_indices(self, hero_ids: List[int], variants: List[int], roles: List[str]) -> Tuple[
        List[int], List[int], List[int]]:
        """
        Преобразует данные команды в нормализованный формат для датасета.

        Выполняет следующие операции:
        1. Конвертирует hero_id в плотные индексы через маппер
        2. Преобразует строковые роли в числовые значения
        3. Сортирует всю команду по индексам героев для детерминированности

        Args:
            hero_ids (List[int]): Список идентификаторов героев
            variants (List[int]): Список вариантов героев (аспекты)
            roles (List[str]): Список ролей игроков ('core'/'support')

        Returns:
            Tuple[List[int], List[int], List[int]]: Кортеж из отсортированных списков:
                - Плотные индексы героев
                - Варианты героев (в том же порядке)
                - Числовые роли (в том же порядке)

        Note:
            Сортировка по индексам героев обеспечивает независимость от порядка игроков в исходных данных.
        """
        # Преобразуем роли в числовые значения
        role_ints = [ROLE_MAPPING[role] for role in roles]

        # Создаем кортежи для сортировки: (плотный_индекс, вариант, роль_число)
        team_data = [(self.hero_mapper.hero_to_index[hero_id], variant, role_int) for hero_id, variant, role_int in
                     zip(hero_ids, variants, role_ints)]

        # Сортируем по плотному индексу героя
        sorted_team_data = sorted(team_data, key=lambda x: x[0])

        # Разделяем отсортированные данные обратно на отдельные списки
        indices, variants_sorted, roles_sorted = zip(*sorted_team_data)

        return list(indices), list(variants_sorted), list(roles_sorted)

    def _load_matches_chunk(self, last_id: Optional[int], limit: int) -> List[Dict[str, Any]]:
        """
        Загружает чанк матчей из базы данных с использованием keyset пагинации.

        Выполняет SQL запрос с агрегацией данных игроков по командам
        и применением фильтров для обеспечения качества данных.
        Использует keyset пагинацию для эффективной работы с большими объемами данных.

        Args:
            last_id (Optional[int]): Последний обработанный match_id для пагинации.
                None для первого чанка
            limit (int): Максимальное количество матчей для загрузки в чанке

        Returns:
            List[Dict[str, Any]]: Список словарей с данными матчей, включающий:
                - Основную информацию матча (id, результат, длительность, счет)
                - Статусы построек (башни, казармы)
                - Агрегированные данные игроков по командам

        Note:
            Функция применяет фильтры на уровне SQL для исключения некорректных
            матчей и героев из EXCLUDED_HERO_IDS. Результаты дополнительно
            валидируются через _validate_team_composition.
        """
        chunk_data = []

        with self.session_maker() as session:
            # Основной запрос с агрегацией данных игроков по командам
            query = (
                session.query(
                    Match.match_id,
                    Match.radiant_win,
                    Match.duration,
                    Match.radiant_score,
                    Match.dire_score,
                    Match.tower_status_radiant,
                    Match.tower_status_dire,
                    Match.barracks_status_radiant,
                    Match.barracks_status_dire,
                    # Агрегация героев по командам
                    func.array_agg(MatchPlayer.hero_id).filter(MatchPlayer.team_number == RADIANT_INDEX).label(
                        "radiant_heroes"),
                    func.array_agg(MatchPlayer.hero_id).filter(MatchPlayer.team_number == DIRE_INDEX).label(
                        "dire_heroes"),
                    # Агрегация вариантов героев по командам
                    func.array_agg(MatchPlayer.hero_variant).filter(MatchPlayer.team_number == RADIANT_INDEX).label(
                        "radiant_variants"),
                    func.array_agg(MatchPlayer.hero_variant).filter(MatchPlayer.team_number == DIRE_INDEX).label(
                        "dire_variants"),
                    # Агрегация ролей по командам
                    func.array_agg(MatchPlayer.role).filter(MatchPlayer.team_number == RADIANT_INDEX).label(
                        "radiant_roles"),
                    func.array_agg(MatchPlayer.role).filter(MatchPlayer.team_number == DIRE_INDEX).label("dire_roles"),
                )
                .join(MatchPlayer, Match.match_id == MatchPlayer.match_id)
                .filter(MatchPlayer.hero_id.notin_(EXCLUDED_HERO_IDS))  # Исключаем нежелательных героев
                .group_by(
                    Match.match_id,
                    Match.radiant_win,
                    Match.duration,
                    Match.radiant_score,
                    Match.dire_score,
                    Match.tower_status_radiant,
                    Match.tower_status_dire,
                    Match.barracks_status_radiant,
                    Match.barracks_status_dire
                )
                .having(
                    and_(
                        func.count(MatchPlayer.id).filter(MatchPlayer.team_number == RADIANT_INDEX) == TEAM_SIZE,
                        func.count(MatchPlayer.id).filter(MatchPlayer.team_number == DIRE_INDEX) == TEAM_SIZE
                    )
                )
            )

            # Применяем keyset пагинацию для эффективности
            if last_id is not None:
                query = query.filter(Match.match_id > last_id)

            query = query.order_by(Match.match_id).limit(limit)

            # Обработка результатов запроса
            for row in query:
                match_id, radiant_win, duration, radiant_score, dire_score, \
                    tower_status_radiant, tower_status_dire, barracks_status_radiant, barracks_status_dire, \
                    radiant_heroes, dire_heroes, radiant_variants, dire_variants, radiant_roles, dire_roles = row

                # Финальная валидация состава команд
                if self._validate_team_composition(radiant_heroes, dire_heroes):
                    chunk_data.append({
                        "match_id": match_id,
                        "radiant_win": radiant_win,
                        "duration": duration,
                        "radiant_score": radiant_score,
                        "dire_score": dire_score,
                        "tower_status_radiant": tower_status_radiant,
                        "tower_status_dire": tower_status_dire,
                        "barracks_status_radiant": barracks_status_radiant,
                        "barracks_status_dire": barracks_status_dire,
                        "radiant_heroes": radiant_heroes,
                        "dire_heroes": dire_heroes,
                        "radiant_variants": radiant_variants,
                        "dire_variants": dire_variants,
                        "radiant_roles": radiant_roles,
                        "dire_roles": dire_roles,
                    })

        return chunk_data

    def _create_match_example(self, match_data: Dict[str, Any]) -> tf.train.Example:
        """
        Создает tf.train.Example для записи в TFRecord файл.

        Преобразует данные матча в формат TensorFlow Example с нормализованными
        составами команд и всеми необходимыми полями.

        Args:
            match_data (Dict[str, Any]): Словарь с данными матча, содержащий
                все поля, загруженные из базы данных

        Returns:
            tf.train.Example: Сериализуемый объект Example для TFRecord файла

        Note:
            Функция выполняет преобразование булевого radiant_win в целое число
            и применяет нормализацию команд через _convert_team_to_indices.
            Все числовые значения сохраняются как int64 для совместимости.
        """
        # Преобразуем булевое значение победы в целое число
        radiant_win_int = 1 if match_data['radiant_win'] else 0

        # Конвертируем и сортируем составы команд
        radiant_indices, radiant_variants, radiant_roles = self._convert_team_to_indices(
            match_data['radiant_heroes'], match_data['radiant_variants'], match_data['radiant_roles']
        )
        dire_indices, dire_variants, dire_roles = self._convert_team_to_indices(
            match_data['dire_heroes'], match_data['dire_variants'], match_data['dire_roles']
        )

        # Создание структуры Features для TFRecord
        features = {
            # Идентификация матча
            'match_id': tf.train.Feature(int64_list=tf.train.Int64List(value=[match_data['match_id']])),

            # Составы команд (нормализованные плотные индексы)
            'radiant_heroes': tf.train.Feature(int64_list=tf.train.Int64List(value=radiant_indices)),
            'dire_heroes': tf.train.Feature(int64_list=tf.train.Int64List(value=dire_indices)),

            # Результат матча (целевая переменная)
            'radiant_win': tf.train.Feature(int64_list=tf.train.Int64List(value=[radiant_win_int])),

            # Временные характеристики матча
            'duration': tf.train.Feature(int64_list=tf.train.Int64List(value=[match_data['duration']])),

            # Боевая статистика команд
            'radiant_score': tf.train.Feature(int64_list=tf.train.Int64List(value=[match_data['radiant_score']])),
            'dire_score': tf.train.Feature(int64_list=tf.train.Int64List(value=[match_data['dire_score']])),

            # Статус игровых построек
            'tower_status_radiant': tf.train.Feature(
                int64_list=tf.train.Int64List(value=[match_data['tower_status_radiant']])),
            'tower_status_dire': tf.train.Feature(
                int64_list=tf.train.Int64List(value=[match_data['tower_status_dire']])),
            'barracks_status_radiant': tf.train.Feature(
                int64_list=tf.train.Int64List(value=[match_data['barracks_status_radiant']])),
            'barracks_status_dire': tf.train.Feature(
                int64_list=tf.train.Int64List(value=[match_data['barracks_status_dire']])),

            # Дополнительные характеристики героев
            'radiant_hero_variants': tf.train.Feature(int64_list=tf.train.Int64List(value=radiant_variants)),
            'dire_hero_variants': tf.train.Feature(int64_list=tf.train.Int64List(value=dire_variants)),

            # Роли игроков в командах
            'radiant_roles': tf.train.Feature(int64_list=tf.train.Int64List(value=radiant_roles)),
            'dire_roles': tf.train.Feature(int64_list=tf.train.Int64List(value=dire_roles)),
        }

        return tf.train.Example(features=tf.train.Features(feature=features))

    def create_tfrecord(self):
        """
        Создает TFRecord файлы с разделением данных на тестовую и основную выборки.

        Основной метод для создания датасета. Выполняет полный цикл обработки:
        1. Анализ объема данных в базе и планирование обработки
        2. Потоковую загрузку данных по чанкам с keyset пагинацией
        3. Применение reservoir sampling для равномерного распределения тестовой выборки
        4. Запись данных в два отдельных TFRecord файла
        5. Вывод детальной статистики процесса

        Алгоритм разделения данных:
        1. Первые TEST_DATASET_SIZE примеров попадают в reservoir (буфер тестовой выборки)
        2. Каждый последующий пример с вероятностью TEST_DATASET_SIZE/processed_count
          заменяет случайный пример в reservoir
        3. Вытесненные из reservoir примеры записываются в основной файл
        4. В конце обработки весь reservoir записывается в тестовый файл

        Случайность reservoir sampling фиксируется изолированным генератором с сидом
        RANDOM_SEED, поэтому разбиение воспроизводимо между прогонами.

        Raises:
            ValueError: Если в базе данных не найдено валидных матчей для обработки
            Exception: При ошибках работы с базой данных или файловой системой

        Note:
            Использует reservoir sampling для обеспечения статистически корректного
            случайного распределения примеров между выборками независимо от порядка
            обработки данных в базе.
        """
        print_section_header("СОЗДАНИЕ TFRECORD ФАЙЛОВ", "🔧", width=100, color=Colors.BRIGHT_GOLD)

        # === ЭТАП 1: ПРЕДВАРИТЕЛЬНАЯ ПОДГОТОВКА ===
        print_subsection_header("Предварительная подготовка", "🛠️", Colors.BRIGHT_CYAN)

        print_info_line("Основной файл", self.main_output_path, "📚", value_color=Colors.BRIGHT_YELLOW)
        print_info_line("Тестовый файл", self.test_output_path, "🧪", value_color=Colors.BRIGHT_BLUE)

        # Получение общего количества матчей для планирования
        total_matches = self._get_total_matches_count()
        if total_matches == 0:
            print_status_message("В базе данных не найдено матчей для обработки!", "error", "❌")
            return

        # Создание выходных директорий
        print_status_message("Создание выходных директорий...", "info", "📁")
        os.makedirs(os.path.dirname(self.test_output_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.main_output_path), exist_ok=True)
        print_status_message("Директории созданы", "success", "✅")

        # === ЭТАП 2: АНАЛИЗ ПАРАМЕТРОВ ОБРАБОТКИ ===
        print_subsection_header("Параметры обработки", "📋", Colors.BRIGHT_ORANGE)
        print_info_line("Алгоритм выборки тестового файла", "Reservoir Sampling", "🎲", value_color=Colors.BRIGHT_TEAL)
        print_info_line("Сид разбиения", f"{RANDOM_SEED}", "🎲", value_color=Colors.BRIGHT_YELLOW)
        print_info_line("Общее количество матчей", f"{total_matches:,}", "🎮", value_color=Colors.BRIGHT_GOLD)
        print_info_line("Размер тестового набора", f"{TEST_DATASET_SIZE:,}", "🧪", value_color=Colors.BRIGHT_BLUE)
        print_info_line("Размер чанка", f"{CHUNK_SIZE:,}", "🗂️", value_color=Colors.BRIGHT_YELLOW)

        total_chunks = (total_matches + CHUNK_SIZE - 1) // CHUNK_SIZE
        print_info_line("Всего чанков", f"{total_chunks}", "📊", value_color=Colors.BRIGHT_PURPLE)

        # Примерная оценка времени выполнения
        estimated_time = (total_chunks * 15) / 60
        print_info_line("Ожидаемое время", f"~{estimated_time:.1f} минут", "⏱️", value_color=Colors.BRIGHT_GOLD)

        # === ЭТАП 3: ИНИЦИАЛИЗАЦИЯ ОБРАБОТКИ ===
        print_subsection_header("Обработка данных по чанкам", "🚀", Colors.BRIGHT_GREEN)

        # Инициализация писателей TFRecord файлов
        main_writer = tf.io.TFRecordWriter(self.main_output_path)
        test_writer = tf.io.TFRecordWriter(self.test_output_path)

        # Изолированный генератор для reservoir sampling: фиксированный сид делает
        # разбиение воспроизводимым и не затрагивает глобальное состояние random.
        rng = random.Random(RANDOM_SEED)

        # Инициализация переменных для reservoir sampling
        reservoir: List[tf.train.Example] = []  # Буфер для тестовой выборки
        processed_matches = 0  # Общий счетчик обработанных матчей
        valid_matches = 0  # Счетчик валидных матчей
        main_written = 0  # Счетчик записей в основной файл
        total_processing_time = 0  # Накопительное время обработки

        last_id: Optional[int] = None  # Для keyset пагинации
        overall_start = time.time()

        # === ЭТАП 4: ОСНОВНОЙ ЦИКЛ ОБРАБОТКИ ПО ЧАНКАМ ===
        for chunk_idx in range(1, total_chunks + 1):
            chunk_start_time = time.time()

            # Загрузка текущего чанка данных
            chunk_size = min(CHUNK_SIZE, total_matches - processed_matches)
            chunk_data = self._load_matches_chunk(last_id, chunk_size)

            # Обновление указателя для следующей итерации пагинации
            if chunk_data:
                last_id = chunk_data[-1]["match_id"]

            # Обработка каждого матча в чанке
            chunk_valid = 0
            for match_data in chunk_data:
                processed_matches += 1
                example = self._create_match_example(match_data)

                # === RESERVOIR SAMPLING АЛГОРИТМ ===
                if len(reservoir) < TEST_DATASET_SIZE:
                    # Фаза заполнения reservoir: добавляем примеры до достижения лимита
                    reservoir.append(example)
                else:
                    # Фаза замещения: с определенной вероятностью заменяем элемент в reservoir
                    random_position = rng.randint(1, processed_matches)
                    if random_position <= TEST_DATASET_SIZE:
                        # Выбираем случайный элемент для замены
                        reservoir_idx = rng.randint(0, TEST_DATASET_SIZE - 1)
                        # Вытесненный элемент отправляем в основной файл
                        main_writer.write(reservoir[reservoir_idx].SerializeToString())
                        main_written += 1
                        reservoir[reservoir_idx] = example
                    else:
                        # Пример не попадает в тестовую выборку, записываем в основной файл
                        main_writer.write(example.SerializeToString())
                        main_written += 1

                valid_matches += 1
                chunk_valid += 1

            # Измерение производительности чанка
            chunk_time = time.time() - chunk_start_time
            total_processing_time += chunk_time

            # Расчет ETA на основе средней скорости обработки
            avg_time_per_chunk = total_processing_time / chunk_idx
            eta_minutes = ((total_chunks - chunk_idx) * avg_time_per_chunk) / 60

            # Вывод статистики обработки чанка
            print(f"{Colors.BRIGHT_BLUE}📦 Чанк #{chunk_idx}{Colors.RESET} | "
                  f"📊 Загружено: {Colors.BRIGHT_YELLOW}{chunk_size:,}{Colors.RESET} | "
                  f"💾 Всего записано: {Colors.BRIGHT_PURPLE}{valid_matches:,}{Colors.RESET} | "
                  f"⏱️ {Colors.BRIGHT_MAGENTA}{chunk_time:.1f}с{Colors.RESET} | "
                  f"⏳ ETA: {Colors.BRIGHT_GOLD}{eta_minutes:.1f}м{Colors.RESET}")

            # Прогресс-бар общего выполнения
            print_progress_bar(chunk_idx, total_chunks, "Общий прогресс:", 40, Colors.BRIGHT_GREEN, Colors.DIM)

        # Проверка на наличие валидных данных
        if valid_matches == 0:
            print_status_message("Не найдено валидных матчей для обработки!", "error", "❌")
            raise ValueError("Не найдено валидных матчей для обработки!")

        # === ЭТАП 5: ФИНАЛИЗАЦИЯ ЗАПИСИ ===
        # Записываем весь reservoir в тестовый файл
        test_written = 0
        for example in reservoir:
            test_writer.write(example.SerializeToString())
            test_written += 1

        # Закрытие файлов
        main_writer.close()
        test_writer.close()

        overall_time = time.time() - overall_start

        # === ЭТАП 6: ИТОГОВАЯ СТАТИСТИКА ===
        print_section_header("РЕЗУЛЬТАТЫ", "🎊", width=100, color=Colors.BRIGHT_GREEN)

        # Расчет размеров файлов
        main_size = os.path.getsize(self.main_output_path) / (1024 * 1024)
        test_size = os.path.getsize(self.test_output_path) / (1024 * 1024)
        total_size = main_size + test_size

        # Статистика основного файла
        print_subsection_header("Статистика основного файла", "📚", Colors.BRIGHT_BLUE)
        print_info_line("Имя файла", os.path.basename(self.main_output_path), "📄", value_color=Colors.BRIGHT_YELLOW)
        print_info_line("Размер файла", f"{main_size:.2f} MB", "📏", value_color=Colors.BRIGHT_CYAN)
        print_info_line("Количество матчей", f"{main_written:,}", "🎯", value_color=Colors.BRIGHT_GREEN)
        print_info_line("Средний размер записи",
                        f"{(main_size * 1024) / main_written:.1f} KB" if main_written > 0 else "N/A", "📐",
                        value_color=Colors.BRIGHT_PURPLE)

        # Статистика тестового файла
        print_subsection_header("Статистика тестового файла", "🧪", Colors.BRIGHT_ORANGE)
        print_info_line("Имя файла", os.path.basename(self.test_output_path), "📄", value_color=Colors.BRIGHT_YELLOW)
        print_info_line("Размер файла", f"{test_size:.2f} MB", "📏", value_color=Colors.BRIGHT_CYAN)
        print_info_line("Количество матчей", f"{test_written:,}", "🎯", value_color=Colors.BRIGHT_GREEN)
        print_info_line("Средний размер записи",
                        f"{(test_size * 1024) / test_written:.1f} KB" if test_written > 0 else "N/A", "📐",
                        value_color=Colors.BRIGHT_PURPLE)

        # Общая статистика
        print_subsection_header("Общая статистика", "📊", Colors.BRIGHT_PURPLE)
        print_info_line("Общий размер", f"{total_size:.2f} MB", "💾", value_color=Colors.BRIGHT_GOLD)
        print_info_line("Всего матчей", f"{valid_matches:,}", "🎮", value_color=Colors.BRIGHT_WHITE)
        print_info_line("Разделение",
                        f"Основной: {(main_written / valid_matches) * 100:.1f}% | Тест: {(test_written / valid_matches) * 100:.1f}%",
                        "✂️", value_color=Colors.BRIGHT_TEAL)
        print_info_line("Время обработки", f"{overall_time:.1f} секунд ({overall_time / 60:.1f} минут)", "⏱️",
                        value_color=Colors.BRIGHT_CYAN)
        print_info_line("Скорость обработки", f"{valid_matches / overall_time:.0f} матчей/сек", "🚀",
                        value_color=Colors.BRIGHT_GREEN)

        # Освобождение ресурсов
        self.hero_mapper.cleanup()

    def validate_tfrecord_files(self):
        """
        Выполняет валидацию созданных TFRecord файлов с детальным анализом.

        Проверяет корректность созданных файлов путем их загрузки и анализа содержимого.
        Выводит статистику по структуре данных, распределению классов, валидности
        индексов героев и примеры записей для ручной проверки.

        Проверяемые аспекты:
        - Корректность парсинга TFRecord файлов
        - Статистическое распределение классов (побед Radiant/Dire)
        - Валидность диапазона индексов героев
        - Корректность сортировки составов команд
        - Уникальность героев в матчах
        - Статистика игровых метрик (длительность, счет)

        Raises:
            Exception: При ошибках чтения или парсинга TFRecord файлов

        Note:
            Функция ограничивает анализ первыми 10000 записями для обеспечения
            приемлемого времени выполнения на больших датасетах.
        """
        # Список файлов для валидации с метаданными
        files_to_validate = [
            (self.main_output_path, "Main", "📚", Colors.BRIGHT_BLUE),
            (self.test_output_path, "Test", "🧪", Colors.BRIGHT_ORANGE)
        ]

        for file_path, file_type, file_icon, header_color in files_to_validate:
            print_section_header(f"ВАЛИДАЦИЯ {file_type.upper()} ФАЙЛА", f"{file_icon}", width=120, color=header_color)

            # === ОСНОВНАЯ ИНФОРМАЦИЯ О ФАЙЛЕ ===
            file_size_mb = os.path.getsize(file_path) / (1024 ** 2)
            print_subsection_header("Основная информация", "📋", Colors.BRIGHT_CYAN)
            print_info_line("Файл", os.path.basename(file_path), "📄", value_color=Colors.BRIGHT_YELLOW)
            print_info_line("Полный путь", file_path, "🔗", value_color=Colors.DIM)
            print_info_line("Размер файла", f"{file_size_mb:.2f} MB", "📏", value_color=Colors.BRIGHT_CYAN)

            try:
                print_status_message("Загрузка и парсинг TFRecord файла...", "info", "⏳")

                # === ЗАГРУЗКА И ПАРСИНГ ДАННЫХ ===
                dataset = tf.data.TFRecordDataset(file_path, buffer_size=1048576)

                # Определение схемы парсинга для всех полей датасета
                parser = lambda x: tf.io.parse_single_example(
                    x,
                    {
                        'match_id': tf.io.FixedLenFeature([1], tf.int64),
                        'radiant_heroes': tf.io.FixedLenFeature([TEAM_SIZE], tf.int64),
                        'dire_heroes': tf.io.FixedLenFeature([TEAM_SIZE], tf.int64),
                        'radiant_win': tf.io.FixedLenFeature([1], tf.int64),
                        'duration': tf.io.FixedLenFeature([1], tf.int64),
                        'radiant_score': tf.io.FixedLenFeature([1], tf.int64),
                        'dire_score': tf.io.FixedLenFeature([1], tf.int64),
                        'tower_status_radiant': tf.io.FixedLenFeature([1], tf.int64),
                        'tower_status_dire': tf.io.FixedLenFeature([1], tf.int64),
                        'barracks_status_radiant': tf.io.FixedLenFeature([1], tf.int64),
                        'barracks_status_dire': tf.io.FixedLenFeature([1], tf.int64),
                        'radiant_hero_variants': tf.io.FixedLenFeature([TEAM_SIZE], tf.int64),
                        'dire_hero_variants': tf.io.FixedLenFeature([TEAM_SIZE], tf.int64),
                        'radiant_roles': tf.io.FixedLenFeature([TEAM_SIZE], tf.int64),
                        'dire_roles': tf.io.FixedLenFeature([TEAM_SIZE], tf.int64),
                    }
                )
                parsed_dataset = dataset.map(parser)

                # === ИНИЦИАЛИЗАЦИЯ ПЕРЕМЕННЫХ ДЛЯ АНАЛИЗА ===
                matches_count = 0
                radiant_wins_count = 0
                min_hero_idx = np.inf
                max_hero_idx = -np.inf
                sample_examples = []
                duration_stats = []
                score_stats = []

                print_status_message("Анализ содержимого файла...", "info", "🔍")

                # === АНАЛИЗ ДАТАСЕТА ===
                # Ограничиваем анализ для производительности
                for features in parsed_dataset.take(VALIDATION_SAMPLE_SIZE):
                    matches_count += 1

                    # Извлечение основных метрик
                    radiant_win = features['radiant_win'].numpy()[0]
                    radiant_wins_count += radiant_win

                    radiant_heroes = features['radiant_heroes'].numpy()
                    dire_heroes = features['dire_heroes'].numpy()
                    duration = features['duration'].numpy()[0]
                    radiant_score = features['radiant_score'].numpy()[0]
                    dire_score = features['dire_score'].numpy()[0]

                    # Отслеживание диапазона индексов героев
                    min_hero_idx = min(min_hero_idx, radiant_heroes.min(), dire_heroes.min())
                    max_hero_idx = max(max_hero_idx, radiant_heroes.max(), dire_heroes.max())

                    # Накопление статистики
                    duration_stats.append(duration)
                    score_stats.append(radiant_score)
                    score_stats.append(dire_score)

                    # Проверки целостности данных
                    radiant_sorted = np.all(np.diff(radiant_heroes) > 0)  # Проверка сортировки
                    dire_sorted = np.all(np.diff(dire_heroes) > 0)
                    all_heroes = set(radiant_heroes) | set(dire_heroes)  # Проверка уникальности
                    unique = len(all_heroes) == 10

                    # Сохранение примеров для детального анализа
                    if matches_count <= 3:
                        sample_examples.append({
                            'match_id': features['match_id'].numpy()[0],
                            'radiant_heroes': radiant_heroes.tolist(),
                            'dire_heroes': dire_heroes.tolist(),
                            'radiant_win': radiant_win,
                            'duration': duration,
                            'radiant_score': radiant_score,
                            'dire_score': features['dire_score'].numpy()[0],
                            'radiant_hero_variants': features['radiant_hero_variants'].numpy().tolist(),
                            'dire_hero_variants': features['dire_hero_variants'].numpy().tolist(),
                            'radiant_roles': features['radiant_roles'].numpy().tolist(),
                            'dire_roles': features['dire_roles'].numpy().tolist(),
                            'sorted': radiant_sorted and dire_sorted,
                            'unique': unique
                        })

                # === СТАТИСТИЧЕСКИЙ АНАЛИЗ ===
                print_subsection_header("Статистический анализ", "📊", Colors.BRIGHT_TEAL)
                print_info_line("Количество матчей", f"{matches_count:,}", "🎮", value_color=Colors.BRIGHT_CYAN)

                # Анализ баланса классов
                radiant_win_rate = (radiant_wins_count / matches_count) * 100 if matches_count > 0 else 0
                win_rate_color = Colors.BRIGHT_GREEN if 48 <= radiant_win_rate <= 52 else Colors.BRIGHT_YELLOW
                print_info_line("Побед Radiant", f"{radiant_wins_count:,} ({radiant_win_rate:.1f}%)", "🌞",
                                value_color=win_rate_color)
                print_info_line("Побед Dire", f"{matches_count - radiant_wins_count:,} ({100 - radiant_win_rate:.1f}%)",
                                "🌑", value_color=win_rate_color)

                # Статистика по длительности матчей
                if duration_stats:
                    avg_duration = np.mean(duration_stats)
                    min_duration = np.min(duration_stats)
                    max_duration = np.max(duration_stats)
                    print_info_line("Средняя длительность", f"{avg_duration / 60:.1f} минут", "⏱️",
                                    value_color=Colors.BRIGHT_PURPLE)
                    print_info_line("Диапазон длительности", f"{min_duration / 60:.1f} - {max_duration / 60:.1f} минут",
                                    "↔️", value_color=Colors.BRIGHT_CYAN)

                # Статистика по игровому счету
                if score_stats:
                    avg_score = np.mean(score_stats)
                    min_score = np.min(score_stats)
                    max_score = np.max(score_stats)
                    print_info_line("Средний счет", f"{avg_score:.1f} очков", "🎯",
                                    value_color=Colors.BRIGHT_GOLD)
                    print_info_line("Диапазон счета", f"{min_score} - {max_score} очков", "↔️",
                                    value_color=Colors.BRIGHT_CYAN)

                # === ВАЛИДАЦИЯ ИНДЕКСОВ ГЕРОЕВ ===
                print_subsection_header("Валидация индексов героев", "🗺️", Colors.BRIGHT_PURPLE)
                print_info_line("Мин. индекс героя", f"{min_hero_idx}", "📉", value_color=Colors.BRIGHT_CYAN)
                print_info_line("Макс. индекс героя", f"{max_hero_idx}", "📈", value_color=Colors.BRIGHT_CYAN)
                print_info_line("Ожидаемый диапазон", f"0 - {self.hero_mapper.total_heroes - 1}", "↔️",
                                value_color=Colors.BRIGHT_YELLOW)

                # Проверка корректности диапазона
                range_valid = (min_hero_idx >= 0) and (max_hero_idx < self.hero_mapper.total_heroes)
                range_status = "Корректный ✅" if range_valid else "Некорректный ❌"
                range_color = Colors.BRIGHT_GREEN if range_valid else Colors.BRIGHT_RED
                print_info_line("Статус диапазона", range_status, "🔍", value_color=range_color)

                # === АНАЛИЗ ПРИМЕРОВ ЗАПИСЕЙ ===
                for i, example in enumerate(sample_examples, 1):
                    print_subsection_header(f"ПРИМЕР МАТЧА #{i}", "🕹️", Colors.BRIGHT_YELLOW)

                    # Основная информация матча
                    print_info_line("Match ID", f"{example['match_id']}", "🆔", value_color=Colors.BRIGHT_WHITE)
                    print_info_line("Radiant команда", f"{example['radiant_heroes']}", "🌞",
                                    value_color=Colors.BRIGHT_BLUE)
                    print_info_line("Dire команда", f"{example['dire_heroes']}", "🌑", value_color=Colors.BRIGHT_RED)

                    # Результат матча
                    winner_text = "Radiant" if example['radiant_win'] else "Dire"
                    winner_color = Colors.BRIGHT_GREEN if example['radiant_win'] else Colors.BRIGHT_RED
                    print_info_line("Победитель", winner_text, "🏆", value_color=winner_color)

                    # Игровые метрики
                    print_info_line("Длительность", f"{example['duration'] / 60:.1f} минут", "🕒",
                                    value_color=Colors.BRIGHT_PURPLE)
                    print_info_line("Счет", f"Radiant: {example['radiant_score']} | Dire: {example['dire_score']}", "📊",
                                    value_color=Colors.BRIGHT_CYAN)

                    # Дополнительные характеристики героев
                    print_info_line("Radiant варианты", f"{example['radiant_hero_variants']}", "⚔️",
                                    value_color=Colors.BRIGHT_BLUE)
                    print_info_line("Dire варианты", f"{example['dire_hero_variants']}", "⚔️",
                                    value_color=Colors.BRIGHT_RED)

                    # Роли игроков
                    print_info_line("Radiant роли", f"{example['radiant_roles']}", "🎭", value_color=Colors.BRIGHT_GREEN)
                    print_info_line("Dire роли", f"{example['dire_roles']}", "🎭", value_color=Colors.BRIGHT_GREEN)

                    # Проверки целостности данных
                    sort_status = "Корректная ✅" if example['sorted'] else "Ошибка ❌"
                    sort_color = Colors.BRIGHT_GREEN if example['sorted'] else Colors.BRIGHT_RED
                    print_info_line("Сортировка команд", sort_status, "🔀", value_color=sort_color)

                    unique_status = "Все уникальны ✅" if example['unique'] else "Есть дубликаты ❌"
                    unique_color = Colors.BRIGHT_GREEN if example['unique'] else Colors.BRIGHT_RED
                    print_info_line("Уникальность героев", unique_status, "👥", value_color=unique_color)

                # === ИТОГИ ВАЛИДАЦИИ ===
                print_subsection_header("Итоги валидации", "🎉", Colors.BRIGHT_GREEN)
                print_status_message(f"{file_type} файл успешно прошел валидацию", "success", "🎊")

            except Exception as e:
                print_status_message(f"Ошибка при валидации {file_type.lower()} файла: {e}", "error", "❌")
                print_info_line("Тип ошибки", type(e).__name__, "⚠️", value_color=Colors.BRIGHT_RED)


def main():
    """
    Основная функция для создания TFRecord файлов из данных матчей Dota 2.

    Выполняет полный цикл создания датасета:
    1. Инициализация конфигурации и путей к файлам
    2. Создание экземпляра препроцессора с подключением к БД
    3. Проверка существующих файлов и предупреждение о перезаписи
    4. Запуск создания TFRecord файлов с разделением на выборки
    5. Валидация созданных файлов с детальным анализом
    6. Обработка ошибок с диагностической информацией

    Raises:
        Exception: При критических ошибках инициализации или создания файлов
    """
    print_section_header("ИНИЦИАЛИЗАЦИЯ СОЗДАНИЯ TFRECORD ФАЙЛОВ", "🤖", width=100, color=Colors.BRIGHT_GOLD)

    # === ИНФОРМАЦИЯ О ФОРМАТЕ ДАННЫХ ===
    print_info_line("Входные данные", f"Составы героев обеих команд ({TEAM_SIZE} vs {TEAM_SIZE})", "👥",
                    value_color=Colors.BRIGHT_CYAN)
    print_info_line("Выходные данные", "Результат матча + дополнительная статистика", "🔮",
                    value_color=Colors.BRIGHT_GREEN)
    print_info_line("Дополнительные поля", "Duration, Scores, Tower/Barracks Status, Hero Variants, Roles", "🧩",
                    value_color=Colors.BRIGHT_YELLOW)
    print_info_line("Исключенные герои", f"{len(EXCLUDED_HERO_IDS)} героев (ID: {list(EXCLUDED_HERO_IDS)})", "❌",
                    value_color=Colors.BRIGHT_RED)
    print_info_line("Маппинг ролей", f"core → 0, support → 1", "🎭", value_color=Colors.BRIGHT_PURPLE)
    print_info_line("Маппинг вариантов", f"1 - 6 (аспекты героев)", "🔢", value_color=Colors.BRIGHT_PURPLE)
    print_info_line("Сид разбиения", f"{RANDOM_SEED}", "🎲", value_color=Colors.BRIGHT_YELLOW)
    print_info_line("Формат вывода", "TFRecord (два файла: основной + тестовый)", "💾", value_color=Colors.BRIGHT_ORANGE)
    print_info_line("Сортировка героев", "Включена (по плотным индексам)", "🔀",
                    value_color=Colors.BRIGHT_GREEN)

    # === КОНФИГУРАЦИЯ ОКРУЖЕНИЯ ===
    print_subsection_header("Конфигурация окружения", "🔧", Colors.BRIGHT_CYAN)

    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    base_output_path = os.path.join(project_root, 'datasets', f'{TFRECORD_FILE_NAME}_{DOTA_VERSION}.tfrecord')

    print_info_line("Директория данных", os.path.join(project_root, 'datasets'), "📂", value_color=Colors.BRIGHT_CYAN)
    print_info_line("Базовое имя файлов", os.path.basename(base_output_path), "📄", value_color=Colors.BRIGHT_GOLD)

    try:
        # === ИНИЦИАЛИЗАЦИЯ ПРЕПРОЦЕССОРА ===
        preprocessor = TFRecordPreprocessor(base_output_path)

        # === ПРОВЕРКА СУЩЕСТВУЮЩИХ ФАЙЛОВ ===
        main_exists = os.path.exists(preprocessor.main_output_path)
        test_exists = os.path.exists(preprocessor.test_output_path)

        if main_exists:
            file_size_mb = os.path.getsize(preprocessor.main_output_path) / (1024 ** 2)
            print_status_message(f"Основной файл уже существует (размер: {file_size_mb:.2f} MB)", "warning", "⚠️")
            print_info_line("Файл", os.path.basename(preprocessor.main_output_path), "📚",
                            value_color=Colors.BRIGHT_YELLOW)

        if test_exists:
            file_size_mb = os.path.getsize(preprocessor.test_output_path) / (1024 ** 2)
            print_status_message(f"Тестовый файл уже существует (размер: {file_size_mb:.2f} MB)", "warning", "⚠️")
            print_info_line("Файл", os.path.basename(preprocessor.test_output_path), "🧪",
                            value_color=Colors.BRIGHT_YELLOW)

        if main_exists or test_exists:
            print_status_message("Существующие файлы будут перезаписаны при создании новых", "info", "🔄")

        # === ОСНОВНОЙ ПРОЦЕСС СОЗДАНИЯ ===
        preprocessor.create_tfrecord()
        preprocessor.validate_tfrecord_files()

    except Exception as e:
        # === ОБРАБОТКА КРИТИЧЕСКИХ ОШИБОК ===
        print_section_header("КРИТИЧЕСКАЯ ОШИБКА ВЫПОЛНЕНИЯ", "💥", width=140, color=Colors.BRIGHT_RED)
        print_status_message(f"Ошибка при создании TFRecord файлов: {e}", "error", "❌")

        # Диагностическая информация для отладки
        print_subsection_header("Диагностическая информация", "🔍", Colors.BRIGHT_YELLOW)
        print_info_line("Тип ошибки", type(e).__name__, "⚠️", value_color=Colors.BRIGHT_RED)
        print_info_line("Текущая директория", os.getcwd(), "📍", value_color=Colors.BRIGHT_BLUE)
        print_info_line("Python версия", sys.version.split()[0], "🐍", value_color=Colors.BRIGHT_GREEN)
        print_info_line("TensorFlow версия", tf.__version__, "🧠", value_color=Colors.BRIGHT_PURPLE)


if __name__ == "__main__":
    main()
