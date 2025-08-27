"""
Модуль для комплексной валидации и анализа датасета матчей Dota 2.

Основные функции:
- Проверка целостности и корректности данных в базе
- Анализ статистики матчей (длительность, режимы, баланс команд)
- Исследование игровых структур (башни, казармы)
- Анализ статистики игроков (KDA, роли, экономика)
- Изучение популярности и винрейта героев
- Генерация комплексного отчета о качестве данных

Валидация включает:
- Проверку уникальности ключевых полей
- Выявление NULL значений в критических полях
- Контроль корректности ролей и номеров команд
- Анализ связности данных между таблицами
- Статистический анализ распределений
"""

import time
from datetime import datetime
from sqlalchemy import create_engine, func, distinct, text, case
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError

# Импорт моделей базы данных
from data_bases.dataset.models import Match, MatchPlayer
from data_bases.heroes.models import Hero
from config import (DATASET_DATABASE_URL, HEROES_DATABASE_URL, PRIVATE_ACCOUNT_ID, MINIMUM_GAMES_FOR_HERO_WINRATE,
                    MINIMUM_GAMES_FOR_HERO_VARIANT_WINRATE, TOP_HEROES_DISPLAY_COUNT,
                    PRIVATE_ACCOUNTS_WARNING_THRESHOLD, DUPLICATE_DISPLAY_LIMIT, TOWERS_BITMASK, BARRACKS_BITMASK)

# Импорт утилит для консольного вывода
from utils.console import (
    Colors,
    print_section_header,
    print_subsection_header,
    print_info_line,
    print_status_message
)

# БД героев (справочная информация)
heroes_engine = create_engine(HEROES_DATABASE_URL)
HeroesSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=heroes_engine)

# Глобальный кэш для героев (для быстрого получения имен по ID)
HEROES_CACHE = {}


def initialize_heroes_cache() -> bool:
    """
    Инициализирует кэш героев из базы данных для быстрого доступа.

    Загружает всех героев из справочной БД в память для избежания
    повторных запросов при анализе. Кэш представляет собой словарь
    формата {hero_id: localized_name}.

    Returns:
        bool: True если кэш успешно загружен, False при ошибке

    Raises:
        Exception: При ошибках подключения к БД или отсутствии данных

    Note:
        Функция обязательна для корректной работы анализа датасета
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
        str: Локализованное название героя или "Unknown Hero (ID: X)"
    """
    return HEROES_CACHE.get(hero_id, f"Unknown Hero (ID: {hero_id})")


def format_timestamp_to_readable_date(timestamp: int) -> str:
    """
    Конвертирует Unix timestamp в читаемую дату и время.

    Args:
        timestamp (int): Unix timestamp в секундах

    Returns:
        str: Отформатированная строка даты в формате 'YYYY-MM-DD HH:MM:SS'
             или сообщение об ошибке для некорректных timestamp
    """
    try:
        return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, OSError):
        return f"Invalid timestamp: {timestamp}"


def get_database_info(session) -> dict:
    """
    Получает информацию о размере и весе датасета из PostgreSQL.

    Извлекает метаинформацию о базе данных, включая:
    - Название текущей базы данных
    - Общий размер БД в читаемом формате
    - Количество записей в основных таблицах
    - Размеры отдельных таблиц

    Args:
        session: Сессия SQLAlchemy для работы с БД

    Returns:
        dict: Словарь с информацией о БД, содержащий:
            - db_name (str): Название базы данных
            - total_size (str): Общий размер в читаемом формате
            - matches_count (int): Количество матчей
            - players_count (int): Количество записей игроков
            - matches_table_size (str): Размер таблицы матчей
            - players_table_size (str): Размер таблицы игроков

    Note:
        При ошибках PostgreSQL-специфичных запросов возвращает fallback
        с базовыми счетчиками записей
    """
    try:
        # Получаем название текущей БД
        db_name = session.execute(text("SELECT current_database()")).scalar()

        # Получаем размер БД в байтах
        size_query = text("SELECT pg_database_size(current_database())")
        db_size_bytes = session.execute(size_query).scalar()

        # Конвертируем в читаемый формат
        if db_size_bytes > 1024 ** 3:  # GB
            db_size_readable = f"{db_size_bytes / (1024 ** 3):.1f} GB"
        elif db_size_bytes > 1024 ** 2:  # MB
            db_size_readable = f"{db_size_bytes / (1024 ** 2):.1f} MB"
        else:  # KB
            db_size_readable = f"{db_size_bytes / 1024:.1f} KB"

        # Получаем количество записей
        matches_count = session.query(func.count(Match.match_id)).scalar()
        players_count = session.query(func.count(MatchPlayer.id)).scalar()

        # Получаем размеры отдельных таблиц
        matches_table_size = session.execute(text("SELECT pg_total_relation_size('matches')")).scalar()
        players_table_size = session.execute(text("SELECT pg_total_relation_size('match_players')")).scalar()

        matches_size_readable = f"{matches_table_size / (1024 ** 2):.1f} MB" if matches_table_size > 1024 ** 2 else f"{matches_table_size / 1024:.1f} KB"
        players_size_readable = f"{players_table_size / (1024 ** 2):.1f} MB" if players_table_size > 1024 ** 2 else f"{players_table_size / 1024:.1f} KB"

        return {
            'db_name': db_name,
            'total_size': db_size_readable,
            'matches_count': matches_count,
            'players_count': players_count,
            'matches_table_size': matches_size_readable,
            'players_table_size': players_size_readable
        }
    except Exception as e:
        print_status_message(f"Не удалось получить информацию о размере БД: {e}", "warning")
        # Fallback - только подсчет записей
        matches_count = session.query(func.count(Match.match_id)).scalar()
        players_count = session.query(func.count(MatchPlayer.id)).scalar()
        return {
            'db_name': 'dota_rate_dataset',
            'total_size': 'Неизвестно',
            'matches_count': matches_count,
            'players_count': players_count,
            'matches_table_size': 'Неизвестно',
            'players_table_size': 'Неизвестно'
        }


