import json
import time
from datetime import datetime
from collections import Counter, defaultdict
from sqlalchemy import create_engine, func, distinct, and_, or_
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError

# Импорт моделей
from data_base.models import Match, MatchPlayer, Base
from config import DATABASE_URL


# ANSI цвета для консоли
class Colors:
    CYAN = '\033[96m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    GREEN = '\033[92m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    RESET = '\033[0m'


def load_hero_data(file_path="../data/dota2_heroes_data.json"):
    """Загружает данные о героях из JSON файла."""
    try:
        with open(file_path, "r", encoding='utf-8') as file:
            return json.load(file)
    except FileNotFoundError:
        print(f"{Colors.YELLOW}⚠️  Файл с данными героев не найден: {file_path}{Colors.RESET}")
        return []
    except json.JSONDecodeError:
        print(f"{Colors.RED}❌ Ошибка чтения JSON файла: {file_path}{Colors.RESET}")
        return []


def get_hero_name_by_id(hero_id, heroes_data):
    """Получает имя героя по его ID."""
    for hero in heroes_data:
        if hero.get('id') == hero_id:
            return hero.get('localized_name', f'Unknown Hero {hero_id}')
    return f'Unknown Hero {hero_id}'


def convert_timestamp_to_date(timestamp):
    """Конвертирует timestamp в читаемую дату."""
    try:
        return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, OSError):
        return f"Invalid timestamp: {timestamp}"


def print_section_header(title):
    """Печатает заголовок секции."""
    print(f"\n{Colors.CYAN}{'=' * 80}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{title.center(80)}{Colors.RESET}")
    print(f"{Colors.CYAN}{'=' * 80}{Colors.RESET}")


def print_subsection_header(title):
    """Печатает заголовок подсекции."""
    print(f"\n{Colors.BLUE}{'─' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}📊 {title}{Colors.RESET}")
    print(f"{Colors.BLUE}{'─' * 60}{Colors.RESET}")


