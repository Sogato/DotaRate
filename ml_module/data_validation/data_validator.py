import time
from datetime import datetime
from collections import defaultdict
from sqlalchemy import create_engine, func, distinct
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError

# Импорт моделей базы данных
from data_bases.dataset.models import Match, MatchPlayer
from data_bases.heroes.models import Hero
from config import (DATASET_DATABASE_URL, HEROES_DATABASE_URL, PRIVATE_ACCOUNT_ID, MINIMUM_GAMES_FOR_HERO_WINRATE,
                    MINIMUM_GAMES_FOR_HERO_VARIANT_WINRATE, TOP_HEROES_DISPLAY_COUNT, TOWERS_BITMASK, BARRACKS_BITMASK)

# Импорт утилит для консольного вывода
from utils.console import (
    Colors,
    print_section_header,
    print_subsection_header,
    print_info_line,
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
        print_status_message("Загрузка данных героев из базы данных...", "info", "📚")

        # Получаем всех героев из БД
        heroes = heroes_session.query(Hero).all()

        if not heroes:
            print_status_message("ВНИМАНИЕ: База данных героев пуста!", "warning", "⚠️")
            return False

        # Заполняем кэш словарем {id: localized_name}
        for hero in heroes:
            HEROES_CACHE[hero.id] = hero.localized_name

        print_status_message(f"Загружено {len(HEROES_CACHE)} героев в кэш", "success", "✅")
        return True

    except Exception as e:
        print_status_message(f"Ошибка при загрузке героев из БД: {e}", "error", "❌")
        return False

    finally:
        heroes_session.close()


def get_hero_name_by_id(hero_id: int) -> str:
    """
    Получает локализованное имя героя по его уникальному ID из кэша.

    Args:
        hero_id (int): Уникальный идентификатор героя

    Returns:
        str: Локализованное название героя или "Unknown Hero (ID: X)" если не найден
    """
    return HEROES_CACHE.get(hero_id, f"Unknown Hero (ID: {hero_id})")


def format_timestamp_to_readable_date(timestamp: int) -> str:
    """
    Конвертирует Unix timestamp в читаемую дату и время

    Args:
        timestamp: Unix timestamp

    Returns:
        Отформатированная строка даты и времени
    """
    try:
        return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, OSError):
        return f"Invalid timestamp: {timestamp}"