def display_database_info(db_info):
    """
    Отображает информацию о весе и размере датасета.

    Выводит структурированную информацию о состоянии базы данных,
    включая количество записей, размеры таблиц и общий вес.

    Args:
        db_info (dict): Словарь с информацией о БД из get_database_info()
    """
    print_section_header("ВЕС И РАЗМЕР ДАТАСЕТА", "⚖️", color=Colors.BRIGHT_CYAN)

    print_info_line("База данных", db_info['db_name'], "🗃️", Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)
    print_info_line("Количество матчей", f"{db_info['matches_count']:,}", "🎮", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_YELLOW)
    print_info_line("Количество записей игроков", f"{db_info['players_count']:,}", "👥", Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_CYAN)

    print()
    print_info_line("Фактический размер БД", db_info['total_size'], "💾", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    if db_info['matches_table_size'] != 'Неизвестно':
        print_info_line("Размер таблицы матчей", db_info['matches_table_size'], "📊", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_PURPLE)
        print_info_line("Размер таблицы игроков", db_info['players_table_size'], "📈", Colors.BRIGHT_WHITE,
                        Colors.BRIGHT_ORANGE)


def validate_data(session) -> list:
    """
    Выполняет комплексную проверку целостности и валидности данных в базе.

    Проводит серию проверок для выявления потенциальных проблем:
    - Уникальность ключевых идентификаторов (match_id, match_seq_num)
    - Отсутствие NULL значений в критических полях
    - Корректность значений ролей (core/support)
    - Правильность номеров команд (0/1)
    - Количество игроков в матчах (должно быть 10)
    - Связность данных между таблицами

    Args:
        session: Сессия SQLAlchemy для работы с БД

    Returns:
        list: Список строк с описанием найденных проблем.
              Пустой список означает отсутствие проблем
    """
    integrity_issues = []

    print_section_header("ПРОВЕРКА ЦЕЛОСТНОСТИ И ВАЛИДНОСТИ ДАННЫХ", "🔍", color=Colors.BRIGHT_CYAN)

    # === ПРОВЕРКА УНИКАЛЬНОСТИ MATCH_ID ===
    print_info_line("Проверка уникальности", "match_id", "🔎", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    duplicate_match_ids = session.query(Match.match_id, func.count(Match.match_id).label('count')) \
        .group_by(Match.match_id) \
        .having(func.count(Match.match_id) > 1) \
        .all()

    if duplicate_match_ids:
        print_status_message(f"Найдены дублирующиеся match_id: {len(duplicate_match_ids)}", "error")
        for match_id, count in duplicate_match_ids[:DUPLICATE_DISPLAY_LIMIT]:
            print_info_line(f"Match ID {match_id}", f"{count} дубликатов", "⚠️", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
        if len(duplicate_match_ids) > DUPLICATE_DISPLAY_LIMIT:
            print_info_line("И еще", f"{len(duplicate_match_ids) - DUPLICATE_DISPLAY_LIMIT} дубликатов", "⚠️",
                            Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
        integrity_issues.append(f"Дублирующиеся match_id: {len(duplicate_match_ids)}")
    else:
        print_status_message("Все match_id уникальны", "success")

    # === ПРОВЕРКА УНИКАЛЬНОСТИ MATCH_SEQ_NUM ===
    print_info_line("Проверка уникальности", "match_seq_num", "🔎", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    duplicate_sequence_numbers = session.query(Match.match_seq_num, func.count(Match.match_seq_num).label('count')) \
        .group_by(Match.match_seq_num) \
        .having(func.count(Match.match_seq_num) > 1) \
        .all()

    if duplicate_sequence_numbers:
        print_status_message(f"Найдены дублирующиеся match_seq_num: {len(duplicate_sequence_numbers)}", "error")
        for seq_num, count in duplicate_sequence_numbers[:DUPLICATE_DISPLAY_LIMIT]:
            print_info_line(f"Seq Num {seq_num}", f"{count} дубликатов", "⚠️", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
        integrity_issues.append(f"Дублирующиеся match_seq_num: {len(duplicate_sequence_numbers)}")
    else:
        print_status_message("Все match_seq_num уникальны", "success")

    # === ПРОВЕРКА NULL ЗНАЧЕНИЙ В ТАБЛИЦЕ MATCH ===
    print_info_line("Проверка NULL значений", "таблица Match", "🔎", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
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
    print_info_line("Проверка NULL значений", "таблица MatchPlayer", "🔎", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
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
    print_info_line("Проверка корректности", "ролей игроков", "🔎", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
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
    print_info_line("Проверка корректности", "team_number", "🔎", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
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
    print_info_line("Проверка количества", "игроков в матчах", "🔎", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    matches_with_wrong_player_count = session.query(
        MatchPlayer.match_id,
        func.count(MatchPlayer.id).label('player_count')
    ).group_by(MatchPlayer.match_id) \
        .having(func.count(MatchPlayer.id) != 10) \
        .limit(DUPLICATE_DISPLAY_LIMIT).all()

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

    # === ПРОВЕРКА СВЯЗНОСТИ ДАННЫХ ===
    print_info_line("Проверка связности", "данных", "🔎", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)

    # Поиск записей игроков без соответствующих матчей (orphaned records)
    orphaned_players_count = session.query(func.count(MatchPlayer.id)) \
        .outerjoin(Match, MatchPlayer.match_id == Match.match_id) \
        .filter(Match.match_id.is_(None)) \
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


def analyze_match_data(session):
    """
    Анализирует статистику матчей с диапазонами и структурами.

    Проводит комплексный анализ данных матчей, включая:
    - Отображение временных диапазонов и sequence numbers
    - Анализ длительности матчей (мин/макс/среднее)
    - Статистику побед по командам (баланс)
    - Распределение по игровым режимам
    - Анализ типов лобби
    - Статистику счета команд
    - Детальный анализ разрушения игровых структур

    Args:
        session: Сессия SQLAlchemy для работы с БД
    """
    print_section_header("СТАТИСТИКА МАТЧЕЙ", "📊", color=Colors.BRIGHT_MAGENTA)

    total_matches = session.query(func.count(Match.match_id)).scalar()
    if total_matches == 0:
        print_status_message("Нет данных для анализа", "error")
        return

    # === ОТОБРАЖЕНИЕ ДИАПАЗОНОВ ВРЕМЕНИ И ID ===
    _display_data_ranges(session)

    # === СТАТИСТИКА ДЛИТЕЛЬНОСТИ МАТЧЕЙ ===
    duration_stats = session.query(
        func.min(Match.duration).label('min_duration'),
        func.max(Match.duration).label('max_duration'),
        func.avg(Match.duration).label('avg_duration')
    ).first()

    # Конвертируем секунды в формат MM:SS для читаемости
    min_time_str = f"{duration_stats.min_duration // 60}:{duration_stats.min_duration % 60:02d}"
    max_time_str = f"{duration_stats.max_duration // 60}:{duration_stats.max_duration % 60:02d}"
    avg_time_str = f"{int(duration_stats.avg_duration) // 60}:{int(duration_stats.avg_duration) % 60:02d}"

    print()
    print_info_line("Длительность матчей", f"{min_time_str} - {max_time_str} (среднее: {avg_time_str})",
                    "⏱️", Colors.BRIGHT_WHITE, Colors.BRIGHT_TEAL)

    # === СТАТИСТИКА ПОБЕД ===
    print_subsection_header("Статистика побед", "🏆", Colors.BRIGHT_GREEN)
    win_stats = session.query(
        Match.radiant_win,
        func.count(Match.radiant_win).label('count')
    ).group_by(Match.radiant_win).all()

    for is_radiant_win, count in win_stats:
        side_name = "Radiant" if is_radiant_win else "Dire"
        side_emoji = "🌅" if is_radiant_win else "🌙"
        percentage = (count / total_matches) * 100
        print_info_line(f"{side_name} побед", f"{count:,} ({percentage:.1f}%)", side_emoji,
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_GOLD)

    # === СТАТИСТИКА ИГРОВЫХ РЕЖИМОВ ===
    print_subsection_header("Режимы игры", "🎮", Colors.BRIGHT_PURPLE)
    game_mode_stats = session.query(
        Match.game_mode,
        func.count(Match.game_mode).label('count')
    ).group_by(Match.game_mode).order_by(func.count(Match.game_mode).desc()).all()

    for mode, count in game_mode_stats:
        percentage = (count / total_matches) * 100
        print_info_line(f"Режим {mode}", f"{count:,} ({percentage:.1f}%)", "🎯",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_CORAL)

    # === СТАТИСТИКА ТИПОВ ЛОББИ ===
    print_subsection_header("Типы лобби", "🛏️", Colors.BRIGHT_ORANGE)
    lobby_stats = session.query(
        Match.lobby_type,
        func.count(Match.lobby_type).label('count')
    ).group_by(Match.lobby_type).order_by(func.count(Match.lobby_type).desc()).all()

    for lobby, count in lobby_stats:
        percentage = (count / total_matches) * 100
        print_info_line(f"Лобби {lobby}", f"{count:,} ({percentage:.1f}%)", "🛠️",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_MINT)

    # === СТАТИСТИКА СЧЕТА КОМАНД ===
    print_subsection_header("Статистика счета", "🎯", Colors.BRIGHT_YELLOW)
    score_stats = session.query(
        func.min(Match.radiant_score).label('min_rad'),
        func.max(Match.radiant_score).label('max_rad'),
        func.avg(Match.radiant_score).label('avg_rad'),
        func.min(Match.dire_score).label('min_dire'),
        func.max(Match.dire_score).label('max_dire'),
        func.avg(Match.dire_score).label('avg_dire')
    ).first()

    print_info_line("Radiant счет",
                    f"{score_stats.min_rad} - {score_stats.max_rad} (среднее: {score_stats.avg_rad:.1f})",
                    "🌅", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    print_info_line("Dire счет",
                    f"{score_stats.min_dire} - {score_stats.max_dire} (среднее: {score_stats.avg_dire:.1f})",
                    "🌙", Colors.BRIGHT_WHITE, Colors.BRIGHT_LAVENDER)

    # === СТАТИСТИКА СТРУКТУР ===
    _analyze_game_structures(session, total_matches)


def _display_data_ranges(session):
    """
    Отображает диапазоны основных полей с улучшенной читаемостью.

    Показывает подробную информацию о:
    - Порядке сбора данных (по sequence numbers)
    - Временном диапазоне матчей
    - Полных диапазонах всех ключевых полей

    Args:
        session: Сессия SQLAlchemy для работы с БД
    """
    print_subsection_header("Диапазоны основных полей", "🔢", Colors.BRIGHT_BLUE)

    # Получаем граничные матчи по порядку сбора
    first_match_by_sequence = session.query(Match).order_by(Match.match_seq_num.asc()).first()
    last_match_by_sequence = session.query(Match).order_by(Match.match_seq_num.desc()).first()

    # Получаем статистику диапазонов всех полей
    range_stats = session.query(
        func.min(Match.match_id).label('min_match_id'),
        func.max(Match.match_id).label('max_match_id'),
        func.min(Match.match_seq_num).label('min_seq'),
        func.max(Match.match_seq_num).label('max_seq'),
        func.min(Match.start_time).label('min_time'),
        func.max(Match.start_time).label('max_time')
    ).first()

    if first_match_by_sequence and last_match_by_sequence and range_stats:
        print(f"{Colors.BRIGHT_WHITE}📊 По порядку сбора данных (sequence):{Colors.RESET}")
        print(
            f"    ├─ Первый матч:  Seq {Colors.BRIGHT_YELLOW}{first_match_by_sequence.match_seq_num:,}{Colors.RESET} | Match ID {Colors.BRIGHT_CYAN}{first_match_by_sequence.match_id:,}{Colors.RESET}")
        print(
            f"    └─ Последний:    Seq {Colors.BRIGHT_YELLOW}{last_match_by_sequence.match_seq_num:,}{Colors.RESET} | Match ID {Colors.BRIGHT_CYAN}{last_match_by_sequence.match_id:,}{Colors.RESET}")

        # Вычисляем разницу для понимания охвата данных
        seq_diff = last_match_by_sequence.match_seq_num - first_match_by_sequence.match_seq_num
        match_id_diff = last_match_by_sequence.match_id - first_match_by_sequence.match_id

        print(
            f"    📏 Диапазон:     Seq {Colors.BRIGHT_GREEN}{seq_diff:,}{Colors.RESET} | Match ID {Colors.BRIGHT_GREEN}{match_id_diff:,}{Colors.RESET}")
        print()

        print(f"{Colors.BRIGHT_WHITE}🕐 Временной диапазон:{Colors.RESET}")
        first_date = format_timestamp_to_readable_date(first_match_by_sequence.start_time)
        last_date = format_timestamp_to_readable_date(last_match_by_sequence.start_time)
        print(f"    ├─ Начало:  {Colors.BRIGHT_PURPLE}{first_date}{Colors.RESET}")
        print(f"    └─ Конец:   {Colors.BRIGHT_PURPLE}{last_date}{Colors.RESET}")

        # Вычисляем временную разницу для анализа периода
        time_diff_hours = (last_match_by_sequence.start_time - first_match_by_sequence.start_time) / 3600
        if time_diff_hours >= 24:
            time_diff_days = time_diff_hours / 24
            print(
                f"    📅 Период:  {Colors.BRIGHT_GREEN}{time_diff_days:.1f} дней{Colors.RESET} ({time_diff_hours:.1f} часов)")
        else:
            print(f"    📅 Период:  {Colors.BRIGHT_GREEN}{time_diff_hours:.1f} часов{Colors.RESET}")
        print()

        print(f"{Colors.BRIGHT_WHITE}📋 Полные диапазоны (мин-макс):{Colors.RESET}")
        print(
            f"    ├─ Sequence:    {Colors.BRIGHT_YELLOW}{range_stats.min_seq:,}{Colors.RESET} → {Colors.BRIGHT_YELLOW}{range_stats.max_seq:,}{Colors.RESET}")
        print(
            f"    ├─ Match ID:    {Colors.BRIGHT_CYAN}{range_stats.min_match_id:,}{Colors.RESET} → {Colors.BRIGHT_CYAN}{range_stats.max_match_id:,}{Colors.RESET}")

        min_date = format_timestamp_to_readable_date(range_stats.min_time)
        max_date = format_timestamp_to_readable_date(range_stats.max_time)
        print(f"    └─ Время:       {Colors.BRIGHT_PURPLE}{min_date}{Colors.RESET}")
        print(f"                    {Colors.BRIGHT_PURPLE}{max_date}{Colors.RESET}")


def _analyze_game_structures(session, total_matches: int):
    """
    Анализирует статистику разрушения башен и казарм в матчах.

    Проводит подробный анализ состояния игровых структур:
    - Статистику разрушения каждой башни по типам и уровням
    - Анализ уничтожения казарм по линиям
    - Расчет среднего количества уничтоженных структур

    Использует битовые операции для интерпретации статусов структур:
    - Бит = 1: структура цела
    - Бит = 0: структура уничтожена

    Args:
        session: Сессия SQLAlchemy для работы с БД
        total_matches (int): Общее количество матчей для расчета процентов

    Note:
        Анализ основан на битовых масках TOWERS_BITMASK и BARRACKS_BITMASK из config.
        Порядок битов соответствует игровой логике Dota 2 из документации Steam API.
    """

    # Определяем структуры для анализа в правильном порядке
    tower_names = [
        # Tier 1 башни
        "Tier 1 Top", "Tier 1 Middle", "Tier 1 Bottom",
        # Tier 2 башни
        "Tier 2 Top", "Tier 2 Middle", "Tier 2 Bottom",
        # Tier 3 башни
        "Tier 3 Top", "Tier 3 Middle", "Tier 3 Bottom",
        # Ancient башни
        "Ancient Top", "Ancient Bottom"
    ]

    # Соответствующие битовые позиции для башен (согласно Dota 2 API)
    tower_bit_positions = [0, 3, 6, 1, 4, 7, 2, 5, 8, 9, 10]

    barracks_names = [
        "Top Melee", "Top Ranged",
        "Middle Melee", "Middle Ranged",
        "Bottom Melee", "Bottom Ranged"
    ]

    if total_matches > 0:
        # === АНАЛИЗ БАШЕН ===
        print_subsection_header("Статистика уничтожения башен", "🗼", Colors.BRIGHT_BLUE)

        # Подсчет уничтоженных башен для каждой команды
        for team_name, tower_field in [("Radiant", "tower_status_radiant"), ("Dire", "tower_status_dire")]:
            team_emoji = "🌅" if team_name == "Radiant" else "🌙"
            print(f"\n{team_emoji} {Colors.BOLD}{team_name}:{Colors.RESET}")

            for tower_name, bit_position in zip(tower_names, tower_bit_positions):
                # Башня уничтожена если бит в позиции bit_position равен 0
                destroyed_count = session.query(func.count(Match.match_id)).filter(
                    func.coalesce(getattr(Match, tower_field), 0).op('&')(1 << bit_position) == 0
                ).scalar()

                destroyed_percentage = (destroyed_count / total_matches) * 100
                standing_count = total_matches - destroyed_count
                standing_percentage = 100 - destroyed_percentage

                print_info_line(tower_name,
                                f"уничтожена {destroyed_count:,} раз ({destroyed_percentage:.1f}%), цела {standing_count:,} раз ({standing_percentage:.1f}%)",
                                "🗼", Colors.BRIGHT_WHITE,
                                Colors.BRIGHT_RED if destroyed_percentage > 50 else Colors.BRIGHT_GREEN)

        # === АНАЛИЗ КАЗАРМ ===
        print_subsection_header("Статистика уничтожения казарм", "🏰", Colors.BRIGHT_GREEN)

        for team_name, barracks_field in [("Radiant", "barracks_status_radiant"), ("Dire", "barracks_status_dire")]:
            team_emoji = "🌅" if team_name == "Radiant" else "🌙"
            print(f"\n{team_emoji} {Colors.BOLD}{team_name}:{Colors.RESET}")

            for bit_position, barracks_name in enumerate(barracks_names):
                # Казарма уничтожена если бит в позиции bit_position равен 0
                destroyed_count = session.query(func.count(Match.match_id)).filter(
                    func.coalesce(getattr(Match, barracks_field), 0).op('&')(1 << bit_position) == 0
                ).scalar()

                destroyed_percentage = (destroyed_count / total_matches) * 100
                standing_count = total_matches - destroyed_count
                standing_percentage = 100 - destroyed_percentage

                print_info_line(barracks_name,
                                f"уничтожена {destroyed_count:,} раз ({destroyed_percentage:.1f}%), цела {standing_count:,} раз ({standing_percentage:.1f}%)",
                                "🏰", Colors.BRIGHT_WHITE,
                                Colors.BRIGHT_RED if destroyed_percentage > 50 else Colors.BRIGHT_GREEN)

        # === ОБЩАЯ СТАТИСТИКА ПО КОЛИЧЕСТВУ УНИЧТОЖЕННЫХ СТРУКТУР ===
        _calculate_average_structures_destroyed(session)

    else:
        print_status_message("Нет данных о структурах для анализа", "error")


def _calculate_average_structures_destroyed(session):
    """
    Подсчитывает среднее количество уничтоженных башен и казарм.

    Вычисляет статистику разрушений используя битовые операции:
    - Инвертирует битовые маски (XOR с полной маской)
    - Подсчитывает единицы в результате (количество уничтоженных структур)
    - Рассчитывает средние значения по всем матчам

    Args:
        session: Сессия SQLAlchemy для работы с БД
    """
    print_subsection_header("Общая статистика разрушений", "📊", Colors.BRIGHT_PURPLE)

    try:
        # Загружаем только необходимые поля
        matches_structure_data = session.query(
            Match.tower_status_radiant,
            Match.tower_status_dire,
            Match.barracks_status_radiant,
            Match.barracks_status_dire
        ).all()

        # Инициализируем списки для подсчета
        radiant_towers_destroyed = []
        dire_towers_destroyed = []
        radiant_barracks_destroyed = []
        dire_barracks_destroyed = []

        for match in matches_structure_data:
            # Подсчет уничтоженных башен (инвертируем биты и считаем единицы)
            # XOR с полной маской дает нам биты уничтоженных структур
            radiant_tower_count = bin((match[0] or 0) ^ TOWERS_BITMASK).count('1') if match[0] is not None else 0
            dire_tower_count = bin((match[1] or 0) ^ TOWERS_BITMASK).count('1') if match[1] is not None else 0
            radiant_towers_destroyed.append(radiant_tower_count)
            dire_towers_destroyed.append(dire_tower_count)

            # Подсчет уничтоженных казарм
            radiant_barracks_count = bin((match[2] or 0) ^ BARRACKS_BITMASK).count('1') if match[2] is not None else 0
            dire_barracks_count = bin((match[3] or 0) ^ BARRACKS_BITMASK).count('1') if match[3] is not None else 0
            radiant_barracks_destroyed.append(radiant_barracks_count)
            dire_barracks_destroyed.append(dire_barracks_count)

        # Вычисляем средние значения
        if matches_structure_data:
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
        else:
            print_status_message("Нет данных для расчета статистики разрушений", "warning")

    except Exception as e:
        print_status_message(f"Ошибка при расчете статистики разрушений: {e}", "error")


def analyze_player_data(session):
    """
    Анализирует статистику игроков с оптимизированными запросами.

    Проводит комплексное исследование данных игроков:
    - Общую информацию о записях и приватных аккаунтах
    - Анализ боевой статистики (KDA)
    - Распределение и статистику по ролям
    - Популярность героев (топ-N)
    - Винрейт героев с минимальным порогом игр
    - Винрейт героев с учетом вариантов
    - Статистику вариантов героев
    - Экономические показатели (GPM, XPM, net worth)
    - Детальный анализ уровней игроков
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
    private_percentage = (private_accounts_count / total_player_records) * 100 if total_player_records > 0 else 0

    print_info_line("Общее количество записей игроков", f"{total_player_records:,}", "📊",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    print_info_line("Записей с приватными аккаунтами", f"{private_accounts_count:,} ({private_percentage:.1f}%)", "🔒",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    print_info_line("Уникальных публичных игроков", f"{unique_public_players:,}", "👤",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)

    # Предупреждение о высоком проценте приватных аккаунтов
    if private_percentage > PRIVATE_ACCOUNTS_WARNING_THRESHOLD:
        print_status_message(
            f"Аномально высокий процент приватных аккаунтов ({private_percentage:.1f}%) может влиять на точность анализа",
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
    _analyze_hero_variants_stats(session, total_player_records)

    # === АНАЛИЗ ЭКОНОМИЧЕСКОЙ СТАТИСТИКИ ===
    _analyze_economic_stats(session)

    # === АНАЛИЗ СТАТИСТИКИ УРОВНЕЙ ===
    _analyze_level_stats(session, total_player_records)

    # === АНАЛИЗ СПЕЦИАЛЬНЫХ ПРЕДМЕТОВ ===
    _analyze_special_items_stats(session, total_player_records)


def _analyze_kda_statistics(session):
    """
    Анализирует статистику KDA (убийства/смерти/помощи).

    Рассчитывает основные статистические показатели боевой активности:
    - Минимальные, максимальные и средние значения kills/deaths/assists
    - Средний коэффициент KDA по всем игрокам

    Args:
        session: Сессия SQLAlchemy для работы с БД
    """
    print_subsection_header("Статистика KDA", "⚔️", Colors.BRIGHT_RED)

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

    print_info_line("Kills",
                    f"{kda_stats.min_kills} - {kda_stats.max_kills} (среднее: {kda_stats.avg_kills:.1f})",
                    "🗡️", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
    print_info_line("Deaths",
                    f"{kda_stats.min_deaths} - {kda_stats.max_deaths} (среднее: {kda_stats.avg_deaths:.1f})",
                    "💀", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    print_info_line("Assists",
                    f"{kda_stats.min_assists} - {kda_stats.max_assists} (среднее: {kda_stats.avg_assists:.1f})",
                    "🤝", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    print_info_line("Средний KDA", f"{kda_stats.avg_kda:.2f}", "📈", Colors.BRIGHT_WHITE, Colors.BRIGHT_BLUE)


def _analyze_role_statistics(session, total_player_records: int):
    """
    Анализирует статистику игроков по ролям.

    Сравнивает показатели core и support игроков:
    - Количество и процентное распределение ролей
    - Средние показатели KDA по ролям
    - Экономические различия (GPM, XPM, net worth, last hits)

    Args:
        session: Сессия SQLAlchemy для работы с БД
        total_player_records (int): Общее количество записей для расчета процентов
    """
    print_subsection_header("Статистика по ролям", "🎭", Colors.BRIGHT_PURPLE)

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
                        "🹹", Colors.BRIGHT_WHITE, Colors.BRIGHT_MINT)


def _analyze_hero_popularity(session, total_player_records: int):
    """
    Анализирует популярность героев (топ-N).

    Показывает наиболее часто выбираемых героев с дополнительной статистикой:
    - Количество игр и процент от общего числа
    - Средний KDA для каждого героя
    - Ранжирование по популярности

    Args:
        session: Сессия SQLAlchemy для работы с БД
        total_player_records (int): Общее количество записей для расчета процентов
    """
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
    """
    Анализирует винрейт героев с использованием SQL агрегатных функций.

    Рассчитывает процент побед для каждого героя:
    - Учитывает команду игрока и результат матча
    - Применяет минимальный порог игр для статистической значимости
    - Сортирует по винрейту от высшего к низшему

    Логика определения победы:
    - Radiant игрок (team_number=0) побеждает если radiant_win=True
    - Dire игрок (team_number=1) побеждает если radiant_win=False

    Args:
        session: Сессия SQLAlchemy для работы с БД
    """
    print_subsection_header(
        f"Топ-{TOP_HEROES_DISPLAY_COUNT} героев по винрейту (мин. {MINIMUM_GAMES_FOR_HERO_WINRATE} игр)", "🏆",
        Colors.BRIGHT_GOLD)

    try:
        # Используем SQL для подсчета винрейта без загрузки всех данных в память
        hero_winrates = session.query(
            MatchPlayer.hero_id,
            func.count(MatchPlayer.id).label('total_games'),
            func.sum(
                case(
                    (
                        ((MatchPlayer.team_number == 0) & (Match.radiant_win == True)) |
                        ((MatchPlayer.team_number == 1) & (Match.radiant_win == False)),
                        1
                    ),
                    else_=0
                )
            ).label('wins')
        ).join(Match, MatchPlayer.match_id == Match.match_id) \
            .group_by(MatchPlayer.hero_id) \
            .having(func.count(MatchPlayer.id) >= MINIMUM_GAMES_FOR_HERO_WINRATE) \
            .all()

        # Вычисляем винрейт и сортируем
        hero_winrates_with_percentage = []
        for hero_id, total_games, wins in hero_winrates:
            winrate = (wins * 100.0) / total_games if total_games > 0 else 0
            hero_winrates_with_percentage.append((hero_id, total_games, wins, winrate))

        # Сортируем по винрейту
        hero_winrates_with_percentage.sort(key=lambda x: x[3], reverse=True)

        # Выводим топ-N
        for rank, (hero_id, total_games, wins, winrate) in enumerate(
                hero_winrates_with_percentage[:TOP_HEROES_DISPLAY_COUNT], 1):
            hero_name = get_hero_name_by_id(hero_id)

            rank_color = Colors.BRIGHT_GOLD if rank <= 3 else Colors.BRIGHT_SILVER if rank <= 10 else Colors.BRIGHT_WHITE
            winrate_color = Colors.BRIGHT_GREEN if winrate >= 55 else Colors.BRIGHT_YELLOW if winrate >= 50 else Colors.BRIGHT_RED

            print(f"{rank_color}{rank:2d}.{Colors.RESET} {Colors.BRIGHT_BLUE}{hero_name}{Colors.RESET}: "
                  f"{winrate_color}{winrate:.1f}%{Colors.RESET} "
                  f"({Colors.BRIGHT_WHITE}{wins}/{total_games}{Colors.RESET})")

        if not hero_winrates_with_percentage:
            print_status_message(f"Нет героев с минимум {MINIMUM_GAMES_FOR_HERO_WINRATE} играми", "warning")

    except Exception as e:
        print_status_message(f"Ошибка при расчете винрейта героев: {e}", "error")


def _analyze_hero_variant_winrates(session):
    """
    Анализирует винрейт героев с учетом вариантов.

    Рассчитывает винрейт для каждой комбинации герой+вариант:
    - Учитывает hero_variant поле для персонализированных версий героев
    - Применяет отдельный минимальный порог игр для вариантов
    - Показывает наиболее успешные варианты героев

    Args:
        session: Сессия SQLAlchemy для работы с БД

    Note:
        Варианты героев (hero_variant) могут представлять различные стили игры. влияющие на игровую статистику.
    """
    print_subsection_header(
        f"Топ-{TOP_HEROES_DISPLAY_COUNT} героев по винрейту (с вариантами, мин. {MINIMUM_GAMES_FOR_HERO_VARIANT_WINRATE} игр)",
        "🎨", Colors.BRIGHT_PURPLE)

    try:
        # Используем SQL для подсчета винрейта по вариантам
        hero_variant_winrates = session.query(
            MatchPlayer.hero_id,
            MatchPlayer.hero_variant,
            func.count(MatchPlayer.id).label('total_games'),
            func.sum(
                case(
                    (
                        ((MatchPlayer.team_number == 0) & (Match.radiant_win == True)) |
                        ((MatchPlayer.team_number == 1) & (Match.radiant_win == False)),
                        1
                    ),
                    else_=0
                )
            ).label('wins')
        ).join(Match, MatchPlayer.match_id == Match.match_id) \
            .group_by(MatchPlayer.hero_id, MatchPlayer.hero_variant) \
            .having(func.count(MatchPlayer.id) >= MINIMUM_GAMES_FOR_HERO_VARIANT_WINRATE) \
            .all()

        # Вычисляем винрейт и сортируем
        hero_variant_winrates_with_percentage = []
        for hero_id, hero_variant, total_games, wins in hero_variant_winrates:
            winrate = (wins * 100.0) / total_games if total_games > 0 else 0
            hero_variant_winrates_with_percentage.append((hero_id, hero_variant, total_games, wins, winrate))

        # Сортируем по винрейту
        hero_variant_winrates_with_percentage.sort(key=lambda x: x[4], reverse=True)

        # Выводим топ-N
        for rank, (hero_id, hero_variant, total_games, wins, winrate) in enumerate(
                hero_variant_winrates_with_percentage[:TOP_HEROES_DISPLAY_COUNT], 1):
            hero_name = get_hero_name_by_id(hero_id)
            variant_text = f" (вариант {hero_variant})" if hero_variant > 0 else ""

            rank_color = Colors.BRIGHT_GOLD if rank <= 3 else Colors.BRIGHT_SILVER if rank <= 10 else Colors.BRIGHT_WHITE
            winrate_color = Colors.BRIGHT_GREEN if winrate >= 55 else Colors.BRIGHT_YELLOW if winrate >= 50 else Colors.BRIGHT_RED

            print(
                f"{rank_color}{rank:2d}.{Colors.RESET} {Colors.BRIGHT_BLUE}{hero_name}{Colors.BRIGHT_PURPLE}{variant_text}{Colors.RESET}: "
                f"{winrate_color}{winrate:.1f}%{Colors.RESET} "
                f"({Colors.BRIGHT_WHITE}{wins}/{total_games}{Colors.RESET})")

        if not hero_variant_winrates_with_percentage:
            print_status_message(f"Нет героев с вариантами с минимум {MINIMUM_GAMES_FOR_HERO_VARIANT_WINRATE} играми",
                                 "warning")

    except Exception as e:
        print_status_message(f"Ошибка при расчете винрейта героев с вариантами: {e}", "error")


def _analyze_hero_variants_stats(session, total_player_records: int):
    """
    Анализирует статистику вариантов героев.

    Показывает распределение использования различных вариантов:
    - Общее количество различных вариантов в датасете
    - Популярность каждого варианта
    - Процентное распределение

    Args:
        session: Сессия SQLAlchemy для работы с БД
        total_player_records (int): Общее количество записей для расчета процентов
    """
    print_subsection_header("Статистика вариантов героев", "🎨", Colors.BRIGHT_PINK)

    variant_stats = session.query(
        MatchPlayer.hero_variant,
        func.count(MatchPlayer.hero_variant).label('count')
    ).group_by(MatchPlayer.hero_variant) \
        .order_by(func.count(MatchPlayer.hero_variant).desc()).all()

    print_info_line("Всего различных вариантов", f"{len(variant_stats)}", "🎭",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_PURPLE)

    for variant, count in variant_stats[:DUPLICATE_DISPLAY_LIMIT]:
        percentage = (count / total_player_records) * 100
        variant_text = f"Вариант {variant}" if variant > 0 else "Базовый (0)"
        print_info_line(variant_text, f"{count:,} ({percentage:.1f}%)", "🎨",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_MINT)


def _analyze_economic_stats(session):
    """
    Анализирует экономическую статистику игроков.

    Рассчитывает диапазоны и средние значения ключевых экономических показателей:
    - GPM (Gold Per Minute) - скорость накопления золота
    - XPM (Experience Per Minute) - скорость получения опыта  
    - Last Hits - добивание крипов для получения золота
    - Net Worth - общая стоимость всех предметов и золота

    Args:
        session: Сессия SQLAlchemy для работы с БД
    """
    print_subsection_header("Экономическая статистика", "💰", Colors.BRIGHT_GOLD)

    economic_stats = session.query(
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
                    f"{economic_stats.min_gpm} - {economic_stats.max_gpm} (среднее: {economic_stats.avg_gpm:.0f})",
                    "💰", Colors.BRIGHT_WHITE, Colors.BRIGHT_GOLD)
    print_info_line("XPM",
                    f"{economic_stats.min_xpm} - {economic_stats.max_xpm} (среднее: {economic_stats.avg_xpm:.0f})",
                    "⭐", Colors.BRIGHT_WHITE, Colors.BRIGHT_YELLOW)
    print_info_line("Last Hits",
                    f"{economic_stats.min_lh} - {economic_stats.max_lh} (среднее: {economic_stats.avg_lh:.0f})",
                    "🹹", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    print_info_line("Net Worth",
                    f"{economic_stats.min_nw:,} - {economic_stats.max_nw:,} (среднее: {economic_stats.avg_nw:,.0f})",
                    "💎", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)


def _analyze_level_stats(session, total_player_records: int):
    """
    Анализирует статистику уровней с выводом всех уровней по порядку.

    Предоставляет подробную картину распределения игроков по уровням:
    - Полное распределение от минимального до максимального уровня
    - Процентное соотношение для каждого уровня
    - Цветовое кодирование по популярности уровня
    - Сравнение средних уровней по ролям
    - Общую статистику уровней

    Args:
        session: Сессия SQLAlchemy для работы с БД
        total_player_records (int): Общее количество записей для расчета процентов
    """
    print_subsection_header("Статистика уровней", "📊", Colors.BRIGHT_BLUE)

    try:
        # Получаем распределение по всем уровням
        level_distribution = session.query(
            MatchPlayer.level,
            func.count(MatchPlayer.level).label('count')
        ).group_by(MatchPlayer.level) \
            .order_by(MatchPlayer.level).all()  # Сортируем по уровню, а не по количеству

        if not level_distribution:
            print_status_message("Нет данных об уровнях игроков", "warning")
            return

        # Создаем словарь для быстрого доступа
        level_dict = {level: count for level, count in level_distribution}

        # Определяем диапазон уровней
        min_level = min(level_dict.keys())
        max_level = max(level_dict.keys())

        print()
        print(f"{Colors.BRIGHT_WHITE}🎯 Распределение игроков по уровням ({min_level}-{max_level}):{Colors.RESET}")

        # Выводим все уровни по порядку
        for level in range(min_level, max_level + 1):
            count = level_dict.get(level, 0)
            if count > 0:
                percentage = (count / total_player_records) * 100

                # Цветовая кодировка в зависимости от популярности уровня
                if percentage >= 8.0:
                    color = Colors.BRIGHT_GREEN
                elif percentage >= 5.0:
                    color = Colors.BRIGHT_YELLOW
                elif percentage >= 2.0:
                    color = Colors.BRIGHT_ORANGE
                else:
                    color = Colors.BRIGHT_WHITE

                print(
                    f"    {Colors.BRIGHT_BLUE}Уровень {level:2d}:{Colors.RESET} {color}{count:>8,}{Colors.RESET} игроков ({color}{percentage:4.1f}%{Colors.RESET})")
            else:
                # Если уровень не найден в данных
                print(
                    f"    {Colors.BRIGHT_BLUE}Уровень {level:2d}:{Colors.RESET} {Colors.BRIGHT_BLACK}       0{Colors.RESET} игроков ({Colors.BRIGHT_BLACK} 0.0%{Colors.RESET})")

        # Средний уровень по ролям
        role_level_stats = session.query(
            MatchPlayer.role,
            func.avg(MatchPlayer.level).label('avg_level'),
            func.min(MatchPlayer.level).label('min_level'),
            func.max(MatchPlayer.level).label('max_level')
        ).group_by(MatchPlayer.role).all()

        print()
        print(f"{Colors.BRIGHT_WHITE}🎭 Статистика по ролям:{Colors.RESET}")
        for role_data in role_level_stats:
            role = role_data.role
            avg_level = role_data.avg_level
            min_level = role_data.min_level
            max_level = role_data.max_level

            role_emoji = "⚔️" if role == "core" else "🛡️"
            role_color = Colors.BRIGHT_RED if role == "core" else Colors.BRIGHT_CYAN

            print(
                f"    {role_emoji} {role_color}{role.upper():<8}{Colors.RESET} среднее {Colors.BRIGHT_YELLOW}{avg_level:5.1f}{Colors.RESET}, диапазон {min_level}-{max_level}")

        # Общая статистика уровней
        overall_level_stats = session.query(
            func.min(MatchPlayer.level).label('min_level'),
            func.max(MatchPlayer.level).label('max_level'),
            func.avg(MatchPlayer.level).label('avg_level')
        ).first()

        print()
        print(f"{Colors.BRIGHT_WHITE}📈 Общая статистика:{Colors.RESET}")
        print(f"    Средний уровень: {Colors.BRIGHT_YELLOW}{overall_level_stats.avg_level:.2f}{Colors.RESET}")
        print(
            f"    Диапазон:        {Colors.BRIGHT_CYAN}{overall_level_stats.min_level} - {overall_level_stats.max_level}{Colors.RESET}")
        print(f"    Всего уровней:   {Colors.BRIGHT_GREEN}{len(level_distribution)}{Colors.RESET}")

    except Exception as e:
        print_status_message(f"Ошибка при анализе уровней: {e}", "error")


def _analyze_special_items_stats(session, total_player_records: int):
    """
    Анализирует статистику специальных предметов (Aghanim's).

    Подсчитывает использование ключевых эндгейм предметов:
    - Aghanim's Scepter - усиливает ультимативную способность героя
    - Aghanim's Shard - дает дополнительную способность или усиливает существующую  
    - Moon Shard - увеличивает скорость атаки, может быть съеден

    Args:
        session: Сессия SQLAlchemy для работы с БД
        total_player_records (int): Общее количество записей для расчета процентов

    Note:
        Предметы считаются использованными если соответствующее поле > 0
    """
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


def generate_final_report(program_start_time: float, matches_count: int,
                          players_count: int, integrity_issues: list, session):
    """
    Генерирует итоговый отчет с наиболее важной аналитической информацией.

    Создает комплексный отчет о качестве и состоянии датасета:
    - Объем проанализированных данных
    - Баланс и качество данных (винрейт команд, средняя длительность)
    - Временной охват данных
    - Производительность анализа
    - Статус целостности данных
    - Рекомендации по использованию

    Args:
        program_start_time (float): Время начала выполнения программы
        matches_count (int): Количество матчей в датасете
        players_count (int): Количество записей игроков
        integrity_issues (list): Список найденных проблем целостности
        session: Сессия SQLAlchemy для дополнительных запросов
    """
    end_time = time.time()
    execution_time = end_time - program_start_time

    # Получаем ключевые статистические данные для отчета
    try:
        # Статистика матчей
        radiant_wins = session.query(func.count(Match.match_id)).filter(Match.radiant_win == True).scalar()
        dire_wins = session.query(func.count(Match.match_id)).filter(Match.radiant_win == False).scalar()
        radiant_winrate = (radiant_wins / matches_count * 100) if matches_count > 0 else 0
        dire_winrate = (dire_wins / matches_count * 100) if matches_count > 0 else 0

        # Временной диапазон
        time_range = session.query(
            func.min(Match.start_time).label('min_time'),
            func.max(Match.start_time).label('max_time')
        ).first()

        days_covered = 0
        if time_range and time_range.min_time and time_range.max_time:
            days_covered = (time_range.max_time - time_range.min_time) / (24 * 3600)

        # Статистика игроков
        unique_players = session.query(func.count(distinct(MatchPlayer.account_id))).filter(
            MatchPlayer.account_id != PRIVATE_ACCOUNT_ID).scalar()

        # Средняя длительность матча
        avg_duration = session.query(func.avg(Match.duration)).scalar()
        avg_duration_str = f"{int(avg_duration) // 60}:{int(avg_duration) % 60:02d}" if avg_duration else "N/A"

    except Exception as e:
        print_status_message(f"Ошибка получения статистики для отчета: {e}", "warning")
        radiant_winrate = 0
        dire_winrate = 0
        days_covered = 0
        unique_players = 0
        avg_duration_str = "N/A"

    print_section_header("ИТОГОВЫЙ ОТЧЕТ", "📋", 80, Colors.BRIGHT_GOLD)

    print(f"{Colors.BRIGHT_WHITE}📊 Объем проанализированных данных:{Colors.RESET}")
    print(f"    ├─ Матчей обработано:     {Colors.BRIGHT_GREEN}{matches_count:>10,}{Colors.RESET}")
    print(f"    ├─ Записей игроков:       {Colors.BRIGHT_CYAN}{players_count:>10,}{Colors.RESET}")
    print(f"    ├─ Уникальных игроков:    {Colors.BRIGHT_BLUE}{unique_players:>10,}{Colors.RESET}")
    print(f"    └─ Героев в базе:         {Colors.BRIGHT_PURPLE}{len(HEROES_CACHE):>10,}{Colors.RESET}")

    print()
    print(f"{Colors.BRIGHT_WHITE}⚖️ Баланс и качество данных:{Colors.RESET}")
    print(f"    ├─ Radiant винрейт:       {Colors.BRIGHT_GREEN}{radiant_winrate:>9.1f}%{Colors.RESET}")
    print(f"    ├─ Dire винрейт:          {Colors.BRIGHT_RED}{dire_winrate:>9.1f}%{Colors.RESET}")
    print(f"    ├─ Средняя длительность:  {Colors.BRIGHT_ORANGE}{avg_duration_str:>10}{Colors.RESET}")
    print(f"    └─ Временной охват:       {Colors.BRIGHT_MAGENTA}{days_covered:>9.1f} дней{Colors.RESET}")

    print()
    print(f"{Colors.BRIGHT_WHITE}⏱️ Производительность анализа:{Colors.RESET}")
    print(f"    └─ Время выполнения:      {Colors.BRIGHT_YELLOW}{execution_time:>9.2f} сек{Colors.RESET}")

    # Статус целостности данных
    if integrity_issues:
        print()
        print(f"{Colors.BRIGHT_WHITE}⚠️ Проблемы целостности данных:{Colors.RESET}")
        print(f"    Обнаружено проблем: {Colors.BRIGHT_RED}{len(integrity_issues)}{Colors.RESET}")
        for i, issue in enumerate(integrity_issues, 1):
            print(f"    {i}. {Colors.BRIGHT_RED}{issue}{Colors.RESET}")

    print_section_header("АНАЛИЗ ЗАВЕРШЕН", "🎉", 80, Colors.BRIGHT_GREEN)

    # Информация о готовности данных
    if not integrity_issues:
        print(f"✅ {Colors.BRIGHT_GREEN}Все проверки целостности пройдены успешно!{Colors.RESET}")
        print(f"✅ {Colors.BRIGHT_GREEN}Данные готовы для использования в анализе и машинном обучении{Colors.RESET}")
    else:
        print(f"⚠️ {Colors.BRIGHT_YELLOW}Обнаружены проблемы целостности данных{Colors.RESET}")
        print(f"🔍 {Colors.BRIGHT_YELLOW}Рекомендуется исправить проблемы перед использованием данных{Colors.RESET}")


def main():
    """
    Главная функция для комплексной проверки и анализа датасета Dota 2.
    Выполняет полный цикл валидации и анализа датасета:

    1. Инициализация:
       - Подключение к базе данных
       - Загрузка кэша героев
       - Проверка существования таблиц

    2. Информационный анализ:
       - Получение размера и веса датасета
       - Отображение основной статистики

    3. Проверки целостности:
       - Валидация уникальности ключевых полей
       - Контроль NULL значений
       - Проверка связности данных между таблицами

    4. Аналитические исследования:
       - Статистика матчей (длительность, режимы, структуры)
       - Статистика игроков (KDA, роли, экономика, уровни)
       - Анализ героев (популярность, винрейт, варианты)

    5. Итоговый отчет:
       - Суммарная статистика качества данных
       - Рекомендации по использованию
       - Производительность анализа

    Optimizations applied:
    - Использование SQL агрегатных функций вместо загрузки данных в память
    - Оптимизированные запросы для проверки связности данных  
    - Эффективная работа с битовыми масками для структур
    - Минимизация количества обращений к БД

    Raises:
        SQLAlchemyError: При ошибках работы с базой данных
        DatabaseConnectionError: При проблемах с подключением к БД
        DataValidationError: При критических ошибках валидации данных
    """
    program_start_time = time.time()
    session = None  # Инициализируем переменную для корректной работы в finally

    # === ЗАГОЛОВОК ПРОГРАММЫ ===
    print_section_header("КОМПЛЕКСНАЯ ПРОВЕРКА И АНАЛИЗ ДАТАСЕТА DOTA 2", "🔍", 100, Colors.BRIGHT_CYAN)

    # === ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ ===
    try:
        engine = create_engine(DATASET_DATABASE_URL)
        session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)  # lowercase
        session = session_local()

        print_status_message("Подключение к базе данных установлено", "success", "✅")

        # === ЗАГРУЗКА ДАННЫХ О ГЕРОЯХ ===
        if not initialize_heroes_cache():
            print_status_message("КРИТИЧЕСКАЯ ОШИБКА: Не удалось загрузить данные героев!", "error", "💥")
            print_status_message("Убедитесь, что база данных героев существует и заполнена.", "warning", "⚠️")
            return

        # === ПРОВЕРКА СУЩЕСТВОВАНИЯ ТАБЛИЦ ===
        db_info = get_database_info(session)
        try:
            matches_count = db_info['matches_count']
            players_count = db_info['players_count']
        except KeyError as e:
            print_status_message(f"Ошибка при получении информации о таблицах: отсутствует ключ {e}", "error")
            return
        except (TypeError, ValueError) as e:
            print_status_message(f"Ошибка при обработке данных таблиц: {e}", "error")
            return

        if matches_count == 0 and players_count == 0:
            print_status_message("База данных пуста, нет данных для анализа", "error")
            return

        # === ПОЛУЧЕНИЕ И ОТОБРАЖЕНИЕ ИНФОРМАЦИИ О ВЕСЕ ДАТАСЕТА ===
        display_database_info(db_info)

        # === ВЫПОЛНЕНИЕ АНАЛИЗА ===
        # 1. Проверка целостности данных
        integrity_issues = validate_data(session)

        # 2. Анализ статистики матчей
        analyze_match_data(session)

        # 3. Анализ статистики игроков
        analyze_player_data(session)

        # === ИТОГОВЫЙ ОТЧЕТ ===
        generate_final_report(program_start_time, matches_count, players_count, integrity_issues, session)

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка базы данных: {e}", "error")
    except ConnectionError as e:
        print_status_message(f"Ошибка подключения к базе данных: {e}", "error")
    except FileNotFoundError as e:
        print_status_message(f"Файл базы данных не найден: {e}", "error")
    except PermissionError as e:
        print_status_message(f"Нет прав доступа к базе данных: {e}", "error")
    except (RuntimeError, ValueError) as e:
        print_status_message(f"Ошибка выполнения: {e}", "error")
    finally:
        # Проверяем, что session был создан перед попыткой закрытия
        if session is not None:
            try:
                session.close()
                print_status_message("Соединение с базой данных закрыто", "success")
            except SQLAlchemyError as e:
                print_status_message(f"Ошибка при закрытии соединения: {e}", "warning")
            except Exception as e:
                print_status_message(f"Неожиданная ошибка при закрытии соединения: {e}", "warning")


if __name__ == "__main__":
    main()
