import json
import time
import requests
from collections import Counter

# Импорты для работы с базой данных
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

# Импорт ваших моделей и базового класса
from data_base.models import Match, MatchPlayer
from config import STEAM_API_KEY, DATABASE_URL

# --- Настройка базы данных ---
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- Константы Steam ---
STEAM_GET_MATCH_HISTORY_API = "https://api.steampowered.com/IDOTA2Match_570/GetMatchHistoryBySequenceNum/V001/"
START_MATCH_SEQ_NUM = 7_063_000_002
MAX_MATCHES = 25_000
CHUNK_SIZE = 10_000
BURST_TIME = 1754697600
MATCHES_PER_REQUEST = 100
DELAY_API_REQUESTS = 4

REQUIRED_MATCH_KEYS = {
    "players", "radiant_win", "duration", "start_time", "tower_status_radiant",
    "tower_status_dire", "barracks_status_radiant", "barracks_status_dire",
    "radiant_score", "dire_score",
}
REQUIRED_PLAYER_KEYS = {"account_id", "team_number", "hero_id"}
SUPPORT_ITEMS = {37, 43, 102, 185, 214, 218, 229, 254, 256, 269, 931, 1128}
GAME_MODS = {1, 2, 3, 4, 5, 8, 16, 22}

# Константы для расчета индекса руинера

# KDA параметры по ролям
CORE_EXPECTED_KDA = 4.8
CORE_KDA_TOLERANCE = 4.3
SUPPORT_EXPECTED_KDA = 3.5
SUPPORT_KDA_TOLERANCE = 3.0

# GPM параметры по ролям
CORE_BASE_GPM = 500
CORE_GPM_GROWTH_RATE = 6.0
CORE_MAX_GPM = 800

SUPPORT_BASE_GPM = 300
SUPPORT_GPM_GROWTH_RATE = 5.0
SUPPORT_MAX_GPM = 600

INCOME_MINIMUM_THRESHOLD = 0.3  # 30% от ожидаемого net_worth

# Веса для расчета индекса руинера
TEAM_DEATH_WEIGHT = 0.25
KDA_WEIGHT = 0.25

# Общие веса (одинаковые для всех ролей)
INCOME_SCORE_WEIGHT = 0.3
CONTRIBUTION_SCORE_WEIGHT = 0.2

# Другие параметры
GPM_CALCULATION_START_MINUTE = 20  # С какой минуты начинается рост GPM
RUINER_EMPTY_SLOTS_THRESHOLD = 4  # Количество пустых слотов для признания руинером
RUINER_SAME_ITEMS_THRESHOLD = 4  # Количество одинаковых предметов для признания руинером

RUINER_LOG_THRESHOLD = 0.45
RUINER_DETECTION_THRESHOLD = 0.55

EXCEPTIONS = {
    97: {102},  # Пример: для героя 97 (Magnus) предмет 102 не считается саппортским
}

# Константы весов
WEIGHT_ITEMS = 5
WEIGHT_NET_WORTH = 10
WEIGHT_LAST_HITS = 5
WEIGHT_GPM = 5
WEIGHT_XPM = 5

# Константы логирования
ENABLE_DETAILED_STATS = True  # Детальная статистика каждого API вызова
ENABLE_RUINER_LOGGING = False  # Логирование руинеров
ENABLE_ROLE_LOGGING = False  # Логирование ролей