def check_data_integrity(session):
    """Проверяет целостность и валидность данных."""
    print_section_header("🔍 ПРОВЕРКА ЦЕЛОСТНОСТИ И ВАЛИДНОСТИ ДАННЫХ")

    integrity_issues = []

    # 1. Проверка уникальности match_id
    print(f"{Colors.YELLOW}🔍 Проверка уникальности match_id...{Colors.RESET}")
    duplicate_match_ids = session.query(Match.match_id, func.count(Match.match_id).label('count')) \
        .group_by(Match.match_id) \
        .having(func.count(Match.match_id) > 1) \
        .all()

    if duplicate_match_ids:
        print(f"{Colors.RED}❌ Найдены дублирующиеся match_id:{Colors.RESET}")
        for match_id, count in duplicate_match_ids[:10]:
            print(f"   Match ID {match_id}: {count} дубликатов")
        if len(duplicate_match_ids) > 10:
            print(f"   ... и еще {len(duplicate_match_ids) - 10} дубликатов")
        integrity_issues.append(f"Дублирующиеся match_id: {len(duplicate_match_ids)}")
    else:
        print(f"{Colors.GREEN}✅ Все match_id уникальны{Colors.RESET}")

    # 2. Проверка уникальности match_seq_num
    print(f"{Colors.YELLOW}🔍 Проверка уникальности match_seq_num...{Colors.RESET}")
    duplicate_seq_nums = session.query(Match.match_seq_num, func.count(Match.match_seq_num).label('count')) \
        .group_by(Match.match_seq_num) \
        .having(func.count(Match.match_seq_num) > 1) \
        .all()

    if duplicate_seq_nums:
        print(f"{Colors.RED}❌ Найдены дублирующиеся match_seq_num:{Colors.RESET}")
        for seq_num, count in duplicate_seq_nums[:10]:
            print(f"   Seq Num {seq_num}: {count} дубликатов")
        integrity_issues.append(f"Дублирующиеся match_seq_num: {len(duplicate_seq_nums)}")
    else:
        print(f"{Colors.GREEN}✅ Все match_seq_num уникальны{Colors.RESET}")

    # 3. Проверка NULL значений в критических полях Match
    print(f"{Colors.YELLOW}🔍 Проверка NULL значений в таблице Match...{Colors.RESET}")
    null_checks_match = [
        ('match_id', session.query(Match).filter(Match.match_id.is_(None)).count()),
        ('match_seq_num', session.query(Match).filter(Match.match_seq_num.is_(None)).count()),
        ('radiant_win', session.query(Match).filter(Match.radiant_win.is_(None)).count()),
        ('duration', session.query(Match).filter(Match.duration.is_(None)).count()),
        ('start_time', session.query(Match).filter(Match.start_time.is_(None)).count()),
    ]

    null_issues_match = [(field, count) for field, count in null_checks_match if count > 0]
    if null_issues_match:
        print(f"{Colors.RED}❌ Найдены NULL значения в таблице Match:{Colors.RESET}")
        for field, count in null_issues_match:
            print(f"   {field}: {count} записей")
        integrity_issues.extend([f"NULL в {field}: {count}" for field, count in null_issues_match])
    else:
        print(f"{Colors.GREEN}✅ Нет NULL значений в критических полях Match{Colors.RESET}")

    # 4. Проверка NULL значений в критических полях MatchPlayer
    print(f"{Colors.YELLOW}🔍 Проверка NULL значений в таблице MatchPlayer...{Colors.RESET}")
    null_checks_player = [
        ('match_id', session.query(MatchPlayer).filter(MatchPlayer.match_id.is_(None)).count()),
        ('account_id', session.query(MatchPlayer).filter(MatchPlayer.account_id.is_(None)).count()),
        ('hero_id', session.query(MatchPlayer).filter(MatchPlayer.hero_id.is_(None)).count()),
        ('team_number', session.query(MatchPlayer).filter(MatchPlayer.team_number.is_(None)).count()),
        ('role', session.query(MatchPlayer).filter(MatchPlayer.role.is_(None)).count()),
    ]

    null_issues_player = [(field, count) for field, count in null_checks_player if count > 0]
    if null_issues_player:
        print(f"{Colors.RED}❌ Найдены NULL значения в таблице MatchPlayer:{Colors.RESET}")
        for field, count in null_issues_player:
            print(f"   {field}: {count} записей")
        integrity_issues.extend([f"NULL в {field}: {count}" for field, count in null_issues_player])
    else:
        print(f"{Colors.GREEN}✅ Нет NULL значений в критических полях MatchPlayer{Colors.RESET}")

    # 5. Проверка корректности ролей
    print(f"{Colors.YELLOW}🔍 Проверка корректности ролей...{Colors.RESET}")
    invalid_roles = session.query(MatchPlayer.role, func.count(MatchPlayer.role)) \
        .filter(~MatchPlayer.role.in_(['core', 'support'])) \
        .group_by(MatchPlayer.role) \
        .all()

    if invalid_roles:
        print(f"{Colors.RED}❌ Найдены некорректные роли:{Colors.RESET}")
        for role, count in invalid_roles:
            print(f"   {role}: {count} записей")
        integrity_issues.append(f"Некорректные роли: {sum(count for _, count in invalid_roles)}")
    else:
        print(f"{Colors.GREEN}✅ Все роли корректны (core/support){Colors.RESET}")

    # 6. Проверка корректности team_number
    print(f"{Colors.YELLOW}🔍 Проверка корректности team_number...{Colors.RESET}")
    invalid_teams = session.query(MatchPlayer.team_number, func.count(MatchPlayer.team_number)) \
        .filter(~MatchPlayer.team_number.in_([0, 1])) \
        .group_by(MatchPlayer.team_number) \
        .all()

    if invalid_teams:
        print(f"{Colors.RED}❌ Найдены некорректные team_number:{Colors.RESET}")
        for team, count in invalid_teams:
            print(f"   {team}: {count} записей")
        integrity_issues.append(f"Некорректные team_number: {sum(count for _, count in invalid_teams)}")
    else:
        print(f"{Colors.GREEN}✅ Все team_number корректны (0/1){Colors.RESET}")

    # 7. Проверка количества игроков в матчах
    print(f"{Colors.YELLOW}🔍 Проверка количества игроков в матчах...{Colors.RESET}")
    matches_with_wrong_player_count = session.query(
        MatchPlayer.match_id,
        func.count(MatchPlayer.id).label('player_count')
    ).group_by(MatchPlayer.match_id) \
        .having(func.count(MatchPlayer.id) != 10) \
        .limit(10).all()

    if matches_with_wrong_player_count:
        total_wrong = session.query(MatchPlayer.match_id) \
            .group_by(MatchPlayer.match_id) \
            .having(func.count(MatchPlayer.id) != 10) \
            .count()
        print(f"{Colors.RED}❌ Найдены матчи с неправильным количеством игроков:{Colors.RESET}")
        for match_id, count in matches_with_wrong_player_count:
            print(f"   Match {match_id}: {count} игроков")
        print(f"   Всего таких матчей: {total_wrong}")
        integrity_issues.append(f"Матчи с неправильным количеством игроков: {total_wrong}")
    else:
        print(f"{Colors.GREEN}✅ Все матчи содержат 10 игроков{Colors.RESET}")

    # 8. Проверка связности данных (orphaned records)
    print(f"{Colors.YELLOW}🔍 Проверка связности данных...{Colors.RESET}")
    orphaned_players = session.query(func.count(MatchPlayer.id)) \
        .filter(~MatchPlayer.match_id.in_(session.query(Match.match_id))) \
        .scalar()

    if orphaned_players > 0:
        print(f"{Colors.RED}❌ Найдены игроки без соответствующих матчей: {orphaned_players}{Colors.RESET}")
        integrity_issues.append(f"Игроки-сироты: {orphaned_players}")
    else:
        print(f"{Colors.GREEN}✅ Все игроки связаны с существующими матчами{Colors.RESET}")

    # Итоговый результат проверки целостности
    print(f"\n{Colors.BOLD}📋 ИТОГИ ПРОВЕРКИ ЦЕЛОСТНОСТИ:{Colors.RESET}")
    if integrity_issues:
        print(f"{Colors.RED}❌ Обнаружены проблемы целостности данных:{Colors.RESET}")
        for issue in integrity_issues:
            print(f"   • {issue}")
    else:
        print(f"{Colors.GREEN}✅ Все проверки целостности пройдены успешно!{Colors.RESET}")

    return integrity_issues


