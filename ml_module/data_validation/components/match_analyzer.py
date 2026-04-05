"""
Модуль анализа статистики матчей Dota 2.

Модуль предоставляет комплексный анализ игровых матчей на основе данных
из БД. Включает анализ временных диапазонов, длительности игр,
статистики побед, игровых режимов, типов лобби, счета и разрушения
игровых структур (башни, казармы).

Виды анализа:
1. Диапазоны основных полей:
   - Match ID: минимальный, максимальный и диапазон
   - Match Sequence Number: порядковые номера матчей в API
   - Временной диапазон: даты начала первого и последнего матчей
   - Период сбора данных в днях и часах

2. Длительность матчей:
   - Минимальная длительность в формате MM:SS
   - Максимальная длительность в формате MM:SS
   - Средняя длительность игр

3. Статистика побед:
   - Количество побед Radiant
   - Количество побед Dire
   - Процентное соотношение побед сторон

4. Игровые режимы (Game Mode):
   - Распределение матчей по режимам
   - Процент каждого режима от общего числа

5. Типы лобби (Lobby Type):
   - Распределение по типам лобби
   - Процент каждого типа от общего числа

6. Статистика счета (Score):
   - Диапазон убийств для Radiant
   - Диапазон убийств для Dire
   - Средний счет для обеих сторон

7. Статистика разрушения игровых структур:
   a) Башни (Towers):
      - Процент уничтожения каждой башни
      - Среднее количество уничтоженных башен

   b) Казармы (Barracks):
      - Процент уничтожения каждой казармы
      - Среднее количество уничтоженных казарм

Статус структур хранится в виде битовых масок:
бит = 1 — структура стоит, бит = 0 — уничтожена.

Для определения уничтоженных структур выполняется XOR с полной маской
и подсчитывается количество единиц в результате.
"""

# Сторонние библиотеки
from sqlalchemy import func
from sqlalchemy.orm import Session

# Локальные импорты
from data_validation.components.database_config import DatabaseConfig
from config import TOWERS_BITMASK, BARRACKS_BITMASK
from utils.time_utils import format_timestamp
from utils.console import (
    Colors,
    print_section_header,
    print_subsection_header,
    print_info_line,
    print_status_message
)