# ANSI цвета для консоли
class Colors:
    CYAN = '\033[96m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    GREEN = '\033[92m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    RESET = '\033[0m'


def load_data(file_path):
    """Загружает данные из файла в формате JSON."""
    with open(file_path, "r") as file:
        return json.load(file)


HEROES_DATA_PATH = "../data/dota2_heroes_data.json"
HEROES_DATA = load_data(HEROES_DATA_PATH)


def get_hero_name_by_id(hero_id, heroes):
    for hero in heroes:
        if hero['id'] == hero_id:
            return hero['localized_name']
    return None


def fetch_steam_matches(last_match_seq_num):
    """Выполняет запрос к Steam API для получения пакета матчей."""
    while True:
        try:
            url = f"{STEAM_GET_MATCH_HISTORY_API}?start_at_match_seq_num={last_match_seq_num}&matches_requested={MATCHES_PER_REQUEST}&key={STEAM_API_KEY}"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json().get("result", {}).get("matches", [])
        except requests.exceptions.Timeout:
            print(f"Steam API | Превышено время ожидания запроса, повторный запрос.")
        except requests.exceptions.RequestException as e:
            print(f"Steam API | Ошибка при запросе: {e}")
            time.sleep(10)


def filter_matches_initial(steam_matches):
    """Первичная фильтрация матчей по базовым критериям."""
    filtered_matches = []

    # Создаем статистику только если включено детальное логирование
    if ENABLE_DETAILED_STATS:
        stats = {
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
        stats = None

    for match in steam_matches:
        # Проверка времени начала матча
        if match["start_time"] <= BURST_TIME:
            if ENABLE_DETAILED_STATS:
                stats['excluded_burst_time'] += 1
            continue

        # Проверка игрового режима
        if match.get("game_mode") not in GAME_MODS:
            if ENABLE_DETAILED_STATS:
                stats['excluded_game_mode'] += 1
            continue

        # Проверка длительности матча
        if match["duration"] <= 1200:
            if ENABLE_DETAILED_STATS:
                stats['excluded_duration'] += 1
            continue

        # Проверка наличия всех необходимых ключей матча
        if not REQUIRED_MATCH_KEYS.issubset(match.keys()):
            if ENABLE_DETAILED_STATS:
                stats['excluded_missing_keys'] += 1
            continue

        # Проверка количества игроков в командах
        radiant_players = [p for p in match["players"] if p["team_number"] == 0]
        dire_players = [p for p in match["players"] if p["team_number"] == 1]
        if len(radiant_players) != 5 or len(dire_players) != 5:
            if ENABLE_DETAILED_STATS:
                stats['excluded_player_count'] += 1
            continue

        # Проверка наличия необходимых ключей у игроков
        if not all(REQUIRED_PLAYER_KEYS.issubset(p.keys()) for p in match["players"]):
            if ENABLE_DETAILED_STATS:
                stats['excluded_player_keys'] += 1
            continue

        filtered_matches.append(match)

    if ENABLE_DETAILED_STATS:
        stats['output_count'] = len(filtered_matches)

    return filtered_matches, stats


def filter_matches_secondary(steam_matches):
    """Вторичная фильтрация матчей по сложным критериям."""
    filtered_matches = []

    # Создаем статистику только если включено детальное логирование
    if ENABLE_DETAILED_STATS:
        stats = {
            'input_count': len(steam_matches),
            'excluded_leavers': 0,
            'excluded_ruiners': 0,
            'excluded_role_assignment': 0,
            'output_count': 0
        }
    else:
        stats = None

    for match in steam_matches:
        # Проверка ливеров
        if any(p.get("leaver_status") not in [0, 1] for p in match["players"]):
            if ENABLE_DETAILED_STATS:
                stats['excluded_leavers'] += 1
            continue

        # Извлекаем и группируем игроков по командам
        radiant_players, dire_players = extract_and_group_players(match["players"])

        # Назначаем роли
        try:
            radiant_players = assign_roles(radiant_players)
            dire_players = assign_roles(dire_players)
        except ValueError:
            if ENABLE_DETAILED_STATS:
                stats['excluded_role_assignment'] += 1
            continue

        # Проверяем на руинеров
        match_duration_minutes = match["duration"] / 60

        # Проверяем команду Radiant
        has_ruiner = False
        for player in radiant_players:
            if is_ruiner(player, match_duration_minutes, match["match_id"], match["radiant_score"],
                         match["dire_score"]):
                has_ruiner = True
                break

        # Проверяем команду Dire, если в Radiant нет руинеров
        if not has_ruiner:
            for player in dire_players:
                if is_ruiner(player, match_duration_minutes, match["match_id"], match["dire_score"],
                             match["radiant_score"]):
                    has_ruiner = True
                    break

        if has_ruiner:
            if ENABLE_DETAILED_STATS:
                stats['excluded_ruiners'] += 1
            continue

        # Логируем роли только для "чистых" матчей
        if ENABLE_ROLE_LOGGING:
            log_role_assignment(match["match_id"], radiant_players, "Radiant")
            log_role_assignment(match["match_id"], dire_players, "Dire")

        # Сохраняем обработанные данные в матче
        match['processed_radiant_players'] = radiant_players
        match['processed_dire_players'] = dire_players
        filtered_matches.append(match)

    if ENABLE_DETAILED_STATS:
        stats['output_count'] = len(filtered_matches)

    return filtered_matches, stats


def extract_and_group_players(players):
    """Извлекает данные игроков и группирует их по командам."""
    radiant_players = []
    dire_players = []

    for player in players:
        player_info = {
            "account_id": player["account_id"],
            "hero_id": player["hero_id"],
            "hero_variant": player.get("hero_variant", 0),
            "net_worth": player.get("net_worth", 0),
            "last_hits": player.get("last_hits", 0),
            "denies": player.get("denies", 0),
            "gold_per_min": player.get("gold_per_min", 0),
            "xp_per_min": player.get("xp_per_min", 0),
            "item_0": player.get("item_0", 0),
            "item_1": player.get("item_1", 0),
            "item_2": player.get("item_2", 0),
            "item_3": player.get("item_3", 0),
            "item_4": player.get("item_4", 0),
            "item_5": player.get("item_5", 0),
            "backpack_0": player.get("backpack_0", 0),
            "backpack_1": player.get("backpack_1", 0),
            "backpack_2": player.get("backpack_2", 0),
            "kills": player.get("kills", 0),
            "deaths": player.get("deaths", 0),
            "assists": player.get("assists", 0),
            "item_neutral": player.get("item_neutral", 0),
            "item_neutral2": player.get("item_neutral2", 0),
            "level": player.get("level", 0),
            "aghanims_scepter": player.get("aghanims_scepter", 0),
            "aghanims_shard": player.get("aghanims_shard", 0),
            "moonshard": player.get("moonshard", 0),
        }

        if player["team_number"] == 0:
            radiant_players.append(player_info)
        else:
            dire_players.append(player_info)

    return radiant_players, dire_players


def create_match_dict(match):
    """Создает итоговый словарь с данными матча для сохранения в БД."""
    return {
        "match_id": match["match_id"],
        "match_seq_num": match["match_seq_num"],
        "radiant_win": match["radiant_win"],
        "duration": match["duration"],
        "start_time": match["start_time"],
        "tower_status_radiant": match["tower_status_radiant"],
        "tower_status_dire": match["tower_status_dire"],
        "barracks_status_radiant": match["barracks_status_radiant"],
        "barracks_status_dire": match["barracks_status_dire"],
        "lobby_type": match["lobby_type"],
        "game_mode": match["game_mode"],
        "radiant_score": match["radiant_score"],
        "dire_score": match["dire_score"],
        "radiant_players": match['processed_radiant_players'],
        "dire_players": match['processed_dire_players'],
    }


def assign_roles(team_players):
    """Назначает роли игрокам команды."""
    if len(team_players) != 5:
        raise ValueError("Команда должна состоять из 5 игроков.")

    # Вычисляем максимумы один раз для всей команды
    team_stats = {
        'max_net_worth': max((p["net_worth"] for p in team_players), default=1),
        'max_last_hits': max((p["last_hits"] for p in team_players), default=1),
        'max_gpm': max((p["gold_per_min"] for p in team_players), default=1),
        'max_xpm': max((p["xp_per_min"] for p in team_players), default=1)
    }

    # Вычисляем support_score для каждого игрока
    for player in team_players:
        support_score = calculate_support_score(player, team_stats)
        player["support_score"] = support_score
        player["role"] = "undefined"

    # Сортируем по support_score (убывающе) и назначаем роли
    team_players.sort(key=lambda x: x["support_score"], reverse=True)

    for i, player in enumerate(team_players):
        player["role"] = "support" if i < 2 else "core"

    # Проверка корректности назначения ролей
    core_count = sum(1 for p in team_players if p["role"] == "core")
    support_count = sum(1 for p in team_players if p["role"] == "support")
    if core_count != 3 or support_count != 2:
        raise ValueError(f"Ошибка распределения ролей: {core_count} коров и {support_count} саппортов.")

    return team_players


def calculate_support_score(player, team_stats):
    """Вычисляет support_score для игрока."""
    hero_id = player.get("hero_id")
    support_items = 0

    # Проверяем основные слоты (item_0 до item_5)
    for i in range(6):
        item = player.get(f"item_{i}")
        if item and item in SUPPORT_ITEMS:
            if hero_id not in EXCEPTIONS or item not in EXCEPTIONS[hero_id]:
                support_items += 1

    # Проверяем рюкзак (backpack_0 до backpack_2)
    for i in range(3):
        item = player.get(f"backpack_{i}")
        if item and item in SUPPORT_ITEMS:
            if hero_id not in EXCEPTIONS or item not in EXCEPTIONS[hero_id]:
                support_items += 1

    # Нормализованные метрики
    norm_net = player["net_worth"] / team_stats['max_net_worth']
    norm_lh = player["last_hits"] / team_stats['max_last_hits']
    norm_gpm = player["gold_per_min"] / team_stats['max_gpm']
    norm_xpm = player["xp_per_min"] / team_stats['max_xpm']

    # Итоговый счет
    support_score = (
            support_items * WEIGHT_ITEMS +
            (1 - norm_net) * WEIGHT_NET_WORTH +
            (1 - norm_lh) * WEIGHT_LAST_HITS +
            (1 - norm_gpm) * WEIGHT_GPM +
            (1 - norm_xpm) * WEIGHT_XPM
    )

    return support_score


def log_role_assignment(match_id, team_players, team_side):
    """Логирование распределения ролей в команде."""
    print(f"{Colors.CYAN}{'─' * 120}{Colors.RESET}")
    print(f"{Colors.GREEN}► РАСПРЕДЕЛЕНИЕ РОЛЕЙ{Colors.RESET} | "
          f"Match ID: {Colors.YELLOW}{match_id}{Colors.RESET} | "
          f"Команда: {Colors.MAGENTA}{team_side}{Colors.RESET}")

    for player in team_players:
        hero_name = get_hero_name_by_id(player['hero_id'], HEROES_DATA)
        role_color = Colors.RED if player["role"] == "support" else Colors.BLUE
        print(f"Player ID: {Colors.YELLOW}{player['account_id']}{Colors.RESET} | "
              f"Герой: {Colors.BLUE}{hero_name}{Colors.RESET}({Colors.YELLOW}{player['hero_id']}{Colors.RESET}) | "
              f"Роль: {role_color}{player['role'].upper()}{Colors.RESET} | "
              f"Support Score: {Colors.GREEN}{player['support_score']:.2f}{Colors.RESET} | "
              f"Net: {player['net_worth']} | LH: {player['last_hits']} | GPM: {player['gold_per_min']} | XPM: {player['xp_per_min']}")

    print(f"{Colors.CYAN}{'─' * 120}{Colors.RESET}")


def is_ruiner(player, match_duration_minutes, match_id, team_score, team_death):
    """Проверяет, является ли игрок руинером."""
    from collections import Counter

    kills = player["kills"]
    deaths = player["deaths"]
    assists = player["assists"]
    net_worth = player["net_worth"]
    role = player["role"]

    # Стандартный расчет индекса руинера
    # Компонент 1: Доля смертей от командных
    team_death_ratio = min(deaths / max(1, team_death), 1)

    # Компонент 2: Инвертированный KDA с учетом роли
    kda = (kills + assists) / max(1, deaths)

    tdr_weight, kda_weight = TEAM_DEATH_WEIGHT, KDA_WEIGHT
    # Ожидаемый KDA в зависимости от роли
    if role == "core":
        expected_kda = CORE_EXPECTED_KDA
        kda_tolerance = CORE_KDA_TOLERANCE
        base_gpm, gpm_growth_rate, max_gpm = CORE_BASE_GPM, CORE_GPM_GROWTH_RATE, CORE_MAX_GPM
    else:
        expected_kda = SUPPORT_EXPECTED_KDA
        kda_tolerance = SUPPORT_KDA_TOLERANCE
        base_gpm, gpm_growth_rate, max_gpm = SUPPORT_BASE_GPM, SUPPORT_GPM_GROWTH_RATE, SUPPORT_MAX_GPM

    # Нормализуем KDA в диапазон 0-1 (где 0 = хороший KDA, 1 = плохой)
    kda_score = max(0, min(1, (expected_kda - kda) / kda_tolerance))

    # Вычисляем ожидаемый GPM и net worth
    expected_gpm = min(base_gpm + gpm_growth_rate * max(0, match_duration_minutes - GPM_CALCULATION_START_MINUTE),
                       max_gpm)
    expected_net_worth = match_duration_minutes * expected_gpm

    # Income Score с учетом минимального порога
    if expected_net_worth > 0:
        income_ratio = net_worth / expected_net_worth
        if income_ratio >= 1.0:
            is_score = 1.0  # Отлично, больше ожидаемого
        elif income_ratio <= INCOME_MINIMUM_THRESHOLD:
            is_score = 0.0  # Ужасно, меньше минимального порога
        else:
            # Линейная интерполяция между минимальным порогом и 100%
            is_score = (income_ratio - INCOME_MINIMUM_THRESHOLD) / (1.0 - INCOME_MINIMUM_THRESHOLD)
    else:
        is_score = 1.0

    # Contribution Score
    cs = (kills + assists) / max(team_score, 1)

    # Итоговый индекс руинера
    ruiner_index = (tdr_weight * team_death_ratio +
                    kda_weight * kda_score +
                    INCOME_SCORE_WEIGHT * (1 - is_score) +
                    CONTRIBUTION_SCORE_WEIGHT * (1 - cs))

    # Проверка предметов на руинерское поведение
    items = []
    empty_slots = 0

    # Собираем все предметы из основных слотов (item_0 до item_5)
    for i in range(6):
        item = player.get(f"item_{i}")
        if item == 0 or item is None:
            empty_slots += 1
        else:
            items.append(item)

    # Если слишком много пустых слотов - руинер
    if empty_slots >= RUINER_EMPTY_SLOTS_THRESHOLD:
        ruiner_index = 1.0
    # Если слишком много одинаковых предметов - руинер
    elif len(items) >= RUINER_SAME_ITEMS_THRESHOLD:
        item_counts = Counter(items)
        for item_id, count in item_counts.items():
            if count >= RUINER_SAME_ITEMS_THRESHOLD:
                ruiner_index = 1.0
                break

    # Логирование
    if ENABLE_RUINER_LOGGING and ruiner_index >= RUINER_LOG_THRESHOLD:
        log_ruiner_stats(match_id, player, ruiner_index, match_duration_minutes,
                         role, team_death_ratio, kda_score, is_score, cs, expected_gpm, team_score,
                         kda, expected_kda, tdr_weight, kda_weight)

    return ruiner_index > RUINER_DETECTION_THRESHOLD


def log_ruiner_stats(match_id, player, ruiner_index, match_duration, role, team_death_ratio, kda_score, is_score, cs,
                     expected_gpm,
                     team_score, kda, expected_kda, tdr_weight, kda_weight):
    """Логирование статистики потенциального руинера."""
    status = "РУИНЕР" if ruiner_index > RUINER_DETECTION_THRESHOLD else "ПОДОЗРЕНИЕ"
    status_color = Colors.RED if ruiner_index > RUINER_DETECTION_THRESHOLD else Colors.YELLOW
    hero_name = get_hero_name_by_id(player['hero_id'], HEROES_DATA)

    print(f"{Colors.CYAN}{'─' * 60}{Colors.RESET}")
    print(f"{status_color}► {status}{Colors.RESET} | "
          f"Match ID: {Colors.YELLOW}{match_id}{Colors.RESET} | "
          f"Player ID: {Colors.YELLOW}{player['account_id']}{Colors.RESET} | "
          f"Герой: {Colors.BLUE}{hero_name}{Colors.RESET}({Colors.YELLOW}{player['hero_id']}{Colors.RESET}) | "
          f"Роль: {Colors.MAGENTA}{role.upper()}{Colors.RESET}")

    print(f"{Colors.GREEN}Индекс:{Colors.RESET} {Colors.RED}{ruiner_index:.3f}{Colors.RESET} "
          f"({Colors.RED}{ruiner_index * 100:.1f}%{Colors.RESET}) | "
          f"{Colors.GREEN}Время:{Colors.RESET} {match_duration:.1f}м | "
          f"{Colors.GREEN}Счет команды:{Colors.RESET} {team_score} | "
          f"{Colors.GREEN}GPM ожид:{Colors.RESET} {expected_gpm:.0f}")

    print(f"{Colors.GREEN}Метрики:{Colors.RESET} "
          f"TDR: {Colors.YELLOW}{tdr_weight * team_death_ratio:.3f}{Colors.RESET} ({Colors.YELLOW}{team_death_ratio * 100:.1f}%{Colors.RESET}) | "
          f"KDA: {Colors.YELLOW}{kda_weight * kda_score:.3f}{Colors.RESET} ({Colors.YELLOW}{kda:.2f}{Colors.RESET}/{Colors.YELLOW}{expected_kda:.1f}{Colors.RESET}|{Colors.YELLOW}{kda_score * 100:.1f}%{Colors.RESET}) | "
          f"IS: {Colors.YELLOW}{INCOME_SCORE_WEIGHT * (1 - is_score):.3f}{Colors.RESET} ({Colors.YELLOW}{(1 - is_score) * 100:.1f}%{Colors.RESET}) | "
          f"CS: {Colors.YELLOW}{CONTRIBUTION_SCORE_WEIGHT * (1 - cs):.3f}{Colors.RESET} ({Colors.YELLOW}{(1 - cs) * 100:.1f}%{Colors.RESET})")

    print(f"{Colors.CYAN}{'─' * 60}{Colors.RESET}")


def process_matches_batch(steam_matches):
    """Основная функция для обработки батча матчей."""
    # Первичная фильтрация
    primary_filtered, primary_stats = filter_matches_initial(steam_matches)

    # Вторичная фильтрация
    secondary_filtered, secondary_stats = filter_matches_secondary(primary_filtered)

    # Создание итоговых словарей
    processed_matches = [create_match_dict(match) for match in secondary_filtered]

    # Объединение статистики (только если включено детальное логирование)
    if ENABLE_DETAILED_STATS:
        combined_stats = {
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
        combined_stats = {
            'api_input': len(steam_matches),
            'primary_filtered': len(primary_filtered),
            'secondary_filtered': len(secondary_filtered),
            'final_processed': len(processed_matches)
        }

    return processed_matches, combined_stats


def print_processing_stats(stats, api_call_number):
    """Выводит детальную статистику обработки матчей (только если включено)."""
    if not ENABLE_DETAILED_STATS:
        return

    print(f"\n{'=' * 60}")
    print(f"СТАТИСТИКА ОБРАБОТКИ - API вызов #{api_call_number}")
    print(f"{'=' * 60}")

    print(f"Получено от API: {stats['api_input']} матчей")
    print(f"После первичной фильтрации: {stats['primary_filtered']} матчей")
    print(f"После вторичной фильтрации: {stats['secondary_filtered']} матчей")
    print(f"Итого обработано: {stats['final_processed']} матчей")

    print(f"\nПЕРВИЧНЫЕ ИСКЛЮЧЕНИЯ:")
    for reason, count in stats['primary_exclusions'].items():
        if count > 0:
            print(f"  - {reason}: {count}")

    print(f"\nВТОРИЧНЫЕ ИСКЛЮЧЕНИЯ:")
    for reason, count in stats['secondary_exclusions'].items():
        if count > 0:
            print(f"  - {reason}: {count}")

    total_excluded = (stats['api_input'] - stats['final_processed'])
    success_rate = (stats['final_processed'] / stats['api_input'] * 100) if stats['api_input'] > 0 else 0
    print(f"\nВСЕГО ИСКЛЮЧЕНО: {total_excluded} ({100 - success_rate:.1f}%)")
    print(f"УСПЕШНОСТЬ: {success_rate:.1f}%")
    print(f"{'=' * 60}")


def save_matches_to_db(session, batch_matches, total_saved_matches, chunk_start_time, program_start_time):
    """Сохраняет матчи в БД с выводом статистики."""
    if not batch_matches:
        return 0

    save_start_time = time.time()
    matches_added_count = 0

    for match_data in batch_matches:
        # Проверяем, существует ли матч
        exists = session.query(Match.match_id).filter_by(match_id=match_data["match_id"]).first()
        if exists:
            continue

        # Создаем объект Match
        match_obj = Match(
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

        # Объединяем игроков для удобства обработки
        all_players_data = [
                               (player, 0) for player in match_data['radiant_players']
                           ] + [
                               (player, 1) for player in match_data['dire_players']
                           ]

        for player_data, team_num in all_players_data:
            kills = player_data.get("kills", 0)
            deaths = player_data.get("deaths", 0)
            assists = player_data.get("assists", 0)

            player_obj = MatchPlayer(
                account_id=player_data["account_id"],
                team_number=team_num,
                hero_id=player_data["hero_id"],
                hero_variant=player_data.get("hero_variant", 0),
                role=player_data["role"],
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
                kills=kills,
                deaths=deaths,
                assists=assists,
                kda=(kills + assists) / max(1, deaths),
                last_hits=player_data.get("last_hits", 0),
                denies=player_data.get("denies", 0),
                gold_per_min=player_data.get("gold_per_min", 0),
                xp_per_min=player_data.get("xp_per_min", 0),
                level=player_data.get("level", 0),
                net_worth=player_data.get("net_worth", 0),
                aghanims_scepter=player_data.get("aghanims_scepter", 0),
                aghanims_shard=player_data.get("aghanims_shard", 0),
                moonshard=player_data.get("moonshard", 0),
            )
            match_obj.players.append(player_obj)

        session.add(match_obj)
        matches_added_count += 1

    try:
        session.commit()

        # Вычисляем статистику сохранения
        current_time = time.time()

        # ИСПРАВЛЕНИЕ: правильное время накопления чанка
        chunk_accumulation_time = current_time - chunk_start_time

        # Время собственно сохранения в БД
        save_time = current_time - save_start_time

        total_time = current_time - program_start_time
        new_total_saved = total_saved_matches + matches_added_count

        # ИСПРАВЛЕНИЕ: скорость чанка основана на времени накопления, а не времени сохранения
        chunk_speed = (matches_added_count / chunk_accumulation_time * 60) if chunk_accumulation_time > 0 else 0

        # Общая скорость (матчи в минуту)
        total_speed = (new_total_saved / total_time * 60) if total_time > 0 else 0

        print(f"\n{Colors.GREEN}🔄 СОХРАНЕНИЕ В БД{Colors.RESET}")
        print(f"  ✅ Сохранено: {Colors.YELLOW}{matches_added_count}{Colors.RESET} матчей")
        print(f"  📊 Всего в БД: {Colors.CYAN}{new_total_saved}{Colors.RESET} матчей")
        print(f"  ⏱️ Время: {Colors.MAGENTA}{total_time / 60:.1f}м{Colors.RESET} с начала")
        print(f"  💾 Время сохранения: {Colors.MAGENTA}{save_time:.2f}с{Colors.RESET}")
        print(f"  📦 Время накопления чанка: {Colors.MAGENTA}{chunk_accumulation_time:.1f}с{Colors.RESET}")
        print(
            f"  🚀 Скорость: {Colors.GREEN}{chunk_speed:.1f}{Colors.RESET} матчей/мин (чанк) | {Colors.GREEN}{total_speed:.1f}{Colors.RESET} матчей/мин (общая)\n")

        return matches_added_count

    except IntegrityError as e:
        print(f"Ошибка целостности данных при сохранении: {e}")
        session.rollback()
        return 0
    except Exception as e:
        print(f"Ошибка при сохранении в БД: {e}")
        session.rollback()
        return 0


def main():
    """Главная функция для сбора и сохранения данных матчей в БД."""
    session = SessionLocal()
    program_start_time = time.time()
    chunk_start_time = time.time()  # Начальное время для первого чанка
    total_saved_matches = 0
    total_processed_matches = 0  # Новый счётчик обработанных матчей
    start_match_seq_num = START_MATCH_SEQ_NUM
    batch_matches = []
    steam_api_calls = 0

    # Накопительная статистика (только если включено детальное логирование)
    if ENABLE_DETAILED_STATS:
        total_stats = {
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

    try:
        print(f"\n{Colors.CYAN}{'=' * 70}{Colors.RESET}")
        print(f"\t\t\t{Colors.CYAN}🚀 ЗАПУСК СБОРА ДАННЫХ О МАТЧАХ DOTA 2{Colors.RESET}")
        print(f"{Colors.CYAN}{'=' * 70}{Colors.RESET}")

        print(f"{Colors.GREEN}📋 КОНФИГУРАЦИЯ:{Colors.RESET}")
        print(f"  🎯 Целевое количество матчей: {Colors.YELLOW}{MAX_MATCHES:,}{Colors.RESET}")
        print(f"  📦 Размер чанка для сохранения: {Colors.YELLOW}{CHUNK_SIZE:,}{Colors.RESET}")
        print(f"  🔢 Матчей за запрос к API: {Colors.YELLOW}{MATCHES_PER_REQUEST}{Colors.RESET}")
        print(f"  🕐 Задержка между запросами: {Colors.YELLOW}{DELAY_API_REQUESTS}с{Colors.RESET}")
        print(f"  🆔 Начальный sequence number: {Colors.YELLOW}{START_MATCH_SEQ_NUM:,}{Colors.RESET}")

        print(f"\n{Colors.BLUE}🔧 НАСТРОЙКИ ФИЛЬТРАЦИИ:{Colors.RESET}")
        print(f"  ⏰ Время burst: {Colors.YELLOW}{BURST_TIME}{Colors.RESET}")
        print(f"  ⏱️ Минимальная длительность: {Colors.YELLOW}20 мин{Colors.RESET}")
        print(f"  🎮 Разрешённые режимы: {Colors.YELLOW}{len(GAME_MODS)} режимов{Colors.RESET}")

        print(f"\n{Colors.MAGENTA}📊 ЛОГИРОВАНИЕ:{Colors.RESET}")
        print(
            f"  📈 Детальная статистика: {Colors.GREEN if ENABLE_DETAILED_STATS else Colors.RED}{'ВКЛ' if ENABLE_DETAILED_STATS else 'ВЫКЛ'}{Colors.RESET}")
        print(
            f"  🔍 Логирование руинеров: {Colors.GREEN if ENABLE_RUINER_LOGGING else Colors.RED}{'ВКЛ' if ENABLE_RUINER_LOGGING else 'ВЫКЛ'}{Colors.RESET}")
        print(
            f"  👥 Логирование ролей: {Colors.GREEN if ENABLE_ROLE_LOGGING else Colors.RED}{'ВКЛ' if ENABLE_ROLE_LOGGING else 'ВЫКЛ'}{Colors.RESET}")

        estimated_api_calls = (MAX_MATCHES // 45) + 1
        estimated_time_min = (estimated_api_calls * (DELAY_API_REQUESTS + 1.5)) / 60

        print(f"\n{Colors.YELLOW}⏳ ПРЕДВАРИТЕЛЬНАЯ ОЦЕНКА:{Colors.RESET}")
        print(f"  🔢 Примерно API вызовов: {Colors.YELLOW}~{estimated_api_calls:,}{Colors.RESET}")
        print(f"  ⏰ Минимальное время: {Colors.YELLOW}~{estimated_time_min:.1f} мин{Colors.RESET}")

        print(f"{Colors.CYAN}{'=' * 70}{Colors.RESET}")
        print(f"\n{Colors.GREEN}▶️ НАЧАЛО ПРОЦЕССА СБОРА ИНФОРМАЦИИ...{Colors.RESET}")

        # ИЗМЕНЕНИЕ: проверяем количество обработанных матчей, а не сохранённых
        while total_processed_matches < MAX_MATCHES:
            iteration_start_time = time.time()
            steam_api_calls += 1

            # Получение данных от Steam API
            steam_matches = fetch_steam_matches(start_match_seq_num)

            if not steam_matches:
                print("Steam API | Матчи не найдены, завершение.")
                break

            start_match_seq_num = steam_matches[-1]["match_seq_num"] + 1

            # Обработка батча матчей
            processed_matches, batch_stats = process_matches_batch(steam_matches)

            # Обновляем накопительную статистику (только если включено)
            if ENABLE_DETAILED_STATS:
                total_stats['api_input'] += batch_stats['api_input']
                total_stats['primary_filtered'] += batch_stats['primary_filtered']
                total_stats['secondary_filtered'] += batch_stats['secondary_filtered']
                total_stats['final_processed'] += batch_stats['final_processed']

                for category in ['primary_exclusions', 'secondary_exclusions']:
                    for reason, count in batch_stats[category].items():
                        total_stats[category][reason] += count

            # ИЗМЕНЕНИЕ: обновляем счётчик обработанных матчей
            current_batch_count = len(processed_matches)

            # ИЗМЕНЕНИЕ: проверяем, не превысим ли лимит
            if total_processed_matches + current_batch_count > MAX_MATCHES:
                # Обрезаем батч до нужного количества
                remaining_needed = MAX_MATCHES - total_processed_matches
                processed_matches = processed_matches[:remaining_needed]
                current_batch_count = len(processed_matches)

                print(
                    f"\n{Colors.YELLOW}⚠️  Достигнут лимит! Обрезаем батч до {current_batch_count} матчей{Colors.RESET}\n")

            # Добавляем обработанные матчи в батч для сохранения
            batch_matches.extend(processed_matches)
            total_processed_matches += current_batch_count

            # ИСПРАВЛЕНИЕ: включаем задержку в время итерации

            # Применяем задержку между запросами только если продолжаем
            if total_processed_matches < MAX_MATCHES:
                time.sleep(DELAY_API_REQUESTS)

            # Время итерации (включая всё: API, обработку, сохранение, задержки)
            iteration_time = time.time() - iteration_start_time

            # Минималистичный основной вывод
            print(f"{Colors.BLUE}📡 API #{steam_api_calls}{Colors.RESET} | "
                  f"Получено: {Colors.YELLOW}{batch_stats['api_input']}{Colors.RESET} → "
                  f"1-й фильтр: {Colors.YELLOW}{batch_stats['primary_filtered']}{Colors.RESET} → "
                  f"2-й фильтр: {Colors.YELLOW}{batch_stats['secondary_filtered']}{Colors.RESET} → "
                  f"Обработано: {Colors.CYAN}{current_batch_count}{Colors.RESET}")

            print(f"  💾 Всего обработано: {Colors.CYAN}{total_processed_matches}{Colors.RESET} | "
                  f"Уже в БД: {Colors.GREEN}{total_saved_matches}{Colors.RESET} | "
                  f"⏱️  {Colors.MAGENTA}{iteration_time:.1f}с{Colors.RESET}")

            # Детальная статистика (если включена)
            print_processing_stats(batch_stats, steam_api_calls)

            # ИСПРАВЛЕНИЕ: проверяем лимит ПЕРЕД сохранением
            if total_processed_matches >= MAX_MATCHES:
                print(
                    f"\n{Colors.GREEN}🎯 Достигнут целевой лимит: {total_processed_matches} матчей!{Colors.RESET}")
                break

            # Сохранение в БД по достижении размера чанка (только если не достигли лимита)
            if len(batch_matches) >= CHUNK_SIZE:
                saved_count = save_matches_to_db(session, batch_matches, total_saved_matches, chunk_start_time,
                                                 program_start_time)
                total_saved_matches += saved_count
                batch_matches = []  # Очищаем буфер
                chunk_start_time = time.time()  # ИСПРАВЛЕНИЕ: сбрасываем время начала нового чанка

        # Сохранение остатка матчей
        if batch_matches:
            print(f"{Colors.YELLOW}💾 Сохраняем остаток: {len(batch_matches)} матчей{Colors.RESET}")
            saved_count = save_matches_to_db(session, batch_matches, total_saved_matches, chunk_start_time,
                                             program_start_time)
            total_saved_matches += saved_count

    finally:
        session.close()

        # Итоговая статистика
        total_time = time.time() - program_start_time
        overall_speed = (total_saved_matches / total_time * 60) if total_time > 0 else 0

        print(f"\n{Colors.CYAN}{'=' * 70}{Colors.RESET}")
        print(f"\t\t\t\t\t{Colors.CYAN}🏁 ИТОГОВАЯ СТАТИСТИКА{Colors.RESET}")
        print(f"{Colors.CYAN}{'=' * 70}{Colors.RESET}")
        print(f"🎯 Целевое количество: {Colors.YELLOW}{MAX_MATCHES:,}{Colors.RESET} матчей")
        print(f"📊 Всего обработано: {Colors.CYAN}{total_processed_matches:,}{Colors.RESET} матчей")
        print(f"✅ Финально сохранено: {Colors.GREEN}{total_saved_matches:,}{Colors.RESET} матчей")
        print(f"🔢 Вызовов Steam API: {Colors.YELLOW}{steam_api_calls}{Colors.RESET}")
        print(f"⏱️ Общее время работы: {Colors.MAGENTA}{total_time / 60:.1f}{Colors.RESET} минут")
        print(f"🚀 Средняя скорость: {Colors.GREEN}{overall_speed:.1f}{Colors.RESET} матчей в минуту")

        # Детальная статистика исключений (если включена)
        if ENABLE_DETAILED_STATS and 'total_stats' in locals():
            if total_stats['api_input'] > 0:
                success_rate = (total_stats['final_processed'] / total_stats['api_input'] * 100)
                print(f"📈 Общая успешность обработки: {Colors.GREEN}{success_rate:.1f}%{Colors.RESET}")

            print(f"\n{Colors.CYAN}ДЕТАЛЬНАЯ СТАТИСТИКА ИСКЛЮЧЕНИЙ:{Colors.RESET}")
            print("Первичные исключения:")
            for reason, count in total_stats['primary_exclusions'].items():
                if count > 0:
                    print(f"  - {reason}: {Colors.RED}{count:,}{Colors.RESET}")

            print("Вторичные исключения:")
            for reason, count in total_stats['secondary_exclusions'].items():
                if count > 0:
                    print(f"  - {reason}: {Colors.RED}{count:,}{Colors.RESET}")

        print(f"{Colors.CYAN}{'=' * 70}{Colors.RESET}")


if __name__ == "__main__":
    main()