def analyze_matches_statistics(session, heroes_data):
    """Анализирует статистику матчей."""
    print_section_header("📊 СТАТИСТИКА МАТЧЕЙ")

    # Общее количество матчей
    total_matches = session.query(func.count(Match.match_id)).scalar()
    print(f"{Colors.GREEN}📈 Общее количество матчей: {Colors.BOLD}{total_matches:,}{Colors.RESET}")

    if total_matches == 0:
        print(f"{Colors.RED}❌ Нет данных для анализа{Colors.RESET}")
        return

    # Диапазоны основных полей
    print_subsection_header("🔢 Диапазоны основных полей")

    # Первый и последний матч по Sequence Number (порядок сбора)
    first_match_by_seq = session.query(Match).order_by(Match.match_seq_num.asc()).first()
    last_match_by_seq = session.query(Match).order_by(Match.match_seq_num.desc()).first()

    # Минимальные и максимальные значения
    range_stats = session.query(
        func.min(Match.match_id).label('min_match_id'),
        func.max(Match.match_id).label('max_match_id'),
        func.min(Match.match_seq_num).label('min_seq'),
        func.max(Match.match_seq_num).label('max_seq'),
        func.min(Match.start_time).label('min_time'),
        func.max(Match.start_time).label('max_time')
    ).first()

    if first_match_by_seq and last_match_by_seq and range_stats:
        # Граничные в БД (по порядку сбора)
        print(
            f"🔢 Sequence Number (граничные): {Colors.YELLOW}{first_match_by_seq.match_seq_num:,}{Colors.RESET} - {Colors.YELLOW}{last_match_by_seq.match_seq_num:,}{Colors.RESET}")
        print(
            f"🆔 Match ID (граничные): {Colors.YELLOW}{first_match_by_seq.match_id:,}{Colors.RESET} - {Colors.YELLOW}{last_match_by_seq.match_id:,}{Colors.RESET}")

        first_date = convert_timestamp_to_date(first_match_by_seq.start_time)
        last_date = convert_timestamp_to_date(last_match_by_seq.start_time)
        print(
            f"🕐 Время (граничные): {Colors.YELLOW}{first_date}{Colors.RESET} - {Colors.YELLOW}{last_date}{Colors.RESET}")

        # Минимальные и максимальные значения
        print(
            f"🔢 Sequence Number (мин/макс): {Colors.CYAN}{range_stats.min_seq:,}{Colors.RESET} - {Colors.CYAN}{range_stats.max_seq:,}{Colors.RESET}")
        print(
            f"🆔 Match ID (мин/макс): {Colors.CYAN}{range_stats.min_match_id:,}{Colors.RESET} - {Colors.CYAN}{range_stats.max_match_id:,}{Colors.RESET}")

        min_date = convert_timestamp_to_date(range_stats.min_time)
        max_date = convert_timestamp_to_date(range_stats.max_time)
        print(f"🕐 Время (мин/макс): {Colors.CYAN}{min_date}{Colors.RESET} - {Colors.CYAN}{max_date}{Colors.RESET}")

    else:
        print(f"{Colors.RED}❌ Не удалось получить данные о диапазонах{Colors.RESET}")

    # Длительность матчей
    duration_stats = session.query(
        func.min(Match.duration).label('min_duration'),
        func.max(Match.duration).label('max_duration'),
        func.avg(Match.duration).label('avg_duration')
    ).first()
    print(
        f"⏱️  Длительность: {Colors.YELLOW}{duration_stats.min_duration // 60}:{duration_stats.min_duration % 60:02d}{Colors.RESET} - {Colors.YELLOW}{duration_stats.max_duration // 60}:{duration_stats.max_duration % 60:02d}{Colors.RESET} (среднее: {Colors.YELLOW}{int(duration_stats.avg_duration) // 60}:{int(duration_stats.avg_duration) % 60:02d}{Colors.RESET})")

    # Статистика побед
    print_subsection_header("🏆 Статистика побед")
    win_stats = session.query(
        Match.radiant_win,
        func.count(Match.radiant_win).label('count')
    ).group_by(Match.radiant_win).all()

    for is_radiant_win, count in win_stats:
        side = "Radiant" if is_radiant_win else "Dire"
        percentage = (count / total_matches) * 100
        print(f"🌅 {side} побед: {Colors.YELLOW}{count:,}{Colors.RESET} ({Colors.GREEN}{percentage:.1f}%{Colors.RESET})")

    # Режимы игры
    print_subsection_header("🎮 Режимы игры")
    game_mode_stats = session.query(
        Match.game_mode,
        func.count(Match.game_mode).label('count')
    ).group_by(Match.game_mode).order_by(func.count(Match.game_mode).desc()).all()

    for mode, count in game_mode_stats:
        percentage = (count / total_matches) * 100
        print(f"🎯 Режим {mode}: {Colors.YELLOW}{count:,}{Colors.RESET} ({Colors.GREEN}{percentage:.1f}%{Colors.RESET})")

    # Типы лобби
    print_subsection_header("🏠 Типы лобби")
    lobby_stats = session.query(
        Match.lobby_type,
        func.count(Match.lobby_type).label('count')
    ).group_by(Match.lobby_type).order_by(func.count(Match.lobby_type).desc()).all()

    for lobby, count in lobby_stats:
        percentage = (count / total_matches) * 100
        print(
            f"🏛️  Лобби {lobby}: {Colors.YELLOW}{count:,}{Colors.RESET} ({Colors.GREEN}{percentage:.1f}%{Colors.RESET})")

    # Статистика счета
    print_subsection_header("🎯 Статистика счета")
    score_stats = session.query(
        func.min(Match.radiant_score).label('min_rad'),
        func.max(Match.radiant_score).label('max_rad'),
        func.avg(Match.radiant_score).label('avg_rad'),
        func.min(Match.dire_score).label('min_dire'),
        func.max(Match.dire_score).label('max_dire'),
        func.avg(Match.dire_score).label('avg_dire')
    ).first()

    print(
        f"🌅 Radiant счет: {Colors.YELLOW}{score_stats.min_rad}{Colors.RESET} - {Colors.YELLOW}{score_stats.max_rad}{Colors.RESET} (среднее: {Colors.YELLOW}{score_stats.avg_rad:.1f}{Colors.RESET})")
    print(
        f"🌙 Dire счет: {Colors.YELLOW}{score_stats.min_dire}{Colors.RESET} - {Colors.YELLOW}{score_stats.max_dire}{Colors.RESET} (среднее: {Colors.YELLOW}{score_stats.avg_dire:.1f}{Colors.RESET})")

    # Анализ структур (башни и казармы)
    print_subsection_header("🏗️  Статистика структур")

    # Определяем структуры для анализа (в нужном порядке)
    tower_names = [
        # Tier 1 башни
        "Top Tier 1", "Middle Tier 1", "Bottom Tier 1",
        # Tier 2 башни
        "Top Tier 2", "Middle Tier 2", "Bottom Tier 2",
        # Tier 3 башни
        "Top Tier 3", "Middle Tier 3", "Bottom Tier 3",
        # Ancient башни
        "Ancient Top", "Ancient Bottom"
    ]

    # Соответствующие битовые позиции для нового порядка
    tower_bit_positions = [
        # Tier 1 (позиции 0, 3, 6)
        0, 3, 6,
        # Tier 2 (позиции 1, 4, 7)
        1, 4, 7,
        # Tier 3 (позиции 2, 5, 8)
        2, 5, 8,
        # Ancient (позиции 9, 10)
        9, 10
    ]

    barracks_names = [
        "Top Melee", "Top Ranged", "Middle Melee",
        "Middle Ranged", "Bottom Melee", "Bottom Ranged"
    ]

    # Получаем все записи матчей для анализа структур
    matches_structures = session.query(
        Match.tower_status_radiant,
        Match.tower_status_dire,
        Match.barracks_status_radiant,
        Match.barracks_status_dire
    ).all()

    total_matches_struct = len(matches_structures)

    if total_matches_struct > 0:
        # Анализ башен
        print(f"\n🗼 {Colors.BOLD}АНАЛИЗ БАШЕН{Colors.RESET} (битовые маски):")

        # Подсчет уничтоженных башен для каждой команды
        for team_name, tower_field in [("Radiant", 0), ("Dire", 1)]:
            team_emoji = "🌅" if team_name == "Radiant" else "🌙"
            print(f"\n{team_emoji} {Colors.BOLD}{team_name}{Colors.RESET}:")

            for tower_name, bit_pos in zip(tower_names, tower_bit_positions):
                destroyed_count = 0
                for match in matches_structures:
                    tower_status = match[tower_field]  # 0 для radiant, 1 для dire
                    # Если бит установлен в 1, то башня ЕЩЕ СТОИТ
                    # Если бит 0, то башня УНИЧТОЖЕНА
                    if not (tower_status & (1 << bit_pos)):
                        destroyed_count += 1

                destroyed_percentage = (destroyed_count / total_matches_struct) * 100
                standing_count = total_matches_struct - destroyed_count
                standing_percentage = 100 - destroyed_percentage

                print(
                    f"   🏗️  {tower_name}: {Colors.RED}уничтожена {destroyed_count:,} раз ({destroyed_percentage:.1f}%){Colors.RESET}, {Colors.GREEN}цела {standing_count:,} раз ({standing_percentage:.1f}%){Colors.RESET}")

        # Анализ казарм
        print(f"\n🏰 {Colors.BOLD}АНАЛИЗ КАЗАРМ{Colors.RESET} (битовые маски):")

        for team_name, barracks_field in [("Radiant", 2), ("Dire", 3)]:
            team_emoji = "🌅" if team_name == "Radiant" else "🌙"
            print(f"\n{team_emoji} {Colors.BOLD}{team_name}{Colors.RESET}:")

            for bit_pos, barracks_name in enumerate(barracks_names):
                destroyed_count = 0
                for match in matches_structures:
                    barracks_status = match[barracks_field]  # 2 для radiant, 3 для dire
                    # Если бит установлен в 1, то казарма ЕЩЕ СТОИТ
                    # Если бит 0, то казарма УНИЧТОЖЕНА
                    if not (barracks_status & (1 << bit_pos)):
                        destroyed_count += 1

                destroyed_percentage = (destroyed_count / total_matches_struct) * 100
                standing_count = total_matches_struct - destroyed_count
                standing_percentage = 100 - destroyed_percentage

                print(
                    f"   🏛️  {barracks_name}: {Colors.RED}уничтожена {destroyed_count:,} раз ({destroyed_percentage:.1f}%){Colors.RESET}, {Colors.GREEN}цела {standing_count:,} раз ({standing_percentage:.1f}%){Colors.RESET}")

        # Общая статистика по количеству уничтоженных структур
        print(f"\n📊 {Colors.BOLD}ОБЩАЯ СТАТИСТИКА РАЗРУШЕНИЙ{Colors.RESET}:")

        # Подсчет среднего количества уничтоженных башен
        radiant_towers_destroyed = []
        dire_towers_destroyed = []
        radiant_barracks_destroyed = []
        dire_barracks_destroyed = []

        for match in matches_structures:
            # Подсчет уничтоженных башен (инвертируем биты)
            rad_towers = bin(match[0] ^ 0x7FF).count('1') if match[0] is not None else 0  # 0x7FF = 11 единиц
            dire_towers = bin(match[1] ^ 0x7FF).count('1') if match[1] is not None else 0
            radiant_towers_destroyed.append(rad_towers)
            dire_towers_destroyed.append(dire_towers)

            # Подсчет уничтоженных казарм (инвертируем биты)
            rad_barracks = bin(match[2] ^ 0x3F).count('1') if match[2] is not None else 0  # 0x3F = 6 единиц
            dire_barracks = bin(match[3] ^ 0x3F).count('1') if match[3] is not None else 0
            radiant_barracks_destroyed.append(rad_barracks)
            dire_barracks_destroyed.append(dire_barracks)

        avg_rad_towers = sum(radiant_towers_destroyed) / len(radiant_towers_destroyed)
        avg_dire_towers = sum(dire_towers_destroyed) / len(dire_towers_destroyed)
        avg_rad_barracks = sum(radiant_barracks_destroyed) / len(radiant_barracks_destroyed)
        avg_dire_barracks = sum(dire_barracks_destroyed) / len(dire_barracks_destroyed)

        print(
            f"🌅 Radiant - Среднее башен уничтожено: {Colors.YELLOW}{avg_rad_towers:.1f}/11{Colors.RESET}, казарм: {Colors.YELLOW}{avg_rad_barracks:.1f}/6{Colors.RESET}")
        print(
            f"🌙 Dire - Среднее башен уничтожено: {Colors.YELLOW}{avg_dire_towers:.1f}/11{Colors.RESET}, казарм: {Colors.YELLOW}{avg_dire_barracks:.1f}/6{Colors.RESET}")

    else:
        print(f"{Colors.RED}❌ Нет данных о структурах для анализа{Colors.RESET}")


