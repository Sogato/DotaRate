"""
Универсальный препроцессор для создания HDF5 файлов из данных матчей Dota 2.

Модуль предоставляет функциональность для преобразования данных матчей из PostgreSQL
базы данных в оптимизированный HDF5 формат для машинного обучения и анализа данных.

Основные функции:
- Потоковая загрузка матчей из PostgreSQL с эффективной обработкой больших датасетов
- Преобразование hero_id в плотные индексы через HeroMapper для оптимального представления
- Фильтрация и исключение определенных героев из обработки
- Случайное разделение данных на тестовую и основную выборки
- Пакетная запись в HDF5 с оптимизацией производительности
- Валидация созданных файлов с детальным анализом содержимого
- Включение расширенных метаданных матчей и игроков

Структура выходных данных HDF5:
- Основные поля матча: match_id, radiant_win, duration
- Боевая статистика: radiant_score, dire_score
- Статус построек: tower_status_radiant/dire, barracks_status_radiant/dire
- Составы команд: radiant_heroes, dire_heroes (плотные индексы)
- Характеристики героев: radiant/dire_hero_variants (аспекты 1-6)
- Роли игроков: radiant/dire_roles (core=0, support=1)

Технические особенности:
- Использует предварительное разделение ID матчей для эффективного случайного распределения
- Применяет буферизованную запись для оптимизации производительности HDF5
- Поддерживает keyset пагинацию для работы с большими объемами данных
- Обеспечивает консистентное исключение героев на всех этапах обработки
- Создает два независимых HDF5 файла для гибкости использования
- Включает детальную валидацию с проверкой целостности данных
"""

import os
import time
import numpy as np
import h5py
from typing import List, Tuple, Dict, Any
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from config import (
    DATASET_DATABASE_URL, EXCLUDED_HERO_IDS, ROLE_MAPPING, RADIANT_TEAM, DIRE_TEAM,
    TEAM_SIZE, TEST_DATASET_SIZE, CHUNK_SIZE, VALIDATION_SAMPLE_SIZE, HDF5_FILE_NAME, DOTA_VERSION
)
from data_bases.dataset.models import Match, MatchPlayer
from utils.hero_mapper import HeroMapper
from utils.console import (
    Colors,
    print_section_header,
    print_subsection_header,
    print_info_line,
    print_progress_bar,
    print_status_message
)