def validate_database_integrity(session) -> list:
    """
    Выполняет комплексную проверку целостности и валидности данных в базе

    Проверяет:
    - Уникальность ключевых полей (match_id, match_seq_num)
    - Отсутствие NULL значений в критических полях
    - Корректность значений ролей и номеров команд
    - Правильное количество игроков в матчах (10 игроков)
    - Связность данных между таблицами

    Args:
        session: Сессия SQLAlchemy для работы с БД

    Returns:
        Список строк с описанием найденных проблем целостности
    """
    print_section_header("ПРОВЕРКА ЦЕЛОСТНОСТИ И ВАЛИДНОСТИ ДАННЫХ", "🔍", color=Colors.BRIGHT_CYAN)

    integrity_issues = []

    # === ПРОВЕРКА УНИКАЛЬНОСТИ MATCH_ID ===
    print_info_line("Проверка уникальности", "match_id", "🔍", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    duplicate_match_ids = session.query(Match.match_id, func.count(Match.match_id).label('count')) \
        .group_by(Match.match_id) \
        .having(func.count(Match.match_id) > 1) \
        .all()

    if duplicate_match_ids:
        print_status_message(f"Найдены дублирующиеся match_id: {len(duplicate_match_ids)}", "error")
        for match_id, count in duplicate_match_ids[:10]:
            print_info_line(f"Match ID {match_id}", f"{count} дубликатов", "⚠️", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
        if len(duplicate_match_ids) > 10:
            print_info_line("И еще", f"{len(duplicate_match_ids) - 10} дубликатов", "⚠️", Colors.BRIGHT_WHITE,
                            Colors.BRIGHT_RED)
        integrity_issues.append(f"Дублирующиеся match_id: {len(duplicate_match_ids)}")
    else:
        print_status_message("Все match_id уникальны", "success")

    # === ПРОВЕРКА УНИКАЛЬНОСТИ MATCH_SEQ_NUM ===
    print_info_line("Проверка уникальности", "match_seq_num", "🔍", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    duplicate_sequence_numbers = session.query(Match.match_seq_num, func.count(Match.match_seq_num).label('count')) \
        .group_by(Match.match_seq_num) \
        .having(func.count(Match.match_seq_num) > 1) \
        .all()

    if duplicate_sequence_numbers:
        print_status_message(f"Найдены дублирующиеся match_seq_num: {len(duplicate_sequence_numbers)}", "error")
        for seq_num, count in duplicate_sequence_numbers[:10]:
            print_info_line(f"Seq Num {seq_num}", f"{count} дубликатов", "⚠️", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
        integrity_issues.append(f"Дублирующиеся match_seq_num: {len(duplicate_sequence_numbers)}")
    else:
        print_status_message("Все match_seq_num уникальны", "success")

    # === ПРОВЕРКА NULL ЗНАЧЕНИЙ В ТАБЛИЦЕ MATCH ===
    print_info_line("Проверка NULL значений", "таблица Match", "🔍", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    critical_match_fields = [
        ('match_id', session.query(Match).filter(Match.match_id.is_(None)).count()),
        ('match_seq_num', session.query(Match).filter(Match.match_seq_num.is_(None)).count()),
        ('radiant_win', session.query(Match).filter(Match.radiant_win.is_(None)).count()),
        ('duration', session.query(Match).filter(Match.duration.is_(None)).count()),
        ('start_time', session.query(Match).filter(Match.start_time.is_(None)).count()),
    ]

    null_issues_in_matches = [(field, count) for field, count in critical_match_fields if count > 0]
    if null_issues_in_matches:
        print_status_message("Найдены NULL значения в таблице Match", "error")
        for field, count in null_issues_in_matches:
            print_info_line(field, f"{count} записей", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
        integrity_issues.extend([f"NULL в {field}: {count}" for field, count in null_issues_in_matches])
    else:
        print_status_message("Нет NULL значений в критических полях Match", "success")

    # === ПРОВЕРКА NULL ЗНАЧЕНИЙ В ТАБЛИЦЕ MATCHPLAYER ===
    print_info_line("Проверка NULL значений", "таблица MatchPlayer", "🔍", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    critical_player_fields = [
        ('match_id', session.query(MatchPlayer).filter(MatchPlayer.match_id.is_(None)).count()),
        ('account_id', session.query(MatchPlayer).filter(MatchPlayer.account_id.is_(None)).count()),
        ('hero_id', session.query(MatchPlayer).filter(MatchPlayer.hero_id.is_(None)).count()),
        ('team_number', session.query(MatchPlayer).filter(MatchPlayer.team_number.is_(None)).count()),
        ('role', session.query(MatchPlayer).filter(MatchPlayer.role.is_(None)).count()),
    ]

    null_issues_in_players = [(field, count) for field, count in critical_player_fields if count > 0]
    if null_issues_in_players:
        print_status_message("Найдены NULL значения в таблице MatchPlayer", "error")
        for field, count in null_issues_in_players:
            print_info_line(field, f"{count} записей", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
        integrity_issues.extend([f"NULL в {field}: {count}" for field, count in null_issues_in_players])
    else:
        print_status_message("Нет NULL значений в критических полях MatchPlayer", "success")

    # === ПРОВЕРКА КОРРЕКТНОСТИ РОЛЕЙ ===
    print_info_line("Проверка корректности", "ролей игроков", "🔍", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    invalid_roles = session.query(MatchPlayer.role, func.count(MatchPlayer.role)) \
        .filter(~MatchPlayer.role.in_(['core', 'support'])) \
        .group_by(MatchPlayer.role) \
        .all()

    if invalid_roles:
        print_status_message("Найдены некорректные роли", "error")
        for role, count in invalid_roles:
            print_info_line(f"Роль '{role}'", f"{count} записей", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
        integrity_issues.append(f"Некорректные роли: {sum(count for _, count in invalid_roles)}")
    else:
        print_status_message("Все роли корректны (core/support)", "success")

    # === ПРОВЕРКА КОРРЕКТНОСТИ НОМЕРОВ КОМАНД ===
    print_info_line("Проверка корректности", "team_number", "🔍", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    invalid_team_numbers = session.query(MatchPlayer.team_number, func.count(MatchPlayer.team_number)) \
        .filter(~MatchPlayer.team_number.in_([0, 1])) \
        .group_by(MatchPlayer.team_number) \
        .all()

    if invalid_team_numbers:
        print_status_message("Найдены некорректные team_number", "error")
        for team, count in invalid_team_numbers:
            print_info_line(f"Team {team}", f"{count} записей", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
        integrity_issues.append(f"Некорректные team_number: {sum(count for _, count in invalid_team_numbers)}")
    else:
        print_status_message("Все team_number корректны (0/1)", "success")

    # === ПРОВЕРКА КОЛИЧЕСТВА ИГРОКОВ В МАТЧАХ ===
    print_info_line("Проверка количества", "игроков в матчах", "🔍", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    matches_with_wrong_player_count = session.query(
        MatchPlayer.match_id,
        func.count(MatchPlayer.id).label('player_count')
    ).group_by(MatchPlayer.match_id) \
        .having(func.count(MatchPlayer.id) != 10) \
        .limit(10).all()

    if matches_with_wrong_player_count:
        total_wrong_matches = session.query(MatchPlayer.match_id) \
            .group_by(MatchPlayer.match_id) \
            .having(func.count(MatchPlayer.id) != 10) \
            .count()
        print_status_message(f"Найдены матчи с неправильным количеством игроков: {total_wrong_matches}", "error")
        for match_id, count in matches_with_wrong_player_count:
            print_info_line(f"Match {match_id}", f"{count} игроков", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
        integrity_issues.append(f"Матчи с неправильным количеством игроков: {total_wrong_matches}")
    else:
        print_status_message("Все матчи содержат 10 игроков", "success")

    # === ПРОВЕРКА СВЯЗНОСТИ ДАННЫХ (ORPHANED RECORDS) ===
    print_info_line("Проверка связности", "данных", "🔍", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    orphaned_players_count = session.query(func.count(MatchPlayer.id)) \
        .filter(~MatchPlayer.match_id.in_(session.query(Match.match_id))) \
        .scalar()

    if orphaned_players_count > 0:
        print_status_message(f"Найдены игроки без соответствующих матчей: {orphaned_players_count}", "error")
        integrity_issues.append(f"Игроки-сироты: {orphaned_players_count}")
    else:
        print_status_message("Все игроки связаны с существующими матчами", "success")

    # === ИТОГОВЫЙ РЕЗУЛЬТАТ ПРОВЕРКИ ЦЕЛОСТНОСТИ ===
    print_subsection_header("Итоги проверки целостности", "📋", Colors.BRIGHT_PURPLE)
    if integrity_issues:
        print_status_message(f"Обнаружены проблемы целостности данных: {len(integrity_issues)}", "error")
        for issue in integrity_issues:
            print_info_line("Проблема", issue, "•", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
    else:
        print_status_message("Все проверки целостности пройдены успешно!", "success")
        print_status_message("Данные готовы для использования в анализе", "info")

    return integrity_issues


def analyze_match_statistics(session):
    """
    Проводит комплексный анализ статистики матчей

    Анализирует:
    - Общее количество матчей и временные диапазоны
    - Диапазоны основных полей (ID, sequence numbers, время)
    - Длительность матчей (мин/макс/среднее)
    - Статистику побед по сторонам (Radiant vs Dire)
    - Распределение по игровым режимам
    - Типы лобби
    - Статистику счета команд
    - Детальный анализ разрушенных структур (башни и казармы)

    Args:
        session: Сессия SQLAlchemy для работы с БД
    """
    print_section_header("СТАТИСТИКА МАТЧЕЙ", "📊", color=Colors.BRIGHT_MAGENTA)

    # === ОБЩАЯ ИНФОРМАЦИЯ О МАТЧАХ ===
    total_matches = session.query(func.count(Match.match_id)).scalar()
    print_info_line("Общее количество матчей", f"{total_matches:,}", "📈", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)

    if total_matches == 0:
        print_status_message("Нет данных для анализа", "error")
        return

    # === ДИАПАЗОНЫ ОСНОВНЫХ ПОЛЕЙ ===
    print_subsection_header("Диапазоны основных полей", "🔢", Colors.BRIGHT_BLUE)

    # Получаем граничные матчи по порядку сбора (sequence number)
    first_match_by_sequence = session.query(Match).order_by(Match.match_seq_num.asc()).first()
    last_match_by_sequence = session.query(Match).order_by(Match.match_seq_num.desc()).first()

    # Получаем статистику диапазонов всех полей
    range_statistics = session.query(
        func.min(Match.match_id).label('min_match_id'),
        func.max(Match.match_id).label('max_match_id'),
        func.min(Match.match_seq_num).label('min_seq'),
        func.max(Match.match_seq_num).label('max_seq'),
        func.min(Match.start_time).label('min_time'),
        func.max(Match.start_time).label('max_time')
    ).first()

    if first_match_by_sequence and last_match_by_sequence and range_statistics:
        # Граничные в БД (по порядку сбора)
        print_info_line("Sequence Number (граничные)",
                        f"{first_match_by_sequence.match_seq_num:,} - {last_match_by_sequence.match_seq_num:,}",
                        "🔢", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
        print_info_line("Match ID (граничные)",
                        f"{first_match_by_sequence.match_id:,} - {last_match_by_sequence.match_id:,}",
                        "🆔", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)

        first_date = format_timestamp_to_readable_date(first_match_by_sequence.start_time)
        last_date = format_timestamp_to_readable_date(last_match_by_sequence.start_time)
        print_info_line("Время (граничные)", f"{first_date} - {last_date}", "🕐", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_PURPLE)

        # Минимальные и максимальные значения
        print_info_line("Sequence Number (мин/макс)",
                        f"{range_statistics.min_seq:,} - {range_statistics.max_seq:,}",
                        "🔢", Colors.BRIGHT_WHITE, Colors.BRIGHT_ORANGE)
        print_info_line("Match ID (мин/макс)",
                        f"{range_statistics.min_match_id:,} - {range_statistics.max_match_id:,}",
                        "🆔", Colors.BRIGHT_WHITE, Colors.BRIGHT_PINK)

        min_date = format_timestamp_to_readable_date(range_statistics.min_time)
        max_date = format_timestamp_to_readable_date(range_statistics.max_time)
        print_info_line("Время (мин/макс)", f"{min_date} - {max_date}", "🕐", Colors.BRIGHT_WHITE, Colors.BRIGHT_LIME)
    else:
        print_status_message("Не удалось получить данные о диапазонах", "error")

    # === АНАЛИЗ ДЛИТЕЛЬНОСТИ МАТЧЕЙ ===
    duration_statistics = session.query(
        func.min(Match.duration).label('min_duration'),
        func.max(Match.duration).label('max_duration'),
        func.avg(Match.duration).label('avg_duration')
    ).first()

    min_time_str = f"{duration_statistics.min_duration // 60}:{duration_statistics.min_duration % 60:02d}"
    max_time_str = f"{duration_statistics.max_duration // 60}:{duration_statistics.max_duration % 60:02d}"
    avg_time_str = f"{int(duration_statistics.avg_duration) // 60}:{int(duration_statistics.avg_duration) % 60:02d}"

    print_info_line("Длительность матчей", f"{min_time_str} - {max_time_str} (среднее: {avg_time_str})",
                    "⏱️", Colors.BRIGHT_WHITE, Colors.BRIGHT_TEAL)

    # === СТАТИСТИКА ПОБЕД ===
    print_subsection_header("Статистика побед", "🏆", Colors.BRIGHT_GREEN)
    win_statistics = session.query(
        Match.radiant_win,
        func.count(Match.radiant_win).label('count')
    ).group_by(Match.radiant_win).all()

    for is_radiant_win, count in win_statistics:
        side_name = "Radiant" if is_radiant_win else "Dire"
        side_emoji = "🌅" if is_radiant_win else "🌙"
        percentage = (count / total_matches) * 100
        print_info_line(f"{side_name} побед", f"{count:,} ({percentage:.1f}%)", side_emoji,
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_GOLD)

    # === АНАЛИЗ ИГРОВЫХ РЕЖИМОВ ===
    print_subsection_header("Режимы игры", "🎮", Colors.BRIGHT_PURPLE)
    game_mode_statistics = session.query(
        Match.game_mode,
        func.count(Match.game_mode).label('count')
    ).group_by(Match.game_mode).order_by(func.count(Match.game_mode).desc()).all()

    for mode, count in game_mode_statistics:
        percentage = (count / total_matches) * 100
        print_info_line(f"Режим {mode}", f"{count:,} ({percentage:.1f}%)", "🎯",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_CORAL)

    # === АНАЛИЗ ТИПОВ ЛОББИ ===
    print_subsection_header("Типы лобби", "🏠", Colors.BRIGHT_ORANGE)
    lobby_statistics = session.query(
        Match.lobby_type,
        func.count(Match.lobby_type).label('count')
    ).group_by(Match.lobby_type).order_by(func.count(Match.lobby_type).desc()).all()

    for lobby, count in lobby_statistics:
        percentage = (count / total_matches) * 100
        print_info_line(f"Лобби {lobby}", f"{count:,} ({percentage:.1f}%)", "🛠️",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_MINT)

    # === СТАТИСТИКА СЧЕТА КОМАНД ===
    print_subsection_header("Статистика счета", "🎯", Colors.BRIGHT_YELLOW)
    score_statistics = session.query(
        func.min(Match.radiant_score).label('min_rad'),
        func.max(Match.radiant_score).label('max_rad'),
        func.avg(Match.radiant_score).label('avg_rad'),
        func.min(Match.dire_score).label('min_dire'),
        func.max(Match.dire_score).label('max_dire'),
        func.avg(Match.dire_score).label('avg_dire')
    ).first()

    print_info_line("Radiant счет",
                    f"{score_statistics.min_rad} - {score_statistics.max_rad} (среднее: {score_statistics.avg_rad:.1f})",
                    "🌅", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    print_info_line("Dire счет",
                    f"{score_statistics.min_dire} - {score_statistics.max_dire} (среднее: {score_statistics.avg_dire:.1f})",
                    "🌙", Colors.BRIGHT_WHITE, Colors.BRIGHT_LAVENDER)

    # === ДЕТАЛЬНЫЙ АНАЛИЗ СТРУКТУР ===
    _analyze_match_structures(session, total_matches)


def _analyze_match_structures(session, total_matches: int):
    """
    Анализирует статистику разрушения башен и казарм в матчах

    Использует битовые маски для определения состояния структур:
    - Для башен: 11 бит (по количеству башен на карте)
    - Для казарм: 6 бит (по количеству казарм на карте)
    - Бит = 1: структура стоит, бит = 0: структура уничтожена

    Args:
        session: Сессия SQLAlchemy для работы с БД
        total_matches: Общее количество матчей для расчета процентов
    """
    print_subsection_header("Статистика структур", "🗼", Colors.BRIGHT_TEAL)

    # Определяем структуры для анализа в правильном порядке
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

    # Соответствующие битовые позиции для башен (новый порядок)
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
    matches_structure_data = session.query(
        Match.tower_status_radiant,
        Match.tower_status_dire,
        Match.barracks_status_radiant,
        Match.barracks_status_dire
    ).all()

    total_matches_with_structure_data = len(matches_structure_data)

    if total_matches_with_structure_data > 0:
        # === АНАЛИЗ БАШЕН ===
        print_subsection_header("Анализ башен (битовые маски)", "🗼", Colors.BRIGHT_BLUE)

        # Подсчет уничтоженных башен для каждой команды
        for team_name, tower_field_index in [("Radiant", 0), ("Dire", 1)]:
            team_emoji = "🌅" if team_name == "Radiant" else "🌙"
            print(f"\n{team_emoji} {Colors.BOLD}{team_name}:{Colors.RESET}")

            for tower_name, bit_position in zip(tower_names, tower_bit_positions):
                destroyed_count = 0
                for match in matches_structure_data:
                    tower_status = match[tower_field_index]  # 0 для radiant, 1 для dire
                    # Если бит установлен в 1, то башня ЕЩЕ СТОИТ
                    # Если бит 0, то башня УНИЧТОЖЕНА
                    if not (tower_status & (1 << bit_position)):
                        destroyed_count += 1

                destroyed_percentage = (destroyed_count / total_matches_with_structure_data) * 100
                standing_count = total_matches_with_structure_data - destroyed_count
                standing_percentage = 100 - destroyed_percentage

                print_info_line(tower_name,
                                f"уничтожена {destroyed_count:,} раз ({destroyed_percentage:.1f}%), цела {standing_count:,} раз ({standing_percentage:.1f}%)",
                                "🗼", Colors.BRIGHT_WHITE,
                                Colors.BRIGHT_RED if destroyed_percentage > 50 else Colors.BRIGHT_GREEN)

        # === АНАЛИЗ КАЗАРМ ===
        print_subsection_header("Анализ казарм (битовые маски)", "🏰", Colors.BRIGHT_GREEN)

        for team_name, barracks_field_index in [("Radiant", 2), ("Dire", 3)]:
            team_emoji = "🌅" if team_name == "Radiant" else "🌙"
            print(f"\n{team_emoji} {Colors.BOLD}{team_name}:{Colors.RESET}")

            for bit_position, barracks_name in enumerate(barracks_names):
                destroyed_count = 0
                for match in matches_structure_data:
                    barracks_status = match[barracks_field_index]  # 2 для radiant, 3 для dire
                    # Если бит установлен в 1, то казарма ЕЩЕ СТОИТ
                    # Если бит 0, то казарма УНИЧТОЖЕНА
                    if not (barracks_status & (1 << bit_position)):
                        destroyed_count += 1

                destroyed_percentage = (destroyed_count / total_matches_with_structure_data) * 100
                standing_count = total_matches_with_structure_data - destroyed_count
                standing_percentage = 100 - destroyed_percentage

                print_info_line(barracks_name,
                                f"уничтожена {destroyed_count:,} раз ({destroyed_percentage:.1f}%), цела {standing_count:,} раз ({standing_percentage:.1f}%)",
                                "🏰", Colors.BRIGHT_WHITE,
                                Colors.BRIGHT_RED if destroyed_percentage > 50 else Colors.BRIGHT_GREEN)

        # === ОБЩАЯ СТАТИСТИКА ПО КОЛИЧЕСТВУ УНИЧТОЖЕННЫХ СТРУКТУР ===
        _calculate_average_structure_destruction(matches_structure_data)

    else:
        print_status_message("Нет данных о структурах для анализа", "error")


def _calculate_average_structure_destruction(matches_structure_data: list):
    """
    Подсчитывает среднее количество уничтоженных башен и казарм для каждой команды

    Args:
        matches_structure_data: Список кортежей с данными о структурах матчей
    """
    print_subsection_header("Общая статистика разрушений", "📊", Colors.BRIGHT_PURPLE)

    # Инициализируем списки для подсчета
    radiant_towers_destroyed = []
    dire_towers_destroyed = []
    radiant_barracks_destroyed = []
    dire_barracks_destroyed = []

    for match in matches_structure_data:
        # Подсчет уничтоженных башен (инвертируем биты и считаем единицы)
        # XOR с полной маской дает инвертированное значение
        radiant_tower_count = bin(match[0] ^ TOWERS_BITMASK).count('1') if match[0] is not None else 0
        dire_tower_count = bin(match[1] ^ TOWERS_BITMASK).count('1') if match[1] is not None else 0
        radiant_towers_destroyed.append(radiant_tower_count)
        dire_towers_destroyed.append(dire_tower_count)

        # Подсчет уничтоженных казарм (инвертируем биты и считаем единицы)
        radiant_barracks_count = bin(match[2] ^ BARRACKS_BITMASK).count('1') if match[2] is not None else 0
        dire_barracks_count = bin(match[3] ^ BARRACKS_BITMASK).count('1') if match[3] is not None else 0
        radiant_barracks_destroyed.append(radiant_barracks_count)
        dire_barracks_destroyed.append(dire_barracks_count)

    # Вычисляем средние значения
    avg_radiant_towers = sum(radiant_towers_destroyed) / len(radiant_towers_destroyed)
    avg_dire_towers = sum(dire_towers_destroyed) / len(dire_towers_destroyed)
    avg_radiant_barracks = sum(radiant_barracks_destroyed) / len(radiant_barracks_destroyed)
    avg_dire_barracks = sum(dire_barracks_destroyed) / len(dire_barracks_destroyed)

    print_info_line("Radiant - Среднее башен уничтожено", f"{avg_radiant_towers:.1f}/11", "🌅",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    print_info_line("Radiant - Среднее казарм уничтожено", f"{avg_radiant_barracks:.1f}/6", "🌅",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_ORANGE)
    print_info_line("Dire - Среднее башен уничтожено", f"{avg_dire_towers:.1f}/11", "🌙",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)
    print_info_line("Dire - Среднее казарм уничтожено", f"{avg_dire_barracks:.1f}/6", "🌙",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_MAGENTA)


def analyze_player_statistics(session):
    """
    Проводит комплексный анализ статистики игроков

    Анализирует:
    - Общее количество записей игроков и уникальных аккаунтов
    - Статистику приватных аккаунтов
    - KDA статистику (убийства/смерти/помощи)
    - Статистику по ролям (core vs support)
    - Топ популярных героев
    - Винрейт героев (с минимальным количеством игр)
    - Винрейт героев с учетом вариантов
    - Статистику вариантов героев
    - Экономическую статистику (GPM, XPM, Last Hits, Net Worth)
    - Статистику уровней игроков
    - Статистику специальных предметов (Aghanim's)

    Args:
        session: Сессия SQLAlchemy для работы с БД
    """
    print_section_header("СТАТИСТИКА ИГРОКОВ", "👥", color=Colors.BRIGHT_GOLD)

    # === ОБЩАЯ ИНФОРМАЦИЯ ОБ ИГРОКАХ ===
    total_player_records = session.query(func.count(MatchPlayer.id)).scalar()

    # Подсчет приватных аккаунтов (ID = 4294967295)
    private_accounts_count = session.query(func.count(MatchPlayer.id)).filter(
        MatchPlayer.account_id == PRIVATE_ACCOUNT_ID).scalar()

    # Уникальные игроки исключая приватные аккаунты
    unique_public_players = session.query(func.count(distinct(MatchPlayer.account_id))).filter(
        MatchPlayer.account_id != PRIVATE_ACCOUNT_ID).scalar()

    # Общее количество уникальных account_id (включая приватные)
    total_unique_accounts = session.query(func.count(distinct(MatchPlayer.account_id))).scalar()

    print_info_line("Общее количество записей игроков", f"{total_player_records:,}", "📊",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    print_info_line("Уникальных публичных игроков", f"{unique_public_players:,}", "👤",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)

    private_percentage = (private_accounts_count / total_player_records) * 100 if total_player_records > 0 else 0
    print_info_line("Записей с приватными аккаунтами", f"{private_accounts_count:,} ({private_percentage:.1f}%)", "🔒",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    print_info_line("Всего уникальных account_id", f"{total_unique_accounts:,} (включая приватные)", "📈",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)

    # Предупреждение о высоком проценте приватных аккаунтов
    if private_percentage > 10:
        print_status_message(
            f"Высокий процент приватных аккаунтов ({private_percentage:.1f}%) может влиять на точность анализа уникальных игроков",
            "warning")

    if total_player_records == 0:
        print_status_message("Нет данных игроков для анализа", "error")
        return

    # === АНАЛИЗ KDA СТАТИСТИКИ ===
    _analyze_kda_statistics(session)

    # === АНАЛИЗ СТАТИСТИКИ ПО РОЛЯМ ===
    _analyze_role_statistics(session, total_player_records)

    # === АНАЛИЗ ПОПУЛЯРНОСТИ ГЕРОЕВ ===
    _analyze_hero_popularity(session, total_player_records)

    # === АНАЛИЗ ВИНРЕЙТА ГЕРОЕВ ===
    _analyze_hero_winrates(session)

    # === АНАЛИЗ ВИНРЕЙТА ГЕРОЕВ С ВАРИАНТАМИ ===
    _analyze_hero_variant_winrates(session)

    # === АНАЛИЗ СТАТИСТИКИ ВАРИАНТОВ ===
    _analyze_hero_variants_statistics(session, total_player_records)

    # === АНАЛИЗ ЭКОНОМИЧЕСКОЙ СТАТИСТИКИ ===
    _analyze_economic_statistics(session)

    # === АНАЛИЗ СТАТИСТИКИ УРОВНЕЙ ===
    _analyze_level_statistics(session, total_player_records)

    # === АНАЛИЗ СПЕЦИАЛЬНЫХ ПРЕДМЕТОВ ===
    _analyze_special_items_statistics(session, total_player_records)


def _analyze_kda_statistics(session):
    """Анализирует статистику KDA (убийства/смерти/помощи)"""
    print_subsection_header("Статистика KDA", "⚔️", Colors.BRIGHT_RED)

    kda_statistics = session.query(
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

    print_info_line("Kills",
                    f"{kda_statistics.min_kills} - {kda_statistics.max_kills} (среднее: {kda_statistics.avg_kills:.1f})",
                    "🗡️", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
    print_info_line("Deaths",
                    f"{kda_statistics.min_deaths} - {kda_statistics.max_deaths} (среднее: {kda_statistics.avg_deaths:.1f})",
                    "💀", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    print_info_line("Assists",
                    f"{kda_statistics.min_assists} - {kda_statistics.max_assists} (среднее: {kda_statistics.avg_assists:.1f})",
                    "🤝", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    print_info_line("Средний KDA", f"{kda_statistics.avg_kda:.2f}", "📈", Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)


def _analyze_role_statistics(session, total_player_records: int):
    """Анализирует статистику игроков по ролям"""
    print_subsection_header("Статистика по ролям", "🎭", Colors.BRIGHT_PURPLE)

    role_statistics = session.query(
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

    for role_data in role_statistics:
        role = role_data.role
        count = role_data.count
        percentage = (count / total_player_records) * 100

        role_emoji = "⚔️" if role == "core" else "🛡️"
        role_color = Colors.BRIGHT_RED if role == "core" else Colors.BRIGHT_CYAN

        print(
            f"\n{role_emoji} {Colors.BOLD}{role_color}{role.upper()}{Colors.RESET} ({Colors.BRIGHT_GREEN}{count:,} записей, {percentage:.1f}%{Colors.RESET}):")

        print_info_line("K/D/A",
                        f"{role_data.avg_kills:.1f}/{role_data.avg_deaths:.1f}/{role_data.avg_assists:.1f} (KDA: {role_data.avg_kda:.2f})",
                        "⚔️", Colors.BRIGHT_WHITE, Colors.BRIGHT_ORANGE)
        print_info_line("GPM | XPM", f"{role_data.avg_gpm:.0f} | {role_data.avg_xpm:.0f}",
                        "💰", Colors.BRIGHT_WHITE, Colors.BRIGHT_GOLD)
        print_info_line("Last Hits | Net Worth", f"{role_data.avg_lh:.0f} | {role_data.avg_nw:.0f}",
                        "🏹", Colors.BRIGHT_WHITE, Colors.BRIGHT_MINT)


def _analyze_hero_popularity(session, total_player_records: int):
    """Анализирует популярность героев (топ-20)"""
    print_subsection_header(f"Топ-{TOP_HEROES_DISPLAY_COUNT} популярных героев", "🦸", Colors.BRIGHT_BLUE)

    hero_popularity_stats = session.query(
        MatchPlayer.hero_id,
        func.count(MatchPlayer.hero_id).label('count'),
        func.avg(MatchPlayer.kda).label('avg_kda')
    ).group_by(MatchPlayer.hero_id) \
        .order_by(func.count(MatchPlayer.hero_id).desc()) \
        .limit(TOP_HEROES_DISPLAY_COUNT).all()

    for rank, (hero_id, count, avg_kda) in enumerate(hero_popularity_stats, 1):
        hero_name = get_hero_name_by_id(hero_id)
        percentage = (count / total_player_records) * 100

        rank_color = Colors.BRIGHT_GOLD if rank <= 3 else Colors.BRIGHT_SILVER if rank <= 10 else Colors.BRIGHT_WHITE

        print(f"{rank_color}{rank:2d}.{Colors.RESET} {Colors.BRIGHT_BLUE}{hero_name}{Colors.RESET} "
              f"(ID: {Colors.BRIGHT_YELLOW}{hero_id}{Colors.RESET}): "
              f"{Colors.BRIGHT_GREEN}{count:,}{Colors.RESET} игр "
              f"({Colors.BRIGHT_CYAN}{percentage:.1f}%{Colors.RESET}), "
              f"KDA: {Colors.BRIGHT_ORANGE}{avg_kda:.2f}{Colors.RESET}")


def _analyze_hero_winrates(session):
    """Анализирует винрейт героев (топ-20 по винрейту, минимум 100 игр)"""
    print_subsection_header(
        f"Топ-{TOP_HEROES_DISPLAY_COUNT} героев по винрейту (мин. {MINIMUM_GAMES_FOR_HERO_WINRATE} игр)", "🏆",
        Colors.BRIGHT_GOLD)

    try:
        # Получаем всех игроков с их матчами для расчета винрейта
        players_match_results = session.query(
            MatchPlayer.hero_id,
            MatchPlayer.team_number,
            Match.radiant_win
        ).join(Match, MatchPlayer.match_id == Match.match_id).all()

        # Группируем по героям и считаем винрейт
        hero_winrate_stats = defaultdict(lambda: {'total': 0, 'wins': 0})

        for hero_id, team_number, radiant_win in players_match_results:
            hero_winrate_stats[hero_id]['total'] += 1
            # Игрок выиграл если: он в radiant и radiant_win=True ИЛИ он в dire и radiant_win=False
            if (team_number == 0 and radiant_win) or (team_number == 1 and not radiant_win):
                hero_winrate_stats[hero_id]['wins'] += 1

        # Фильтруем героев с минимум 100 играми и вычисляем винрейт
        hero_winrates = []
        for hero_id, stats in hero_winrate_stats.items():
            if stats['total'] >= MINIMUM_GAMES_FOR_HERO_WINRATE:
                winrate = (stats['wins'] * 100.0) / stats['total']
                hero_winrates.append((hero_id, stats['total'], stats['wins'], winrate))

        # Сортируем по винрейту
        hero_winrates.sort(key=lambda x: x[3], reverse=True)

        # Выводим топ-20
        for rank, (hero_id, total_games, wins, winrate) in enumerate(hero_winrates[:TOP_HEROES_DISPLAY_COUNT], 1):
            hero_name = get_hero_name_by_id(hero_id)

            rank_color = Colors.BRIGHT_GOLD if rank <= 3 else Colors.BRIGHT_SILVER if rank <= 10 else Colors.BRIGHT_WHITE
            winrate_color = Colors.BRIGHT_GREEN if winrate >= 55 else Colors.BRIGHT_YELLOW if winrate >= 50 else Colors.BRIGHT_RED

            print(f"{rank_color}{rank:2d}.{Colors.RESET} {Colors.BRIGHT_BLUE}{hero_name}{Colors.RESET}: "
                  f"{winrate_color}{winrate:.1f}%{Colors.RESET} "
                  f"({Colors.BRIGHT_WHITE}{wins}/{total_games}{Colors.RESET})")

        if not hero_winrates:
            print_status_message(f"Нет героев с минимум {MINIMUM_GAMES_FOR_HERO_WINRATE} играми", "warning")

    except Exception as e:
        print_status_message(f"Ошибка при расчете винрейта героев: {e}", "error")


def _analyze_hero_variant_winrates(session):
    """Анализирует винрейт героев с учетом вариантов (топ-20 по винрейту, минимум 50 игр)"""
    print_subsection_header(
        f"Топ-{TOP_HEROES_DISPLAY_COUNT} героев по винрейту (с вариантами, мин. {MINIMUM_GAMES_FOR_HERO_VARIANT_WINRATE} игр)",
        "🎨", Colors.BRIGHT_PURPLE)

    try:
        # Получаем всех игроков с их матчами для расчета винрейта по вариантам
        players_variant_match_results = session.query(
            MatchPlayer.hero_id,
            MatchPlayer.hero_variant,
            MatchPlayer.team_number,
            Match.radiant_win
        ).join(Match, MatchPlayer.match_id == Match.match_id).all()

        # Группируем по героям И вариантам и считаем винрейт
        hero_variant_winrate_stats = defaultdict(lambda: {'total': 0, 'wins': 0})

        for hero_id, hero_variant, team_number, radiant_win in players_variant_match_results:
            key = (hero_id, hero_variant)
            hero_variant_winrate_stats[key]['total'] += 1
            # Игрок выиграл если: он в radiant и radiant_win=True ИЛИ он в dire и radiant_win=False
            if (team_number == 0 and radiant_win) or (team_number == 1 and not radiant_win):
                hero_variant_winrate_stats[key]['wins'] += 1

        # Фильтруем комбинации с минимум 50 играми и вычисляем винрейт
        hero_variant_winrates = []
        for (hero_id, hero_variant), stats in hero_variant_winrate_stats.items():
            if stats['total'] >= MINIMUM_GAMES_FOR_HERO_VARIANT_WINRATE:
                winrate = (stats['wins'] * 100.0) / stats['total']
                hero_variant_winrates.append((hero_id, hero_variant, stats['total'], stats['wins'], winrate))

        # Сортируем по винрейту
        hero_variant_winrates.sort(key=lambda x: x[4], reverse=True)

        # Выводим топ-20
        for rank, (hero_id, hero_variant, total_games, wins, winrate) in enumerate(
                hero_variant_winrates[:TOP_HEROES_DISPLAY_COUNT], 1):
            hero_name = get_hero_name_by_id(hero_id)
            variant_text = f" (вариант {hero_variant})" if hero_variant > 0 else ""

            rank_color = Colors.BRIGHT_GOLD if rank <= 3 else Colors.BRIGHT_SILVER if rank <= 10 else Colors.BRIGHT_WHITE
            winrate_color = Colors.BRIGHT_GREEN if winrate >= 55 else Colors.BRIGHT_YELLOW if winrate >= 50 else Colors.BRIGHT_RED

            print(
                f"{rank_color}{rank:2d}.{Colors.RESET} {Colors.BRIGHT_BLUE}{hero_name}{Colors.BRIGHT_PURPLE}{variant_text}{Colors.RESET}: "
                f"{winrate_color}{winrate:.1f}%{Colors.RESET} "
                f"({Colors.BRIGHT_WHITE}{wins}/{total_games}{Colors.RESET})")

        if not hero_variant_winrates:
            print_status_message(f"Нет героев с вариантами с минимум {MINIMUM_GAMES_FOR_HERO_VARIANT_WINRATE} играми",
                                 "warning")

    except Exception as e:
        print_status_message(f"Ошибка при расчете винрейта героев с вариантами: {e}", "error")


def _analyze_hero_variants_statistics(session, total_player_records: int):
    """Анализирует статистику вариантов героев"""
    print_subsection_header("Статистика вариантов героев", "🎨", Colors.BRIGHT_PINK)

    variant_statistics = session.query(
        MatchPlayer.hero_variant,
        func.count(MatchPlayer.hero_variant).label('count')
    ).group_by(MatchPlayer.hero_variant) \
        .order_by(func.count(MatchPlayer.hero_variant).desc()).all()

    print_info_line("Всего различных вариантов", f"{len(variant_statistics)}", "🎭",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_PURPLE)

    for variant, count in variant_statistics[:10]:
        percentage = (count / total_player_records) * 100
        variant_text = f"Вариант {variant}" if variant > 0 else "Базовый (0)"
        print_info_line(variant_text, f"{count:,} ({percentage:.1f}%)", "🎨",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_MINT)


def _analyze_economic_statistics(session):
    """Анализирует экономическую статистику игроков"""
    print_subsection_header("Экономическая статистика", "💰", Colors.BRIGHT_GOLD)

    economic_statistics = session.query(
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

    print_info_line("GPM",
                    f"{economic_statistics.min_gpm} - {economic_statistics.max_gpm} (среднее: {economic_statistics.avg_gpm:.0f})",
                    "💰", Colors.BRIGHT_WHITE, Colors.BRIGHT_GOLD)
    print_info_line("XPM",
                    f"{economic_statistics.min_xpm} - {economic_statistics.max_xpm} (среднее: {economic_statistics.avg_xpm:.0f})",
                    "⭐", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    print_info_line("Last Hits",
                    f"{economic_statistics.min_lh} - {economic_statistics.max_lh} (среднее: {economic_statistics.avg_lh:.0f})",
                    "🏹", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    print_info_line("Net Worth",
                    f"{economic_statistics.min_nw:,} - {economic_statistics.max_nw:,} (среднее: {economic_statistics.avg_nw:,.0f})",
                    "💎", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)


def _analyze_level_statistics(session, total_player_records: int):
    """Анализирует статистику уровней игроков"""
    print_subsection_header("Статистика уровней", "📊", Colors.BRIGHT_BLUE)

    try:
        # Детальная статистика по всем уровням
        level_distribution = session.query(
            MatchPlayer.level,
            func.count(MatchPlayer.level).label('count')
        ).group_by(MatchPlayer.level) \
            .order_by(MatchPlayer.level).all()

        print_info_line("Распределение по уровням", f"{len(level_distribution)} уникальных уровней", "📈",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)

        # Показываем топ-10 самых частых уровней
        level_distribution_sorted = sorted(level_distribution, key=lambda x: x[1], reverse=True)
        for level, count in level_distribution_sorted[:10]:
            percentage = (count / total_player_records) * 100
            print_info_line(f"Уровень {level}", f"{count:,} ({percentage:.1f}%)", "🎯",
                            Colors.BRIGHT_WHITE, Colors.BRIGHT_ORANGE)

        # Средний уровень по ролям
        role_level_statistics = session.query(
            MatchPlayer.role,
            func.avg(MatchPlayer.level).label('avg_level'),
            func.min(MatchPlayer.level).label('min_level'),
            func.max(MatchPlayer.level).label('max_level')
        ).group_by(MatchPlayer.role).all()

        print_subsection_header("Статистика уровней по ролям", "🎭", Colors.BRIGHT_PURPLE)
        for role_data in role_level_statistics:
            role = role_data.role
            avg_level = role_data.avg_level
            min_level = role_data.min_level
            max_level = role_data.max_level

            role_emoji = "⚔️" if role == "core" else "🛡️"
            role_color = Colors.BRIGHT_RED if role == "core" else Colors.BRIGHT_CYAN

            print_info_line(f"{role_emoji} {role.upper()}",
                            f"среднее {avg_level:.1f}, диапазон {min_level}-{max_level}",
                            "📊", Colors.BRIGHT_WHITE, role_color)

        # Общая статистика уровней
        overall_level_statistics = session.query(
            func.min(MatchPlayer.level).label('min_level'),
            func.max(MatchPlayer.level).label('max_level'),
            func.avg(MatchPlayer.level).label('avg_level')
        ).first()

        print_info_line("Общая статистика",
                        f"среднее {overall_level_statistics.avg_level:.1f}, диапазон {overall_level_statistics.min_level}-{overall_level_statistics.max_level}",
                        "🎯", Colors.BRIGHT_WHITE, Colors.BRIGHT_MINT)

    except Exception as e:
        print_status_message(f"Ошибка при анализе уровней: {e}", "error")
        # Fallback к простой статистике
        level_statistics = session.query(
            func.min(MatchPlayer.level).label('min_level'),
            func.max(MatchPlayer.level).label('max_level'),
            func.avg(MatchPlayer.level).label('avg_level')
        ).first()
        print_info_line("Уровень",
                        f"{level_statistics.min_level} - {level_statistics.max_level} (среднее: {level_statistics.avg_level:.1f})",
                        "📈", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)


def _analyze_special_items_statistics(session, total_player_records: int):
    """Анализирует статистику специальных предметов (Aghanim's)"""
    print_subsection_header("Статистика Aghanim's предметов", "🔮", Colors.BRIGHT_MAGENTA)

    try:
        # Подсчет предметов простыми запросами
        scepter_count = session.query(func.count(MatchPlayer.id)).filter(MatchPlayer.aghanims_scepter > 0).scalar()
        shard_count = session.query(func.count(MatchPlayer.id)).filter(MatchPlayer.aghanims_shard > 0).scalar()
        moonshard_count = session.query(func.count(MatchPlayer.id)).filter(MatchPlayer.moonshard > 0).scalar()

        scepter_percentage = (scepter_count / total_player_records) * 100 if scepter_count else 0
        shard_percentage = (shard_count / total_player_records) * 100 if shard_count else 0
        moonshard_percentage = (moonshard_count / total_player_records) * 100 if moonshard_count else 0

        print_info_line("Aghanim's Scepter", f"{scepter_count:,} ({scepter_percentage:.1f}%)", "🔱",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_PURPLE)
        print_info_line("Aghanim's Shard", f"{shard_count:,} ({shard_percentage:.1f}%)", "💎",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)
        print_info_line("Moon Shard", f"{moonshard_count:,} ({moonshard_percentage:.1f}%)", "🌙",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)

    except Exception as e:
        print_status_message(f"Ошибка при анализе Aghanim's предметов: {e}", "error")


def main(database_url: str = None):
    """
    Главная функция для комплексной проверки и анализа датасета Dota 2

    Выполняет полный цикл проверки и анализа:
    1. Подключение к базе данных
    2. Загрузка данных о героях из БД
    3. Проверка целостности данных
    4. Анализ статистики матчей
    5. Анализ статистики игроков
    6. Вывод итогового отчета

    Args:
        database_url: URL базы данных. Если None, использует DATABASE_URL из config
    """
    program_start_time = time.time()

    # === ЗАГОЛОВОК ПРОГРАММЫ ===
    print_section_header("КОМПЛЕКСНАЯ ПРОВЕРКА И АНАЛИЗ ДАТАСЕТА DOTA 2", "🔍", 100, Colors.BRIGHT_CYAN)

    # === ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ ===
    try:
        database_connection_url = database_url or DATASET_DATABASE_URL
        engine = create_engine(database_connection_url)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session = SessionLocal()

        print_status_message("Подключение к базе данных установлено", "success")

        # === ЗАГРУЗКА ДАННЫХ О ГЕРОЯХ ===
        if not initialize_heroes_cache():
            print_status_message("КРИТИЧЕСКАЯ ОШИБКА: Не удалось загрузить данные героев!", "error", "💥")
            print_status_message("Убедитесь, что база данных героев существует и заполнена.", "warning", "⚠️")
            return

        # === ПРОВЕРКА СУЩЕСТВОВАНИЯ ТАБЛИЦ ===
        try:
            matches_count = session.query(func.count(Match.match_id)).scalar()
            players_count = session.query(func.count(MatchPlayer.id)).scalar()
            print_status_message(f"Найдено {matches_count:,} матчей и {players_count:,} записей игроков", "success")
        except Exception as e:
            print_status_message(f"Ошибка при проверке таблиц: {e}", "error")
            return

        if matches_count == 0 and players_count == 0:
            print_status_message("База данных пуста, нет данных для анализа", "error")
            return

        # === ВЫПОЛНЕНИЕ АНАЛИЗА ===
        print_status_message("Начинаем комплексный анализ...", "info")

        # 1. Проверка целостности данных
        integrity_issues = validate_database_integrity(session)

        # 2. Анализ статистики матчей
        analyze_match_statistics(session)

        # 3. Анализ статистики игроков
        analyze_player_statistics(session)

        # === ИТОГОВЫЙ ОТЧЕТ ===
        _generate_final_report(program_start_time, matches_count, players_count, integrity_issues)

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка базы данных: {e}", "error")
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error")
    finally:
        try:
            session.close()
            print_status_message("Соединение с базой данных закрыто", "success")
        except:
            pass


def _generate_final_report(program_start_time: float, matches_count: int,
                           players_count: int, integrity_issues: list):
    """
    Генерирует итоговый отчет о выполненном анализе

    Args:
        program_start_time: Время начала выполнения программы
        matches_count: Количество проанализированных матчей
        players_count: Количество проанализированных записей игроков
        integrity_issues: Список проблем целостности данных
    """
    end_time = time.time()
    execution_time = end_time - program_start_time

    print_section_header("ИТОГОВЫЙ ОТЧЕТ", "📋", 80, Colors.BRIGHT_GOLD)

    print_info_line("Время выполнения анализа", f"{execution_time:.2f} секунд", "⏱️",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    print_info_line("Проанализировано матчей", f"{matches_count:,}", "📊",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    print_info_line("Проанализировано записей игроков", f"{players_count:,}", "👥",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)
    print_info_line("Загружено героев в кэш", f"{len(HEROES_CACHE):,}", "🦸",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)

    if integrity_issues:
        print_status_message(f"ВНИМАНИЕ! Обнаружены проблемы целостности данных: {len(integrity_issues)}", "warning")
        for issue in integrity_issues:
            print_info_line("Проблема", issue, "•", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
        print_status_message("Рекомендуется исправить эти проблемы перед использованием данных для анализа", "warning")
    else:
        print_status_message("Все проверки целостности пройдены успешно!", "success")
        print_status_message("Данные готовы для использования в анализе и машинном обучении", "success")

    print_section_header("АНАЛИЗ ЗАВЕРШЕН", "🎉", 80, Colors.BRIGHT_GREEN)


if __name__ == "__main__":
    main()