def analyze_players_statistics(session, heroes_data):
    """Анализирует статистику игроков."""
    print_section_header("👥 СТАТИСТИКА ИГРОКОВ")

    # Общее количество записей игроков и уникальных игроков
    total_player_records = session.query(func.count(MatchPlayer.id)).scalar()

    # Подсчет приватных аккаунтов (ID = 4294967295)
    private_accounts_count = session.query(func.count(MatchPlayer.id)).filter(
        MatchPlayer.account_id == 4294967295).scalar()

    # Уникальные игроки исключая приватные аккаунты
    unique_public_players = session.query(func.count(distinct(MatchPlayer.account_id))).filter(
        MatchPlayer.account_id != 4294967295).scalar()

    # Общее количество уникальных account_id (включая приватные)
    total_unique_accounts = session.query(func.count(distinct(MatchPlayer.account_id))).scalar()

    print(f"{Colors.GREEN}📊 Общее количество записей игроков: {Colors.BOLD}{total_player_records:,}{Colors.RESET}")
    print(f"{Colors.GREEN}👤 Уникальных публичных игроков: {Colors.BOLD}{unique_public_players:,}{Colors.RESET}")
    print(
        f"{Colors.YELLOW}🔒 Записей с приватными аккаунтами: {Colors.BOLD}{private_accounts_count:,}{Colors.RESET} ({(private_accounts_count / total_player_records) * 100:.1f}% от всех записей)")
    print(
        f"{Colors.BLUE}📈 Всего уникальных account_id: {Colors.BOLD}{total_unique_accounts:,}{Colors.RESET} (включая приватные)")

    private_percentage = (private_accounts_count / total_player_records) * 100 if total_player_records > 0 else 0
    if private_percentage > 10:
        print(
            f"{Colors.YELLOW}⚠️  Высокий процент приватных аккаунтов может влиять на точность анализа уникальных игроков{Colors.RESET}")

    if total_player_records == 0:
        print(f"{Colors.RED}❌ Нет данных игроков для анализа{Colors.RESET}")
        return

    # Статистика KDA
    print_subsection_header("⚔️ Статистика KDA")
    kda_stats = session.query(
        func.min(MatchPlayer.kills).label('min_kills'),
        func.max(MatchPlayer.kills).label('max_kills'),
        func.avg(MatchPlayer.kills).label('avg_kills'),
        func.min(MatchPlayer.deaths).label('min_deaths'),
        func.max(MatchPlayer.deaths).label('max_deaths'),
        func.avg(MatchPlayer.deaths).label('avg_deaths'),
        func.min(MatchPlayer.assists).label('min_assists'),
        func.max(MatchPlayer.assists).label('max_assists'),
        func.avg(MatchPlayer.assists).label('avg_assists'),
        func.avg(MatchPlayer.kda).label('avg_kda')
    ).first()

    print(
        f"🗡️  Kills: {Colors.YELLOW}{kda_stats.min_kills}{Colors.RESET} - {Colors.YELLOW}{kda_stats.max_kills}{Colors.RESET} (среднее: {Colors.YELLOW}{kda_stats.avg_kills:.1f}{Colors.RESET})")
    print(
        f"💀 Deaths: {Colors.YELLOW}{kda_stats.min_deaths}{Colors.RESET} - {Colors.YELLOW}{kda_stats.max_deaths}{Colors.RESET} (среднее: {Colors.YELLOW}{kda_stats.avg_deaths:.1f}{Colors.RESET})")
    print(
        f"🤝 Assists: {Colors.YELLOW}{kda_stats.min_assists}{Colors.RESET} - {Colors.YELLOW}{kda_stats.max_assists}{Colors.RESET} (среднее: {Colors.YELLOW}{kda_stats.avg_assists:.1f}{Colors.RESET})")
    print(f"📈 Средний KDA: {Colors.YELLOW}{kda_stats.avg_kda:.2f}{Colors.RESET}")

    # Статистика по ролям
    print_subsection_header("🎭 Статистика по ролям")
    role_stats = session.query(
        MatchPlayer.role,
        func.count(MatchPlayer.role).label('count'),
        func.avg(MatchPlayer.kills).label('avg_kills'),
        func.avg(MatchPlayer.deaths).label('avg_deaths'),
        func.avg(MatchPlayer.assists).label('avg_assists'),
        func.avg(MatchPlayer.kda).label('avg_kda'),
        func.avg(MatchPlayer.gold_per_min).label('avg_gpm'),
        func.avg(MatchPlayer.xp_per_min).label('avg_xpm'),
        func.avg(MatchPlayer.last_hits).label('avg_lh'),
        func.avg(MatchPlayer.net_worth).label('avg_nw')
    ).group_by(MatchPlayer.role).all()

    for role_data in role_stats:
        role = role_data.role
        count = role_data.count
        percentage = (count / total_player_records) * 100

        print(
            f"\n🎪 {Colors.BOLD}{role.upper()}{Colors.RESET} ({Colors.GREEN}{count:,} записей, {percentage:.1f}%{Colors.RESET}):")
        print(
            f"   ⚔️  K/D/A: {role_data.avg_kills:.1f}/{role_data.avg_deaths:.1f}/{role_data.avg_assists:.1f} (KDA: {role_data.avg_kda:.2f})")
        print(f"   💰 GPM: {role_data.avg_gpm:.0f} | XPM: {role_data.avg_xpm:.0f}")
        print(f"   🏹 Last Hits: {role_data.avg_lh:.0f} | Net Worth: {role_data.avg_nw:.0f}")

    # Статистика по героям (топ-20)
    print_subsection_header("🦸 Топ-20 популярных героев")
    hero_stats = session.query(
        MatchPlayer.hero_id,
        func.count(MatchPlayer.hero_id).label('count'),
        func.avg(MatchPlayer.kda).label('avg_kda')
    ).group_by(MatchPlayer.hero_id) \
        .order_by(func.count(MatchPlayer.hero_id).desc()) \
        .limit(20).all()

    for i, (hero_id, count, avg_kda) in enumerate(hero_stats, 1):
        hero_name = get_hero_name_by_id(hero_id, heroes_data)
        percentage = (count / total_player_records) * 100
        print(
            f"{i:2d}. {Colors.BLUE}{hero_name}{Colors.RESET} (ID: {hero_id}): {Colors.YELLOW}{count:,}{Colors.RESET} игр ({Colors.GREEN}{percentage:.1f}%{Colors.RESET}), KDA: {Colors.YELLOW}{avg_kda:.2f}{Colors.RESET}")

    # Анализ винрейта героев (топ-20 по винрейту, минимум 100 игр)
    print_subsection_header("🏆 Топ-20 героев по винрейту (мин. 100 игр)")

    try:
        # Получаем всех игроков с их матчами для расчета винрейта
        players_matches = session.query(
            MatchPlayer.hero_id,
            MatchPlayer.team_number,
            Match.radiant_win
        ).join(Match, MatchPlayer.match_id == Match.match_id).all()

        # Группируем по героям и считаем винрейт
        hero_stats = defaultdict(lambda: {'total': 0, 'wins': 0})

        for hero_id, team_number, radiant_win in players_matches:
            hero_stats[hero_id]['total'] += 1
            # Игрок выиграл если: он в radiant и radiant_win=True ИЛИ он в dire и radiant_win=False
            if (team_number == 0 and radiant_win) or (team_number == 1 and not radiant_win):
                hero_stats[hero_id]['wins'] += 1

        # Фильтруем героев с минимум 100 играми и вычисляем винрейт
        hero_winrates = []
        for hero_id, stats in hero_stats.items():
            if stats['total'] >= 100:
                winrate = (stats['wins'] * 100.0) / stats['total']
                hero_winrates.append((hero_id, stats['total'], stats['wins'], winrate))

        # Сортируем по винрейту
        hero_winrates.sort(key=lambda x: x[3], reverse=True)

        # Выводим топ-20
        for i, (hero_id, total_games, wins, winrate) in enumerate(hero_winrates[:20], 1):
            hero_name = get_hero_name_by_id(hero_id, heroes_data)
            print(
                f"{i:2d}. {Colors.BLUE}{hero_name}{Colors.RESET}: {Colors.GREEN}{winrate:.1f}%{Colors.RESET} ({wins}/{total_games})")

        if not hero_winrates:
            print(f"{Colors.YELLOW}⚠️  Нет героев с минимум 100 играми{Colors.RESET}")

    except Exception as e:
        print(f"{Colors.RED}❌ Ошибка при расчете винрейта героев: {e}{Colors.RESET}")
        print(f"{Colors.YELLOW}ℹ️  Пропускаем анализ винрейта героев{Colors.RESET}")

    # Анализ винрейта героев с учетом вариантов (топ-20 по винрейту, минимум 50 игр)
    print_subsection_header("🎨 Топ-20 героев по винрейту (с вариантами, мин. 50 игр)")

    try:
        # Получаем всех игроков с их матчами для расчета винрейта по вариантам
        players_matches_variants = session.query(
            MatchPlayer.hero_id,
            MatchPlayer.hero_variant,
            MatchPlayer.team_number,
            Match.radiant_win
        ).join(Match, MatchPlayer.match_id == Match.match_id).all()

        # Группируем по героям И вариантам и считаем винрейт
        hero_variant_stats = defaultdict(lambda: {'total': 0, 'wins': 0})

        for hero_id, hero_variant, team_number, radiant_win in players_matches_variants:
            key = (hero_id, hero_variant)
            hero_variant_stats[key]['total'] += 1
            # Игрок выиграл если: он в radiant и radiant_win=True ИЛИ он в dire и radiant_win=False
            if (team_number == 0 and radiant_win) or (team_number == 1 and not radiant_win):
                hero_variant_stats[key]['wins'] += 1

        # Фильтруем комбинации с минимум 50 играми и вычисляем винрейт
        hero_variant_winrates = []
        for (hero_id, hero_variant), stats in hero_variant_stats.items():
            if stats['total'] >= 50:
                winrate = (stats['wins'] * 100.0) / stats['total']
                hero_variant_winrates.append((hero_id, hero_variant, stats['total'], stats['wins'], winrate))

        # Сортируем по винрейту
        hero_variant_winrates.sort(key=lambda x: x[4], reverse=True)

        # Выводим топ-20
        for i, (hero_id, hero_variant, total_games, wins, winrate) in enumerate(hero_variant_winrates[:20], 1):
            hero_name = get_hero_name_by_id(hero_id, heroes_data)
            variant_text = f" (вариант {hero_variant})" if hero_variant > 0 else ""
            print(
                f"{i:2d}. {Colors.BLUE}{hero_name}{variant_text}{Colors.RESET}: {Colors.GREEN}{winrate:.1f}%{Colors.RESET} ({wins}/{total_games})")

        if not hero_variant_winrates:
            print(f"{Colors.YELLOW}⚠️  Нет героев с вариантами с минимум 50 играми{Colors.RESET}")

    except Exception as e:
        print(f"{Colors.RED}❌ Ошибка при расчете винрейта героев с вариантами: {e}{Colors.RESET}")
        print(f"{Colors.YELLOW}ℹ️  Пропускаем анализ винрейта с вариантами{Colors.RESET}")

    # Статистика по вариантам героев
    print_subsection_header("🎨 Статистика вариантов героев")
    variant_stats = session.query(
        MatchPlayer.hero_variant,
        func.count(MatchPlayer.hero_variant).label('count')
    ).group_by(MatchPlayer.hero_variant) \
        .order_by(func.count(MatchPlayer.hero_variant).desc()).all()

    print(f"🎭 Всего различных вариантов: {Colors.YELLOW}{len(variant_stats)}{Colors.RESET}")
    for variant, count in variant_stats[:10]:
        percentage = (count / total_player_records) * 100
        print(
            f"   Вариант {variant}: {Colors.YELLOW}{count:,}{Colors.RESET} ({Colors.GREEN}{percentage:.1f}%{Colors.RESET})")

    # Экономическая статистика
    print_subsection_header("💰 Экономическая статистика")
    eco_stats = session.query(
        func.min(MatchPlayer.gold_per_min).label('min_gpm'),
        func.max(MatchPlayer.gold_per_min).label('max_gpm'),
        func.avg(MatchPlayer.gold_per_min).label('avg_gpm'),
        func.min(MatchPlayer.xp_per_min).label('min_xpm'),
        func.max(MatchPlayer.xp_per_min).label('max_xpm'),
        func.avg(MatchPlayer.xp_per_min).label('avg_xpm'),
        func.min(MatchPlayer.last_hits).label('min_lh'),
        func.max(MatchPlayer.last_hits).label('max_lh'),
        func.avg(MatchPlayer.last_hits).label('avg_lh'),
        func.min(MatchPlayer.net_worth).label('min_nw'),
        func.max(MatchPlayer.net_worth).label('max_nw'),
        func.avg(MatchPlayer.net_worth).label('avg_nw')
    ).first()

    print(
        f"💰 GPM: {Colors.YELLOW}{eco_stats.min_gpm}{Colors.RESET} - {Colors.YELLOW}{eco_stats.max_gpm}{Colors.RESET} (среднее: {Colors.YELLOW}{eco_stats.avg_gpm:.0f}{Colors.RESET})")
    print(
        f"⭐ XPM: {Colors.YELLOW}{eco_stats.min_xpm}{Colors.RESET} - {Colors.YELLOW}{eco_stats.max_xpm}{Colors.RESET} (среднее: {Colors.YELLOW}{eco_stats.avg_xpm:.0f}{Colors.RESET})")
    print(
        f"🏹 Last Hits: {Colors.YELLOW}{eco_stats.min_lh}{Colors.RESET} - {Colors.YELLOW}{eco_stats.max_lh}{Colors.RESET} (среднее: {Colors.YELLOW}{eco_stats.avg_lh:.0f}{Colors.RESET})")
    print(
        f"💎 Net Worth: {Colors.YELLOW}{eco_stats.min_nw:,}{Colors.RESET} - {Colors.YELLOW}{eco_stats.max_nw:,}{Colors.RESET} (среднее: {Colors.YELLOW}{eco_stats.avg_nw:,.0f}{Colors.RESET})")

    # Статистика уровней
    print_subsection_header("📊 Статистика уровней")

    try:
        # Детальная статистика по всем уровням
        level_distribution = session.query(
            MatchPlayer.level,
            func.count(MatchPlayer.level).label('count')
        ).group_by(MatchPlayer.level) \
            .order_by(MatchPlayer.level).all()

        print(f"📈 {Colors.BOLD}Распределение по уровням:{Colors.RESET}")
        for level, count in level_distribution:
            percentage = (count / total_player_records) * 100
            print(
                f"   Уровень {level}: {Colors.YELLOW}{count:,}{Colors.RESET} ({Colors.GREEN}{percentage:.1f}%{Colors.RESET})")

        # Средний уровень по ролям
        role_level_stats = session.query(
            MatchPlayer.role,
            func.avg(MatchPlayer.level).label('avg_level'),
            func.min(MatchPlayer.level).label('min_level'),
            func.max(MatchPlayer.level).label('max_level')
        ).group_by(MatchPlayer.role).all()

        print(f"\n📊 {Colors.BOLD}Статистика уровней по ролям:{Colors.RESET}")
        for role_data in role_level_stats:
            role = role_data.role
            avg_level = role_data.avg_level
            min_level = role_data.min_level
            max_level = role_data.max_level

            role_emoji = "⚔️" if role == "core" else "🛡️"
            print(
                f"   {role_emoji} {Colors.BOLD}{role.upper()}{Colors.RESET}: среднее {Colors.YELLOW}{avg_level:.1f}{Colors.RESET}, диапазон {Colors.YELLOW}{min_level}-{max_level}{Colors.RESET}")

        # Общая статистика уровней
        overall_level_stats = session.query(
            func.min(MatchPlayer.level).label('min_level'),
            func.max(MatchPlayer.level).label('max_level'),
            func.avg(MatchPlayer.level).label('avg_level')
        ).first()

        print(
            f"\n🎯 {Colors.BOLD}Общая статистика:{Colors.RESET} среднее {Colors.YELLOW}{overall_level_stats.avg_level:.1f}{Colors.RESET}, диапазон {Colors.YELLOW}{overall_level_stats.min_level}-{overall_level_stats.max_level}{Colors.RESET}")

    except Exception as e:
        print(f"{Colors.RED}❌ Ошибка при анализе уровней: {e}{Colors.RESET}")
        # Fallback к простой статистике
        level_stats = session.query(
            func.min(MatchPlayer.level).label('min_level'),
            func.max(MatchPlayer.level).label('max_level'),
            func.avg(MatchPlayer.level).label('avg_level')
        ).first()
        print(
            f"📈 Уровень: {Colors.YELLOW}{level_stats.min_level}{Colors.RESET} - {Colors.YELLOW}{level_stats.max_level}{Colors.RESET} (среднее: {Colors.YELLOW}{level_stats.avg_level:.1f}{Colors.RESET})")

    # Статистика Aghanim's предметов
    print_subsection_header("🔮 Статистика Aghanim's предметов")

    try:
        # Подсчет предметов простыми запросами
        scepter_count = session.query(func.count(MatchPlayer.id)).filter(MatchPlayer.aghanims_scepter > 0).scalar()
        shard_count = session.query(func.count(MatchPlayer.id)).filter(MatchPlayer.aghanims_shard > 0).scalar()
        moonshard_count = session.query(func.count(MatchPlayer.id)).filter(MatchPlayer.moonshard > 0).scalar()

        scepter_pct = (scepter_count / total_player_records) * 100 if scepter_count else 0
        shard_pct = (shard_count / total_player_records) * 100 if shard_count else 0
        moonshard_pct = (moonshard_count / total_player_records) * 100 if moonshard_count else 0

        print(
            f"🔱 Aghanim's Scepter: {Colors.YELLOW}{scepter_count:,}{Colors.RESET} ({Colors.GREEN}{scepter_pct:.1f}%{Colors.RESET})")
        print(
            f"💎 Aghanim's Shard: {Colors.YELLOW}{shard_count:,}{Colors.RESET} ({Colors.GREEN}{shard_pct:.1f}%{Colors.RESET})")
        print(
            f"🌙 Moon Shard: {Colors.YELLOW}{moonshard_count:,}{Colors.RESET} ({Colors.GREEN}{moonshard_pct:.1f}%{Colors.RESET})")

    except Exception as e:
        print(f"{Colors.RED}❌ Ошибка при анализе Aghanim's предметов: {e}{Colors.RESET}")
        print(f"{Colors.YELLOW}ℹ️  Пропускаем анализ предметов{Colors.RESET}")


