import time
import requests
import json

# Импорты для работы с базой данных
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

# Импорт ваших моделей и базового класса
from data_base.models import Match, MatchPlayer
from config import STEAM_API_KEY, DATABASE_URL

# --- Настройка базы данных ---
# Используем SQLite для простоты. Вы можете заменить на вашу строку подключения (e.g., PostgreSQL, MySQL).
engine = create_engine(DATABASE_URL)
# Создаем фабрику сессий
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- Константы Steam (без изменений) ---
STEAM_GET_MATCH_HISTORY_API = "https://api.steampowered.com/IDOTA2Match_570/GetMatchHistoryBySequenceNum/V001/"
START_MATCH_SEQ_NUM = 7_063_000_000
MAX_MATCHES = 25_000
CHUNK_SIZE = 10000  # Уменьшен для более частых сохранений в БД
BURST_TIME = 1754697600
MATCHES_PER_REQUEST = 100

REQUIRED_MATCH_KEYS = {
    "players", "radiant_win", "duration", "start_time", "tower_status_radiant",
    "tower_status_dire", "barracks_status_radiant", "barracks_status_dire",
    "radiant_score", "dire_score",
}
REQUIRED_PLAYER_KEYS = {"account_id", "team_number", "hero_id"}
SUPPORT_ITEMS = {37, 43, 102, 185, 214, 218, 229, 254, 256, 269, 931, 1128}
GAME_MODS = {1, 2, 3, 4, 5, 8, 16, 22}

EXCEPTIONS = {
    97: {102},  # Пример: для героя 97 (Magnus) предмет 102 не считается саппортским
    # Добавьте больше, если нужно: 123: {456, 789},
}

# Добавьте эти константы в начало файла после других констант
ENABLE_RUINER_LOGGING = True  # Включает/выключает логирование
RUINER_LOG_THRESHOLD = 0.40  # Минимальный ruiner_index для логирования
RUINER_DETECTION_THRESHOLD = 0.50  # Порог для определения руинера

ENABLE_ROLE_LOGGING = False  # Новая константа для включения/выключения логирования ролей
# Константы весов (можно настраивать)
WEIGHT_ITEMS = 5  # За каждый саппорт-айтем
WEIGHT_NET_WORTH = 10
WEIGHT_LAST_HITS = 5
WEIGHT_GPM = 5
WEIGHT_XPM = 5


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
    """
    Загружает данные из файла в формате JSON.

    :param file_path: Строка, указывающая путь к файлу с данными.
    :return: Объект Python, загруженный из файла JSON.
    """
    with open(file_path, "r") as file:
        return json.load(file)


HEROES_DATA_PATH = "../data/dota2_heroes_data.json"
HEROES_DATA = load_data(HEROES_DATA_PATH)


def fetch_steam_matches(last_match_seq_num):
    """
    Выполняет запрос к Steam API для получения пакета матчей. (без изменений)
    """
    while True:
        try:
            url = f"{STEAM_GET_MATCH_HISTORY_API}?start_at_match_seq_num={last_match_seq_num}&matches_requested={MATCHES_PER_REQUEST}&key={STEAM_API_KEY}"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json().get("result", {}).get("matches", [])
        except requests.exceptions.Timeout:
            print(f"Steam API | Превышено время ожидания запроса {url}, повторный запрос.")
        except requests.exceptions.RequestException as e:
            print(f"Steam API | Ошибка при запросе: {e}")
            time.sleep(10)


def filter_matches_initial(steam_matches):
    """
    Первичная фильтрация матчей. (без изменений)
    """
    filtered_matches = []
    initial_excluded = 0

    for match in steam_matches:
        if match["start_time"] <= BURST_TIME:
            initial_excluded += 1
            print(f"Steam API | Обнаружен матч {match['match_id']} с start_time <= {BURST_TIME}.")
            continue

        if match.get("game_mode") not in GAME_MODS:
            initial_excluded += 1
            continue

        if match["duration"] <= 1200:
            initial_excluded += 1
            continue

        if not REQUIRED_MATCH_KEYS.issubset(match.keys()):
            initial_excluded += 1
            continue

        radiant_players = [p for p in match["players"] if p["team_number"] == 0]
        dire_players = [p for p in match["players"] if p["team_number"] == 1]
        if len(radiant_players) != 5 or len(dire_players) != 5:
            initial_excluded += 1
            continue

        if not all(REQUIRED_PLAYER_KEYS.issubset(p.keys()) for p in match["players"]):
            initial_excluded += 1
            continue

        filtered_matches.append(match)

    return filtered_matches, initial_excluded


def filter_matches_secondary(match):
    """
    Вторичная фильтрация и формирование словаря с данными.
    Добавлено извлечение новых полей для соответствия модели MatchPlayer.
    """
    match_id = match["match_id"]

    if any(p.get("leaver_status") not in [0, 1] for p in match["players"]):
        return None

    radiant_players = []
    dire_players = []
    for player in match["players"]:
        # Расширяем словарь, чтобы включить все поля из модели MatchPlayer
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

    radiant_players = assign_roles(radiant_players, match_id, "Radiant")
    dire_players = assign_roles(dire_players, match_id, "Dire")

    match_duration_minutes = match["duration"] / 60
    for player in radiant_players:
        if is_ruiner(player, match_duration_minutes, match_id, match["radiant_score"]):
            return None

    for player in dire_players:
        if is_ruiner(player, match_duration_minutes, match_id, match["dire_score"]):
            return None

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
        "radiant_players": radiant_players,
        "dire_players": dire_players,
    }


