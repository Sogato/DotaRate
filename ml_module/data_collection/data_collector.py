"""
Модуль для сбора и анализа данных матчей Dota 2 из Steam API.

Основные функции:
- Получение данных матчей из Steam API
- Фильтрация матчей по различным критериям
- Анализ игроков и назначение ролей
- Детекция руинеров в матчах
- Сохранение обработанных данных в базу данных
"""

import time
import requests
from datetime import datetime
from collections import Counter

# Импорты для работы с базой данных
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

# Импорт моделей базы данных
from data_bases.dataset.models import Match, MatchPlayer
from data_bases.heroes.models import Hero
from config import (DATASET_DATABASE_URL, HEROES_DATABASE_URL, STEAM_API_KEY, STEAM_API_MATCH_HISTORY_URL,
                    STARTING_MATCH_SEQUENCE_NUMBER, TARGET_MATCHES_COUNT, DATABASE_SAVE_CHUNK_SIZE,
                    BURST_TIME_TIMESTAMP, MATCHES_PER_API_REQUEST, API_REQUEST_DELAY_SECONDS,
                    ENABLE_DETAILED_STATISTICS, ENABLE_RUINER_LOGGING, ENABLE_ROLE_LOGGING, REQUIRED_MATCH_FIELDS,
                    REQUIRED_PLAYER_FIELDS, ALLOWED_GAME_MODES, MINIMUM_MATCH_DURATION, NUMBER_OF_PLAYERS_PER_TEAM,
                    SUPPORT_ITEM_IDS, CORE_EXPECTED_KDA, CORE_KDA_TOLERANCE, SUPPORT_EXPECTED_KDA,
                    SUPPORT_KDA_TOLERANCE, CORE_BASE_GPM, CORE_GPM_GROWTH_RATE, CORE_MAX_GPM, SUPPORT_BASE_GPM,
                    SUPPORT_GPM_GROWTH_RATE, SUPPORT_MAX_GPM, INCOME_MINIMUM_THRESHOLD, RUINER_DETECTION_THRESHOLD,
                    RUINER_LOGGING_THRESHOLD, RUINER_EMPTY_SLOTS_LIMIT, RUINER_SAME_ITEMS_LIMIT,
                    TEAM_DEATH_RATIO_WEIGHT, KDA_SCORE_WEIGHT, INCOME_SCORE_WEIGHT, CONTRIBUTION_SCORE_WEIGHT,
                    GPM_CALCULATION_START_MINUTE, SUPPORT_ITEMS_WEIGHT, NET_WORTH_WEIGHT, LAST_HITS_WEIGHT, GPM_WEIGHT,
                    XPM_WEIGHT, HERO_ITEM_EXCEPTIONS)

# Импорт утилит для консольного вывода
from utils.console import (
    Colors,
    print_section_header,
    print_subsection_header,
    print_info_line,
    print_progress_bar,
    print_status_message
)

# === НАСТРОЙКИ БАЗЫ ДАННЫХ ===
# Основная БД (датасет матчей dota 2)
dataset_engine = create_engine(DATASET_DATABASE_URL)
DatasetSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=dataset_engine)

# БД героев (справочная информация)
heroes_engine = create_engine(HEROES_DATABASE_URL)
HeroesSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=heroes_engine)

# Глобальный кэш для героев (загружается один раз при старте кода)
HEROES_CACHE = {}


def initialize_heroes_cache():
    """
    Инициализирует кэш героев из базы данных для быстрого доступа.

    Загружает всех героев из БД в память для избежания множественных запросов
    при обработке матчей. Кэш представляет собой словарь {hero_id: hero_name}.

    Returns:
        bool: True если кэш успешно загружен, False в случае ошибки

    Raises:
        Exception: При ошибках подключения к БД или отсутствии данных
    """
    global HEROES_CACHE

    heroes_session = HeroesSessionLocal()
    try:
        if ENABLE_DETAILED_STATISTICS:
            print_status_message("Загрузка данных героев из базы данных...", "info", "📚")

        # Получаем всех героев из БД
        heroes = heroes_session.query(Hero).all()

        if not heroes:
            if ENABLE_DETAILED_STATISTICS:
                print_status_message("ВНИМАНИЕ: База данных героев пуста!", "warning", "⚠️")
            return False

        # Заполняем кэш словарем {id: localized_name}
        for hero in heroes:
            HEROES_CACHE[hero.id] = hero.localized_name

        if ENABLE_DETAILED_STATISTICS:
            print_status_message(f"Загружено {len(HEROES_CACHE)} героев в кэш", "success", "✅")
        return True

    except Exception as e:
        print_status_message(f"Ошибка при загрузке героев из БД: {e}", "error", "❌")
        return False

    finally:
        heroes_session.close()


def get_hero_name_by_id(hero_id: int) -> str:
    """
    Получает название героя по его ID из кэша.

    Args:
        hero_id (int): Уникальный идентификатор героя

    Returns:
        str: Локализованное название героя или "Unknown Hero (ID: X)" если не найден
    """
    return HEROES_CACHE.get(hero_id, f"Unknown Hero (ID: {hero_id})")