def data_check(database_url=None, heroes_data_path="../data/dota2_heroes_data.json"):
    """
    Основная функция для комплексной проверки и анализа датасета Dota 2.

    Args:
        database_url (str): URL базы данных. Если None, используется DATABASE_URL из config
        heroes_data_path (str): Путь к файлу с данными героев
    """
    start_time = time.time()

    # Заголовок программы
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}🔍 КОМПЛЕКСНАЯ ПРОВЕРКА И АНАЛИЗ ДАТАСЕТА DOTA 2{Colors.RESET}".center(100))
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.RESET}")

    # Подключение к базе данных
    try:
        db_url = database_url or DATABASE_URL
        engine = create_engine(db_url)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session = SessionLocal()

        print(f"{Colors.GREEN}✅ Подключение к базе данных установлено{Colors.RESET}")

        # Загрузка данных о героях
        heroes_data = load_hero_data(heroes_data_path)
        print(f"{Colors.GREEN}✅ Загружено данных о {len(heroes_data)} героях{Colors.RESET}")

        # Проверка существования таблиц
        try:
            matches_count = session.query(func.count(Match.match_id)).scalar()
            players_count = session.query(func.count(MatchPlayer.id)).scalar()
            print(f"{Colors.GREEN}✅ Найдено {matches_count:,} матчей и {players_count:,} записей игроков{Colors.RESET}")
        except Exception as e:
            print(f"{Colors.RED}❌ Ошибка при проверке таблиц: {e}{Colors.RESET}")
            return

        if matches_count == 0 and players_count == 0:
            print(f"{Colors.RED}❌ База данных пуста, нет данных для анализа{Colors.RESET}")
            return

        # Выполнение проверок и анализа
        print(f"\n{Colors.YELLOW}🚀 Начинаем комплексный анализ...{Colors.RESET}")

        # 1. Проверка целостности данных
        integrity_issues = check_data_integrity(session)

        # 2. Анализ статистики матчей
        analyze_matches_statistics(session, heroes_data)

        # 3. Анализ статистики игроков
        analyze_players_statistics(session, heroes_data)

        # Итоговый отчет
        end_time = time.time()
        execution_time = end_time - start_time

        print_section_header("📋 ИТОГОВЫЙ ОТЧЕТ")

        print(f"⏱️  {Colors.YELLOW}Время выполнения анализа: {execution_time:.2f} секунд{Colors.RESET}")
        print(f"📊 {Colors.GREEN}Проанализировано матчей: {matches_count:,}{Colors.RESET}")
        print(f"👥 {Colors.GREEN}Проанализировано записей игроков: {players_count:,}{Colors.RESET}")

        if integrity_issues:
            print(f"\n⚠️  {Colors.RED}ВНИМАНИЕ! Обнаружены проблемы целостности данных:{Colors.RESET}")
            for issue in integrity_issues:
                print(f"   • {Colors.RED}{issue}{Colors.RESET}")
            print(
                f"\n{Colors.YELLOW}Рекомендуется исправить эти проблемы перед использованием данных для анализа.{Colors.RESET}")
        else:
            print(f"\n✅ {Colors.GREEN}Все проверки целостности пройдены успешно!{Colors.RESET}")
            print(f"✅ {Colors.GREEN}Данные готовы для использования в анализе и машинном обучении.{Colors.RESET}")

        print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}🎉 АНАЛИЗ ЗАВЕРШЕН{Colors.RESET}".center(100))
        print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 100}{Colors.RESET}")

    except SQLAlchemyError as e:
        print(f"{Colors.RED}❌ Ошибка базы данных: {e}{Colors.RESET}")
    except Exception as e:
        print(f"{Colors.RED}❌ Неожиданная ошибка: {e}{Colors.RESET}")
    finally:
        try:
            session.close()
            print(f"{Colors.GREEN}✅ Соединение с базой данных закрыто{Colors.RESET}")
        except:
            pass


if __name__ == "__main__":
    # Пример использования
    data_check()