def is_ruiner(player, match_duration, match_id, team_score):
    """
    Проверяет, является ли игрок руинером с улучшенным логированием.

    Args:
        player: Данные игрока
        match_duration: Длительность матча в минутах
        team_score: Количество убийств команды игрока (radiant_score или dire_score)
    """
    kills = player["kills"]
    deaths = player["deaths"]
    assists = player["assists"]
    net_worth = player["net_worth"]

    ds = deaths / (kills + assists + 1)
    ds_norm = min(ds / 3.0, 1)

    if player["role"] == "core":
        base_gpm, gpm_growth_rate, max_gpm = 500, 6.5, 800
    else:
        base_gpm, gpm_growth_rate, max_gpm = 300, 4.5, 500

    expected_gpm = min(base_gpm + gpm_growth_rate * max(0, match_duration - 15), max_gpm)
    expected_net_worth = match_duration * expected_gpm
    is_score = min(net_worth / expected_net_worth, 1) if expected_net_worth > 0 else 1

    # Используем реальный счет команды вместо match_duration * 0.5
    cs = (kills + assists) / max(team_score, 1)  # max для избежания деления на 0
    ruiner_index = 0.4 * ds_norm + 0.3 * (1 - is_score) + 0.3 * (1 - cs)

    # Улучшенное логирование
    log_ruiner_stats(match_id, player, ruiner_index, match_duration, player["role"], ds_norm, is_score, cs,
                     expected_gpm, team_score)

    return ruiner_index > RUINER_DETECTION_THRESHOLD


def log_ruiner_stats(match_id, player, ruiner_index, match_duration, role, ds_norm, is_score, cs, expected_gpm,
                     team_score):
    """
    Компактное логирование статистики потенциального руинера.
    """
    if not ENABLE_RUINER_LOGGING or ruiner_index < RUINER_LOG_THRESHOLD:
        return

    # Определяем статус
    status = "РУИНЕР" if ruiner_index > RUINER_DETECTION_THRESHOLD else "ПОДОЗРЕНИЕ"
    status_color = Colors.RED if ruiner_index > RUINER_DETECTION_THRESHOLD else Colors.YELLOW
    hero_name = get_hero_name_by_id(player['hero_id'], HEROES_DATA)

    print(f"\n{Colors.CYAN}{'─' * 60}{Colors.RESET}")
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
          f"DS: {Colors.YELLOW}{0.4 * ds_norm:.3f}{Colors.RESET} ({Colors.YELLOW}{ds_norm * 100:.1f}%{Colors.RESET}) | "
          f"IS: {Colors.YELLOW}{0.3 * (1 - is_score):.3f}{Colors.RESET} ({Colors.YELLOW}{(1 - is_score) * 100:.1f}%{Colors.RESET}) | "
          f"CS: {Colors.YELLOW}{0.3 * (1 - cs):.3f}{Colors.RESET} ({Colors.YELLOW}{(1 - cs) * 100:.1f}%{Colors.RESET})")

    print(f"{Colors.CYAN}{'─' * 60}{Colors.RESET}")


def get_hero_name_by_id(hero_id, heroes):
    for hero in heroes:
        if hero['id'] == hero_id:
            return hero['localized_name']
    return None