def analyze_database_state(session) -> dict:
    """
    Анализирует текущее состояние базы данных и определяет стратегию сбора данных.

    Проверяет количество существующих матчей в БД и определяет один из сценариев:
    - 'empty': БД пуста, начинаем сбор с начала
    - 'continue': БД частично заполнена, продолжаем сбор
    - 'complete': БД уже содержит достаточно матчей

    Args:
        session: Сессия SQLAlchemy для работы с БД

    Returns:
        dict: Словарь с информацией о состоянии БД, включающий:
            - is_empty (bool): Пуста ли БД
            - existing_matches_count (int): Количество существующих матчей
            - max_match_seq_num (int): Максимальный sequence number
            - scenario (str): Сценарий действий ('empty'/'continue'/'complete')
            - remaining_needed (int): Сколько матчей еще нужно (для 'continue')
            - next_seq_num (int): Следующий sequence number (для 'continue')
    """
    print_section_header("АНАЛИЗ СОСТОЯНИЯ БАЗЫ ДАННЫХ", "📊", color=Colors.BRIGHT_CYAN)

    # Получаем количество матчей в базе данных
    existing_matches_count = session.query(func.count(Match.match_id)).scalar() or 0

    # Сценарий 1: БД пуста
    if existing_matches_count == 0:
        print_info_line("Состояние базы", "ПУСТАЯ", "📊", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
        print_info_line("Целевое количество", f"{TARGET_MATCHES_COUNT:,} матчей", "🎯", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_GREEN)
        print_info_line("Стартовый sequence number", f"{STARTING_MATCH_SEQUENCE_NUMBER:,}", "🚀", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_BLUE)
        print()

        return {
            'is_empty': True,
            'existing_matches_count': 0,
            'max_match_seq_num': None,
            'scenario': 'empty'
        }

    # Получаем статистику существующих данных
    max_sequence_number = session.query(func.max(Match.match_seq_num)).scalar()
    min_start_time = session.query(func.min(Match.start_time)).scalar()
    max_start_time = session.query(func.max(Match.start_time)).scalar()

    # Форматируем временной диапазон для отображения
    min_time_formatted = datetime.fromtimestamp(min_start_time).strftime(
        '%Y-%m-%d %H:%M:%S') if min_start_time else "N/A"
    max_time_formatted = datetime.fromtimestamp(max_start_time).strftime(
        '%Y-%m-%d %H:%M:%S') if max_start_time else "N/A"

    # Выводим текущую статистику
    print_info_line("Матчей в базе", f"{existing_matches_count:,}", "📊", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    print_info_line("Максимальный sequence", f"{max_sequence_number:,}", "🔢", Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)
    print_info_line("Временной диапазон", f"{min_time_formatted} → {max_time_formatted}", "📅", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_MAGENTA)
    print_info_line("Целевое количество", f"{TARGET_MATCHES_COUNT:,}", "🎯", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)

    # Сценарий 2: БД уже заполнена
    if existing_matches_count >= TARGET_MATCHES_COUNT:
        print_info_line("Статус", "ЗАВЕРШЕНО", "✅", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
        print()

        return {
            'is_empty': False,
            'existing_matches_count': existing_matches_count,
            'max_match_seq_num': max_sequence_number,
            'scenario': 'complete'
        }

    # Сценарий 3: БД частично заполнена, нужно продолжить сбор
    remaining_matches_needed = TARGET_MATCHES_COUNT - existing_matches_count
    next_sequence_number = max_sequence_number + 1

    print()
    print_info_line("Статус", "ТРЕБУЕТ ДОПОЛНЕНИЯ", "📈", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    print_info_line("Следующий sequence", f"{next_sequence_number:,}", "🔄", Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)
    print_info_line("Нужно собрать ещё", f"{remaining_matches_needed:,} матчей", "📊", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_GREEN)
    print()

    return {
        'is_empty': False,
        'existing_matches_count': existing_matches_count,
        'max_match_seq_num': max_sequence_number,
        'scenario': 'continue',
        'remaining_needed': remaining_matches_needed,
        'next_seq_num': next_sequence_number
    }


def print_collection_configuration_header(database_state: dict):
    """
    Выводит заголовок конфигурации сбора данных.

    Отображает текущие настройки программы, включая целевые параметры,
    настройки фильтрации и логирования. Пропускает вывод если БД уже заполнена.

    Args:
        database_state (dict): Словарь с информацией о состоянии базы данных
    """
    if database_state['scenario'] == 'complete':
        return  # Не выводим заголовок если база данных уже заполнена

    print_section_header("ЗАПУСК СБОРА ДАННЫХ О МАТЧАХ DOTA 2", "🚀", color=Colors.BRIGHT_MAGENTA)

    print_subsection_header("Конфигурация сбора", "📋", Colors.BRIGHT_GREEN)

    # Определяем параметры в зависимости от сценария
    if database_state['scenario'] == 'empty':
        print_info_line("Целевое количество", f"{TARGET_MATCHES_COUNT:,} матчей", "🎯")
        print_info_line("Начальный sequence", f"{STARTING_MATCH_SEQUENCE_NUMBER:,}", "🚀")
    else:  # continue
        print_info_line("Целевое количество", f"{TARGET_MATCHES_COUNT:,} матчей", "🎯")
        print_info_line("Уже в базе", f"{database_state['existing_matches_count']:,}", "📊", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_CYAN)
        print_info_line("Нужно собрать", f"{database_state['remaining_needed']:,}", "📈", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_YELLOW)
        print_info_line("Продолжение с sequence", f"{database_state['next_seq_num']:,}", "🔄", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_BLUE)

    # Настройки производительности
    print_info_line("Размер чанка для сохранения", f"{DATABASE_SAVE_CHUNK_SIZE:,}", "📦", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_ORANGE)
    print_info_line("Матчей за запрос к API", f"{MATCHES_PER_API_REQUEST}", "🔢", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_PURPLE)
    print_info_line("Задержка между запросами", f"{API_REQUEST_DELAY_SECONDS}с", "⌛", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_PINK)

    # Настройки фильтрации
    print_subsection_header("Настройки фильтрации", "🔧", Colors.BRIGHT_BLUE)
    burst_time_readable = datetime.fromtimestamp(BURST_TIME_TIMESTAMP).strftime('%Y-%m-%d %H:%M:%S')
    print_info_line("Время burst", f"{BURST_TIME_TIMESTAMP} ({burst_time_readable})", "⏰", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_GOLD)
    print_info_line("Минимальная длительность", "20 мин", "⏱️", Colors.BRIGHT_WHITE, Colors.BRIGHT_MINT)
    print_info_line("Разрешённые режимы", f"{len(ALLOWED_GAME_MODES)} режимов", "🎮", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_CORAL)

    # Настройки логирования
    print_subsection_header("Настройки логирования", "📝", Colors.BRIGHT_MAGENTA)
    print_info_line("Детальная статистика", "ВКЛ" if ENABLE_DETAILED_STATISTICS else "ВЫКЛ", "📈",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN if ENABLE_DETAILED_STATISTICS else Colors.BRIGHT_RED)
    print_info_line("Логирование руинеров", "ВКЛ" if ENABLE_RUINER_LOGGING else "ВЫКЛ", "📝",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN if ENABLE_RUINER_LOGGING else Colors.BRIGHT_RED)
    print_info_line("Логирование ролей", "ВКЛ" if ENABLE_ROLE_LOGGING else "ВЫКЛ", "👥",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN if ENABLE_ROLE_LOGGING else Colors.BRIGHT_RED)

    # Примерная оценка времени выполнения
    estimated_api_calls = ((TARGET_MATCHES_COUNT - database_state['existing_matches_count']) // 38) + 1
    estimated_time_minutes = (estimated_api_calls * (API_REQUEST_DELAY_SECONDS + 2.5)) / 60

    print_subsection_header("Предварительная оценка", "⏳", Colors.BRIGHT_YELLOW)
    print_info_line("Примерно API вызовов", f"~{estimated_api_calls:,}", "📡", Colors.BRIGHT_WHITE, Colors.BRIGHT_TEAL)
    print_info_line("Минимальное время", f"~{estimated_time_minutes:.1f} мин", "⏰", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_LAVENDER)

    print()
    print()
    print_status_message("НАЧИНАЕМ ПРОЦЕСС СБОРА...", "info", "▶️")


def fetch_matches_from_steam_api(last_match_sequence_number: int) -> list:
    """
    Выполняет запрос к Steam API для получения пакета матчей.

    Функция автоматически обрабатывает ошибки сети и таймауты,
    повторяя запросы при необходимости с задержками.

    Args:
        last_match_sequence_number (int): Последний sequence number для запроса

    Returns:
        list: Список матчей от Steam API или пустой список при отсутствии новых матчей

    Note:
        Функция использует бесконечный цикл с обработкой исключений
    """
    while True:
        try:
            # Формируем URL запроса к Steam API
            url = f"{STEAM_API_MATCH_HISTORY_URL}?start_at_match_seq_num={last_match_sequence_number}&matches_requested={MATCHES_PER_API_REQUEST}&key={STEAM_API_KEY}"

            # Выполняем HTTP запрос с таймаутом
            response = requests.get(url, timeout=30)
            response.raise_for_status()  # Проверяем статус ответа

            # Извлекаем матчи из JSON ответа
            return response.json().get("result", {}).get("matches", [])

        except requests.exceptions.Timeout:
            print_status_message("Steam API | Превышено время ожидания запроса, повторный запрос через 5 секунд...",
                                 "warning")
            time.sleep(10)
        except requests.exceptions.RequestException as e:
            print_status_message(f"Steam API | Ошибка при запросе: {e}, повтор через 10 секунд...", "error")
            time.sleep(10)


def primary_match_filters(steam_matches: list) -> tuple[list, dict]:
    """
    Применяет первичные фильтры к матчам (базовые критерии валидации).

    Первичная фильтрация включает проверку:
    - Времени начала матча (после BURST_TIME_TIMESTAMP)
    - Игрового режима (должен быть в ALLOWED_GAME_MODES)
    - Длительности матча (минимум установленный в MINIMUM_MATCH_DURATION)
    - Наличия всех необходимых полей в API
    - Количества игроков (обычно 5v5)

    Args:
        steam_matches (list): Список матчей от Steam API

    Returns:
        tuple[list, dict]: Кортеж из отфильтрованных матчей и статистики фильтрации
            - list: Матчи, прошедшие первичную фильтрацию
            - dict: Статистика исключений (если включено детальное логирование)
    """
    filtered_matches = []

    # Инициализируем статистику только если включено детальное логирование
    if ENABLE_DETAILED_STATISTICS:
        filter_stats = {
            'input_count': len(steam_matches),
            'excluded_burst_time': 0,
            'excluded_game_mode': 0,
            'excluded_duration': 0,
            'excluded_missing_keys': 0,
            'excluded_player_count': 0,
            'excluded_player_keys': 0,
            'output_count': 0
        }
    else:
        filter_stats = None

    for match in steam_matches:
        # Фильтр по времени начала матча (должен быть после burst time)
        if match.get("start_time", 0) <= BURST_TIME_TIMESTAMP:
            if ENABLE_DETAILED_STATISTICS:
                filter_stats['excluded_burst_time'] += 1
            continue

        # Фильтр по игровому режиму (только разрешенные режимы)
        if match.get("game_mode") not in ALLOWED_GAME_MODES:
            if ENABLE_DETAILED_STATISTICS:
                filter_stats['excluded_game_mode'] += 1
            continue

        # Фильтр по длительности матча (в секундах)
        if match.get("duration", 0) <= MINIMUM_MATCH_DURATION:
            if ENABLE_DETAILED_STATISTICS:
                filter_stats['excluded_duration'] += 1
            continue

        # Проверка наличия всех необходимых ключей матча
        if not REQUIRED_MATCH_FIELDS.issubset(match.keys()):
            if ENABLE_DETAILED_STATISTICS:
                filter_stats['excluded_missing_keys'] += 1
            continue

        # Проверка количества игроков в командах (должно быть 5v5)
        players = match.get("players", [])
        radiant_players = [p for p in players if p.get("team_number") == 0]
        dire_players = [p for p in players if p.get("team_number") == 1]

        if len(radiant_players) != NUMBER_OF_PLAYERS_PER_TEAM or len(dire_players) != NUMBER_OF_PLAYERS_PER_TEAM:
            if ENABLE_DETAILED_STATISTICS:
                filter_stats['excluded_player_count'] += 1
            continue

        # Проверка наличия необходимых ключей у игроков
        if not all(REQUIRED_PLAYER_FIELDS.issubset(player.keys()) for player in players):
            if ENABLE_DETAILED_STATISTICS:
                filter_stats['excluded_player_keys'] += 1
            continue

        # Матч прошел все проверки
        filtered_matches.append(match)

    if ENABLE_DETAILED_STATISTICS:
        filter_stats['output_count'] = len(filtered_matches)

    return filtered_matches, filter_stats


def secondary_match_filters(steam_matches: list) -> tuple[list, dict]:
    """
    Применяет вторичные фильтры к матчам (сложные критерии анализа).

    Вторичная фильтрация включает:
    - Проверку на ливеров (игроков, покинувших матч)
    - Назначение ролей игрокам (core/support)
    - Обнаружение руинеров (игроков, специально саботирующих игровой процесс)
    - Логирование ролей (если включено)

    Args:
        steam_matches (list): Список предварительно отфильтрованных матчей

    Returns:
        tuple[list, dict]: Кортеж из финально отфильтрованных матчей и статистики
            - list: Матчи, прошедшие все фильтры
            - dict: Статистика исключений (если включено детальное логирование)
    """
    filtered_matches = []

    # Инициализируем статистику только если включено детальное логирование
    if ENABLE_DETAILED_STATISTICS:
        filter_stats = {
            'input_count': len(steam_matches),
            'excluded_leavers': 0,
            'excluded_ruiners': 0,
            'excluded_role_assignment': 0,
            'output_count': 0
        }
    else:
        filter_stats = None

    for match in steam_matches:
        # Фильтр по ливерам - исключаем матчи где кто-то покинул игру
        # leaver_status: 0 = finished match, 1 = player DC (no abandon), 2+ = abandoned
        players = match.get("players", [])
        if any(player.get("leaver_status", 0) not in [0, 1] for player in players):
            if ENABLE_DETAILED_STATISTICS:
                filter_stats['excluded_leavers'] += 1
            continue

        # Извлекаем и группируем игроков по командам
        radiant_players, dire_players = extract_and_group_players_data(players)

        # Пытаемся назначить роли игрокам
        try:
            radiant_players = assign_player_roles(radiant_players)
            dire_players = assign_player_roles(dire_players)
        except ValueError:
            # Не удалось корректно назначить роли
            if ENABLE_DETAILED_STATISTICS:
                filter_stats['excluded_role_assignment'] += 1
            continue

        # Проверяем наличие руинеров в матче
        match_duration_minutes = match.get("duration", 0) / 60
        ruiners_found = []

        # Проверяем команду Radiant на руинеров
        for player in radiant_players:
            if detect_ruiner_player(player, match_duration_minutes, match["match_id"],
                                    match["radiant_score"], match["dire_score"]):
                ruiners_found.append(('Radiant', player))

        # Проверяем команду Dire на руинеров
        for player in dire_players:
            if detect_ruiner_player(player, match_duration_minutes, match["match_id"],
                                    match["dire_score"], match["radiant_score"]):
                ruiners_found.append(('Dire', player))

        # Если найдены руинеры - исключаем матч
        if ruiners_found:
            # Дополнительное логирование для матчей с множественными руинерами
            if ENABLE_RUINER_LOGGING and len(ruiners_found) > 1:
                print()
                print_status_message(f"МАТЧ С МНОЖЕСТВЕННЫМИ РУИНЕРАМИ", "error", "🚨")
                print_info_line("Match ID", f"{match['match_id']}", "🆔", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
                print_info_line("Всего руинеров", f"{len(ruiners_found)}", "💀", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
                for team, player in ruiners_found:
                    hero_name = get_hero_name_by_id(player['hero_id'])
                    print_info_line(f"{team} команда", f"{hero_name} (ID: {player['account_id']})", "🏴",
                                    Colors.BRIGHT_WHITE, Colors.CORAL)

            if ENABLE_DETAILED_STATISTICS:
                filter_stats['excluded_ruiners'] += 1
            continue

        # Логируем роли только для "чистых" матчей
        if ENABLE_ROLE_LOGGING:
            print_subsection_header(f"DEBUG | Распределение ролей (Match ID: {match["match_id"]})", "👥",
                                    Colors.BRIGHT_ORANGE)
            print(f"🌳 {Colors.BRIGHT_GREEN}Команда Radiant: {Colors.RESET}")
            log_team_role_assignment(radiant_players)
            print(f"💀 {Colors.BRIGHT_RED}Команда Dire: {Colors.RESET}")
            log_team_role_assignment(dire_players)
            print()

        # Сохраняем обработанные данные в матче для последующего использования
        match['processed_radiant_players'] = radiant_players
        match['processed_dire_players'] = dire_players
        filtered_matches.append(match)

    if ENABLE_DETAILED_STATISTICS:
        filter_stats['output_count'] = len(filtered_matches)

    if ENABLE_RUINER_LOGGING:
        print()

    return filtered_matches, filter_stats


def extract_and_group_players_data(players: list) -> tuple[list, list]:
    """
    Извлекает данные игроков и группирует их по командам.

    Обрабатывает сырые данные игроков от Steam API, извлекая только
    необходимые поля и группируя по командам (Radiant/Dire).

    Args:
        players (list): Список игроков из Steam API

    Returns:
        tuple[list, list]: Кортеж (игроки_radiant, игроки_dire)
            Каждый список содержит словари с обработанными данными игроков
    """
    radiant_players = []
    dire_players = []

    for player in players:
        # Извлекаем основные характеристики игрока
        player_data = {
            # Базовая информация
            "account_id": player.get("account_id", 0),
            "hero_id": player.get("hero_id", 0),
            "hero_variant": player.get("hero_variant", 0),

            # Экономические показатели
            "net_worth": player.get("net_worth", 0),
            "last_hits": player.get("last_hits", 0),
            "denies": player.get("denies", 0),
            "gold_per_min": player.get("gold_per_min", 0),
            "xp_per_min": player.get("xp_per_min", 0),

            # Предметы в основных слотах (0-5)
            "item_0": player.get("item_0", 0),
            "item_1": player.get("item_1", 0),
            "item_2": player.get("item_2", 0),
            "item_3": player.get("item_3", 0),
            "item_4": player.get("item_4", 0),
            "item_5": player.get("item_5", 0),

            # Предметы в рюкзаке
            "backpack_0": player.get("backpack_0", 0),
            "backpack_1": player.get("backpack_1", 0),
            "backpack_2": player.get("backpack_2", 0),

            # Боевая статистика
            "kills": player.get("kills", 0),
            "deaths": player.get("deaths", 0),
            "assists": player.get("assists", 0),

            # Нейтральные предметы и дополнительные характеристики
            "item_neutral": player.get("item_neutral", 0),
            "item_neutral2": player.get("item_neutral2", 0),
            "level": player.get("level", 0),
            "aghanims_scepter": player.get("aghanims_scepter", 0),
            "aghanims_shard": player.get("aghanims_shard", 0),
            "moonshard": player.get("moonshard", 0),
        }

        # Распределяем по командам (0 = Radiant, 1 = Dire)
        if player.get("team_number") == 0:
            radiant_players.append(player_data)
        else:
            dire_players.append(player_data)

    return radiant_players, dire_players


def create_database_match_record(match: dict) -> dict:
    """
    Создаёт итоговый словарь с данными матча для сохранения в БД.

    Преобразует обработанные данные матча в формат, подходящий для
    записи в базу данных, включая все необходимые поля.

    Args:
        match (dict): Обработанные данные матча с назначенными ролями

    Returns:
        dict: Словарь с данными матча для базы данных, включающий:
            - Основную информацию о матче
            - Обработанные данные игроков обеих команд
    """
    return {
        # Основная информация о матче
        "match_id": match["match_id"],
        "match_seq_num": match["match_seq_num"],
        "radiant_win": match["radiant_win"],
        "duration": match["duration"],
        "start_time": match["start_time"],

        # Статус построек
        "tower_status_radiant": match["tower_status_radiant"],
        "tower_status_dire": match["tower_status_dire"],
        "barracks_status_radiant": match["barracks_status_radiant"],
        "barracks_status_dire": match["barracks_status_dire"],

        # Дополнительная информация
        "lobby_type": match["lobby_type"],
        "game_mode": match["game_mode"],
        "radiant_score": match["radiant_score"],
        "dire_score": match["dire_score"],

        # Обработанные данные игроков
        "radiant_players": match['processed_radiant_players'],
        "dire_players": match['processed_dire_players'],
    }


def assign_player_roles(team_players: list) -> list:
    """
    Назначает роли игрокам команды на основе их игровых характеристик.

    Использует алгоритм на основе support_score, который учитывает:
    - Количество поддерживающих предметов
    - Экономические показатели (net worth, GPM, XPM, last hits)
    - Нормализацию относительно максимумов команды

    Args:
        team_players (list): Список игроков команды (должно быть 5 игроков)

    Returns:
        list: Список игроков с назначенными ролями ('core' или 'support')

    Raises:
        ValueError: Если количество игроков не равно 5 или роли назначены некорректно

    Note:
        Алгоритм назначает первых 2 игроков с высшим support_score как саппортов,
        остальных 3 - как коров
    """
    if len(team_players) != NUMBER_OF_PLAYERS_PER_TEAM:
        raise ValueError("Команда должна состоять из 5 игроков.")

    # Вычисляем максимумы один раз для всей команды (для нормализации)
    team_statistics = {
        'max_net_worth': max((player["net_worth"] for player in team_players), default=1),
        'max_last_hits': max((player["last_hits"] for player in team_players), default=1),
        'max_gpm': max((player["gold_per_min"] for player in team_players), default=1),
        'max_xpm': max((player["xp_per_min"] for player in team_players), default=1)
    }

    # Вычисляем support_score для каждого игрока
    for player in team_players:
        support_score = calculate_player_support_score(player, team_statistics)
        player["support_score"] = support_score
        player["role"] = "undefined"

    # Сортируем по support_score (убывающий порядок) и назначаем роли
    team_players.sort(key=lambda player: player["support_score"], reverse=True)

    for index, player in enumerate(team_players):
        # Первые 2 игрока с высшим support_score становятся саппортами
        player["role"] = "support" if index < 2 else "core"

    # Проверка корректности назначения ролей
    core_count = sum(1 for player in team_players if player["role"] == "core")
    support_count = sum(1 for player in team_players if player["role"] == "support")

    if core_count != 3 or support_count != 2:
        raise ValueError(f"Ошибка распределения ролей: {core_count} коров и {support_count} саппортов.")

    return team_players


def calculate_player_support_score(player: dict, team_stats: dict) -> float:
    """
    Вычисляет support_score для игрока на основе его предметов и игровых показателей.

    Support_score представляет собой взвешенную сумму факторов, указывающих
    на то, что игрок играет роль поддержки:

    Факторы (чем выше, тем больше похож на саппорта):
    - Количество поддерживающих предметов
    - Низкие экономические показатели (инвертированные и нормализованные)

    Args:
        player (dict): Словарь с данными игрока
        team_stats (dict): Словарь с максимальными значениями команды

    Returns:
        float: Численный support_score (чем выше, тем больше похож на саппорта)

    Note:
        Учитываются исключения саппорт-предметов для конкретных героев из HERO_ITEM_EXCEPTIONS
    """
    hero_id = player.get("hero_id")
    support_items_count = 0

    # Проверяем основные слоты (item_0 до item_5)
    for slot_index in range(6):
        item_id = player.get(f"item_{slot_index}")
        if item_id and item_id in SUPPORT_ITEM_IDS:
            # Учитываем исключения для конкретных героев
            if hero_id not in HERO_ITEM_EXCEPTIONS or item_id not in HERO_ITEM_EXCEPTIONS[hero_id]:
                support_items_count += 1

    # Проверяем рюкзак (backpack_0 до backpack_2)
    for backpack_index in range(3):
        item_id = player.get(f"backpack_{backpack_index}")
        if item_id and item_id in SUPPORT_ITEM_IDS:
            # Учитываем исключения для конкретных героев
            if hero_id not in HERO_ITEM_EXCEPTIONS or item_id not in HERO_ITEM_EXCEPTIONS[hero_id]:
                support_items_count += 1

    # Нормализованные метрики (0-1, где 1 = максимум команды)
    normalized_net_worth = player["net_worth"] / team_stats['max_net_worth']
    normalized_last_hits = player["last_hits"] / team_stats['max_last_hits']
    normalized_gpm = player["gold_per_min"] / team_stats['max_gpm']
    normalized_xpm = player["xp_per_min"] / team_stats['max_xpm']

    # Итоговый счет (инвертируем экономические показатели)
    # Чем меньше экономика - тем больше похож на саппорта
    support_score = (
            support_items_count * SUPPORT_ITEMS_WEIGHT +
            (1 - normalized_net_worth) * NET_WORTH_WEIGHT +
            (1 - normalized_last_hits) * LAST_HITS_WEIGHT +
            (1 - normalized_gpm) * GPM_WEIGHT +
            (1 - normalized_xpm) * XPM_WEIGHT
    )

    return support_score


def log_team_role_assignment(team_players: list):
    """
    Выводит распределение ролей в команде для отладки.

    Выводит детальную информацию о каждом игроке команды,
    включая ID, героя, роль, support_score и ключевые метрики.

    Args:
        team_players (list): Список игроков команды с назначенными ролями
    """
    for player in team_players:
        hero_name = get_hero_name_by_id(player['hero_id'])
        role_color = Colors.BRIGHT_RED if player["role"] == "support" else Colors.BRIGHT_BLUE

        print(
            f"  {Colors.BRIGHT_WHITE}Player ID:{Colors.RESET} {Colors.BRIGHT_YELLOW}{player['account_id']}{Colors.RESET} | "
            f"{Colors.BRIGHT_WHITE}Герой:{Colors.RESET} {Colors.BRIGHT_CYAN}{hero_name}{Colors.RESET}({Colors.BRIGHT_YELLOW}{player['hero_id']}{Colors.RESET}) | "
            f"{Colors.BRIGHT_WHITE}Роль:{Colors.RESET} {role_color}{player['role'].upper()}{Colors.RESET} | "
            f"{Colors.BRIGHT_WHITE}Support Score:{Colors.RESET} {Colors.BRIGHT_GREEN}{player['support_score']:.2f}{Colors.RESET} | "
            f"{Colors.BRIGHT_WHITE}Net: {player['net_worth']}{Colors.RESET} | {Colors.BRIGHT_WHITE}LH: {player['last_hits']}{Colors.RESET} | {Colors.BRIGHT_WHITE}GPM: {player['gold_per_min']}{Colors.RESET} | {Colors.BRIGHT_WHITE}XPM: {player['xp_per_min']}{Colors.RESET}")


def detect_ruiner_player(player: dict, match_duration_minutes: float, match_id: int,
                         team_score: int, enemy_team_score: int) -> bool:
    """
    Определяет, является ли игрок руинером на основе комплексного анализа.

    Алгоритм детекции руинеров основан на комбинации факторов:

    1. Team Death Ratio - доля смертей игрока от общих смертей вражеской команды
    2. KDA Score - отклонение от ожидаемого KDA для роли
    3. Income Score - соответствие экономических показателей ожиданиям
    4. Contribution Score - вклад в убийства команды
    5. Аномалии в предметах (пустые слоты, дубликаты)

    Args:
        player (dict): Словарь с данными игрока
        match_duration_minutes (float): Длительность матча в минутах
        match_id (int): ID матча для логирования
        team_score (int): Счет команды игрока
        enemy_team_score (int): Счет вражеской команды

    Returns:
        bool: True если игрок является руинером, False иначе

    Note:
        Итоговый ruiner_index вычисляется как взвешенная сумма всех факторов.
        Порог детекции определяется константой RUINER_DETECTION_THRESHOLD.
    """
    # Извлекаем основные характеристики
    kills = player.get("kills", 0)
    deaths = player.get("deaths", 0)
    assists = player.get("assists", 0)
    net_worth = player.get("net_worth", 0)
    role = player.get("role", "core")

    # === КОМПОНЕНТ 1: Доля смертей от командных ===
    # Показывает, сколько смертей игрока от общих смертей врагов (чем больше, тем хуже)
    team_death_ratio = min(deaths / max(1, enemy_team_score), 1)

    # === КОМПОНЕНТ 2: KDA Score с учетом роли ===
    actual_kda = (kills + assists) / max(1, deaths)

    # Определяем ожидаемые значения в зависимости от роли
    if role == "core":
        expected_kda = CORE_EXPECTED_KDA
        kda_tolerance = CORE_KDA_TOLERANCE
        base_gpm, gpm_growth_rate, max_gpm = CORE_BASE_GPM, CORE_GPM_GROWTH_RATE, CORE_MAX_GPM
    else:  # support
        expected_kda = SUPPORT_EXPECTED_KDA
        kda_tolerance = SUPPORT_KDA_TOLERANCE
        base_gpm, gpm_growth_rate, max_gpm = SUPPORT_BASE_GPM, SUPPORT_GPM_GROWTH_RATE, SUPPORT_MAX_GPM

    # Нормализуем KDA в диапазон 0-1 (где 0 = хороший KDA, 1 = плохой)
    kda_score = max(0.0, min(1.0, (expected_kda - actual_kda) / kda_tolerance))

    # === КОМПОНЕНТ 3: Income Score ===
    # Вычисляем ожидаемый GPM и net worth с учетом роли
    expected_gpm = min(base_gpm + gpm_growth_rate * max(0.0, match_duration_minutes - GPM_CALCULATION_START_MINUTE),
                       max_gpm)
    expected_net_worth = match_duration_minutes * expected_gpm

    if expected_net_worth > 0:
        income_ratio = net_worth / expected_net_worth
        if income_ratio >= 1.0:
            income_score = 1.0  # Отлично, больше ожидаемого
        elif income_ratio <= INCOME_MINIMUM_THRESHOLD:
            income_score = 0.0  # Ужасно, меньше минимального порога
        else:
            # Линейная интерполяция между минимальным порогом и 100%
            income_score = (income_ratio - INCOME_MINIMUM_THRESHOLD) / (1.0 - INCOME_MINIMUM_THRESHOLD)
    else:
        income_score = 1.0

    # === КОМПОНЕНТ 4: Contribution Score ===
    # Доля участия в убийствах команды
    contribution_score = (kills + assists) / max(team_score, 1)

    # === ВЫЧИСЛЕНИЕ БАЗОВОГО ИНДЕКСА РУИНЕРА ===
    ruiner_index = (
            TEAM_DEATH_RATIO_WEIGHT * team_death_ratio +
            KDA_SCORE_WEIGHT * kda_score +
            INCOME_SCORE_WEIGHT * (1 - income_score) +
            CONTRIBUTION_SCORE_WEIGHT * (1 - contribution_score)
    )

    # === ПРОВЕРКА АНОМАЛИЙ В ПРЕДМЕТАХ ===
    items = []
    empty_slots_count = 0

    # Собираем все предметы из основных слотов (item_0 до item_5)
    for slot_index in range(6):
        item_id = player.get(f"item_{slot_index}")
        if item_id == 0 or item_id is None:
            empty_slots_count += 1
        else:
            items.append(item_id)

    # Автоматическое определение как руинера при критических аномалиях
    if empty_slots_count >= RUINER_EMPTY_SLOTS_LIMIT:
        # Слишком много пустых слотов
        ruiner_index = 1.0
    elif len(items) >= RUINER_SAME_ITEMS_LIMIT:
        # Проверяем на слишком много одинаковых предметов (обычно так поступают боты в подставных матчах)
        item_counts = Counter(items)
        for item_id, count in item_counts.items():
            if count >= RUINER_SAME_ITEMS_LIMIT:
                ruiner_index = 1.0
                break

    # === ЛОГИРОВАНИЕ ===
    if ENABLE_RUINER_LOGGING and ruiner_index >= RUINER_LOGGING_THRESHOLD:
        log_ruiner_detection_details(match_id, player, ruiner_index, match_duration_minutes,
                                     role, team_death_ratio, kda_score, income_score, contribution_score,
                                     expected_gpm, team_score, actual_kda, expected_kda)

    return ruiner_index > RUINER_DETECTION_THRESHOLD


def log_ruiner_detection_details(match_id: int, player: dict, ruiner_index: float,
                                 match_duration: float, role: str, team_death_ratio: float,
                                 kda_score: float, income_score: float, contribution_score: float,
                                 expected_gpm: float, team_score: int, actual_kda: float, expected_kda: float):
    """
    Логирует детальную статистику потенциального руинера для отладки.

    Выводит подробную информацию о всех компонентах ruiner_index,
    что позволяет анализировать точность алгоритма.

    Args:
        match_id (int): ID матча
        player (dict): Данные игрока
        ruiner_index (float): Вычисленный индекс руинера
        match_duration (float): Длительность матча в минутах
        role (str): Роль игрока ('core' или 'support')
        team_death_ratio (float): Компонент team death ratio
        kda_score (float): Компонент KDA score
        income_score (float): Компонент income score
        contribution_score (float): Компонент contribution score
        expected_gpm (float): Ожидаемый GPM для роли
        team_score (int): Счет команды
        actual_kda (float): Фактический KDA игрока
        expected_kda (float): Ожидаемый KDA для роли
    """
    status = "РУИНЕР" if ruiner_index > RUINER_DETECTION_THRESHOLD else "ПОДОЗРЕНИЕ"
    status_color = Colors.BRIGHT_RED if ruiner_index > RUINER_DETECTION_THRESHOLD else Colors.BRIGHT_YELLOW
    hero_name = get_hero_name_by_id(player['hero_id'])

    print_subsection_header(f"DEBUG | Определение руинеров: {status}", "🔍", status_color)

    # Основная информация
    print(f"{Colors.BRIGHT_WHITE}Match ID:{Colors.RESET} {Colors.BRIGHT_YELLOW}{match_id}{Colors.RESET} | "
          f"{Colors.BRIGHT_WHITE}Player ID:{Colors.RESET} {Colors.BRIGHT_CYAN}{player['account_id']}{Colors.RESET} | "
          f"{Colors.BRIGHT_WHITE}Герой:{Colors.RESET} {Colors.BRIGHT_BLUE}{hero_name}{Colors.RESET}({Colors.BRIGHT_YELLOW}{player['hero_id']}{Colors.RESET}) | "
          f"{Colors.BRIGHT_WHITE}Роль:{Colors.RESET} {Colors.BRIGHT_MAGENTA}{role.upper()}{Colors.RESET}")

    # Основные метрики
    print(f"{Colors.BRIGHT_WHITE}Индекс руинера:{Colors.RESET} {Colors.BRIGHT_RED}{ruiner_index:.3f}{Colors.RESET} "
          f"({Colors.BRIGHT_RED}{ruiner_index * 100:.1f}%{Colors.RESET}) | "
          f"{Colors.BRIGHT_WHITE}Время:{Colors.RESET} {Colors.BRIGHT_GOLD}{match_duration:.1f} мин.{Colors.RESET} | "
          f"{Colors.BRIGHT_WHITE}Счет команды:{Colors.RESET} {Colors.BRIGHT_GREEN}{team_score}{Colors.RESET} | "
          f"{Colors.BRIGHT_WHITE}GPM ожид:{Colors.RESET} {Colors.BRIGHT_ORANGE}{expected_gpm:.0f}{Colors.RESET} | "
          f"{Colors.BRIGHT_WHITE}KDA (факт/ожид):{Colors.RESET} {Colors.BRIGHT_ORANGE}{actual_kda:.2f}{Colors.RESET}/"
          f"{Colors.BRIGHT_ORANGE}{expected_kda:.1f}{Colors.RESET}")

    # Детальные метрики (показываем взвешенные значения и проценты)
    print(f"{Colors.BRIGHT_WHITE}Метрики:{Colors.RESET} "
          f"TDR: {Colors.BRIGHT_YELLOW}{TEAM_DEATH_RATIO_WEIGHT * team_death_ratio:.3f}{Colors.RESET} "
          f"({Colors.BRIGHT_YELLOW}{team_death_ratio * 100:.1f}%{Colors.RESET}) | "
          f"KDA: {Colors.BRIGHT_YELLOW}{KDA_SCORE_WEIGHT * kda_score:.3f}{Colors.RESET} "
          f"({Colors.BRIGHT_YELLOW}{kda_score * 100:.1f}%{Colors.RESET}) | "
          f"IS: {Colors.BRIGHT_YELLOW}{INCOME_SCORE_WEIGHT * (1 - income_score):.3f}{Colors.RESET} "
          f"({Colors.BRIGHT_YELLOW}{(1 - income_score) * 100:.1f}%{Colors.RESET}) | "
          f"CS: {Colors.BRIGHT_YELLOW}{CONTRIBUTION_SCORE_WEIGHT * (1 - contribution_score):.3f}{Colors.RESET} "
          f"({Colors.BRIGHT_YELLOW}{(1 - contribution_score) * 100:.1f}%{Colors.RESET})")


def process_match_batch(steam_matches: list) -> tuple[list, dict]:
    """
    Основная функция для обработки одного запроса (батча) матчей из Steam API.

    Выполняет полный цикл обработки матчей:
    1. Первичную фильтрацию (базовые критерии)
    2. Вторичную фильтрацию (сложный анализ)
    3. Создание итоговых записей для БД
    4. Сбор статистики обработки

    Args:
        steam_matches (list): Список матчей от Steam API

    Returns:
        tuple[list, dict]: Кортеж из обработанных матчей и объединенной статистики
            - list: Готовые для сохранения в БД записи матчей
            - dict: Объединенная статистика всех этапов обработки
    """
    # Первичная фильтрация
    primary_filtered_matches, primary_stats = primary_match_filters(steam_matches)

    # Вторичная фильтрация
    secondary_filtered_matches, secondary_stats = secondary_match_filters(primary_filtered_matches)

    # Создание итоговых словарей для сохранения в БД
    processed_matches = [create_database_match_record(match) for match in secondary_filtered_matches]

    # Объединение статистики (только если включено детальное логирование)
    if ENABLE_DETAILED_STATISTICS:
        combined_statistics = {
            'api_input': primary_stats['input_count'],
            'primary_filtered': primary_stats['output_count'],
            'secondary_filtered': secondary_stats['output_count'],
            'final_processed': len(processed_matches),
            'primary_exclusions': {
                'burst_time': primary_stats['excluded_burst_time'],
                'game_mode': primary_stats['excluded_game_mode'],
                'duration': primary_stats['excluded_duration'],
                'missing_keys': primary_stats['excluded_missing_keys'],
                'player_count': primary_stats['excluded_player_count'],
                'player_keys': primary_stats['excluded_player_keys']
            },
            'secondary_exclusions': {
                'leavers': secondary_stats['excluded_leavers'],
                'ruiners': secondary_stats['excluded_ruiners'],
                'role_assignment': secondary_stats['excluded_role_assignment']
            }
        }
    else:
        # Минимальная статистика для основного вывода
        combined_statistics = {
            'api_input': len(steam_matches),
            'primary_filtered': len(primary_filtered_matches),
            'secondary_filtered': len(secondary_filtered_matches),
            'final_processed': len(processed_matches)
        }

    return processed_matches, combined_statistics


def print_batch_processing_statistics(stats: dict):
    """
    Выводит детальную статистику обработки матчей (только если включено).

    Показывает количество исключений по каждому критерию фильтрации,
    общую успешность обработки и причины отклонения матчей.

    Args:
        stats (dict): Словарь со статистикой обработки, включающий:
            - Общие счетчики (input, filtered, processed)
            - Детальные исключения по категориям (если включены)
    """
    if not ENABLE_DETAILED_STATISTICS:
        return

    # Показываем исключения только если они есть
    primary_exclusions = {key: value for key, value in stats['primary_exclusions'].items() if value > 0}
    secondary_exclusions = {key: value for key, value in stats['secondary_exclusions'].items() if value > 0}

    # Если есть исключения - показываем их
    if primary_exclusions or secondary_exclusions:

        total_excluded = (stats['api_input'] - stats['final_processed'])
        success_rate = (stats['final_processed'] / stats['api_input'] * 100) if stats['api_input'] > 0 else 0

        print_info_line("Успешность", f"{success_rate:.1f}%", "📊", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
        print_info_line("Исключено", f"{total_excluded}", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)

        if primary_exclusions:
            print_status_message("Первичные исключения:", "info", "🚫")
            exclusion_emojis = {
                'burst_time': '⏰',
                'game_mode': '🎮',
                'duration': '⏱️',
                'missing_keys': '🔑',
                'player_count': '👥',
                'player_keys': '🔐'
            }
            for reason, count in primary_exclusions.items():
                emoji = exclusion_emojis.get(reason, '❌')
                print_info_line(reason, f"{count}", emoji, Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)

        if secondary_exclusions:
            print_status_message("Вторичные исключения:", "info", "⚠️")
            exclusion_emojis = {
                'leavers': '🚪',
                'ruiners': '💀',
                'role_assignment': '🎭'
            }
            for reason, count in secondary_exclusions.items():
                emoji = exclusion_emojis.get(reason, '⚠️')
                print_info_line(reason, f"{count}", emoji, Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
        print()
    else:
        # Если исключений нет - краткое сообщение
        print_status_message("Все матчи прошли фильтрацию успешно", "success")


def save_matches_to_database(session, batch_matches: list, total_saved_matches: int,
                             chunk_start_time: float, program_start_time: float) -> int:
    """
    Сохраняет матчи в базу данных с выводом статистики производительности.

    Функция обрабатывает список матчей, создает соответствующие записи
    в БД (Match + MatchPlayer), выполняет коммит и выводит детальную
    статистику производительности операции.

    Args:
        session: Сессия SQLAlchemy для работы с БД
        batch_matches (list): Список матчей для сохранения
        total_saved_matches (int): Общее количество уже сохраненных матчей
        chunk_start_time (float): Время начала накопления текущего чанка
        program_start_time (float): Время запуска программы

    Returns:
        int: Количество успешно добавленных матчей

    Raises:
        IntegrityError: При нарушении ограничений БД (дубликаты и т.д.)
        Exception: При других ошибках БД

    Note:
        Функция автоматически пропускает матчи, которые уже существуют в БД
    """
    if not batch_matches:
        return 0

    save_operation_start_time = time.time()
    matches_successfully_added = 0

    for match_data in batch_matches:
        # Проверяем, существует ли матч в БД (проверка по match_id)
        if session.query(Match.match_id).filter_by(match_id=match_data["match_id"]).first():
            print_status_message(f"Матч {match_data['match_id']} уже существует в БД, пропускаем", "warning", "⚠️")
            continue

        # Создаем объект Match для сохранения
        match_record = Match(
            match_id=match_data["match_id"],
            match_seq_num=match_data["match_seq_num"],
            radiant_win=match_data["radiant_win"],
            duration=match_data["duration"],
            start_time=match_data["start_time"],
            tower_status_radiant=match_data["tower_status_radiant"],
            tower_status_dire=match_data["tower_status_dire"],
            barracks_status_radiant=match_data["barracks_status_radiant"],
            barracks_status_dire=match_data["barracks_status_dire"],
            lobby_type=match_data["lobby_type"],
            game_mode=match_data["game_mode"],
            radiant_score=match_data["radiant_score"],
            dire_score=match_data["dire_score"],
        )

        # Объединяем игроков обеих команд для удобства обработки
        # Добавляем team_number для различения команд
        all_players_with_teams = [
                                     (player_data, 0) for player_data in match_data['radiant_players']
                                 ] + [
                                     (player_data, 1) for player_data in match_data['dire_players']
                                 ]

        # Создаем записи игроков
        for player_data, team_number in all_players_with_teams:
            kills = player_data.get("kills", 0)
            deaths = player_data.get("deaths", 0)
            assists = player_data.get("assists", 0)

            player_record = MatchPlayer(
                account_id=player_data["account_id"],
                team_number=team_number,
                hero_id=player_data["hero_id"],
                hero_variant=player_data.get("hero_variant", 0),
                role=player_data["role"],

                # Предметы
                item_0=player_data.get("item_0", 0),
                item_1=player_data.get("item_1", 0),
                item_2=player_data.get("item_2", 0),
                item_3=player_data.get("item_3", 0),
                item_4=player_data.get("item_4", 0),
                item_5=player_data.get("item_5", 0),
                backpack_0=player_data.get("backpack_0", 0),
                backpack_1=player_data.get("backpack_1", 0),
                backpack_2=player_data.get("backpack_2", 0),
                item_neutral=player_data.get("item_neutral", 0),
                item_neutral2=player_data.get("item_neutral2", 0),

                # Боевая статистика
                kills=kills,
                deaths=deaths,
                assists=assists,
                kda=(kills + assists) / max(1, deaths),  # Вычисляем KDA

                # Экономическая статистика
                last_hits=player_data.get("last_hits", 0),
                denies=player_data.get("denies", 0),
                gold_per_min=player_data.get("gold_per_min", 0),
                xp_per_min=player_data.get("xp_per_min", 0),
                level=player_data.get("level", 0),
                net_worth=player_data.get("net_worth", 0),

                # Дополнительные предметы
                aghanims_scepter=player_data.get("aghanims_scepter", 0),
                aghanims_shard=player_data.get("aghanims_shard", 0),
                moonshard=player_data.get("moonshard", 0),
            )
            match_record.players.append(player_record)

        session.add(match_record)
        matches_successfully_added += 1

    try:
        session.commit()

        # Вычисляем статистику сохранения
        current_time = time.time()
        chunk_accumulation_time = current_time - chunk_start_time
        database_save_time = current_time - save_operation_start_time
        total_program_time = current_time - program_start_time
        new_total_saved = total_saved_matches + matches_successfully_added

        # Скорость чанка основана на времени накопления, а не времени сохранения
        chunk_processing_speed = (
                matches_successfully_added / chunk_accumulation_time * 60) if chunk_accumulation_time > 0 else 0
        overall_processing_speed = (new_total_saved / total_program_time * 60) if total_program_time > 0 else 0

        # Вывод статистики сохранения
        print_subsection_header("Сохранение в базу данных", "💾", Colors.BRIGHT_GREEN)
        print_info_line("Сохранено матчей", f"{matches_successfully_added}", "✅", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_GREEN)
        print_info_line("Всего в БД", f"{new_total_saved:,}", "📊", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)
        print_info_line("Время работы", f"{total_program_time / 60:.1f}м", "⏰", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_MAGENTA)
        print_info_line("Время сохранения", f"{database_save_time:.2f}с", "💾", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_ORANGE)
        print_info_line("Время накопления чанка", f"{chunk_accumulation_time:.1f}с", "📦", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_PURPLE)
        print_info_line("Скорость (чанк)", f"{chunk_processing_speed:.1f} матчей/мин", "🚀", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_LIME)
        print_info_line("Скорость (общая)", f"{overall_processing_speed:.1f} матчей/мин", "🌟", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_GOLD)
        print()

        return matches_successfully_added

    except IntegrityError as e:
        print_status_message(f"Ошибка целостности данных при сохранении: {e}", "error")
        session.rollback()
        return 0
    except Exception as e:
        print_status_message(f"Ошибка при сохранении в БД: {e}", "error")
        session.rollback()
        return 0


def print_final_collection_statistics(total_saved_matches: int, total_processed_matches: int,
                                      steam_api_calls: int, program_start_time: float,
                                      database_state: dict, accumulated_statistics: dict = None):
    """
    Выводит итоговую статистику работы программы.

    Показывает полную сводку по результатам выполнения программы,
    включая количество обработанных матчей, время выполнения,
    производительность и детальную статистику исключений.

    Args:
        total_saved_matches (int): Количество сохраненных матчей
        total_processed_matches (int): Количество обработанных матчей
        steam_api_calls (int): Количество вызовов Steam API
        program_start_time (float): Время запуска программы
        database_state (dict): Состояние базы данных
        accumulated_statistics (dict, optional): Накопленная статистика (если включена)
    """
    if database_state['scenario'] == 'complete':
        return  # Не выводим статистику если сбор не производился

    total_execution_time = time.time() - program_start_time
    overall_processing_speed = (total_saved_matches / total_execution_time * 60) if total_execution_time > 0 else 0

    print_section_header("ИТОГОВАЯ СТАТИСТИКА", "🏆", color=Colors.BRIGHT_GOLD)

    # Показываем контекст выполнения
    if database_state['scenario'] == 'empty':
        print_info_line("Целевое количество", f"{TARGET_MATCHES_COUNT:,} матчей", "🎯", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_CYAN)
    else:  # continue
        print_info_line("Целевое количество", f"{TARGET_MATCHES_COUNT:,} матчей", "🎯", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_CYAN)
        print_info_line("Было в базе", f"{database_state['existing_matches_count']:,} матчей", "📊", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_BLUE)
        print_info_line("Нужно было собрать", f"{database_state['remaining_needed']:,} матчей", "📈",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)

    # Основные результаты
    print_info_line("Всего обработано", f"{total_processed_matches:,} матчей", "📊", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_CYAN)
    print_info_line("Финально сохранено", f"{total_saved_matches:,} матчей", "✅", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_GREEN)

    final_total_in_database = database_state['existing_matches_count'] + total_saved_matches
    print_info_line("Итого в базе", f"{final_total_in_database:,} матчей", "🏆", Colors.BRIGHT_WHITE, Colors.BRIGHT_GOLD)

    # Производительность
    print_info_line("Вызовов Steam API", f"{steam_api_calls}", "📡", Colors.BRIGHT_WHITE, Colors.BRIGHT_PURPLE)
    print_info_line("Общее время работы", f"{total_execution_time / 60:.1f} минут", "⏰", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_MAGENTA)
    print_info_line("Средняя скорость", f"{overall_processing_speed:.1f} матчей/мин", "🚀", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_LIME)

    # Прогресс-бар завершения
    print_progress_bar(final_total_in_database, TARGET_MATCHES_COUNT, "Общий прогресс:", 30, Colors.BRIGHT_GREEN,
                       Colors.DIM)

    # Детальная статистика исключений (если включена)
    if ENABLE_DETAILED_STATISTICS and accumulated_statistics:
        print_subsection_header("Детальная статистика исключений", "📋", Colors.BRIGHT_TEAL)

        if accumulated_statistics['api_input'] > 0:
            success_rate = (accumulated_statistics['final_processed'] / accumulated_statistics['api_input'] * 100)
            print_info_line("Общая успешность обработки", f"{success_rate:.1f}%", "📈", Colors.BRIGHT_WHITE,
                            Colors.BRIGHT_GREEN)

        print_status_message("Первичные исключения:", "info", "🚫")
        for reason, count in accumulated_statistics['primary_exclusions'].items():
            if count > 0:
                print_info_line(reason, f"{count:,}", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)

        print_status_message("Вторичные исключения:", "info", "⚠️")
        for reason, count in accumulated_statistics['secondary_exclusions'].items():
            if count > 0:
                print_info_line(reason, f"{count:,}", "⚠️", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)


def main():
    """
    Главная функция программы для сбора и анализа данных матчей Dota 2.

    Выполняет полный цикл работы программы:
    1. Инициализация кэша героев
    2. Анализ состояния базы данных
    3. Определение стратегии сбора данных
    4. Основной цикл сбора данных от Steam API
    5. Обработка и фильтрация матчей
    6. Сохранение в базу данных чанками
    7. Вывод итоговой статистики

    Функция обрабатывает различные сценарии:
    - Пустая БД (начинаем с начала)
    - Частично заполненная БД (продолжаем сбор)
    - Полностью заполненная БД (завершаем работу)

    Raises:
        Exception: При критических ошибках инициализации или работы с БД
    """
    # Инициализация кэша героев
    if not initialize_heroes_cache():
        print_status_message("КРИТИЧЕСКАЯ ОШИБКА: Не удалось загрузить данные героев!", "error", "💥")
        print_status_message("Убедитесь, что база данных героев существует и заполнена.", "warning", "⚠️")
        return

    dataset_session = DatasetSessionLocal()

    try:
        # Анализируем текущее состояние базы данных
        database_state = analyze_database_state(dataset_session)

        # Если база данных уже заполнена - завершаем работу
        if database_state['scenario'] == 'complete':
            print_section_header("БАЗА ДАННЫХ ЗАПОЛНЕНА", "🎉", color=Colors.BRIGHT_GREEN)
            print_info_line("Матчей в базе", f"{database_state['existing_matches_count']:,}", "📊",
                            Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
            print_info_line("Цель достигнута", f"{TARGET_MATCHES_COUNT:,} матчей", "🎯",
                            Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)
            return

        # Выводим заголовок конфигурации
        print_collection_configuration_header(database_state)

        # Определяем параметры сбора в зависимости от состояния БД
        if database_state['scenario'] == 'empty':
            start_sequence_number = STARTING_MATCH_SEQUENCE_NUMBER
            target_matches_to_collect = TARGET_MATCHES_COUNT
        else:  # continue
            start_sequence_number = database_state['next_seq_num']
            target_matches_to_collect = database_state['remaining_needed']

        # Инициализация переменных для отслеживания прогресса
        program_start_time = time.time()
        chunk_accumulation_start_time = time.time()
        total_saved_matches = 0
        total_processed_matches = 0
        current_batch_for_saving = []
        api_calls_counter = 0

        # Накопительная статистика (только если включено детальное логирование)
        if ENABLE_DETAILED_STATISTICS:
            accumulated_statistics = {
                'api_input': 0,
                'primary_filtered': 0,
                'secondary_filtered': 0,
                'final_processed': 0,
                'primary_exclusions': {
                    'burst_time': 0, 'game_mode': 0, 'duration': 0,
                    'missing_keys': 0, 'player_count': 0, 'player_keys': 0
                },
                'secondary_exclusions': {
                    'leavers': 0, 'ruiners': 0, 'role_assignment': 0
                }
            }

        # === ОСНОВНОЙ ЦИКЛ СБОРА ДАННЫХ ===
        while total_processed_matches < target_matches_to_collect:
            iteration_start_time = time.time()
            api_calls_counter += 1

            # Получение данных от Steam API
            raw_steam_matches = fetch_matches_from_steam_api(start_sequence_number)

            if not raw_steam_matches:
                print_status_message("Steam API | Матчи не найдены, завершение работы.", "warning")
                break

            # Обновляем стартовый номер для следующего запроса
            start_sequence_number = raw_steam_matches[-1]["match_seq_num"] + 1

            # Обработка батча матчей
            processed_matches, batch_statistics = process_match_batch(raw_steam_matches)

            # Обновляем накопительную статистику (только если включено)
            if ENABLE_DETAILED_STATISTICS:
                accumulated_statistics['api_input'] += batch_statistics['api_input']
                accumulated_statistics['primary_filtered'] += batch_statistics['primary_filtered']
                accumulated_statistics['secondary_filtered'] += batch_statistics['secondary_filtered']
                accumulated_statistics['final_processed'] += batch_statistics['final_processed']

                for category in ['primary_exclusions', 'secondary_exclusions']:
                    for reason, count in batch_statistics[category].items():
                        accumulated_statistics[category][reason] += count

            # Определяем количество матчей в текущем батче
            current_batch_size = len(processed_matches)

            # Проверяем, не превысим ли лимит
            if total_processed_matches + current_batch_size > target_matches_to_collect:
                # Обрезаем батч до нужного количества
                remaining_needed = target_matches_to_collect - total_processed_matches
                processed_matches = processed_matches[:remaining_needed]
                current_batch_size = remaining_needed

                if ENABLE_DETAILED_STATISTICS:
                    print_status_message(f"Достигнут лимит! Обрезаем батч до {current_batch_size} матчей", "warning")

            # Добавляем обработанные матчи в батч для сохранения
            current_batch_for_saving.extend(processed_matches)
            total_processed_matches += current_batch_size

            # Применяем задержку между запросами только если продолжаем
            if total_processed_matches < target_matches_to_collect:
                time.sleep(API_REQUEST_DELAY_SECONDS)

            # Время итерации (включая всё: API, обработку, задержки)
            iteration_duration = time.time() - iteration_start_time

            # Основной вывод прогресса
            total_including_existing = total_processed_matches + database_state['existing_matches_count']

            print(f"{Colors.BRIGHT_BLUE}📡 API #{api_calls_counter}{Colors.RESET} | "
                  f"Получено: {Colors.BRIGHT_YELLOW}{batch_statistics['api_input']}{Colors.RESET} → "
                  f"1-й фильтр: {Colors.BRIGHT_ORANGE}{batch_statistics['primary_filtered']}{Colors.RESET} → "
                  f"2-й фильтр: {Colors.BRIGHT_PURPLE}{batch_statistics['secondary_filtered']}{Colors.RESET} → "
                  f"Обработано: {Colors.BRIGHT_CYAN}{current_batch_size}{Colors.RESET}")

            print(f"  💾 Всего обработано: {Colors.BRIGHT_CYAN}{total_including_existing:,}{Colors.RESET} | "
                  f"Уже в БД: {Colors.BRIGHT_GREEN}{total_saved_matches + database_state['existing_matches_count']:,}{Colors.RESET} | "
                  f"⏱️ {Colors.BRIGHT_MAGENTA}{iteration_duration:.1f}с{Colors.RESET}")

            # Прогресс-бар
            print_progress_bar(total_including_existing, TARGET_MATCHES_COUNT, "Прогресс сбора:", 30)

            # Детальная статистика (если включена)
            print_batch_processing_statistics(batch_statistics)

            # Проверяем лимит ПЕРЕД сохранением
            if total_processed_matches >= target_matches_to_collect:
                print()
                print_status_message(f"Достигнут целевой лимит: {total_processed_matches} матчей!", "success", "🎯")
                break

            # Сохранение в БД по достижении размера чанка (только если не достигли лимита)
            if len(current_batch_for_saving) >= DATABASE_SAVE_CHUNK_SIZE:
                saved_count = save_matches_to_database(dataset_session, current_batch_for_saving, total_saved_matches,
                                                       chunk_accumulation_start_time, program_start_time)
                total_saved_matches += saved_count
                current_batch_for_saving = []  # Очищаем буфер
                chunk_accumulation_start_time = time.time()  # Сбрасываем время начала нового чанка

        # Сохранение остатка матчей
        if current_batch_for_saving:
            print_status_message(f"Сохраняем остаток батча: {len(current_batch_for_saving)} матчей", "info", "💾")
            saved_count = save_matches_to_database(dataset_session, current_batch_for_saving, total_saved_matches,
                                                   chunk_accumulation_start_time, program_start_time)
            total_saved_matches += saved_count

        # Выводим итоговую статистику
        accumulated_stats_for_output = accumulated_statistics if ENABLE_DETAILED_STATISTICS else None
        print_final_collection_statistics(total_saved_matches, total_processed_matches, api_calls_counter,
                                          program_start_time, database_state, accumulated_stats_for_output)

    finally:
        dataset_session.close()


if __name__ == "__main__":
    main()
