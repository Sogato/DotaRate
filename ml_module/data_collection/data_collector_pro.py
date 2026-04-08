"""
Модуль сбора и анализа данных профессиональных матчей Dota 2 из OpenDota API.

Модуль обеспечивает полный цикл работы с данными PRO матчей:
двухэтапный сбор через OpenDota API, многоступенчатую фильтрацию, анализ игроков,
обнаружение аномалий (мёртвых матчей) и сохранение в базу данных.

Сбор из API происходит от новых к старым.

Основные этапы обработки:
1. Анализ состояния БД и определение сценария сбора
2. Этап 1: сбор списка матчей
    - Получение списка профессиональных матчей через OpenDota /proMatches endpoint
    - Фильтрация по базовым критериям (время, тип серии, команды)
    - Определение момента остановки на основе сценария
    - Результат: список ID матчей для детальной обработки
3. Этап 2: детальная обработка каждого матча
    - Получение полных данных через OpenDota /matches/{match_id} endpoint
    - Расширенная фильтрация (аномалии в данных, мёртвые матчи)
    - Извлечение и группировка данных игроков по командам
    - Назначение ролей игрокам (core/support)
4. Формирование записей для БД с полной информацией о матче и игроках
5. Единовременное сохранение всех обработанных матчей

Сценарии сбора данных:
1. INITIAL COLLECTION (начальный сбор)
   Условие: БД пуста (existing_count == 0)
   Задача: первичное заполнение базы данных
   Стратегия Этап 1: собираем от новейших матчей до BURST_TIME или TARGET_MATCHES_COUNT
   Стратегия Этап 2: обрабатываем все собранные матчи
   Проверка дубликатов: не требуется (БД пуста)
   Пример: первый запуск программы

2. UPDATE COLLECTION (обновление коллекции)
   Условие: БД достигла BURST_TIME (min_start_time <= BURST_TIME + tolerance)
   Задача: добавление новых матчей поверх существующей коллекции
   Стратегия Этап 1: собираем от новейших до первого существующего match_id
   Стратегия Этап 2: обрабатываем все собранные матчи
   Проверка дубликатов: на Этапе 1 (останавливаемся при обнаружении)
   Пример: регулярное обновление ранее заполненной БД

3. BACKFILL COLLECTION (заполнение пробелов)
   Условие: БД НЕ достигла BURST_TIME (min_start_time > BURST_TIME + tolerance)
   Задача: заполнение всех пробелов в данных до достижения временной границы
   Стратегия Этап 1: собираем ВСЕ матчи до BURST_TIME
   Стратегия Этап 2: обрабатываем все собранные матчи, проверяем дубликаты перед запросом деталей
   Проверка дубликатов: на Этапе 2 ПЕРЕД запросом API (экономия запросов)
   Пример: восстановление после сбоя, изменение изначального BURST_TIME

4. NO COLLECTION NEEDED (сбор не требуется)
   Условие: целевое количество достигнуто (existing_count >= TARGET_MATCHES_COUNT)
   Задача: нет задачи, завершаем работу
   Действие: выводим статус и завершаем программу
   Пример: база данных уже полностью заполнена

Архитектурные особенности:
- Two-stage collection: раздельный сбор ID матчей и детальных данных
- Smart scenario detection: автоматическое определение стратегии на основе состояния БД
- Role assignment: алгоритм определения ролей игроков на основе support_score
- Dead match detection: фильтрация аномальных матчей (все на 1 lvl, 0 LH)
- No ruiner detection: не требуется для профессиональных матчей
- Single-batch saving: сохранение всех матчей единовременно после полной обработки
- Progressive statistics: информативный вывод с опциональной детальной статистикой
"""

# Стандартные библиотеки
import time
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional

# Сторонние библиотеки
import requests
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import IntegrityError

# Локальные импорты
from data_bases.pro_matches.models import ProMatch, ProMatchPlayer
from config import (
    PRO_DATABASE_URL, OPENDOTA_PRO_MATCHES_URL, OPENDOTA_MATCH_DETAILS_URL, BURST_TIME_TIMESTAMP,
    TEAM_SIZE, RADIANT_INDEX, DIRE_INDEX, PLAYER_SLOT_TEAM_BITMASK, SERIES_TYPES, VALID_LEAVER_STATUSES,
    SUPPORT_ITEM_IDS, HERO_ITEM_EXCEPTIONS, HERO_SUPPORT_SCORE_EXCEPTIONS
)
from utils.hero_cache import HeroCache
from utils.console import (
    Colors,
    print_section_header,
    print_subsection_header,
    print_info_line,
    print_progress_bar,
    print_status_message
)

# === ПОДКЛЮЧЕНИЯ К БАЗАМ ДАННЫХ ===
pro_engine = create_engine(PRO_DATABASE_URL)
ProSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=pro_engine)

# === КЭШ HEROES DATABASE ===
hero_cache = HeroCache()

# === КОНСТАНТЫ ОТЛАДКИ ===
ENABLE_DETAILED_STATISTICS = False          # Детальная статистика обработки каждого батча
ENABLE_ROLE_LOGGING = False                 # Логирование процесса назначения ролей игрокам

# === КОНСТАНТЫ КОНФИГУРАЦИИ ===
TARGET_MATCHES_COUNT = 10_000              # Целевое количество матчей для сбора
BURST_TIME_TOLERANCE = 1 * 24 * 60 * 60     # Допустимое отклонение от BURST_TIME (1 день в секундах)

# === КОНСТАНТЫ API ===
API_REQUEST_DELAY = 0                       # Задержка между запросами в секундах
MAX_RETRIES = 5                             # Количество повторных попыток получения данных
RETRY_DELAY = 30                            # Задержка между повторами (секунды)

# === ИНДИФИКАТОРЫ СЦЕНАРИЕВ СБОРА ===
SCENARIO_INITIAL = 'initial_collection'     # Изначальный сбор в пустую БД
SCENARIO_UPDATE = 'update_collection'       # Обновление: добавление новых матчей
SCENARIO_BACKFILL = 'backfill_collection'   # Заполнение пробелов до BURST_TIME
SCENARIO_COMPLETE = 'no_collection_needed'  # Сбор не требуется

# === ПАРАМЕТРЫ ОПРЕДЕЛЕНИЯ РОЛЕЙ ===
# Веса компонентов для расчета support_score при назначении роли игроку
SUPPORT_ITEMS_WEIGHT = 5                    # Вес количества саппорт предметов
SUPPORT_SCORE_EXCEPTION_WEIGHT = 10         # Бонус для героев из HERO_SUPPORT_SCORE_EXCEPTIONS
NET_WORTH_WEIGHT = 10                       # Вес net worth (инвертированный)
LAST_HITS_WEIGHT = 5                        # Вес last hits (инвертированный)
GPM_WEIGHT = 5                              # Вес GPM (инвертированный)
XPM_WEIGHT = 5                              # Вес XPM (инвертированный)

# === ПАРАМЕТРЫ ФИЛЬТРАЦИИ "МЁРТВЫХ" МАТЧЕЙ ===
# Пороговые значения для исключения матчей с ботами/афк игроками
MAX_LEVEL_1_RATIO = 0.1                     # Максимальная доля игроков на 1 уровне (10%)
MAX_ZERO_LASTHITS_RATIO = 0.1               # Максимальная доля игроков с 0 last_hits (10%)
MIN_TOTAL_LAST_HITS = 30                    # Минимальное суммарное количество last_hits на матч