def assign_roles(team_players, match_id=None, team_side="Unknown"):
    """
    Назначает роли игрокам (3 кора, 2 саппорта) по системе баллов.

    :param team_players: Список игроков команды.
    :param match_id: ID матча для логирования (опционально).
    :param team_side: Сторона команды (Radiant/Dire) для логирования.
    :return: Список игроков с назначенными ролями.
    """
    if len(team_players) != 5:
        raise ValueError("Команда должна состоять из 5 игроков.")

    # Находим максимумы для нормализации
    max_net_worth = max(p["net_worth"] for p in team_players) if team_players else 1
    max_last_hits = max(p["last_hits"] for p in team_players) if team_players else 1
    max_gpm = max(p["gold_per_min"] for p in team_players) if team_players else 1
    max_xpm = max(p["xp_per_min"] for p in team_players) if team_players else 1

    # Вычисляем support_score для каждого игрока
    for player in team_players:
        hero_id = player.get("hero_id")

        # Кол-во саппорт-айтемов с учётом исключений из константы
        support_items = 0

        # Проверяем слоты инвентаря (item_0 до item_5)
        for i in range(6):
            item = player.get(f"item_{i}", None)
            if item is not None and item in SUPPORT_ITEMS:
                # Проверяем исключение: если герой в EXCEPTIONS и item в списке исключений, то не считаем
                if hero_id not in EXCEPTIONS or item not in EXCEPTIONS[hero_id]:
                    support_items += 1

        # Проверяем backpack (backpack_0 до backpack_2)
        for i in range(3):
            item = player.get(f"backpack_{i}", None)
            if item is not None and item in SUPPORT_ITEMS:
                # Аналогичная проверка исключения
                if hero_id not in EXCEPTIONS or item not in EXCEPTIONS[hero_id]:
                    support_items += 1

        # Нормализованные метрики (0-1, где 1 — максимум в команде)
        norm_net = player["net_worth"] / max_net_worth
        norm_lh = player["last_hits"] / max_last_hits
        norm_gpm = player["gold_per_min"] / max_gpm
        norm_xpm = player["xp_per_min"] / max_xpm

        # Баллы: + за айтемы, + за "бедность" (1 - norm)
        support_score = (support_items * WEIGHT_ITEMS) + \
                        ((1 - norm_net) * WEIGHT_NET_WORTH) + \
                        ((1 - norm_lh) * WEIGHT_LAST_HITS) + \
                        ((1 - norm_gpm) * WEIGHT_GPM) + \
                        ((1 - norm_xpm) * WEIGHT_XPM)

        player["support_score"] = support_score
        player["role"] = "undefined"

    # Сортируем по support_score descending (самые саппортные сверху)
    team_players.sort(key=lambda x: x["support_score"], reverse=True)

    # Назначаем роли: топ-2 — support, остальные — core
    for i, player in enumerate(team_players):
        if i < 2:
            player["role"] = "support"
        else:
            player["role"] = "core"

    # Проверка (опционально, но полезно)
    core_count = sum(1 for p in team_players if p["role"] == "core")
    support_count = sum(1 for p in team_players if p["role"] == "support")
    if core_count != 3 or support_count != 2:
        raise ValueError(f"Ошибка распределения ролей: {core_count} коров и {support_count} саппортов.")

    # Логирование, если включено
    if ENABLE_ROLE_LOGGING:
        log_role_assignment(match_id, team_players, team_side)

    return team_players


def log_role_assignment(match_id, team_players, team_side):
    """
    Компактное логирование распределения ролей в команде.
    """
    print(f"\n{Colors.CYAN}{'─' * 120}{Colors.RESET}")
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


def save_matches_to_db(session, batch_matches):
    """
    Преобразует список словарей с данными матчей в объекты SQLAlchemy
    и сохраняет их в базу данных.
    """
    if not batch_matches:
        return

    matches_added_count = 0
    for match_data in batch_matches:
        # Проверяем, существует ли матч, чтобы избежать дубликатов
        exists = session.query(Match.match_id).filter_by(match_id=match_data["match_id"]).first()
        if exists:
            print(f"Матч {match_data['match_id']} уже существует в БД, пропуск.")
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
        print(f"\nПромежуточное сохранение: {matches_added_count} новых матчей добавлено в БД.")
    except IntegrityError as e:
        print(f"Ошибка целостности данных при сохранении (возможно, дубликат): {e}")
        session.rollback()
    except Exception as e:
        print(f"Произошла ошибка при сохранении в БД: {e}")
        session.rollback()


def main():
    """
    Главная функция для сбора и сохранения данных матчей в БД.
    """
    session = SessionLocal()
    start = time.time()
    matches_counter = 0
    start_match_seq_num = START_MATCH_SEQ_NUM
    batch_matches = []
    steam_api_calls = 0

    try:
        while matches_counter < MAX_MATCHES:
            steam_api_calls += 1
            steam_matches = fetch_steam_matches(start_match_seq_num)

            if not steam_matches:
                print("Steam API | Матчи не найдены, завершение.")
                break

            start_match_seq_num = steam_matches[-1]["match_seq_num"] + 1

            filtered_matches, initial_excluded_first = filter_matches_initial(steam_matches)

            initial_excluded_second = 0
            for match in filtered_matches:
                processed_match = filter_matches_secondary(match)
                if processed_match:
                    batch_matches.append(processed_match)
                else:
                    initial_excluded_second += 1

            matches_counter += len(steam_matches) - initial_excluded_first - initial_excluded_second
            print(f"\nПроход API #{steam_api_calls} | Собрано {len(steam_matches)} матчей | "
                  f"Обработано {len(steam_matches) - initial_excluded_first - initial_excluded_second}")
            print(f"Первичная фильтрация: {initial_excluded_first} | Вторичная фильтрация: {initial_excluded_second}")
            print(f"Прогресс: {matches_counter} матчей обработано | Steam API")

            if len(batch_matches) >= CHUNK_SIZE:
                save_matches_to_db(session, batch_matches)
                batch_matches = []  # Очищаем буфер
                print(f"Время работы: {(time.time() - start) / 60:.2f} мин.\n")

            time.sleep(4)

        if batch_matches:
            save_matches_to_db(session, batch_matches)

    finally:
        session.close()
        print("\n" + "=" * 50)
        print("Сбор данных завершен.")
        print(f"Всего обработано матчей: {matches_counter}")
        print(f"Всего вызовов Steam API: {steam_api_calls}")
        print(f"Общее время работы: {(time.time() - start) / 60:.2f} мин.")
        print("=" * 50)


if __name__ == "__main__":
    main()