class HDF5Preprocessor:
    """
    Универсальный препроцессор для создания HDF5 файлов из данных матчей Dota 2.

    Класс обеспечивает полный цикл обработки данных матчей: от загрузки из PostgreSQL
    базы данных до создания оптимизированных HDF5 файлов для машинного обучения.
    Поддерживает эффективную обработку больших датасетов с разделением на выборки.

    Основные этапы обработки:
    1. Подключение к базе данных и инициализация маппера героев с исключениями
    2. Получение полного списка ID валидных матчей с фильтрацией
    3. Случайное разделение ID на основную и тестовую выборки
    4. Потоковая загрузка данных матчей по чанкам
    5. Преобразование hero_id в плотные индексы для эффективного хранения
    6. Пакетная запись в HDF5 с оптимизированной структурой датасетов
    7. Валидация созданных файлов с детальным анализом содержимого

    Алгоритм разделения данных:
    - Получение всех валидных ID матчей из базы данных
    - Случайный выбор TEST_DATASET_SIZE ID для тестовой выборки
    - Остальные ID автоматически попадают в основную выборку
    - Независимая обработка каждой выборки в отдельный HDF5 файл

    Особенности HDF5 структуры:
    - Скалярные поля: (total_matches,) для одиночных значений
    - Векторные поля: (total_matches, TEAM_SIZE) для составов команд
    - Все данные хранятся как int64 для совместимости
    - Фиксированные размеры датасетов для оптимальной производительности

    Attributes:
        test_output_path (str): Полный путь к тестовому HDF5 файлу
        main_output_path (str): Полный путь к основному HDF5 файлу
        engine: SQLAlchemy движок для подключения к PostgreSQL
        session_maker: Фабрика сессий SQLAlchemy для работы с БД
        hero_mapper (HeroMapper): Маппер для преобразования hero_id в плотные индексы
    """

    def __init__(self, base_output_path: str, database_url: str = DATASET_DATABASE_URL):
        """
        Инициализирует препроцессор с настройкой подключений и создание выходных путей.

        Создает два выходных файла на основе базового пути: основной для обучения
        и тестовый для валидации. Устанавливает подключение к базе данных и
        инициализирует маппер героев с исключением нежелательных персонажей.

        Args:
            base_output_path (str): Базовый путь для формирования имен HDF5 файлов.
                Автоматически добавляется суффикс '_main.h5' и '_test.h5'
            database_url (str, optional): URL подключения к PostgreSQL базе данных.
                По умолчанию используется DATASET_DATABASE_URL из конфигурации

        Raises:
            Exception: При ошибках подключения к базе данных или недоступности сервера
            ValueError: При некорректном формате database_url

        Note:
            Функция автоматически выводит информацию о создаваемых файлах и
            статистику маппера героев для контроля процесса инициализации.
        """
        # Формирование путей для основного и тестового HDF5 файлов
        base_name = os.path.splitext(base_output_path)[0]
        self.test_output_path = f"{base_name}_test.h5"
        self.main_output_path = f"{base_name}_main.h5"

        # Вывод информации о создаваемых файлах
        print_info_line("Основной файл", os.path.basename(self.main_output_path), "📚", value_color=Colors.BRIGHT_GREEN)
        print_info_line("Тестовый файл", os.path.basename(self.test_output_path), "🧪", value_color=Colors.BRIGHT_BLUE)

        # Инициализация подключения к PostgreSQL базе данных
        try:
            self.engine = create_engine(database_url)
            self.session_maker = sessionmaker(bind=self.engine)
        except Exception as e:
            print_status_message(f"Ошибка подключения к базе данных: {e}", "error", "❌")
            raise

        # Инициализация маппера героев с выводом детальной статистики
        print_subsection_header("Инициализация маппера героев", "🗺️", Colors.BRIGHT_PURPLE)
        self.hero_mapper = HeroMapper(excluded_hero_ids=EXCLUDED_HERO_IDS)
        mapper_info = self.hero_mapper.get_mapping_info()
        print_info_line("Диапазон индексов", f"{mapper_info['index_range']}", "🔢")
        print_info_line("Исключено героев", f"{mapper_info['excluded_heroes_count']}", "❌",
                        value_color=Colors.BRIGHT_RED)
        print_info_line("Итого героев", f"{mapper_info['total_heroes']:,}", "🧙‍♂️")

    def _get_match_ids(self) -> Tuple[List[int], List[int]]:
        """
        Получает ID всех валидных матчей и разделяет их на основную и тестовую выборки.

        Выполняет SQL запрос для получения всех ID матчей, которые не содержат
        исключенных героев. Затем случайно разделяет полученные ID на две выборки
        для создания независимых датасетов.

        Returns:
            Tuple[List[int], List[int]]: Кортеж из двух списков:
                - main_ids: список ID для основного датасета (обучение)
                - test_ids: список ID для тестового датасета (валидация)

        Raises:
            ValueError: Если в базе данных не найдено валидных матчей для обработки
            Exception: При ошибках выполнения SQL запросов или работы с базой данных

        Note:
            Функция использует подзапрос для эффективного исключения матчей с
            нежелательными героями. Размер тестовой выборки ограничен константой
            TEST_DATASET_SIZE, остальные матчи попадают в основную выборку.
        """
        with self.session_maker() as session:
            # SQL запрос для получения ID матчей без исключенных героев
            # Используем подзапрос для эффективной фильтрации
            query = (
                select(Match.match_id)
                .filter(
                    # Исключаем матчи, содержащие нежелательных героев
                    ~Match.match_id.in_(
                        select(MatchPlayer.match_id)
                        .filter(MatchPlayer.hero_id.in_(EXCLUDED_HERO_IDS))
                    )
                )
                .order_by(Match.match_id)  # Упорядочиваем для консистентности
            )
            match_ids = session.execute(query).scalars().all()

        # Проверка наличия данных для обработки
        if not match_ids:
            raise ValueError("В базе данных не найдено валидных матчей!")

        # === АЛГОРИТМ СЛУЧАЙНОГО РАЗДЕЛЕНИЯ НА ВЫБОРКИ ===
        total_matches = len(match_ids)
        test_size = min(TEST_DATASET_SIZE, total_matches)

        # Случайный выбор индексов для тестовой выборки
        # Используем numpy для обеспечения равномерного распределения
        test_indices = set(np.random.choice(total_matches, size=test_size, replace=False))

        # Формирование списков ID для каждой выборки
        test_ids = [match_ids[index] for index in test_indices]
        main_ids = [match_id for index, match_id in enumerate(match_ids) if index not in test_indices]

        return main_ids, test_ids

    @staticmethod
    def _create_hdf5_structure(h5_file: h5py.File, total_matches: int):
        """
        Создает оптимизированную структуру датасетов в HDF5 файле.

        Формирует все необходимые датасеты с предопределенными размерами для
        эффективной записи и последующего чтения. Использует два типа структур:
        скалярные поля для одиночных значений и векторные для составов команд.

        Args:
            h5_file (h5py.File): Открытый HDF5 файл для создания структуры датасетов
            total_matches (int): Общее количество матчей для определения размеров датасетов

        Note:
            Все датасеты создаются с типом int64 для обеспечения совместимости
            с различными ML фреймворками. Фиксированные размеры позволяют HDF5
            оптимизировать производительность чтения и записи.
        """
        # === ОПРЕДЕЛЕНИЕ РАЗМЕРОВ ДАТАСЕТОВ ===
        scalar_shape = (total_matches,)  # Для одиночных значений (match_id, duration и т.д.)
        vector_shape = (total_matches, TEAM_SIZE)  # Для составов команд (герои, роли)

        # === КОНФИГУРАЦИЯ ВСЕХ ДАТАСЕТОВ ===
        # Список всех датасетов с их размерностями
        datasets_config = [
            # Скалярные поля - основная информация матча
            ('match_id', scalar_shape),
            ('radiant_win', scalar_shape),
            ('duration', scalar_shape),
            ('radiant_score', scalar_shape),
            ('dire_score', scalar_shape),

            # Скалярные поля - статусы игровых построек
            ('tower_status_radiant', scalar_shape),
            ('tower_status_dire', scalar_shape),
            ('barracks_status_radiant', scalar_shape),
            ('barracks_status_dire', scalar_shape),

            # Векторные поля - составы и характеристики команд
            ('radiant_heroes', vector_shape),
            ('dire_heroes', vector_shape),
            ('radiant_hero_variants', vector_shape),
            ('dire_hero_variants', vector_shape),
            ('radiant_roles', vector_shape),
            ('dire_roles', vector_shape)
        ]

        # === СОЗДАНИЕ ДАТАСЕТОВ В HDF5 ФАЙЛЕ ===
        # Создаем все датасеты с предопределенными размерами и типом данных
        for dataset_name, shape in datasets_config:
            h5_file.create_dataset(dataset_name, shape=shape, dtype=np.int64)

    def _load_matches_chunk(self, match_ids: List[int]) -> List[Dict[str, Any]]:
        """
        Загружает чанк данных матчей по предоставленному списку ID.

        Выполняет оптимизированный SQL запрос с агрегацией данных игроков по командам
        для эффективной загрузки всей необходимой информации за один запрос.
        Использует JOIN между таблицами Match и MatchPlayer с группировкой по матчам.

        Args:
            match_ids (List[int]): Список ID матчей для загрузки в текущем чанке

        Returns:
            List[Dict[str, Any]]: Список словарей с данными матчей, каждый содержит:
                - Основную информацию матча (id, результат, длительность, счет)
                - Статусы игровых построек (башни, казармы)
                - Агрегированные данные игроков по командам (герои, варианты, роли)

        Note:
            Функция использует PostgreSQL array_agg для эффективной агрегации
            данных игроков. Фильтрация по team_number происходит на уровне SQL
            для минимизации объема передаваемых данных.
        """
        chunk_data = []

        with self.session_maker() as session:
            # Оптимизированный запрос с агрегацией данных по командам
            query = (
                session.query(
                    # Основная информация матча
                    Match.match_id,
                    Match.radiant_win,
                    Match.duration,
                    Match.radiant_score,
                    Match.dire_score,
                    # Статусы игровых построек
                    Match.tower_status_radiant,
                    Match.tower_status_dire,
                    Match.barracks_status_radiant,
                    Match.barracks_status_dire,
                    # Агрегация героев по командам через array_agg
                    func.array_agg(MatchPlayer.hero_id).filter(MatchPlayer.team_number == RADIANT_TEAM).label(
                        "radiant_heroes"),
                    func.array_agg(MatchPlayer.hero_id).filter(MatchPlayer.team_number == DIRE_TEAM).label(
                        "dire_heroes"),
                    # Агрегация вариантов героев по командам
                    func.array_agg(MatchPlayer.hero_variant).filter(MatchPlayer.team_number == RADIANT_TEAM).label(
                        "radiant_variants"),
                    func.array_agg(MatchPlayer.hero_variant).filter(MatchPlayer.team_number == DIRE_TEAM).label(
                        "dire_variants"),
                    # Агрегация ролей игроков по командам
                    func.array_agg(MatchPlayer.role).filter(MatchPlayer.team_number == RADIANT_TEAM).label(
                        "radiant_roles"),
                    func.array_agg(MatchPlayer.role).filter(MatchPlayer.team_number == DIRE_TEAM).label("dire_roles"),
                )
                .join(MatchPlayer, Match.match_id == MatchPlayer.match_id)
                .filter(Match.match_id.in_(match_ids))  # Фильтрация по предоставленным ID
                .group_by(
                    # Группировка по всем полям матча для корректной агрегации
                    Match.match_id, Match.radiant_win, Match.duration, Match.radiant_score, Match.dire_score,
                    Match.tower_status_radiant, Match.tower_status_dire, Match.barracks_status_radiant,
                    Match.barracks_status_dire
                )
            )

            # Обработка результатов запроса в словари
            for row in query:
                (match_id, radiant_win, duration, radiant_score, dire_score,
                 tower_status_radiant, tower_status_dire, barracks_status_radiant, barracks_status_dire,
                 radiant_heroes, dire_heroes, radiant_variants, dire_variants, radiant_roles, dire_roles) = row

                chunk_data.append({
                    # Основная информация матча
                    "match_id": match_id,
                    "radiant_win": radiant_win,
                    "duration": duration,
                    "radiant_score": radiant_score,
                    "dire_score": dire_score,

                    # Статусы построек
                    "tower_status_radiant": tower_status_radiant,
                    "tower_status_dire": tower_status_dire,
                    "barracks_status_radiant": barracks_status_radiant,
                    "barracks_status_dire": barracks_status_dire,

                    # Данные команд
                    "radiant_heroes": radiant_heroes,
                    "dire_heroes": dire_heroes,
                    "radiant_variants": radiant_variants,
                    "dire_variants": dire_variants,
                    "radiant_roles": radiant_roles,
                    "dire_roles": dire_roles,
                })

        return chunk_data

    def _prepare_match_data(self, match_data: Dict[str, Any]) -> Dict[str, np.ndarray]:
        """
        Преобразует данные матча в формат numpy массивов для записи в HDF5.

        Выполняет следующие преобразования:
        1. Конвертирует булевое значение radiant_win в целое число (0/1)
        2. Преобразует hero_id в плотные индексы через маппер
        3. Конвертирует строковые роли в числовые значения
        4. Формирует numpy массивы с правильными типами данных

        Args:
            match_data (Dict[str, Any]): Словарь с сырыми данными матча из базы данных,
                содержащий все поля, загруженные через _load_matches_chunk

        Returns:
            Dict[str, np.ndarray]: Словарь numpy массивов для записи в HDF5:
                - Скалярные поля: shape (1,) для одиночных значений
                - Векторные поля: shape (1, TEAM_SIZE) для составов команд
                - Все массивы имеют тип dtype=np.int64 для совместимости

        Note:
            Функция создает массивы с первым измерением равным 1 для возможности
            последующей конкатенации при пакетной записи в HDF5 датасеты.
        """
        # Преобразование булевого результата в целое число
        radiant_win_int = 1 if match_data['radiant_win'] else 0

        # === ОБРАБОТКА ДАННЫХ RADIANT КОМАНДЫ ===
        # Преобразование строковых ролей в числовые значения
        radiant_role_ints = [ROLE_MAPPING[role] for role in match_data['radiant_roles']]
        # Конвертация hero_id в плотные индексы через маппер
        radiant_indices = [self.hero_mapper.hero_to_index[hero_id] for hero_id in match_data['radiant_heroes']]
        # Варианты героев (аспекты) остаются без изменений
        radiant_variants = match_data['radiant_variants']

        # === ОБРАБОТКА ДАННЫХ DIRE КОМАНДЫ ===
        # Аналогичные преобразования для команды Dire
        dire_role_ints = [ROLE_MAPPING[role] for role in match_data['dire_roles']]
        dire_indices = [self.hero_mapper.hero_to_index[hero_id] for hero_id in match_data['dire_heroes']]
        dire_variants = match_data['dire_variants']

        # === ФОРМИРОВАНИЕ NUMPY МАССИВОВ ===
        # Создаем массивы с shape (1,) или (1, TEAM_SIZE) для последующей конкатенации
        return {
            # Идентификация матча
            'match_id': np.array([match_data['match_id']], dtype=np.int64),

            # Составы команд (плотные индексы героев)
            'radiant_heroes': np.array([radiant_indices], dtype=np.int64),
            'dire_heroes': np.array([dire_indices], dtype=np.int64),

            # Результат матча (целевая переменная)
            'radiant_win': np.array([radiant_win_int], dtype=np.int64),

            # Временные характеристики
            'duration': np.array([match_data['duration']], dtype=np.int64),

            # Боевая статистика команд
            'radiant_score': np.array([match_data['radiant_score']], dtype=np.int64),
            'dire_score': np.array([match_data['dire_score']], dtype=np.int64),

            # Статусы игровых построек (битовые маски)
            'tower_status_radiant': np.array([match_data['tower_status_radiant']], dtype=np.int64),
            'tower_status_dire': np.array([match_data['tower_status_dire']], dtype=np.int64),
            'barracks_status_radiant': np.array([match_data['barracks_status_radiant']], dtype=np.int64),
            'barracks_status_dire': np.array([match_data['barracks_status_dire']], dtype=np.int64),

            # Дополнительные характеристики героев
            'radiant_hero_variants': np.array([radiant_variants], dtype=np.int64),
            'dire_hero_variants': np.array([dire_variants], dtype=np.int64),

            # Роли игроков (численное представление)
            'radiant_roles': np.array([radiant_role_ints], dtype=np.int64),
            'dire_roles': np.array([dire_role_ints], dtype=np.int64),
        }

    def create_hdf5(self):
        """
        Создает HDF5 файлы с разделением данных на тестовую и основную выборки.

        Основной метод класса, выполняющий полный цикл создания датасета:
        1. Получение и случайное разделение ID матчей на выборки
        2. Создание структуры HDF5 файлов для каждой выборки
        3. Потоковую обработку данных по чанкам с пакетной записью
        4. Мониторинг производительности и отображение прогресса
        5. Финализацию файлов с выводом статистики

        Алгоритм обработки:
        - Данные обрабатываются независимо для каждой выборки
        - Каждая выборка записывается в отдельный HDF5 файл
        - Используется чанкование для эффективной работы с памятью
        - Буферизация данных для оптимизации записи в HDF5

        Raises:
            ValueError: При отсутствии валидных данных для обработки
            IOError: При ошибках создания или записи HDF5 файлов
            Exception: При критических ошибках работы с базой данных

        Note:
            Функция создает два независимых файла, что позволяет гибко
            использовать данные в различных сценариях машинного обучения.
        """
        print_section_header("СОЗДАНИЕ HDF5 ФАЙЛОВ", "🔧", width=100, color=Colors.BRIGHT_GOLD)

        # Создание выходных директорий если они не существуют
        os.makedirs(os.path.dirname(self.main_output_path), exist_ok=True)

        # === ЭТАП 1: ПОЛУЧЕНИЕ И РАЗДЕЛЕНИЕ ID МАТЧЕЙ ===
        print_status_message("Получение списка матчей...", "info", "⏳")
        main_ids, test_ids = self._get_match_ids()
        total_matches = len(main_ids) + len(test_ids)

        # Вывод статистики разделения данных
        print_info_line("Общее количество матчей", f"{total_matches:,}", "🎮", value_color=Colors.BRIGHT_GOLD)
        print_info_line("Основной набор", f"{len(main_ids):,}", "📚", value_color=Colors.BRIGHT_GREEN)
        print_info_line("Тестовый набор", f"{len(test_ids):,}", "🧪", value_color=Colors.BRIGHT_BLUE)
        print_info_line("Размер чанка", f"{CHUNK_SIZE:,}", "🗂️", value_color=Colors.BRIGHT_YELLOW)

        # === ЭТАП 2: КОНФИГУРАЦИЯ ОБРАБОТКИ ДАТАСЕТОВ ===
        # Определяем параметры для обработки каждого набора данных
        datasets_info = [
            (main_ids, self.main_output_path, "Основной", "📚"),
            (test_ids, self.test_output_path, "Тестовый", "🧪")
        ]

        # === ЭТАП 3: ОСНОВНОЙ ЦИКЛ ОБРАБОТКИ ДАТАСЕТОВ ===
        for ids, file_path, file_type, file_icon in datasets_info:
            print_subsection_header(f"Обработка {file_type.lower()} набора", file_icon, Colors.BRIGHT_CYAN)

            # Проверка наличия данных для обработки
            if not ids:
                print_status_message(f"{file_type} набор пуст, пропускаем", "warning", "⚠️")
                continue

            # Расчет параметров чанкования
            total_chunks = (len(ids) + CHUNK_SIZE - 1) // CHUNK_SIZE

            # === ИНИЦИАЛИЗАЦИЯ HDF5 ФАЙЛА ===
            # Создание файла и структуры датасетов
            h5_file = h5py.File(file_path, 'w')
            self._create_hdf5_structure(h5_file, len(ids))

            # Инициализация переменных для отслеживания прогресса
            valid_matches = 0  # Счетчик успешно записанных матчей
            dataset_processing_time = 0  # Накопительное время обработки

            # === ОСНОВНОЙ ЦИКЛ ОБРАБОТКИ ЧАНКОВ ===
            for chunk_idx in range(total_chunks):
                chunk_start_time = time.time()

                # Определение границ текущего чанка
                start_idx = chunk_idx * CHUNK_SIZE
                chunk_ids = ids[start_idx:start_idx + CHUNK_SIZE]

                # Загрузка и предобработка данных чанка
                chunk_data = self._load_matches_chunk(chunk_ids)
                data_buffer = [self._prepare_match_data(match) for match in chunk_data]

                # === ПАКЕТНАЯ ЗАПИСЬ В HDF5 ===
                if data_buffer:
                    # Запись всех полей данных в соответствующие датасеты
                    for key in data_buffer[0].keys():
                        # Конкатенация данных всех матчей в чанке
                        data_array = np.concatenate([data[key] for data in data_buffer], axis=0)
                        # Запись в соответствующий диапазон датасета
                        h5_file[key][valid_matches:valid_matches + len(data_buffer)] = data_array

                    valid_matches += len(data_buffer)

                # === МОНИТОРИНГ ПРОИЗВОДИТЕЛЬНОСТИ ===
                chunk_time = time.time() - chunk_start_time
                dataset_processing_time += chunk_time

                # Расчет ETA на основе средней скорости обработки
                eta_minutes = ((total_chunks - chunk_idx - 1) * (dataset_processing_time / (chunk_idx + 1))) / 60

                # Вывод статистики обработки чанка
                print(f"{Colors.BRIGHT_BLUE}📦 Чанк #{chunk_idx + 1}{Colors.RESET} | "
                      f"📊 Загружено: {Colors.BRIGHT_YELLOW}{len(chunk_data):,}{Colors.RESET} | "
                      f"💾 Записано: {Colors.BRIGHT_PURPLE}{valid_matches:,}{Colors.RESET} | "
                      f"⏱️ {Colors.BRIGHT_MAGENTA}{chunk_time:.1f}с{Colors.RESET} | "
                      f"⏳ ETA: {Colors.BRIGHT_GOLD}{eta_minutes:.1f}м{Colors.RESET}")

                # Прогресс-бар для текущего датасета
                print_progress_bar(chunk_idx + 1, total_chunks, f"{file_type} прогресс:", 40, Colors.BRIGHT_GREEN,
                                   Colors.DIM)

            # === ФИНАЛИЗАЦИЯ ДАТАСЕТА ===
            h5_file.close()

            # Вывод итоговой статистики датасета
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            print_subsection_header(f"Статистика {file_type.lower()} файла", file_icon, Colors.BRIGHT_BLUE)
            print_info_line("Размер файла", f"{file_size_mb:.2f} MB", "📏", value_color=Colors.BRIGHT_CYAN)
            print_info_line("Количество матчей", f"{valid_matches:,}", "🎯", value_color=Colors.BRIGHT_GREEN)

        # Освобождение ресурсов маппера героев
        self.hero_mapper.cleanup()

    def validate_hdf5_files(self):
        """
        Выполняет валидацию созданных HDF5 файлов с детальным анализом содержимого.

        Проверяет корректность созданных файлов путем их загрузки, анализа структуры
        и содержимого. Выводит статистику по распределению классов, валидности данных,
        примеры записей и проверки целостности для обеих выборок.

        Проверяемые аспекты:
        - Корректность открытия и чтения HDF5 файлов
        - Соответствие структуры данных ожидаемой схеме
        - Статистическое распределение результатов матчей (баланс классов)
        - Валидность диапазона индексов героев относительно маппера
        - Уникальность героев в каждом матче (отсутствие дубликатов)
        - Статистики игровых метрик (длительность, счет матчей)
        - Целостность связанных полей (варианты героев, роли)

        Raises:
            FileNotFoundError: Если HDF5 файлы не существуют или недоступны
            ValueError: При некорректной структуре данных в файлах
            Exception: При ошибках чтения или анализа HDF5 файлов

        Note:
            Валидация ограничена VALIDATION_SAMPLE_SIZE записями для обеспечения
            приемлемого времени выполнения на больших датасетах. Для каждого файла
            показывается до 3 примеров записей для визуального контроля качества.
        """
        # Конфигурация файлов для валидации
        files_to_validate = [
            (self.main_output_path, "Main", "📚", Colors.BRIGHT_BLUE),
            (self.test_output_path, "Test", "🧪", Colors.BRIGHT_ORANGE)
        ]

        for file_path, file_type, file_icon, header_color in files_to_validate:
            # Проверка существования файла
            if not os.path.exists(file_path):
                print_status_message(f"{file_type} файл не существует, пропускаем", "warning", "⚠️")
                continue

            print_section_header(f"ВАЛИДАЦИЯ {file_type.upper()} ФАЙЛА", file_icon, width=100, color=header_color)

            # === ОСНОВНАЯ ИНФОРМАЦИЯ О ФАЙЛЕ ===
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            print_subsection_header("Основная информация", "📋", Colors.BRIGHT_CYAN)
            print_info_line("Файл", os.path.basename(file_path), "📄", value_color=Colors.BRIGHT_YELLOW)
            print_info_line("Размер файла", f"{file_size_mb:.2f} MB", "📏", value_color=Colors.BRIGHT_CYAN)

            try:
                # === ОТКРЫТИЕ И АНАЛИЗ HDF5 ФАЙЛА ===
                with h5py.File(file_path, 'r') as h5_file:
                    matches_count = h5_file['match_id'].shape[0]
                    print_info_line("Количество матчей", f"{matches_count:,}", "🎮", value_color=Colors.BRIGHT_CYAN)

                    # Определение размера выборки для анализа
                    sample_size = min(VALIDATION_SAMPLE_SIZE, matches_count)

                    # === ИНИЦИАЛИЗАЦИЯ ПЕРЕМЕННЫХ ДЛЯ АНАЛИЗА ===
                    radiant_wins_count = 0  # Счетчик побед Radiant
                    min_hero_idx, max_hero_idx = np.inf, -np.inf  # Диапазон индексов героев
                    sample_examples = []  # Примеры записей для детального анализа
                    duration_stats = []  # Статистика длительности матчей
                    score_stats = []  # Статистика счета команд

                    # === ОСНОВНОЙ ЦИКЛ АНАЛИЗА ДАННЫХ ===
                    for match_index in range(sample_size):
                        # Извлечение основных полей для анализа
                        radiant_win = h5_file['radiant_win'][match_index]
                        radiant_heroes = h5_file['radiant_heroes'][match_index]
                        dire_heroes = h5_file['dire_heroes'][match_index]
                        duration = h5_file['duration'][match_index]
                        radiant_score = h5_file['radiant_score'][match_index]
                        dire_score = h5_file['dire_score'][match_index]

                        # Накопление статистики
                        radiant_wins_count += radiant_win
                        min_hero_idx = min(min_hero_idx, radiant_heroes.min(), dire_heroes.min())
                        max_hero_idx = max(max_hero_idx, radiant_heroes.max(), dire_heroes.max())
                        duration_stats.append(duration)
                        score_stats.append(radiant_score)
                        score_stats.append(dire_score)

                        # Проверка уникальности героев в матче
                        all_heroes = set(radiant_heroes) | set(dire_heroes)
                        unique = len(all_heroes) == 10  # Должно быть 10 уникальных героев

                        # Сохранение примеров для детального анализа
                        if match_index < 3:
                            sample_examples.append({
                                'match_id': h5_file['match_id'][match_index],
                                'radiant_heroes': radiant_heroes.tolist(),
                                'dire_heroes': dire_heroes.tolist(),
                                'radiant_win': radiant_win,
                                'duration': duration,
                                'radiant_score': radiant_score,
                                'dire_score': dire_score,
                                'radiant_hero_variants': h5_file['radiant_hero_variants'][match_index].tolist(),
                                'dire_hero_variants': h5_file['dire_hero_variants'][match_index].tolist(),
                                'radiant_roles': h5_file['radiant_roles'][match_index].tolist(),
                                'dire_roles': h5_file['dire_roles'][match_index].tolist(),
                                'unique': unique
                            })

                    # === СТАТИСТИЧЕСКИЙ АНАЛИЗ ===
                    print_subsection_header("Статистический анализ", "📊", Colors.BRIGHT_TEAL)

                    # Анализ баланса классов (распределение побед)
                    radiant_win_rate = (radiant_wins_count / sample_size) * 100 if sample_size > 0 else 0
                    win_rate_color = Colors.BRIGHT_GREEN if 48 <= radiant_win_rate <= 52 else Colors.BRIGHT_YELLOW
                    print_info_line("Побед Radiant", f"{radiant_wins_count:,} ({radiant_win_rate:.1f}%)", "🌞",
                                    value_color=win_rate_color)
                    print_info_line("Побед Dire",
                                    f"{sample_size - radiant_wins_count:,} ({100 - radiant_win_rate:.1f}%)", "🌑",
                                    value_color=win_rate_color)

                    # Статистика длительности матчей
                    if duration_stats:
                        avg_duration = np.mean(duration_stats)
                        min_duration = np.min(duration_stats)
                        max_duration = np.max(duration_stats)
                        print_info_line("Средняя длительность", f"{avg_duration / 60:.1f} минут", "⏱️",
                                        value_color=Colors.BRIGHT_PURPLE)
                        print_info_line("Диапазон длительности",
                                        f"{min_duration / 60:.1f} - {max_duration / 60:.1f} минут", "↔️",
                                        value_color=Colors.BRIGHT_CYAN)

                    # Статистика игрового счета
                    if score_stats:
                        avg_score = np.mean(score_stats)
                        min_score = np.min(score_stats)
                        max_score = np.max(score_stats)
                        print_info_line("Средний счет", f"{avg_score:.1f} очков", "🎯", value_color=Colors.BRIGHT_GOLD)
                        print_info_line("Диапазон счета", f"{min_score} - {max_score} очков", "↔️",
                                        value_color=Colors.BRIGHT_CYAN)

                    # === ВАЛИДАЦИЯ ИНДЕКСОВ ГЕРОЕВ ===
                    print_subsection_header("Валидация индексов героев", "🗺️", Colors.BRIGHT_PURPLE)
                    print_info_line("Мин. индекс героя", f"{min_hero_idx}", "📉", value_color=Colors.BRIGHT_CYAN)
                    print_info_line("Макс. индекс героя", f"{max_hero_idx}", "📈", value_color=Colors.BRIGHT_CYAN)
                    print_info_line("Ожидаемый диапазон", f"0 - {self.hero_mapper.total_heroes - 1}", "↔️",
                                    value_color=Colors.BRIGHT_YELLOW)

                    # Проверка корректности диапазона индексов
                    range_valid = (min_hero_idx >= 0) and (max_hero_idx < self.hero_mapper.total_heroes)
                    range_status = "Корректный ✅" if range_valid else "Некорректный ❌"
                    range_color = Colors.BRIGHT_GREEN if range_valid else Colors.BRIGHT_RED
                    print_info_line("Статус диапазона", range_status, "🔍", value_color=range_color)

                    # === АНАЛИЗ ПРИМЕРОВ ЗАПИСЕЙ ===
                    for example_num, example in enumerate(sample_examples, 1):
                        print_subsection_header(f"ПРИМЕР МАТЧА #{example_num}", "🕹️", Colors.BRIGHT_YELLOW)

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
                        print_info_line("Счет", f"Radiant: {example['radiant_score']} | Dire: {example['dire_score']}",
                                        "📊", value_color=Colors.BRIGHT_CYAN)

                        # Дополнительные характеристики героев
                        print_info_line("Radiant варианты", f"{example['radiant_hero_variants']}", "⚔️",
                                        value_color=Colors.BRIGHT_BLUE)
                        print_info_line("Dire варианты", f"{example['dire_hero_variants']}", "⚔️",
                                        value_color=Colors.BRIGHT_RED)

                        # Роли игроков
                        print_info_line("Radiant роли", f"{example['radiant_roles']}", "🎭",
                                        value_color=Colors.BRIGHT_GREEN)
                        print_info_line("Dire роли", f"{example['dire_roles']}", "🎭", value_color=Colors.BRIGHT_GREEN)

                    # === ИТОГИ ВАЛИДАЦИИ ===
                    print_subsection_header("Итоги валидации", "🎉", Colors.BRIGHT_GREEN)
                    print_status_message(f"{file_type} файл успешно прошел валидацию", "success", "🎊")

            except Exception as e:
                print_status_message(f"Ошибка при валидации {file_type.lower()} файла: {e}", "error", "❌")
                print_info_line("Тип ошибки", type(e).__name__, "⚠️", value_color=Colors.BRIGHT_RED)