def analyze_database_state(session: Session) -> Dict[str, Any]:
    """
    Анализирует текущее состояние базы данных и определяет сценарий сбора.

    Определяет один из четырёх сценариев на основе содержимого БД и временных границ:
    1. INITIAL_COLLECTION - БД пуста, начинаем сбор с нуля
    2. UPDATE_COLLECTION - БД достигла BURST_TIME, добавляем новые матчи
    3. BACKFILL_COLLECTION - БД НЕ достигла BURST_TIME, заполняем пробелы до границы
    4. NO_COLLECTION_NEEDED - TARGET_MATCHES_COUNT уже достигнут, сбор не требуется

    Args:
        session (Session): Сессия SQLAlchemy для работы с БД

    Returns:
        Dict[str, Any]: Словарь с информацией о состоянии БД, включающий:
            - scenario (str): Идентификатор определённого сценария
            - existing_count (int): Количество матчей в БД
            - burst_time_reached (bool): Достигнута ли временная граница
            - matches_needed (int): Сколько матчей ещё нужно собрать
            - min_match_id (int | None): Минимальный ID матча в БД
            - max_match_id (int | None): Максимальный ID матча в БД
            - min_start_time (int | None): Время начала самого старого матча
            - max_start_time (int | None): Время начала самого нового матча
            - coverage_days (float): Временное покрытие БД в днях
            - match_density (float): Средняя плотность матчей в день
            - missing_time_days (float): Недостающий период до BURST_TIME (для BACKFILL)
            - estimated_gap_matches (float): Оценка матчей в пробеле (для BACKFILL)
            - days_since_last_match (float): Дней с последнего матча (для UPDATE)
            - estimated_new_matches (float): Оценка новых матчей (для UPDATE)

    Note:
        Функция автоматически выводит детальную информацию о состоянии БД
        и определённом сценарии с цветным форматированием.
    """

    print_section_header("АНАЛИЗ БАЗЫ ДАННЫХ", "🔍", color=Colors.BRIGHT_CYAN)

    existing_count = session.query(func.count(ProMatch.match_id)).scalar() or 0

    # === СЦЕНАРИЙ 1: ПУСТАЯ БД ===
    if existing_count == 0:
        print_subsection_header("Состояние базы данных", "📊", Colors.BRIGHT_YELLOW)
        print_info_line("Матчей в БД", "0", "📊", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)

        print_subsection_header("Определён сценарий сбора", "🎯", Colors.BRIGHT_GREEN)
        print_info_line("Сценарий", "НАЧАЛЬНЫЙ СБОР", "🆕", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
        print_info_line("Описание", "Первичное заполнение пустой базы данных", "📝", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_CYAN)
        print_info_line("Целевое количество", f"{TARGET_MATCHES_COUNT:,} матчей", "🎯", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_GREEN)

        return {
            'scenario': SCENARIO_INITIAL,
            'existing_count': 0,
            'burst_time_reached': False,
            'matches_needed': TARGET_MATCHES_COUNT,
            'min_match_id': None,
            'max_match_id': None,
            'min_start_time': None,
            'max_start_time': None,
            'coverage_days': 0,
            'match_density': 0
        }

    # === ПОЛУЧЕНИЕ СТАТИСТИКИ БД ===
    max_match_id = session.query(func.max(ProMatch.match_id)).scalar()
    min_match_id = session.query(func.min(ProMatch.match_id)).scalar()
    min_start_time = session.query(func.min(ProMatch.start_time)).scalar()
    max_start_time = session.query(func.max(ProMatch.start_time)).scalar()

    # Вычисление аналитических метрик
    coverage_seconds = max_start_time - min_start_time
    coverage_days = coverage_seconds / (24 * 60 * 60)
    coverage_hours = (coverage_seconds % (24 * 60 * 60)) / 3600
    match_density = existing_count / coverage_days if coverage_days > 0 else 0
    matches_needed = TARGET_MATCHES_COUNT - existing_count

    # Вычисление расстояния до BURST_TIME
    time_to_burst_seconds = min_start_time - BURST_TIME_TIMESTAMP
    time_to_burst_days = abs(time_to_burst_seconds) / (24 * 60 * 60)
    time_to_burst_hours = (abs(time_to_burst_seconds) % (24 * 60 * 60)) / 3600

    # Процент заполненности относительно цели
    fill_percentage = (existing_count / TARGET_MATCHES_COUNT * 100) if TARGET_MATCHES_COUNT > 0 else 0

    # Проверка достижения BURST_TIME с учётом допустимого отклонения
    burst_time_reached = min_start_time <= (BURST_TIME_TIMESTAMP + BURST_TIME_TOLERANCE)

    # Форматирование дат для вывода
    min_time_str = datetime.fromtimestamp(min_start_time).strftime('%Y-%m-%d %H:%M:%S')
    max_time_str = datetime.fromtimestamp(max_start_time).strftime('%Y-%m-%d %H:%M:%S')
    burst_time_str = datetime.fromtimestamp(BURST_TIME_TIMESTAMP).strftime('%Y-%m-%d %H:%M:%S')

    # === ВЫВОД СОСТОЯНИЯ БД ===
    print_subsection_header("Состояние базы данных", "📊", Colors.BRIGHT_YELLOW)
    print_info_line("Матчей в БД", f"{existing_count:,}", "📊", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    print_info_line("Заполнено", f"{fill_percentage:.1f}%", "📈", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)
    print_info_line("Диапазон Match ID", f"{min_match_id:,} → {max_match_id:,}", "🔢", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_BLUE)
    print_info_line("Период", f"{min_time_str} → {max_time_str}", "📅", Colors.BRIGHT_WHITE, Colors.BRIGHT_MAGENTA)

    # Временное покрытие
    if coverage_days >= 1:
        print_info_line("Временной охват", f"{coverage_days:.1f} дней ({coverage_hours:.1f}ч)", "🕐",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_GOLD)
    else:
        print_info_line("Временной охват", f"{coverage_hours:.1f} часов", "🕐",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_GOLD)

    print_info_line("Плотность матчей", f"{match_density:.1f} матчей/день", "📊", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_LIME)

    # === ИНФОРМАЦИЯ О ВРЕМЕННОЙ ГРАНИЦЕ ===
    print_subsection_header("Временная граница", "⏰", Colors.BRIGHT_CYAN)
    print_info_line("BURST_TIME", burst_time_str, "🎯", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)

    # Расстояние до/от BURST_TIME
    if time_to_burst_seconds > 0:
        # БД НЕ достигла BURST_TIME (самый старый матч НОВЕЕ границы)
        print_info_line(
            "Расстояние до границы",
            f"{time_to_burst_days:.1f} дней {time_to_burst_hours:.1f} ч.",
            "📏",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_ORANGE
        )
        print_info_line(
            "Статус",
            "НЕ достигнута ✗",
            "🚨",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_RED
        )
    else:
        # БД достигла BURST_TIME (самый старый матч СТАРШЕ границы)
        print_info_line(
            "Превышение границы",
            f"{time_to_burst_days:.1f} дней ({time_to_burst_hours:.1f}ч)",
            "📏",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_GREEN
        )
        print_info_line(
            "Статус",
            "Достигнута ✓",
            "✅",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_GREEN
        )

    # === СЦЕНАРИЙ 2: ЦЕЛЕВОЕ КОЛИЧЕСТВО ДОСТИГНУТО ===
    if existing_count >= TARGET_MATCHES_COUNT:
        print_subsection_header("Определён сценарий сбора", "🎯", Colors.BRIGHT_GREEN)
        print_info_line("Сценарий", "СБОР НЕ ТРЕБУЕТСЯ", "✅", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
        print_info_line("Причина", "Целевое количество матчей достигнуто", "📝", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)

        if existing_count > TARGET_MATCHES_COUNT:
            excess = existing_count - TARGET_MATCHES_COUNT
            print_info_line("Превышение цели", f"+{excess:,} матчей", "➕", Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)
        print()

        return {
            'scenario': SCENARIO_COMPLETE,
            'existing_count': existing_count,
            'burst_time_reached': burst_time_reached,
            'matches_needed': 0,
            'min_match_id': min_match_id,
            'max_match_id': max_match_id,
            'min_start_time': min_start_time,
            'max_start_time': max_start_time,
            'coverage_days': coverage_days,
            'match_density': match_density
        }

    # === СЦЕНАРИЙ 3: BACKFILL - BURST_TIME НЕ ДОСТИГНУТ ===
    if not burst_time_reached:
        # Оценка недостающего временного покрытия
        missing_time_seconds = min_start_time - BURST_TIME_TIMESTAMP
        missing_time_days = missing_time_seconds / (24 * 60 * 60)

        # Оценка количества матчей для заполнения на основе текущей плотности
        estimated_matches_in_gap = match_density * missing_time_days if match_density > 0 else 0

        print_subsection_header("Определён сценарий сбора", "🎯", Colors.BRIGHT_YELLOW)
        print_info_line("Сценарий", "ЗАПОЛНЕНИЕ ПРОБЕЛОВ", "🔄", Colors.BRIGHT_WHITE, Colors.BRIGHT_ORANGE)
        print_info_line("Описание", "Временная граница не достигнута", "📝", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)
        print_info_line("Цель", "Заполнить все пробелы до временной границы", "🎯", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_YELLOW)
        print_info_line("Нужно собрать", f"{matches_needed:,} матчей", "📊", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)

        # Аналитика пробела
        print_info_line("Недостающий период", f"{missing_time_days:.1f} дней", "📅", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_MAGENTA)
        if estimated_matches_in_gap > 0:
            print_info_line("Ожидается матчей", f"~{estimated_matches_in_gap:.0f} матчей", "🔮",
                            Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)

        print_info_line("Примечание", "Дубликаты будут пропущены", "ℹ️", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)
        print()

        return {
            'scenario': SCENARIO_BACKFILL,
            'existing_count': existing_count,
            'burst_time_reached': False,
            'matches_needed': matches_needed,
            'min_match_id': min_match_id,
            'max_match_id': max_match_id,
            'min_start_time': min_start_time,
            'max_start_time': max_start_time,
            'coverage_days': coverage_days,
            'match_density': match_density,
            'missing_time_days': missing_time_days,
            'estimated_gap_matches': estimated_matches_in_gap
        }

    # === СЦЕНАРИЙ 4: UPDATE - BURST_TIME ДОСТИГНУТ ===
    # Вычисляем временной промежуток от последнего матча до текущего момента
    current_time = time.time()
    time_since_last_match = current_time - max_start_time
    days_since_last_match = time_since_last_match / (24 * 60 * 60)
    hours_since_last_match = (time_since_last_match % (24 * 60 * 60)) / 3600

    # Оценка количества новых матчей на основе исторической плотности
    estimated_new_matches = match_density * days_since_last_match if match_density > 0 else 0

    print_subsection_header("Определён сценарий сбора", "🎯", Colors.BRIGHT_GREEN)
    print_info_line("Сценарий", "ОБНОВЛЕНИЕ КОЛЛЕКЦИИ", "➕", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    print_info_line("Описание", "Временная граница достигнута, добавление новых матчей", "📝", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_CYAN)
    print_info_line("Задача", f"Собрать {matches_needed:,} новых матчей", "🎯", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    print_info_line("Последний Match ID", f"{max_match_id:,}", "🔄", Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)

    if days_since_last_match >= 1:
        print_info_line("Время с последнего матча", f"{days_since_last_match:.1f} дней ({hours_since_last_match:.1f}ч)",
                        "⏱️", Colors.BRIGHT_WHITE, Colors.BRIGHT_ORANGE)
    else:
        print_info_line("Время с последнего матча", f"{hours_since_last_match:.1f} часов",
                        "⏱️", Colors.BRIGHT_WHITE, Colors.BRIGHT_ORANGE)

    if estimated_new_matches > 0:
        print_info_line("Ожидается матчей", f"~{estimated_new_matches:.0f} матчей", "🔮",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)
    print()

    return {
        'scenario': SCENARIO_UPDATE,
        'existing_count': existing_count,
        'burst_time_reached': True,
        'matches_needed': matches_needed,
        'min_match_id': min_match_id,
        'max_match_id': max_match_id,
        'min_start_time': min_start_time,
        'max_start_time': max_start_time,
        'coverage_days': coverage_days,
        'match_density': match_density,
        'days_since_last_match': days_since_last_match,
        'estimated_new_matches': estimated_new_matches
    }


def print_collection_configuration(db_state: Dict[str, Any]) -> None:
    """
    Выводит конфигурацию сбора данных для текущего сценария.

    Отображает текущие настройки программы и стратегию выполнения,
    адаптированную под определённый сценарий сбора. Включает общие параметры
    (целевое количество, временная граница) и специфичную стратегию сценария.

    Args:
        db_state (Dict[str, Any]): Словарь с информацией о состоянии БД и сценарии,
            должен содержать ключ 'scenario' с одним из значений:
            SCENARIO_INITIAL, SCENARIO_UPDATE, SCENARIO_BACKFILL, SCENARIO_COMPLETE

    Note:
        Функция не выводит ничего если тип сценария SCENARIO_COMPLETE,
        так как в этом случае сбор не требуется.
    """

    # Не выводим заголовок если база данных уже заполнена
    if db_state['scenario'] == SCENARIO_COMPLETE:
        return

    scenario = db_state['scenario']

    print_section_header("КОНФИГУРАЦИЯ СБОРА", "⚙️", color=Colors.BRIGHT_MAGENTA)

    # ОБЩИЕ ПАРАМЕТРЫ
    print_subsection_header("Параметры сбора", "📋", Colors.BRIGHT_GREEN)
    print_info_line("Целевое количество", f"{TARGET_MATCHES_COUNT:,} матчей", "🎯")
    burst_time_str = datetime.fromtimestamp(BURST_TIME_TIMESTAMP).strftime('%Y-%m-%d %H:%M:%S')
    print_info_line("Временная граница", burst_time_str, "⏰", Colors.BRIGHT_WHITE, Colors.BRIGHT_GOLD)

    tolerance_days = BURST_TIME_TOLERANCE / (24 * 60 * 60)
    print_info_line("Допустимое отклонение от временной границы", f"{tolerance_days:.0f} дней", "📏",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_MAGENTA)
    print_info_line("Задержка между запросами", f"{API_REQUEST_DELAY}с", "⌛", Colors.BRIGHT_WHITE, Colors.BRIGHT_PINK)

    # === СТРАТЕГИЯ СБОРА === (зависит от сценария)
    print_subsection_header("Стратегия выполнения", "🎯", Colors.BRIGHT_ORANGE)

    if scenario == SCENARIO_INITIAL:
        print_info_line("Этап 1", "Сбор всех матчей от новейших к BURST_TIME", "📋", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_CYAN)
        print_info_line("Остановка Этап 1", "При достижении BURST_TIME ИЛИ целевого количества", "🛑",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
        print_info_line("Этап 2", "Обработка всех собранных матчей", "🔍", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)

    elif scenario == SCENARIO_UPDATE:
        print_info_line("Этап 1", "Сбор новых матчей сверху коллекции", "📋", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)
        print_info_line("Остановка Этап 1", "При обнаружении существующего матча ИЛИ целевого количества", "🛑",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
        print_info_line("Этап 2", "Обработка всех собранных матчей", "🔍", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)

    elif scenario == SCENARIO_BACKFILL:
        print_info_line("Этап 1", "Сбор всех матчей до BURST_TIME", "📋", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)
        print_info_line("Остановка Этап 1", "При достижении BURST_TIME (дубликаты игнорируются)", "🛑",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
        print_info_line("Этап 2", "Обработка с проверкой дубликатов ПЕРЕД запросом деталей", "🔍", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_ORANGE)
        print_info_line("Особенность", "Остановка при достижении целевого количества НА ЭТАПЕ 2", "💡",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)

    # === НАСТРОЙКИ ОТЛАДКИ ===
    print_subsection_header("Настройки вывода", "📝", Colors.BRIGHT_MAGENTA)
    print_info_line("Детальная статистика", "ВКЛ" if ENABLE_DETAILED_STATISTICS else "ВЫКЛ", "📈",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN if ENABLE_DETAILED_STATISTICS else Colors.BRIGHT_RED)
    print_info_line("Логирование ролей", "ВКЛ" if ENABLE_ROLE_LOGGING else "ВЫКЛ", "👥",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN if ENABLE_ROLE_LOGGING else Colors.BRIGHT_RED)
    print()


def print_stage1_batch_processing_statistics(stats: Dict) -> None:
    """
    Выводит детальную статистику обработки батча (только если включено).

    Показывает исключения только если есть что показать (есть хотя бы
    одно исключение). Выводит общую успешность и детальную разбивку
    по причинам исключения.

    Args:
        stats (Dict): Словарь со статистикой, включающий
            input_count, output_count и счётчики excluded_*

    Note:
        Вывод производится только если ENABLE_DETAILED_STATISTICS=True
        и есть хотя бы одно исключение для отображения.
    """

    if not ENABLE_DETAILED_STATISTICS:
        return

    # Показываем только если есть исключения
    primary_exclusions = {key: value for key, value in stats.items()
                          if key.startswith('excluded_') and value > 0}

    if primary_exclusions:
        input_count = stats.get('input_count', 0)
        output_count = stats.get('output_count', 0)
        total_excluded = input_count - output_count
        success_rate = (output_count / input_count * 100) if input_count > 0 else 0

        print_info_line("Успешность", f"{success_rate:.1f}%", "📊", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
        print_info_line("Исключено", f"{total_excluded}", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)

        print_status_message("Первичные исключения:", "info", "🚫")
        exclusion_emojis = {
            'excluded_burst_time': '⏰',
            'excluded_series_type': '🏆',
            'excluded_missing_keys': '🔑',
            'excluded_team_name': '🏢',
            'excluded_dead_match': '💀'
        }
        exclusion_names = {
            'excluded_burst_time': 'время burst',
            'excluded_series_type': 'тип серии',
            'excluded_missing_keys': 'отсутствующие ключи',
            'excluded_team_name': 'название команды',
            'excluded_dead_match': 'мёртвые матчи'
        }
        for reason, count in primary_exclusions.items():
            emoji = exclusion_emojis.get(reason, '❌')
            reason_name = exclusion_names.get(reason, reason)
            print_info_line(reason_name, f"{count:,}", emoji, Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
        print()


def print_stage1_batch_progress(batch_num: int, batch_size: int, filtered_size: int, added_size: int,
                                iteration_time: float, total_collected: int, target: int,
                                batch_stats: Optional[Dict], scenario: str) -> None:
    """
    Выводит прогресс обработки батча на Этапе 1.

    Отображает:
    - Номер батча
    - Количество отфильторованных и добавленных матчей
    - Время обработки батча
    - Общий прогресс сбора (для не-backfill сценариев)
    - Детальную статистику исключений (если включена)

    Args:
        batch_num (int): Номер текущего батча (для идентификации)
        batch_size (int): Количество матчей полученных от API
        filtered_size (int): Количество прошедших первичную фильтрацию
        added_size (int): Количество добавленных в коллекцию (учитывая лимиты)
        iteration_time (float): Время обработки батча в секундах
        total_collected (int): Общее количество собранных матчей
        target (int): Целевое количество матчей (для прогресс-бара)
        batch_stats (Optional[Dict]): Статистика первичной фильтрации
        scenario (str): Текущий сценарий сбора

    Note:
        Для сценария BACKFILL прогресс-бар не отображается, так как целью
        является достижение BURST_TIME, а не определённого количества матчей.
    """

    print(f"{Colors.BRIGHT_BLUE}📡 Batch #{batch_num}{Colors.RESET} | "
          f"Получено: {Colors.BRIGHT_YELLOW}{batch_size}{Colors.RESET} → "
          f"Прошло фильтры: {Colors.BRIGHT_ORANGE}{filtered_size}{Colors.RESET} → "
          f"Добавлено: {Colors.BRIGHT_CYAN}{added_size}{Colors.RESET} | "
          f"⏱️ {Colors.BRIGHT_MAGENTA}{iteration_time:.2f}с{Colors.RESET}")

    # Для backfill не показываем прогресс-бар
    if scenario == SCENARIO_BACKFILL:
        print(f"  💾 Всего собрано: {Colors.BRIGHT_GREEN}{total_collected:,}{Colors.RESET} | "
              f"Цель: {Colors.BRIGHT_CYAN}Достигнуть временной границы{Colors.RESET}")
    else:
        print(f"  💾 Всего собрано: {Colors.BRIGHT_GREEN}{total_collected:,}{Colors.RESET} | "
              f"Цель: {Colors.BRIGHT_CYAN}{target:,}{Colors.RESET}")
        print_progress_bar(total_collected, target, "Прогресс сбора:", 30)

    # Детальная статистика первичной фильтрации (если включена)
    if batch_stats:
        print_stage1_batch_processing_statistics(batch_stats)


def apply_primary_filters(pro_matches: List[Dict]) -> Tuple[List[Dict], Optional[Dict]]:
    """
    Применяет первичные фильтры к списку PRO матчей (базовые критерии валидации).

    Первичная фильтрация включает проверку:
    - Времени начала матча (должен быть после BURST_TIME_TIMESTAMP)
    - Типа серии матча
    - Наличия всех обязательных полей в данных матча
    - Наличия названий обеих команд (Radiant и Dire)

    Args:
        pro_matches (List[Dict]): Список матчей от OpenDota PRO matches API

    Returns:
        Tuple[List[Dict], Optional[Dict]]: Кортеж из отфильтрованных матчей и статистики
            - List[Dict]: Матчи, прошедшие первичную фильтрацию
            - Optional[Dict]: Статистика исключений (только если ENABLE_DETAILED_STATISTICS)
                Содержит счётчики: input_count, excluded_burst_time, excluded_series_type,
                excluded_missing_keys, excluded_team_name, output_count

    Note:
        Матчи, не прошедшие фильтрацию, просто пропускаются без сохранения.
    """

    filtered_matches = []

    # Инициализируем статистику (если включено детальное логирование)
    if ENABLE_DETAILED_STATISTICS:
        filter_stats = {
            'input_count': len(pro_matches),
            'excluded_burst_time': 0,
            'excluded_series_type': 0,
            'excluded_missing_keys': 0,
            'excluded_team_name': 0,
            'output_count': 0
        }
    else:
        filter_stats = None

    for match in pro_matches:
        # Фильтр по времени начала
        if match.get("start_time", 0) <= BURST_TIME_TIMESTAMP:
            if ENABLE_DETAILED_STATISTICS:
                filter_stats['excluded_burst_time'] += 1
            continue

        # Фильтр по типу серии
        if match.get("series_type", 0) not in SERIES_TYPES:
            if ENABLE_DETAILED_STATISTICS:
                filter_stats['excluded_series_type'] += 1
            continue

        # Проверка наличия всех обязательных полей
        required_fields = {'match_id', 'duration', 'start_time', 'radiant_win',
                           'radiant_name', 'dire_name', 'radiant_score', 'dire_score'}
        if not required_fields.issubset(match.keys()):
            if ENABLE_DETAILED_STATISTICS:
                filter_stats['excluded_missing_keys'] += 1
            continue

        # Проверка наличия названий команд (не должны быть пустыми)
        if not match.get("radiant_name") or not match.get("dire_name"):
            if ENABLE_DETAILED_STATISTICS:
                filter_stats['excluded_team_name'] += 1
            continue

        # Матч прошёл все проверки
        filtered_matches.append(match)

    if ENABLE_DETAILED_STATISTICS:
        filter_stats['output_count'] = len(filtered_matches)

    return filtered_matches, filter_stats


def fetch_pro_matches_list(less_than_match_id: Optional[int] = None) -> Optional[List[Dict]]:
    """
    Получает список профессиональных матчей из OpenDota API.

    Выполняет GET запрос к OpenDota /proMatches endpoint с опциональной пагинацией.
    Автоматически обрабатывает ошибки сети и таймауты, повторяя запросы при необходимости.

    Args:
        less_than_match_id (Optional[int]): Получить матчи с ID меньше этого значения.
            Используется для пагинации - движение от новых матчей к старым.
            Если None, получает самые новые матчи.

    Returns:
        Optional[List[Dict]]: Список словарей с данными профессиональных матчей.
            Возвращает None если не удалось получить данные после всех попыток.
            Возвращает пустой список если матчей больше нет.

    Note:
        Функция делает до MAX_RETRIES попыток с задержкой RETRY_DELAY между ними.
    """

    retry_count = 0

    while retry_count < MAX_RETRIES:
        try:
            params = {}
            if less_than_match_id:
                params['less_than_match_id'] = less_than_match_id

            response = requests.get(OPENDOTA_PRO_MATCHES_URL, params=params, timeout=10)
            response.raise_for_status()

            return response.json()

        except requests.exceptions.Timeout:
            retry_count += 1
            if retry_count < MAX_RETRIES:
                print_status_message(
                    f"OpenDota API | Таймаут запроса, попытка {retry_count}/{MAX_RETRIES}, "
                    f"повтор через {RETRY_DELAY} сек...",
                    "warning",
                    "⏱️"
                )
                time.sleep(RETRY_DELAY)
            else:
                print_status_message(
                    f"OpenDota API | Таймаут после {MAX_RETRIES} попыток",
                    "error",
                    "❌"
                )

        except requests.exceptions.RequestException as e:
            retry_count += 1
            if retry_count < MAX_RETRIES:
                print_status_message(
                    f"OpenDota API | Ошибка запроса: {e}, попытка {retry_count}/{MAX_RETRIES}, "
                    f"повтор через {RETRY_DELAY} сек...",
                    "error",
                    "❌"
                )
                time.sleep(RETRY_DELAY)
            else:
                print_status_message(
                    f"OpenDota API | Не удалось получить данные после {MAX_RETRIES} попыток: {e}",
                    "error",
                    "❌"
                )

    return None


def collect_pro_matches(target_count: int, db_state: Dict,
                        existing_match_ids: set, accumulated_stats: Optional[Dict]) -> Tuple[List[Dict], int, int]:
    """
    ЭТАП 1: Собирает список PRO матчей с первичной фильтрацией.

    Выполняет последовательные запросы к OpenDota PRO matches API,
    применяя фильтрацию и управляя остановкой в зависимости от сценария.

    Логика остановки зависит от сценария:
    - INITIAL: остановка при достижении BURST_TIME ИЛИ target_count
    - UPDATE: остановка при обнаружении существующего match_id ИЛИ target_count
    - BACKFILL: остановка при достижении BURST_TIME (существующие match_ids игнорируются)

    Функция использует пагинацию через параметр less_than_match_id для движения
    от новых матчей к старым.

    Args:
        target_count (int): Целевое количество матчей для сбора
        db_state (Dict): Состояние БД и сценарий сбора
        existing_match_ids (set): Множество ID существующих в БД матчей
        accumulated_stats (Optional[Dict]): Накопительная статистика для обновления

    Returns:
        Tuple[List[Dict], int, int]: Кортеж из:
            - List[Dict]: Список собранных матчей после первичной фильтрации
            - int: Количество собранных матчей
            - int: Количество вызовов API

    Note:
        Функция автоматически обновляет accumulated_stats при каждом батче,
        если ENABLE_DETAILED_STATISTICS=True.
    """

    print_section_header("ЭТАП 1: СБОР СПИСКА МАТЧЕЙ", "📋", color=Colors.BRIGHT_BLUE)

    scenario = db_state['scenario']

    pro_matches_list = []
    total_collected = 0
    api_calls = 0
    less_than_match_id = None

    # === ОСНОВНОЙ ЦИКЛ СБОРА ===
    while True:
        # Проверка лимита для не-backfill сценариев
        if scenario != SCENARIO_BACKFILL and total_collected >= target_count:
            break

        iteration_start = time.time()
        api_calls += 1

        # Получаем батч матчей
        pro_batch = fetch_pro_matches_list(less_than_match_id)

        if not pro_batch:
            print_status_message("OpenDota API | Больше нет доступных матчей", "warning", "🚫")
            break

        # Применяем первичную фильтрацию
        filtered_batch, batch_stats = apply_primary_filters(pro_batch)

        # Обновляем накопительную статистику (если включена)
        if ENABLE_DETAILED_STATISTICS and accumulated_stats and batch_stats:
            for key in accumulated_stats['primary_exclusions']:
                accumulated_stats['primary_exclusions'][key] += batch_stats.get(key, 0)

        # Проверяем достижение BURST_TIME (по статистике исключений)
        reached_burst_time = (ENABLE_DETAILED_STATISTICS and batch_stats and
                              batch_stats.get('excluded_burst_time', 0) > 0)

        # Проверяем наличие существующих матчей (для INITIAL и UPDATE)
        found_existing = False
        if scenario in [SCENARIO_INITIAL, SCENARIO_UPDATE] and not reached_burst_time:
            for i, match in enumerate(filtered_batch):
                if match['match_id'] in existing_match_ids:
                    print_status_message(f"Найден существующий матч {match['match_id']}, останавливаем сбор", "info",
                                         "🛑")
                    found_existing = True
                    filtered_batch = filtered_batch[:i]  # Обрезаем до существующего
                    break

        # Проверяем лимит перед добавлением (для backfill лимита нет на Этапе 1)
        matches_to_add = filtered_batch
        if scenario != SCENARIO_BACKFILL and total_collected + len(matches_to_add) > target_count:
            remaining_needed = target_count - total_collected
            matches_to_add = matches_to_add[:remaining_needed]

            if ENABLE_DETAILED_STATISTICS:
                print_status_message(f"Лимит достигнут! Обрезаем батч до {len(matches_to_add)} матчей", "warning", "✂️")

        # Добавляем матчи в коллекцию
        pro_matches_list.extend(matches_to_add)
        total_collected += len(matches_to_add)

        # Обновляем параметр пагинации для следующего запроса
        if pro_batch:
            less_than_match_id = pro_batch[-1]["match_id"]

        # Выводим прогресс батча
        iteration_time = time.time() - iteration_start
        print_stage1_batch_progress(api_calls, len(pro_batch), len(filtered_batch), len(matches_to_add),
                                    iteration_time, total_collected, target_count, batch_stats, scenario)

        # === УСЛОВИЯ ЗАВЕРШЕНИЯ ===
        if scenario != SCENARIO_BACKFILL and total_collected >= target_count:
            print()
            print_status_message(f"Остановка: достигнут лимит {total_collected} матчей", "info", "🎯")
            print()
            break
        elif reached_burst_time:
            print()
            print_status_message("Остановка: достигнута временная граница", "info", "⏰")
            print()
            break
        elif found_existing:
            print()
            print_status_message("Остановка: обнаружены существующие матчи", "info", "🛑")
            print()
            break
        elif len(matches_to_add) == 0:
            print()
            print_status_message("Новых матчей не найдено", "warning", "🚫")
            print()
            break

        time.sleep(API_REQUEST_DELAY)

    return pro_matches_list, total_collected, api_calls


def print_stage2_match_progress(index: int, total: int, match_id: int, status: str,
                                detail_time: float, successful: int, failed: int, skipped: int) -> None:
    """
    Выводит прогресс обработки отдельного матча на Этапе 2.

    Отображает статус обработки каждого матча с цветовым кодированием:
    - success: матч успешно обработан и прошёл все фильтры
    - filtered: матч не прошёл вторичную фильтрацию
    - failed: не удалось получить детали матча от API
    - skipped: матч пропущен (существует в БД)

    Args:
        index (int): Индекс текущего матча в списке (начиная с 0)
        total (int): Общее количество матчей для обработки
        match_id (int): ID обрабатываемого матча
        status (str): Статус обработки ('success', 'filtered', 'failed', 'skipped')
        detail_time (float): Время обработки матча в секундах
        successful (int): Общее количество успешно обработанных матчей
        failed (int): Общее количество неудачных обработок (failed + filtered)
        skipped (int): Общее количество пропущенных матчей
    """

    status_icons = {
        'success': ('🔍', Colors.BRIGHT_GREEN),
        'filtered': ('⚠️', Colors.BRIGHT_YELLOW),
        'failed': ('❌', Colors.BRIGHT_RED),
        'skipped': ('⏭️', Colors.BRIGHT_CYAN)
    }

    icon, color = status_icons.get(status, ('❓', Colors.BRIGHT_WHITE))

    print(f"{color}{icon} Матч [{index + 1}/{total}]{Colors.RESET} | "
          f"Match ID: {Colors.BRIGHT_YELLOW}{match_id}{Colors.RESET} | "
          f"Принято: {Colors.BRIGHT_GREEN}{successful}{Colors.RESET} | "
          f"Пропущено: {Colors.BRIGHT_BLUE}{skipped}{Colors.RESET} | "
          f"Сбоев: {Colors.BRIGHT_RED}{failed}{Colors.RESET} | "
          f"⏱️ {Colors.BRIGHT_MAGENTA}{detail_time:.2f}с{Colors.RESET}")

    print_progress_bar(index + 1, total, "Прогресс:", 25)


def log_team_role_assignment(team_players: List[Dict]) -> None:
    """
    Выводит распределение ролей в команде для отладки.

    Выводит детальную информацию о каждом игроке команды,
    включая ID, героя, роль, support_score и ключевые метрики,
    а также информацию об использовании бонуса для потенциальных сапортов.

    Args:
        team_players (List[Dict]): Список игроков команды с назначенными ролями

    Note:
        Вывод производится только если ENABLE_ROLE_LOGGING=True.
        Функция используется исключительно для отладки алгоритма назначения ролей.
    """

    for player in team_players:
        hero_name = hero_cache.get_hero_name(player['hero_id'])
        role_color = Colors.BRIGHT_RED if player["role"] == "support" else Colors.BRIGHT_BLUE

        # Проверяем, использовался ли бонус SUPPORT_SCORE_EXCEPTION_WEIGHT для support_score
        hero_id = player.get("hero_id")
        hero_variant = player.get("hero_variant", 0)
        hero_key = (hero_id, hero_variant)

        support_score_info = f"{player['support_score']:.2f}"
        if (hero_key in HERO_SUPPORT_SCORE_EXCEPTIONS or
                hero_id in HERO_SUPPORT_SCORE_EXCEPTIONS):
            support_score_info += f" {Colors.BRIGHT_ORANGE}(ИСКЛЮЧЕНИЕ){Colors.RESET}"

        print(
            f"  {Colors.BRIGHT_WHITE}Player ID:{Colors.RESET} {Colors.BRIGHT_YELLOW}{player['account_id']}{Colors.RESET} | "
            f"{Colors.BRIGHT_WHITE}Герой:{Colors.RESET} {Colors.BRIGHT_CYAN}{hero_name}{Colors.RESET}({Colors.BRIGHT_YELLOW}{player['hero_id']}{Colors.RESET}) | "
            f"{Colors.BRIGHT_WHITE}Роль:{Colors.RESET} {role_color}{player['role'].upper()}{Colors.RESET} | "
            f"{Colors.BRIGHT_WHITE}Support Score:{Colors.RESET} {Colors.BRIGHT_GREEN}{support_score_info}{Colors.RESET} | "
            f"{Colors.BRIGHT_WHITE}Net: {player['net_worth']}{Colors.RESET} | {Colors.BRIGHT_WHITE}LH: {player['last_hits']}{Colors.RESET} | {Colors.BRIGHT_WHITE}GPM: {player['gold_per_min']}{Colors.RESET} | {Colors.BRIGHT_WHITE}XPM: {player['xp_per_min']}{Colors.RESET}")


def calculate_player_support_score(player: Dict, team_stats: Dict) -> float:
    """
    Вычисляет support_score для игрока на основе его предметов и игровых показателей.

    Support_score представляет собой взвешенную сумму факторов, указывающих
    на то, что игрок играет роль поддержки. Чем выше support_score, тем больше
    игрок похож на саппорта.

    Формула учитывает:
    - Количество поддерживающих предметов (ward observer, sentry, smoke и т.д.)
    - Инвертированные экономические показатели (низкие = саппорт)
    - Исключения для специфичных героев
    - Дополнительный бонус для героев, которые "классически" могут быть только саппортами

    Args:
        player (Dict): Словарь с данными игрока
        team_stats (Dict): Словарь с максимальными значениями команды для нормализации,
            должен содержать: max_net_worth, max_last_hits, max_gpm, max_xpm

    Returns:
        float: Численный support_score (чем выше, тем больше похож на саппорта)

    Note:
        Учитываются исключения саппорт-предметов для конкретных героев из HERO_ITEM_EXCEPTIONS
        и дополнительный бонус для героев из HERO_SUPPORT_SCORE_EXCEPTIONS
    """

    hero_id = player.get("hero_id")
    hero_variant = player.get("hero_variant", 0)
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
    normalized_net_worth = player["net_worth"] / max(team_stats['max_net_worth'], 1)
    normalized_last_hits = player["last_hits"] / max(team_stats['max_last_hits'], 1)
    normalized_gpm = player["gold_per_min"] / max(team_stats['max_gpm'], 1)
    normalized_xpm = player["xp_per_min"] / max(team_stats['max_xpm'], 1)

    # Базовый расчёт support_score (инвертируем экономические показатели)
    # Чем меньше экономика - тем больше похож на саппорта
    support_score = (
            support_items_count * SUPPORT_ITEMS_WEIGHT +
            (1 - normalized_net_worth) * NET_WORTH_WEIGHT +
            (1 - normalized_last_hits) * LAST_HITS_WEIGHT +
            (1 - normalized_gpm) * GPM_WEIGHT +
            (1 - normalized_xpm) * XPM_WEIGHT
    )

    # Добавляем бонус для героев из списка HERO_SUPPORT_SCORE_EXCEPTIONS
    hero_key = (hero_id, hero_variant)
    if hero_key in HERO_SUPPORT_SCORE_EXCEPTIONS or hero_id in HERO_SUPPORT_SCORE_EXCEPTIONS:
        support_score += SUPPORT_SCORE_EXCEPTION_WEIGHT

    return support_score


def assign_player_roles(team_players: List[Dict]) -> List[Dict]:
    """
    Назначает роли игрокам команды на основе их игровых характеристик.

    Использует алгоритм на основе support_score, который учитывает:
    - Количество поддерживающих предметов (wards, smoke и т.д.)
    - Экономические показатели (net worth, GPM, XPM, last hits)
    - Нормализацию относительно максимумов команды

    Алгоритм назначения:
    1. Вычислить support_score для каждого игрока
    2. Отсортировать по убыванию support_score
    3. Первые 2 игрока с высшим score → support
    4. Остальные 3 → core

    Args:
        team_players (List[Dict]): Список игроков команды (должно быть 5)

    Returns:
        List[Dict]: Игроки с назначенными ролями ('core' или 'support')

    Raises:
        ValueError: Если количество игроков не равно 5 или
            роли назначены некорректно (не 3 кора и 2 саппорта)

    Note:
        Функция изменяет исходный список, добавляя ключи "support_score" и "role"
        к каждому словарю игрока.
    """

    if len(team_players) != TEAM_SIZE:
        raise ValueError("Команда должна состоять из 5 игроков.")

    # Вычисляем максимумы команды один раз для всех игроков (для нормализации)
    # Используем max(..., 1) для предотвращения деления на ноль
    raw_max_net_worth = max(player["net_worth"] for player in team_players)
    raw_max_last_hits = max(player["last_hits"] for player in team_players)
    raw_max_gpm = max(player["gold_per_min"] for player in team_players)
    raw_max_xpm = max(player["xp_per_min"] for player in team_players)

    team_statistics = {
        'max_net_worth': max(raw_max_net_worth, 1),
        'max_last_hits': max(raw_max_last_hits, 1),
        'max_gpm': max(raw_max_gpm, 1),
        'max_xpm': max(raw_max_xpm, 1)
    }

    # Вычисляем support_score для каждого игрока
    for player in team_players:
        support_score = calculate_player_support_score(player, team_statistics)
        player["support_score"] = support_score
        player["role"] = "undefined"

    # Сортируем по support_score (убывающий порядок) и назначаем роли
    team_players.sort(key=lambda player: player["support_score"], reverse=True)

    # Назначаем роли: первые 2 = support, остальные 3 = core
    for index, player in enumerate(team_players):
        player["role"] = "support" if index < 2 else "core"

    # Проверка корректности назначения ролей
    core_count = sum(1 for player in team_players if player["role"] == "core")
    support_count = sum(1 for player in team_players if player["role"] == "support")

    if core_count != 3 or support_count != 2:
        raise ValueError(f"Ошибка распределения ролей: {core_count} коров и {support_count} саппортов.")

    return team_players


def extract_and_group_players_data(players: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """
    Извлекает данные игроков и группирует их по командам.

    Обрабатывает сырые данные от OpenDota API, извлекая только
    необходимые поля и распределяя игроков по командам (Radiant/Dire).

    Распределение команд определяется полем player_slot:
    - player_slot от 0 до 127 = Radiant
    - player_slot от 128 до 255 = Dire

    Args:
        players (List[Dict]): Список игроков из OpenDota match details API

    Returns:
        Tuple[List[Dict], List[Dict]]: Кортеж (radiant_players, dire_players)
            Каждый список содержит словари с обработанными данными игроков:
            - Базовая информация: account_id, hero_id, hero_variant
            - Экономические показатели: net_worth, last_hits, denies, gpm, xpm
            - Предметы: item_0-5, backpack_0-2, item_neutral, item_neutral2
            - Боевая статистика: kills, deaths, assists
            - Дополнительно: level, aghanims_scepter, aghanims_shard, moonshard
    """

    radiant_players = []
    dire_players = []

    for player in players:
        # Извлекаем все необходимые характеристики игрока
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

        # Распределяем по командам на основе player_slot
        player_slot = player.get("player_slot", 0)
        is_dire = (player_slot & PLAYER_SLOT_TEAM_BITMASK) != 0

        if is_dire:
            dire_players.append(player_data)
        else:
            radiant_players.append(player_data)

    return radiant_players, dire_players


def is_dead_match(players: List[Dict], match_id: int) -> bool:
    """
    Определяет, является ли матч "мёртвым".

    Мёртвый матч - это матч, где все или большинство игроков вышли в самом начале,
    не начав играть. По какой-то причине некоторые из таких матчей не классифицируются Valve как abandoned.
    Такие матчи необходимо исключать из датасета, так как они не содержат
    полезной информации для анализа.

    Критерии "мёртвого" матча:
    - Большинство (≥ MAX_LEVEL_1_RATIO) игроков остались на 1 уровне
    - Большинство (≥ MAX_ZERO_LASTHITS_RATIO) игроков с 0 last_hits
    - Суммарно всех игроков < MIN_TOTAL_LAST_HITS last_hits

    Args:
        players (List[Dict]): Список игроков матча с их статистикой
        match_id (str): ID матча для логирования в случае обнаружения

    Returns:
        bool: True если матч мёртвый (нужно исключить), False если нормальный

    Note:
        При обнаружении мёртвого матча выводится детальная информация
        о причинах, если включён ENABLE_DETAILED_STATISTICS.
    """

    if not players:
        return True

    total_players = len(players)
    level_1_players = 0
    zero_last_hits_players = 0
    total_last_hits = 0

    # Собираем статистику по всем игрокам
    for player in players:
        level = player.get("level", 1)
        last_hits = player.get("last_hits", 0)

        if level <= 1:
            level_1_players += 1

        if last_hits == 0:
            zero_last_hits_players += 1

        total_last_hits += last_hits

    # Вычисляем соотношения
    level_1_ratio = level_1_players / total_players
    zero_lasthits_ratio = zero_last_hits_players / total_players

    # Проверяем критерии "мёртвого" матча
    is_dead = False
    dead_reasons = []

    if level_1_ratio >= MAX_LEVEL_1_RATIO:
        is_dead = True
        dead_reasons.append(f"{level_1_players}/{total_players} игроков на 1 уровне")

    if zero_lasthits_ratio >= MAX_ZERO_LASTHITS_RATIO:
        is_dead = True
        dead_reasons.append(f"{zero_last_hits_players}/{total_players} игроков с 0 last_hits")

    if total_last_hits < MIN_TOTAL_LAST_HITS:
        is_dead = True
        dead_reasons.append(f"всего {total_last_hits} last_hits на матч")

    # Логируем обнаружение мёртвого матча (если включено)
    if is_dead and ENABLE_DETAILED_STATISTICS:
        print()
        print_status_message(f"МЁРТВЫЙ МАТЧ ОБНАРУЖЕН | ID: {match_id}", "warning", "💀")
        print_info_line("Причины исключения", f"{'; '.join(dead_reasons)}", "📋", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
        print_info_line("Диагноз", "Все игроки покинули матч в начале игры", "🚪", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_YELLOW)
        print()

    return is_dead


def apply_secondary_filters(match_details: Dict) -> Tuple[Optional[Dict], Optional[Dict]]:
    """
    Применяет вторичные фильтры к матчу.

    Вторичная фильтрация включает:
    - Проверку количества игроков
    - Проверку ливеров (leaver_status)
    - Детекцию мёртвых матчей
    - Извлечение и группировку данных игроков по командам
    - Назначение ролей игрокам (core/support)
    - Логирование ролей (если включено)

    Args:
        match_details (Dict): Детальные данные матча от OpenDota match details API

    Returns:
        Tuple[Optional[Dict], Optional[Dict]]: Кортеж (обработанный матч или None, статистика)
            - Optional[Dict]: Матч с добавленными полями processed_radiant_players
                и processed_dire_players, или None если матч не прошёл фильтрацию
            - Optional[Dict]: Статистика исключений (только если ENABLE_DETAILED_STATISTICS)
                Содержит счётчики: excluded_leavers, excluded_dead_match,
                excluded_role_assignment, excluded_player_count, excluded_none_details
    """

    # Статистика только если включена детальная отладка
    if ENABLE_DETAILED_STATISTICS:
        exclusion_stats = {
            'excluded_leavers': 0,
            'excluded_dead_match': 0,
            'excluded_role_assignment': 0,
            'excluded_player_count': 0,
            'excluded_none_details': 0
        }
    else:
        exclusion_stats = None

    # Пропускаем матчи, которые не удалось получить
    if match_details is None:
        if ENABLE_DETAILED_STATISTICS:
            exclusion_stats['excluded_none_details'] = 1
        return None, exclusion_stats

    players = match_details.get("players", [])

    # Проверка соотношения команд (используем битовую маску)
    radiant_players = [p for p in players if not (p.get("player_slot", 0) & PLAYER_SLOT_TEAM_BITMASK)]
    dire_players = [p for p in players if p.get("player_slot", 0) & PLAYER_SLOT_TEAM_BITMASK]

    if len(radiant_players) != TEAM_SIZE or len(dire_players) != TEAM_SIZE:
        if ENABLE_DETAILED_STATISTICS:
            exclusion_stats['excluded_player_count'] = 1
        return None, exclusion_stats

    # Фильтр по leaver_status
    if any(player.get("leaver_status", 0) not in VALID_LEAVER_STATUSES for player in players):
        if ENABLE_DETAILED_STATISTICS:
            exclusion_stats['excluded_leavers'] = 1
        return None, exclusion_stats

    # Фильтрация мёртвых матчей
    if is_dead_match(players, match_details["match_id"]):
        if ENABLE_DETAILED_STATISTICS:
            exclusion_stats['excluded_dead_match'] = 1
        return None, exclusion_stats

    # Извлекаем и группируем игроков по командам
    processed_radiant_players, processed_dire_players = extract_and_group_players_data(players)

    # Назначаем роли
    try:
        processed_radiant_players = assign_player_roles(processed_radiant_players)
        processed_dire_players = assign_player_roles(processed_dire_players)
    except ValueError:
        if ENABLE_DETAILED_STATISTICS:
            exclusion_stats['excluded_role_assignment'] = 1
        return None, exclusion_stats

    # Логируем роли (если включено)
    if ENABLE_ROLE_LOGGING:
        print_subsection_header(f"DEBUG | Роли (Match ID: {match_details['match_id']})", "👥", Colors.BRIGHT_ORANGE)
        print(f"🌞 {Colors.BRIGHT_GREEN}Radiant: {Colors.RESET}")
        log_team_role_assignment(processed_radiant_players)
        print(f"🌑 {Colors.BRIGHT_RED}Dire: {Colors.RESET}")
        log_team_role_assignment(processed_dire_players)
        print()

    # Сохраняем обработанные данные в матч
    match_details['processed_radiant_players'] = processed_radiant_players
    match_details['processed_dire_players'] = processed_dire_players

    return match_details, exclusion_stats


def fetch_match_details(match_id: int) -> Tuple[Optional[Dict], bool]:
    """
    Получает детальные данные конкретного матча из OpenDota API.

    Выполняет GET запрос к OpenDota /api/matches endpoint для получения полной
    информации о матче, включая данные всех игроков, предметы, статистику и т.д.
    При неудаче автоматически повторяет запрос с задержкой.

    Args:
        match_id (int): Уникальный идентификатор матча для запроса деталей

    Returns:
        Tuple[Optional[Dict], bool]: Кортеж (данные матча, флаг rate limit):
            - (Dict, False)  — успешный ответ API. Dict включает:
                - Основную информацию (match_id, duration, winner и т.д.)
                - Список игроков с полной статистикой
                - Информацию о постройках
                - Временные метки событий
            - (None, True)   — сервер заблокировал запросы, дальнейшие попытки бессмысленны
            - (None, False)  — другая ошибка (таймаут, сетевая и т.д.)

    Note:
        Функция делает до MAX_RETRIES попыток получения данных с задержкой RETRY_DELAY между ними.
        При обнаружении 429 флаг rate_limited выставляется немедленно и сохраняется
        до исчерпания всех попыток, после чего сигнализирует вызывающему коду о необходимости
        остановки дальнейших запросов.
    """

    retry_count = 0
    rate_limited = False

    while retry_count < MAX_RETRIES:
        try:
            url = f"{OPENDOTA_MATCH_DETAILS_URL}/{match_id}"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json(), False

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                rate_limited = True
                retry_count += 1
                if retry_count < MAX_RETRIES:
                    print_status_message(
                        f"OpenDota API | Слишком много запросов, сервер просит подождать. "
                        f"Попытка {retry_count}/{MAX_RETRIES}, повтор через {RETRY_DELAY} сек...",
                        "warning",
                        "⏳"
                    )
                    time.sleep(RETRY_DELAY)
                else:
                    print_status_message(
                        f"OpenDota API | Сервер продолжает отклонять запросы после {MAX_RETRIES} попыток "
                        f"для матча {match_id}. Останавливаем сбор и сохраняем собранные данные.",
                        "error",
                        "❌"
                    )
            else:
                retry_count += 1
                if retry_count < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                else:
                    print_status_message(
                        f"OpenDota API | HTTP ошибка для матча {match_id} после {MAX_RETRIES} попыток: {e}",
                        "error",
                        "❌"
                    )

        except requests.exceptions.Timeout:
            retry_count += 1
            if retry_count < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                print_status_message(
                    f"OpenDota API | Таймаут для матча {match_id} после {MAX_RETRIES} попыток",
                    "error",
                    "⏱️"
                )

        except requests.exceptions.RequestException as e:
            retry_count += 1
            if retry_count < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                print_status_message(
                    f"OpenDota API | Не удалось получить матч {match_id} после {MAX_RETRIES} попыток: {e}",
                    "error",
                    "❌"
                )

    return None, rate_limited


def collect_and_process_match_details(pro_matches_list: List[Dict], db_state: Dict,
                                      existing_match_ids: set,
                                      accumulated_stats: Optional[Dict]) -> Tuple[List[Dict], int, int, Dict]:
    """
    ЭТАП 2: Получает детальные данные о матчах и применяет вторичную фильтрацию.

    Для каждого матча из списка Этапа 1:
    1. Запрашивает детальные данные от OpenDota match details API
    2. Применяет вторичную фильтрацию
    3. Формирует пары (pro_match, match_details) для сохранения

    Логика зависит от сценария:
    - BACKFILL: проверяет существование в БД ПЕРЕД запросом API, останавливается при достижении цели
    - INITIAL/UPDATE: запрашивает детали для всех матчей

    При превышении rate limit API (429) цикл немедленно прерывается,
    а все уже успешно обработанные матчи передаются на сохранение в БД.

    Args:
        pro_matches_list (List[Dict]): Список матчей с Этапа 1 (после первичной фильтрации)
        db_state (Dict): Состояние БД и сценарий сбора
        existing_match_ids (set): Множество ID существующих в БД матчей
        accumulated_stats (Optional[Dict]): Накопительная статистика

    Returns:
        Tuple[List[Dict], int, int, Dict]: Кортеж из:
            - List[Tuple[Dict, Dict]]: Список пар (pro_match, match_details) успешно обработанных матчей
            - int: Количество успешно обработанных матчей
            - int: Количество вызовов API
            - Dict: Дополнительная статистика Этапа 2, включая:
                - skipped (int): пропущенные дубликаты (BACKFILL)
                - rate_limit_stopped (bool): была ли прервана обработка из-за rate limit
    """
    print_section_header("ЭТАП 2: ДЕТАЛЬНАЯ ОБРАБОТКА", "🔍", color=Colors.BRIGHT_GREEN)

    scenario = db_state['scenario']
    existing_count = db_state.get('existing_count', 0)

    processed_matches = []
    successful_count = 0
    failed_count = 0
    filtered_count = 0
    skipped_count = 0
    api_calls = 0

    # === ОБРАБОТКА КАЖДОГО МАТЧА ===
    for index, pro_match in enumerate(pro_matches_list):
        # ДЛЯ BACKFILL: проверяем достижение цели ПЕРЕД обработкой
        if scenario == SCENARIO_BACKFILL:
            current_total = existing_count + successful_count
            if current_total >= TARGET_MATCHES_COUNT:
                print()
                print_status_message(
                    f"🎯 Цель достигнута! В БД будет {current_total:,} матчей",
                    "success",
                    "✅"
                )
                print_status_message(
                    f"Обработано {index} из {len(pro_matches_list)} матчей",
                    "info",
                    "🛑"
                )
                print()
                break

        detail_start = time.time()
        match_id = pro_match["match_id"]

        # ДЛЯ BACKFILL: проверяем существование ПЕРЕД запросом API (экономит API запросы)
        if scenario == SCENARIO_BACKFILL and match_id in existing_match_ids:
            skipped_count += 1
            detail_time = time.time() - detail_start

            print_stage2_match_progress(index, len(pro_matches_list), match_id, 'skipped',
                                        detail_time, successful_count, failed_count + filtered_count,
                                        skipped_count)
            continue

        # Получаем детальные данные матча
        match_details, rate_limited = fetch_match_details(match_id)
        api_calls += 1

        # При rate limit прерываем цикл и сохраняем всё уже обработанное
        if rate_limited:
            print()
            print_status_message(
                f"Сервер временно заблокировал запросы. Остановка на матче {match_id} "
                f"({index} из {len(pro_matches_list)} обработано). "
                f"Сохраняем {successful_count} успешно собранных матчей.",
                "warning",
                "🛑"
            )
            print()
            return processed_matches, successful_count, api_calls, {
                'skipped': skipped_count,
                'rate_limit_stopped': True
            }

        if match_details:
            # Применяем вторичную фильтрацию
            filtered_match, exclusion_stats = apply_secondary_filters(match_details)

            # Обновляем накопительную статистику (если включена)
            if ENABLE_DETAILED_STATISTICS and accumulated_stats and exclusion_stats:
                for key in accumulated_stats['secondary_exclusions']:
                    accumulated_stats['secondary_exclusions'][key] += exclusion_stats.get(key, 0)

            if filtered_match:
                # Матч прошёл все фильтры
                processed_matches.append((pro_match, filtered_match))
                successful_count += 1
                status = 'success'
            else:
                # Матч не прошёл вторичную фильтрацию
                filtered_count += 1
                status = 'filtered'
        else:
            # Не удалось получить детали матча (не rate limit)
            failed_count += 1
            status = 'failed'

        detail_time = time.time() - detail_start

        # Выводим прогресс обработки матча
        print_stage2_match_progress(index, len(pro_matches_list), match_id, status,
                                    detail_time, successful_count, failed_count + filtered_count,
                                    skipped_count)

        time.sleep(API_REQUEST_DELAY)
    print()

    # === ДЕТАЛЬНАЯ СТАТИСТИКА ВТОРИЧНОЙ ФИЛЬТРАЦИИ ===
    if ENABLE_DETAILED_STATISTICS and accumulated_stats:
        secondary_exclusions = {k: v for k, v in accumulated_stats['secondary_exclusions'].items() if v > 0}

        if secondary_exclusions:
            print()
            print_subsection_header("Детальная статистика вторичной фильтрации", "📋", Colors.BRIGHT_TEAL)

            exclusion_emojis = {
                'excluded_leavers': '🚪',
                'excluded_dead_match': '💀',
                'excluded_role_assignment': '🎭',
                'excluded_player_count': '👥',
                'excluded_none_details': '❓'
            }
            exclusion_names = {
                'excluded_leavers': 'ливеры',
                'excluded_dead_match': 'мёртвые матчи',
                'excluded_role_assignment': 'назначение ролей',
                'excluded_player_count': 'количество игроков',
                'excluded_none_details': 'отсутствующие детали'
            }

            for reason, count in secondary_exclusions.items():
                emoji = exclusion_emojis.get(reason, '⚠️')
                reason_name = exclusion_names.get(reason, reason)
                print_info_line(reason_name, f"{count:,}", emoji, Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
            print()

    return processed_matches, successful_count, api_calls, {
        'skipped': skipped_count,
        'rate_limit_stopped': False
    }


def create_database_match_record(pro_match_data: Dict, match_details: Dict) -> Dict:
    """
    Создаёт итоговый словарь с данными матча для сохранения в БД.

    Объединяет данные из двух источников:
    - pro_match_data: информация о командах, турнире, серии
    - match_details: детальная информация о матче и игроках

    Args:
        pro_match_data (Dict): Первичные данные из OpenDota PRO matches API
        match_details (Dict): Детальные данные из OpenDota match details API

    Returns:
        Dict: Словарь с данными матча для БД, включающий:
            - Основную информацию (match_id, duration, winner и т.д.)
            - Информацию о командах (ID, названия)
            - Данные турнира и серии
            - Статус построек (башни, бараки)
            - Обработанных игроков обеих команд с назначенными ролями
    """

    return {
        # Основная информация о матче
        "match_id": match_details["match_id"],
        "match_seq_num": match_details.get("match_seq_num", 0),
        "radiant_win": match_details["radiant_win"],
        "duration": match_details["duration"],
        "start_time": match_details["start_time"],

        # Команды (из PRO API)
        "radiant_team_id": pro_match_data.get("radiant_team_id"),
        "radiant_name": pro_match_data.get("radiant_name"),
        "dire_team_id": pro_match_data.get("dire_team_id"),
        "dire_name": pro_match_data.get("dire_name"),

        # Турнир
        "leagueid": pro_match_data.get("leagueid"),
        "league_name": pro_match_data.get("league_name"),

        # Серия
        "series_id": pro_match_data.get("series_id"),
        "series_type": pro_match_data.get("series_type"),

        # Статус построек
        "tower_status_radiant": match_details.get("tower_status_radiant", 0),
        "tower_status_dire": match_details.get("tower_status_dire", 0),
        "barracks_status_radiant": match_details.get("barracks_status_radiant", 0),
        "barracks_status_dire": match_details.get("barracks_status_dire", 0),

        # Дополнительная информация
        "lobby_type": match_details.get("lobby_type", 0),
        "game_mode": match_details.get("game_mode", 0),
        "radiant_score": match_details.get("radiant_score", 0),
        "dire_score": match_details.get("dire_score", 0),

        # Обработанные игроки с назначенными ролями
        "radiant_players": match_details['processed_radiant_players'],
        "dire_players": match_details['processed_dire_players'],
    }


def save_matches_to_database(session: Session, processed_matches: List[Dict]) -> int:
    """
    Сохраняет обработанные матчи в базу данных.

    Выполняет единовременное сохранение всех обработанных матчей с созданием
    соответствующих записей Match + MatchPlayer.
    Пропускает матчи, которые уже существуют в БД.

    Args:
        session (Session): Сессия SQLAlchemy для работы с БД
        processed_matches (List[Dict]): Список обработанных матчей для сохранения

    Returns:
        int: Количество успешно добавленных матчей

    Raises:
        IntegrityError: При нарушении ограничений БД (дубликаты и т.д.)
        Exception: При других ошибках БД
    """

    if not processed_matches:
        return 0

    matches_added = 0

    try:
        for match_data in processed_matches:
            # Проверяем существование матча в БД (проверка по match_id)
            if session.query(ProMatch.match_id).filter_by(match_id=match_data["match_id"]).first():
                print_status_message(f"Матч {match_data['match_id']} уже существует в БД, пропускаем", "warning", "⚠️")
                continue

            # Создаём объект ProMatch для сохранения
            match_record = ProMatch(
                match_id=match_data["match_id"],
                match_seq_num=match_data["match_seq_num"],
                radiant_win=match_data["radiant_win"],
                duration=match_data["duration"],
                start_time=match_data["start_time"],

                radiant_team_id=match_data["radiant_team_id"],
                radiant_name=match_data["radiant_name"],
                dire_team_id=match_data["dire_team_id"],
                dire_name=match_data["dire_name"],

                leagueid=match_data["leagueid"],
                league_name=match_data["league_name"],

                series_id=match_data["series_id"],
                series_type=match_data["series_type"],

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
            # Добавляем team_number для различия команд
            all_players_with_teams = [
                (player_data, RADIANT_INDEX) for player_data in match_data['radiant_players']
            ] + [
                (player_data, DIRE_INDEX) for player_data in match_data['dire_players']
            ]

            # Создаём записи игроков
            for player_data, team_number in all_players_with_teams:
                kills = player_data.get("kills", 0)
                deaths = player_data.get("deaths", 0)
                assists = player_data.get("assists", 0)

                player_record = ProMatchPlayer(
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
                    kda=(kills + assists) / max(1, deaths),

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
            matches_added += 1

        session.commit()
        return matches_added

    except IntegrityError as e:
        print_status_message(f"Ошибка целостности данных при сохранении: {e}", "error", "❌")
        session.rollback()
        return 0
    except Exception as e:
        print_status_message(f"Ошибка при сохранении в БД: {e}", "error", "❌")
        session.rollback()
        return 0



def print_final_collection_statistics(total_saved: int, total_processed: int,
                                      total_found: int, total_api_calls: int,
                                      program_start_time: float, db_state: Dict,
                                      accumulated_stats: Optional[Dict] = None,
                                      stage2_stats: Optional[Dict] = None) -> None:
    """
    Выводит итоговую статистику работы программы.

    Показывает полную сводку по результатам выполнения программы,
    включая количество обработанных матчей, время работы,
    производительность и детальную статистику исключений.

    Args:
        total_saved (int): Количество сохранённых в БД матчей
        total_processed (int): Количество успешно обработанных матчей
        total_found (int): Количество найденных матчей на Этапе 1
        total_api_calls (int): Общее количество вызовов API
        program_start_time (float): Время запуска программы (timestamp)
        db_state (Dict): Состояние БД и сценарий
        accumulated_stats (Optional[Dict]): Накопительная статистика исключений
        stage2_stats (Optional[Dict]): Статистика Этапа 2 (пропущенные дубликаты,
            флаг досрочной остановки из-за блокировки сервера)

    Note:
        Функция не выводит ничего если сценарий SCENARIO_COMPLETE,
        так как в этом случае сбор не производился.
    """
    # Не выводим статистику если сбор не производился
    if db_state['scenario'] == SCENARIO_COMPLETE:
        return

    total_execution_time = time.time() - program_start_time
    processing_speed = (total_saved / total_execution_time * 60) if total_execution_time > 0 else 0

    print_section_header("ИТОГОВАЯ СТАТИСТИКА", "🏆", color=Colors.BRIGHT_GOLD)

    scenario = db_state['scenario']

    # === КОНТЕКСТ ВЫПОЛНЕНИЯ ===
    print_subsection_header("Контекст сбора", "📊", Colors.BRIGHT_CYAN)
    print_info_line("Целевое количество", f"{TARGET_MATCHES_COUNT:,} матчей", "🎯", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_CYAN)

    if scenario in [SCENARIO_UPDATE, SCENARIO_BACKFILL]:
        print_info_line("Было в БД", f"{db_state['existing_count']:,} матчей", "📊",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)

        if scenario == SCENARIO_BACKFILL and stage2_stats:
            print_info_line("Пропущено дубликатов", f"{stage2_stats.get('skipped', 0):,}", "⏭️",
                            Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)

        if scenario == SCENARIO_UPDATE:
            print_info_line("Требовалось собрать", f"{db_state.get('matches_needed', 0):,}", "📈",
                            Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)

    # === РЕЗУЛЬТАТЫ ОБРАБОТКИ ===
    print_subsection_header("Результаты обработки", "📈", Colors.BRIGHT_GREEN)
    print_info_line("Найдено в API", f"{total_found:,}", "🔍", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)
    print_info_line("Детально обработано", f"{total_processed:,}", "🔬", Colors.BRIGHT_WHITE, Colors.BRIGHT_PURPLE)
    print_info_line("Сохранено в БД", f"{total_saved:,} матчей", "✅", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)

    if stage2_stats and stage2_stats.get('rate_limit_stopped'):
        print_info_line("Причина остановки", "Сервер временно заблокировал запросы", "⚠️",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_ORANGE)

    final_total = db_state['existing_count'] + total_saved
    print_info_line("Итого в БД", f"{final_total:,} матчей", "🏆", Colors.BRIGHT_WHITE, Colors.BRIGHT_GOLD)

    # === ПРОИЗВОДИТЕЛЬНОСТЬ ===
    print_subsection_header("Производительность", "⚡", Colors.BRIGHT_MAGENTA)
    print_info_line("Запросов к API", f"{total_api_calls}", "📡", Colors.BRIGHT_WHITE, Colors.BRIGHT_PURPLE)
    print_info_line("Время работы", f"{total_execution_time / 60:.1f} минут", "⏰", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_MAGENTA)
    print_info_line("Производительность", f"{processing_speed:.1f} матчей/мин", "🚀", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_LIME)

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print_info_line("Завершено", current_time, "🕐", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)

    # Прогресс-бар завершения
    print_progress_bar(final_total, TARGET_MATCHES_COUNT, "Общий прогресс:", 30, Colors.BRIGHT_GREEN, Colors.DIM)
    print()

    # === ДЕТАЛЬНАЯ СТАТИСТИКА ИСКЛЮЧЕНИЙ ===
    if ENABLE_DETAILED_STATISTICS and accumulated_stats:
        print_subsection_header("Детальная статистика исключений", "📋", Colors.BRIGHT_TEAL)

        if total_found > 0:
            success_rate = (total_saved / total_found * 100)
            print_info_line("Общая успешность обработки", f"{success_rate:.1f}%", "📈", Colors.BRIGHT_WHITE,
                            Colors.BRIGHT_GREEN)

        print_status_message("Первичные исключения:", "info", "🚫")
        for reason, count in accumulated_stats['primary_exclusions'].items():
            if count > 0:
                print_info_line(reason, f"{count:,}", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)

        print_status_message("Вторичные исключения:", "info", "⚠️")
        for reason, count in accumulated_stats['secondary_exclusions'].items():
            if count > 0:
                print_info_line(reason, f"{count:,}", "⚠️", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)


def main() -> None:
    """
    Главная функция программы для сбора профессиональных матчей Dota 2.

    Выполняет полный цикл работы программы:
    1. Инициализацию кэша героев
    2. Анализ состояния БД и определение сценария сбора
    3. Вывод конфигурации для определённого сценария
    4. ЭТАП 1: Сбор списка матчей с первичной фильтрацией
    5. ЭТАП 2: Детальная обработка каждого матча с вторичной фильтрацией
    6. Формирование итоговых записей для БД
    7. Единовременное сохранение всех матчей
    8. Вывод итоговой статистики

    Raises:
        Exception: При критических ошибках инициализации или работы с БД
    """

    # Инициализация кэша героев
    if not hero_cache.initialize():
        print_status_message("КРИТИЧЕСКАЯ ОШИБКА: Не удалось загрузить данных героев!", "error", "💥")
        print_status_message("Убедитесь, что база данных героев существует и заполнена.", "warning", "⚠️")
        return

    pro_session = ProSessionLocal()

    try:
        # Анализируем текущее состояние базы данных
        db_state = analyze_database_state(pro_session)

        # Если база данных уже заполнена - завершаем работу
        if db_state['scenario'] == SCENARIO_COMPLETE:
            print_section_header("БАЗА ДАННЫХ ЗАПОЛНЕНА", "🎉", color=Colors.BRIGHT_GREEN)
            print_info_line("Матчей в БД", f"{db_state['existing_count']:,}", "📊",
                            Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
            print_info_line("Цель достигнута", f"{TARGET_MATCHES_COUNT:,} матчей", "🎯",
                            Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)
            return

        # Выводим заголовок конфигурации
        print_collection_configuration(db_state)

        # Определяем target_count в зависимости от сценария
        if db_state['scenario'] == SCENARIO_BACKFILL:
            # Для backfill: "бесконечный" лимит на Этапе 1 (остановка по BURST_TIME)
            target_count = 100000000
        else:
            target_count = db_state.get('matches_needed', TARGET_MATCHES_COUNT)

        program_start_time = time.time()

        # Получения множества уже существующих в БД Match ID
        existing_match_ids = set()
        if db_state['scenario'] in [SCENARIO_UPDATE, SCENARIO_BACKFILL]:
            existing_match_ids = set(
                pro_session.execute(select(ProMatch.match_id)).scalars().all()
            )

        # Инициализация накопительной статистики (только если включено детальное логирование)
        accumulated_stats = None
        if ENABLE_DETAILED_STATISTICS:
            accumulated_stats = {
                'primary_exclusions': {
                    'excluded_burst_time': 0,
                    'excluded_series_type': 0,
                    'excluded_missing_keys': 0,
                    'excluded_team_name': 0
                },
                'secondary_exclusions': {
                    'excluded_leavers': 0,
                    'excluded_dead_match': 0,
                    'excluded_role_assignment': 0,
                    'excluded_player_count': 0,
                    'excluded_none_details': 0
                }
            }

        # === ЭТАП 1: СБОР СПИСКА МАТЧЕЙ ===
        pro_matches_list, total_found, stage1_api_calls = collect_pro_matches(
            target_count, db_state, existing_match_ids, accumulated_stats
        )

        if total_found == 0:
            print_status_message("Нет новых матчей для обработки", "info", "🤷")
            return

        # === ЭТАП 2: ДЕТАЛЬНАЯ ОБРАБОТКА МАТЧЕЙ ===
        processed_matches_with_details, total_processed, stage2_api_calls, stage2_stats = collect_and_process_match_details(
            pro_matches_list, db_state, existing_match_ids, accumulated_stats
        )

        total_api_calls = stage1_api_calls + stage2_api_calls

        # Формирование записей и сохранение в БД
        if processed_matches_with_details:
            final_matches_for_db = []
            # Формируем итоговые записи для БД
            for pro_match, match_details in processed_matches_with_details:
                db_record = create_database_match_record(pro_match, match_details)
                final_matches_for_db.append(db_record)

            # Сохранение всех матчей
            print_section_header("СОХРАНЕНИЕ В БАЗУ ДАННЫХ", "💾", color=Colors.BRIGHT_GREEN)
            total_saved = save_matches_to_database(pro_session, final_matches_for_db)

            if total_saved > 0:
                print_status_message(f"Успешно сохранено {total_saved} матчей в базу данных", "success", "✅")
            print()
        else:
            total_saved = 0
            total_processed = 0
            print_status_message("Нет матчей для сохранения", "warning", "⚠️")

        # Вывод итоговОЙ статистикИ
        print_final_collection_statistics(total_saved, total_processed, total_found,
                                          total_api_calls, program_start_time, db_state,
                                          accumulated_stats, stage2_stats)

    finally:
        pro_session.close()


if __name__ == "__main__":
    main()