class MatchAnalyzer:
    """
    Анализатор статистики матчей Dota 2 на основе данных из базы.

    Класс выполняет анализ игровых матчей: временные диапазоны,
    длительность, статистику побед, режимы игры, типы лобби, счет и
    разрушение игровых структур (башни, казармы).

    Attributes:
        session (Session): Активная сессия SQLAlchemy для запросов к БД
        config (DatabaseConfig): Конфигурация с моделями и типом БД
    """

    def __init__(self, session: Session, config: DatabaseConfig) -> None:
        """
        Инициализирует модуль для выбранного типа базы данных.

        Args:
            session (Session): Активная сессия SQLAlchemy
            config (DatabaseConfig): Конфигурация с моделями и типом БД
        """

        self.session = session
        self.config = config

    def analyze(self) -> None:
        """
        Оркестратор. Выполняет полный анализ статистики матчей.

        Последовательно запускает все виды анализа:
        1. Диапазоны основных полей (Match ID, Sequence, время)
        2. Длительность матчей
        3. Статистика побед (Radiant/Dire)
        4. Игровые режимы (Game Mode)
        5. Типы лобби (Lobby Type)
        6. Статистика счета
        7. Статистика разрушения структур (башни и казармы)

        При отсутствии данных выводит сообщение об ошибке и прерывает анализ.
        """

        # Вывод заголовка с типом базы данных
        header = f"СТАТИСТИКА МАТЧЕЙ ({self.config.database_type.upper()})"
        print_section_header(header, "📊", color=Colors.BRIGHT_MAGENTA)

        # Получение общего количества матчей
        total_matches = self.session.query(
            func.count(self.config.match_model.match_id)
        ).scalar()

        # Проверка на наличие данных
        if total_matches == 0:
            print_status_message("Нет данных для анализа", "error")
            return

        # Последовательное выполнение всех видов анализа
        self._display_data_ranges()
        self._analyze_duration()
        self._analyze_win_statistics(total_matches)
        self._analyze_game_modes(total_matches)
        self._analyze_lobby_types(total_matches)
        self._analyze_score_statistics()
        self._analyze_game_structures(total_matches)

    def _display_data_ranges(self) -> None:
        """
        Отображает диапазоны основных полей матчей.

        Выводит информацию о:
        - Первом и последнем матчах по sequence number
        - Диапазоне Match ID и Sequence Number
        - Временном диапазоне сбора данных
        - Периоде в днях и часах
        """

        print_subsection_header("Диапазоны основных полей", "🔢", Colors.BRIGHT_BLUE)

        # Получение первого и последнего матчей по sequence
        first_match = self.session.query(self.config.match_model).order_by(
            self.config.match_model.match_seq_num.asc()
        ).first()

        last_match = self.session.query(self.config.match_model).order_by(
            self.config.match_model.match_seq_num.desc()
        ).first()

        # Получение статистики диапазонов
        range_stats = self.session.query(
            func.min(self.config.match_model.match_id).label('min_match_id'),
            func.max(self.config.match_model.match_id).label('max_match_id'),
            func.min(self.config.match_model.match_seq_num).label('min_seq'),
            func.max(self.config.match_model.match_seq_num).label('max_seq'),
            func.min(self.config.match_model.start_time).label('min_time'),
            func.max(self.config.match_model.start_time).label('max_time')
        ).first()


        if first_match and last_match and range_stats:
            # === ДИАПАЗОН SEQUENCE И MATCH ID ===
            print(f"{Colors.BRIGHT_WHITE}📊 По порядку сбора данных (sequence):{Colors.RESET}")
            print(
                f"    ├─ Первый матч:  Seq {Colors.BRIGHT_YELLOW}{first_match.match_seq_num:,}{Colors.RESET} | "
                f"Match ID {Colors.BRIGHT_CYAN}{first_match.match_id:,}{Colors.RESET}"
            )
            print(
                f"    └─ Последний:    Seq {Colors.BRIGHT_YELLOW}{last_match.match_seq_num:,}{Colors.RESET} | "
                f"Match ID {Colors.BRIGHT_CYAN}{last_match.match_id:,}{Colors.RESET}"
            )

            # Расчет диапазонов
            seq_diff = last_match.match_seq_num - first_match.match_seq_num
            match_id_diff = last_match.match_id - first_match.match_id

            print(
                f"    📏 Диапазон:     Seq {Colors.BRIGHT_GREEN}{seq_diff:,}{Colors.RESET} | "
                f"Match ID {Colors.BRIGHT_GREEN}{match_id_diff:,}{Colors.RESET}"
            )

            # === ВРЕМЕННОЙ ДИАПАЗОН ===
            print()
            print(f"{Colors.BRIGHT_WHITE}🕐 Временной диапазон:{Colors.RESET}")

            first_date = format_timestamp(first_match.start_time)
            last_date = format_timestamp(last_match.start_time)

            print(f"    ├─ Начало:  {Colors.BRIGHT_PURPLE}{first_date}{Colors.RESET}")
            print(f"    └─ Конец:   {Colors.BRIGHT_PURPLE}{last_date}{Colors.RESET}")

            # Расчет периода
            time_diff_hours = (last_match.start_time - first_match.start_time) / 3600

            if time_diff_hours >= 24:
                time_diff_days = time_diff_hours / 24
                print(
                    f"    📅 Период:  {Colors.BRIGHT_GREEN}{time_diff_days:.1f} дней{Colors.RESET} "
                    f"({time_diff_hours:.1f} часов)"
                )
            else:
                print(f"    📅 Период:  {Colors.BRIGHT_GREEN}{time_diff_hours:.1f} часов{Colors.RESET}")

    def _analyze_duration(self) -> None:
        """
        Анализирует длительность матчей (мин/макс/среднее).

        Длительность форматируется в MM:SS.
        """

        # Запрос для статистики длительности
        duration_stats = self.session.query(
            func.min(self.config.match_model.duration).label('min_duration'),
            func.max(self.config.match_model.duration).label('max_duration'),
            func.avg(self.config.match_model.duration).label('avg_duration')
        ).first()

        if not duration_stats:
            print_status_message("Нет данных о длительности матчей", "error")
            return

        # Форматирование длительности в MM:SS
        min_time = f"{duration_stats.min_duration // 60}:{duration_stats.min_duration % 60:02d}"
        max_time = f"{duration_stats.max_duration // 60}:{duration_stats.max_duration % 60:02d}"
        avg_time = f"{int(duration_stats.avg_duration) // 60}:{int(duration_stats.avg_duration) % 60:02d}"

        print()
        print_info_line(
            "Длительность матчей",
            f"{min_time} - {max_time} (среднее: {avg_time})",
            "⏱️",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_TEAL
        )

    def _analyze_win_statistics(self, total_matches: int) -> None:
        """
        Анализирует статистику побед по сторонам (Radiant/Dire).

        Args:
            total_matches (int): Общее количество матчей для расчёта процентов
        """

        print_subsection_header("Статистика побед", "🏆", Colors.BRIGHT_GREEN)

        # Запрос статистики побед с группировкой по radiant_win
        win_stats = self.session.query(
            self.config.match_model.radiant_win,
            func.count(self.config.match_model.radiant_win).label('count')
        ).group_by(self.config.match_model.radiant_win).order_by(
            self.config.match_model.radiant_win.desc()
        ).all()

        # Вывод статистики для каждой стороны
        for is_radiant_win, count in win_stats:
            side_name = "Radiant" if is_radiant_win else "Dire"
            side_emoji = "🌅" if is_radiant_win else "🌙"
            percentage = (count / total_matches) * 100

            print_info_line(
                f"{side_name} побед",
                f"{count:,} ({percentage:.1f}%)",
                side_emoji,
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_GOLD
            )

    def _analyze_game_modes(self, total_matches: int) -> None:
        """
        Анализирует распределение матчей по игровым режимам.

        Args:
            total_matches (int): Общее количество матчей для расчёта процентов
        """

        print_subsection_header("Режимы игры", "🎮", Colors.BRIGHT_PURPLE)

        # Запрос статистики режимов с группировкой и сортировкой
        game_mode_stats = self.session.query(
            self.config.match_model.game_mode,
            func.count(self.config.match_model.game_mode).label('count')
        ).group_by(self.config.match_model.game_mode).order_by(
            func.count(self.config.match_model.game_mode).desc()
        ).all()

        # Вывод статистики для каждого режима
        for mode, count in game_mode_stats:
            percentage = (count / total_matches) * 100

            print_info_line(
                f"Режим {mode}",
                f"{count:,} ({percentage:.1f}%)",
                "🎯",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_CORAL
            )

    def _analyze_lobby_types(self, total_matches: int) -> None:
        """
        Анализирует распределение матчей по типам лобби.

        Args:
            total_matches (int): Общее количество матчей для расчёта процентов
        """

        print_subsection_header("Типы лобби", "🛡️", Colors.BRIGHT_ORANGE)

        # Запрос статистики лобби с группировкой и сортировкой
        lobby_stats = self.session.query(
            self.config.match_model.lobby_type,
            func.count(self.config.match_model.lobby_type).label('count')
        ).group_by(self.config.match_model.lobby_type).order_by(
            func.count(self.config.match_model.lobby_type).desc()
        ).all()

        # Вывод статистики для каждого типа
        for lobby, count in lobby_stats:
            percentage = (count / total_matches) * 100

            print_info_line(
                f"Лобби {lobby}",
                f"{count:,} ({percentage:.1f}%)",
                "🛠️",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_MINT
            )

    def _analyze_score_statistics(self) -> None:
        """
        Анализирует статистику счёта (убийств героев) для каждой стороны.

        Рассчитывает мин/макс/среднее количество убийств
        для Radiant и Dire.
        """

        print_subsection_header("Статистика счета", "🎯", Colors.BRIGHT_YELLOW)

        # Агрегирующий запрос для статистики счета обеих сторон
        score_stats = self.session.query(
            func.min(self.config.match_model.radiant_score).label('min_rad'),
            func.max(self.config.match_model.radiant_score).label('max_rad'),
            func.avg(self.config.match_model.radiant_score).label('avg_rad'),
            func.min(self.config.match_model.dire_score).label('min_dire'),
            func.max(self.config.match_model.dire_score).label('max_dire'),
            func.avg(self.config.match_model.dire_score).label('avg_dire')
        ).first()

        if not score_stats:
            print_status_message("Нет данных о счёте матчей", "error")
            return

        # Вывод статистики для Radiant
        print_info_line(
            "Radiant счет",
            f"{score_stats.min_rad} - {score_stats.max_rad} "
            f"(среднее: {score_stats.avg_rad:.1f})",
            "🌅",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_YELLOW
        )

        # Вывод статистики для Dire
        print_info_line(
            "Dire счет",
            f"{score_stats.min_dire} - {score_stats.max_dire} "
            f"(среднее: {score_stats.avg_dire:.1f})",
            "🌙",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_LAVENDER
        )

    def _analyze_game_structures(self, total_matches: int) -> None:
        """
        Анализирует статистику разрушения игровых структур (башен и казарм).

        Структуры:
        - Башни (11 на сторону): 3 линии × 3 яруса + 2 у Ancient
        - Казармы (6 на сторону): 3 линии × 2 типа (Melee, Ranged)

        Статус структур хранится в виде битовых масок:
        бит = 1 — структура стоит, бит = 0 — уничтожена.

        Для каждой структуры выводится количество и процент уничтожений,
        а также средние значения разрушений по всем матчам.

        Args:
            total_matches (int): Общее количество матчей для расчёта процентов
        """

        # Загрузка данных о структурах одним запросом
        matches_data = self.session.query(
            self.config.match_model.tower_status_radiant,
            self.config.match_model.tower_status_dire,
            self.config.match_model.barracks_status_radiant,
            self.config.match_model.barracks_status_dire
        ).all()

        # Названия башен, сгруппированные по ярусам
        tower_names = [
            "Tier 1 Top", "Tier 1 Middle", "Tier 1 Bottom",
            "Tier 2 Top", "Tier 2 Middle", "Tier 2 Bottom",
            "Tier 3 Top", "Tier 3 Middle", "Tier 3 Bottom",
            "Ancient Top", "Ancient Bottom"
        ]

        # Битовые позиции башен: API хранит биты по линиям (Top: 0,1,2; Mid: 3,4,5; Bot: 6,7,8),
        # а мы выводим по ярусам, поэтому порядок переставлен
        tower_bit_positions = [0, 3, 6, 1, 4, 7, 2, 5, 8, 9, 10]

        barracks_names = [
            "Top Melee", "Top Ranged",
            "Middle Melee", "Middle Ranged",
            "Bottom Melee", "Bottom Ranged"
        ]

        # Индексы полей в результате запроса
        structure_fields = {
            "Radiant": {"towers": 0, "barracks": 2},
            "Dire": {"towers": 1, "barracks": 3},
        }
        team_emojis = {"Radiant": "🌅", "Dire": "🌙"}

        # === АНАЛИЗ БАШЕН ===
        print_subsection_header("Статистика уничтожения башен", "🗼", Colors.BRIGHT_BLUE)

        # Подсчёт уничтожений каждой башни для обеих сторон
        tower_destroyed = {team: [0] * len(tower_names) for team in structure_fields}

        for match in matches_data:
            for team, fields in structure_fields.items():
                status = match[fields["towers"]]
                for i, bit_pos in enumerate(tower_bit_positions):
                    if status & (1 << bit_pos) == 0:
                        tower_destroyed[team][i] += 1

        for idx, team in enumerate(structure_fields):
            prefix = "\n" if idx > 0 else ""
            print(f"{prefix}{team_emojis[team]} {Colors.BOLD}{team}:{Colors.RESET}")
            for i, name in enumerate(tower_names):
                count = tower_destroyed[team][i]
                pct = (count / total_matches) * 100
                color = Colors.BRIGHT_RED if pct > 50 else Colors.BRIGHT_GREEN
                print_info_line(
                    name,
                    f"уничтожена {count:,} раз ({pct:.1f}%)",
                    "🗼", Colors.BRIGHT_WHITE, color
                )

        # === АНАЛИЗ КАЗАРМ ===
        print_subsection_header("Статистика уничтожения казарм", "🏰", Colors.BRIGHT_GREEN)

        barracks_destroyed = {team: [0] * len(barracks_names) for team in structure_fields}

        for match in matches_data:
            for team, fields in structure_fields.items():
                status = match[fields["barracks"]]
                for bit_pos in range(len(barracks_names)):
                    if status & (1 << bit_pos) == 0:
                        barracks_destroyed[team][bit_pos] += 1

        for idx, team in enumerate(structure_fields):
            prefix = "\n" if idx > 0 else ""
            print(f"{prefix}{team_emojis[team]} {Colors.BOLD}{team}:{Colors.RESET}")
            for i, name in enumerate(barracks_names):
                count = barracks_destroyed[team][i]
                pct = (count / total_matches) * 100
                color = Colors.BRIGHT_RED if pct > 50 else Colors.BRIGHT_GREEN
                print_info_line(
                    name,
                    f"уничтожена {count:,} раз ({pct:.1f}%)",
                    "🏰", Colors.BRIGHT_WHITE, color
                )

        # === ОБЩАЯ СТАТИСТИКА ===
        self._calculate_average_structures_destroyed(matches_data)

    @staticmethod
    def _calculate_average_structures_destroyed(matches_data: list) -> None:
        """
        Рассчитывает среднее количество уничтоженных башен и казарм за матч.

        Алгоритм: XOR статуса с полной маской даёт биты уничтоженных структур,
        bin().count('1') — количество разрушений.

        Args:
            matches_data: Список кортежей (tower_radiant, tower_dire,
                          barracks_radiant, barracks_dire) из БД
        """

        print_subsection_header("Общая статистика разрушений", "📊", Colors.BRIGHT_PURPLE)

        try:
            # (индекс_поля, битовая_маска, максимум_структур, тип)
            structure_config = [
                (0, TOWERS_BITMASK, 11, "башен"),
                (2, BARRACKS_BITMASK, 6, "казарм"),
                (1, TOWERS_BITMASK, 11, "башен"),
                (3, BARRACKS_BITMASK, 6, "казарм"),
            ]

            # Соответствие: индекс поля → сторона
            team_info = {
                0: ("Radiant", "🌅", Colors.BRIGHT_YELLOW),
                2: ("Radiant", "🌅", Colors.BRIGHT_ORANGE),
                1: ("Dire", "🌙", Colors.BRIGHT_CYAN),
                3: ("Dire", "🌙", Colors.BRIGHT_MAGENTA),
            }

            for field_idx, bitmask, max_count, structure_type in structure_config:
                total_destroyed = sum(
                    bin(match[field_idx] ^ bitmask).count('1')
                    for match in matches_data
                )
                avg = total_destroyed / len(matches_data)
                team_name, emoji, color = team_info[field_idx]

                print_info_line(
                    f"{team_name} - Среднее {structure_type} уничтожено",
                    f"{avg:.1f}/{max_count}",
                    emoji, Colors.BRIGHT_WHITE, color
                )

        except Exception as e:
            print_status_message(f"Ошибка при расчёте статистики разрушений: {e}", "error")