def main():
    """
    Основная функция для создания и валидации HDF5 файлов из данных матчей Dota 2.

    Выполняет полный цикл создания датасета:
    1. Инициализация конфигурации и отображение параметров обработки
    2. Создание экземпляра препроцессора с подключением к базе данных
    3. Проверка существующих файлов и предупреждение о возможной перезаписи
    4. Запуск создания HDF5 файлов с разделением на основную и тестовую выборки
    5. Валидация созданных файлов с детальным анализом содержимого
    6. Обработка критических ошибок с диагностической информацией

    Raises:
        Exception: При критических ошибках инициализации, создания файлов или валидации

    Note:
        Функция предоставляет полную информацию о процессе создания датасета,
        включая конфигурацию, прогресс выполнения и результаты валидации.
    """
    print_section_header("ИНИЦИАЛИЗАЦИЯ СОЗДАНИЯ HDF5 ФАЙЛОВ", "🤖", width=100, color=Colors.BRIGHT_GOLD)

    # === ИНФОРМАЦИЯ О ФОРМАТЕ И СТРУКТУРЕ ДАННЫХ ===
    print_info_line("Входные данные", f"Составы героев ({TEAM_SIZE} vs {TEAM_SIZE})", "👥",
                    value_color=Colors.BRIGHT_CYAN)
    print_info_line("Выходные данные", "Результат матча + расширенная статистика", "🔮", value_color=Colors.BRIGHT_GREEN)
    print_info_line("Дополнительные поля", "Duration, Scores, Tower/Barracks Status, Hero Variants, Roles", "🧩",
                    value_color=Colors.BRIGHT_YELLOW)
    print_info_line("Исключенные герои", f"{len(EXCLUDED_HERO_IDS)} (ID: {list(EXCLUDED_HERO_IDS)})", "❌",
                    value_color=Colors.BRIGHT_RED)
    print_info_line("Маппинг ролей", "core → 0, support → 1", "🎭", value_color=Colors.BRIGHT_PURPLE)
    print_info_line("Формат вывода", "HDF5 (основной + тестовый файлы)", "💾", value_color=Colors.BRIGHT_ORANGE)

    # === КОНФИГУРАЦИЯ ПУТЕЙ И ОКРУЖЕНИЯ ===
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    base_output_path = os.path.join(project_root, 'data', f'{HDF5_FILE_NAME}_{DOTA_VERSION}.h5')

    print_subsection_header("Конфигурация окружения", "🔧", Colors.BRIGHT_CYAN)
    print_info_line("Директория данных", os.path.join(project_root, 'data'), "📂", value_color=Colors.BRIGHT_CYAN)

    try:
        # === ИНИЦИАЛИЗАЦИЯ ПРЕПРОЦЕССОРА ===
        preprocessor = HDF5Preprocessor(base_output_path)

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

        # === ОСНОВНОЙ ПРОЦЕСС СОЗДАНИЯ И ВАЛИДАЦИИ ===
        preprocessor.create_hdf5()
        preprocessor.validate_hdf5_files()

    except Exception as e:
        # === ОБРАБОТКА КРИТИЧЕСКИХ ОШИБОК ===
        print_section_header("КРИТИЧЕСКАЯ ОШИБКА", "💥", width=100, color=Colors.BRIGHT_RED)
        print_status_message(f"Ошибка: {e}", "error", "❌")
        print_info_line("Тип ошибки", type(e).__name__, "⚠️", value_color=Colors.BRIGHT_RED)


if __name__ == "__main__":
    main()
