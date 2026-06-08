"""
Модуль проверки целостности и валидности данных Dota 2 матчей.

Модуль реализует комплексную систему проверки данных в базах матчей,
обеспечивая выявление аномалий, дубликатов, NULL значений, некорректных
связей и других проблем целостности.

Типы проверок:
1. Проверка уникальности идентификаторов:
   - Уникальность match_id (первичный ключ)
   - Уникальность match_seq_num (порядковый номер от Steam API)
   - Обнаружение дубликатов с указанием количества

2. Проверка NULL значений:
   - Критические поля таблицы Match (match_id, duration, start_time и т.д.)
   - Критические поля таблицы MatchPlayer (hero_id, team_number, role и т.д.)
   - Подсчет количества NULL значений для каждого поля

3. Проверка корректности значений:
   - Роли игроков
   - Номера команд
   - Количество игроков в матче

4. Проверка связности данных:
   - Наличие записей Match для всех записей MatchPlayer
   - Отсутствие "игроков-сирот" без соответствующего матча

5. Дополнительные проверки для профессиональных матчей:
   - Консистентность данных команд (team_id / team_name)
   - Консистентность данных лиг (leagueid / league_name)
   - Валидность типов серий
"""

# Стандартные библиотеки
from typing import List, Tuple

# Сторонние библиотеки
from sqlalchemy import func, case
from sqlalchemy.orm import Session

# Локальные импорты
from data_validation.components.database_config import DatabaseConfig
from data_validation.components.pro_analyzer import ProAnalyzer
from config import TEAM_SIZE, RADIANT_INDEX, DIRE_INDEX, ROLE_MAPPING
from utils.console import (
    Colors,
    print_section_header,
    print_subsection_header,
    print_info_line,
    print_status_message
)

# === КОНСТАНТЫ ОТОБРАЖЕНИЯ ===
DUPLICATE_DISPLAY_LIMIT = 10    # Максимальное количество дубликатов для отображения


class IntegrityChecker:
    """
     Класс для проверки целостности и валидности данных Dota 2 матчей.

    Выполняет комплексную проверку данных в базе матчей:
    уникальность идентификаторов, отсутствие NULL значений, корректность
    значений полей, связность данных между таблицами. Накапливает список
    найденных проблем для итогового отчёта в DataValidator.

    Attributes:
        session (Session): Активная сессия SQLAlchemy для запросов к БД
        config (DatabaseConfig): Конфигурация типа базы данных
        issues (List[str]): Список найденных проблем целостности
    """

    def __init__(self, session: Session, config: DatabaseConfig) -> None:
        """
        Инициализирует чекер для выбранного типа базы данных.

        Args:
            session (Session): Активная сессия SQLAlchemy
            config (DatabaseConfig): Конфигурация с моделями и типом БД
        """

        self.session = session
        self.config = config
        self.issues: List[str] = []

    def check(self) -> List[str]:
        """
        Оркестратор. Выполняет комплексную проверку целостности и валидности данных.

        Последовательно запускает все виды проверок:
        1. Уникальность match_id
        2. Уникальность match_seq_num
        3. NULL значения в таблице Match
        4. NULL значения в таблице MatchPlayer
        5. Корректность ролей игроков
        6. Корректность номеров команд
        7. Количество игроков в матчах
        8. Связность данных между таблицами
        9. Проверки профессиональных данных (если применимо)

        Все найденные проблемы накапливаются в self.issues и выводятся
        в итоговой сводке с цветовым кодированием.

        Returns:
            List[str]: Список строк с описанием найденных проблем
        """

        # Вывод заголовка с типом базы данных
        header = (
            f"ПРОВЕРКА ЦЕЛОСТНОСТИ И ВАЛИДНОСТИ ДАННЫХ "
            f"({self.config.database_type.upper()})"
        )
        print_section_header(header, "🔍", color=Colors.BRIGHT_CYAN)

        # Последовательное выполнение всех проверок
        self._check_unique_match_ids()
        self._check_unique_sequence_numbers()
        self._check_null_values_matches()
        self._check_null_values_players()
        self._check_roles()
        self._check_team_numbers()
        self._check_player_counts()
        self._check_data_consistency()

        # Дополнительные проверки для профессиональных БД
        if (self.config.supports_teams or
                self.config.supports_leagues or
                self.config.supports_series):
            self._check_professional_data()

        # Итоговая сводка
        self._display_summary()

        return self.issues

    def _check_unique_match_ids(self) -> None:
        """
        Проверяет уникальность match_id в таблице матчей.

        match_id является первичным ключом и должен быть уникальным.
        Дубликаты указывают на проблемы при загрузке данных (повторное
        добавление одного матча) или ошибки в логике сбора.

        Выводит:
        - Общее количество дубликатов
        - Первые DUPLICATE_DISPLAY_LIMIT дубликатов с количеством повторений
        - Предупреждение если найдено больше DUPLICATE_DISPLAY_LIMIT дубликатов

        Добавляет в issues: "Дублирующиеся match_id: N"
        """

        print_info_line(
            "Проверка уникальности",
            "match_id",
            "🔎",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_YELLOW
        )

        # Запрос дубликатов с группировкой и агрегацией
        duplicate_match_ids = self.session.query(
            self.config.match_model.match_id,
            func.count(self.config.match_model.match_id).label('count')
        ).group_by(self.config.match_model.match_id) \
            .having(func.count(self.config.match_model.match_id) > 1) \
            .all()

        if duplicate_match_ids:
            print_status_message(
                f"Найдены дублирующиеся match_id: {len(duplicate_match_ids)}",
                "error"
            )

            # Вывод первых DUPLICATE_DISPLAY_LIMIT дубликатов
            for match_id, count in duplicate_match_ids[:DUPLICATE_DISPLAY_LIMIT]:
                print_info_line(
                    f"Match ID {match_id}",
                    f"{count} дубликатов",
                    "⚠️",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_RED
                )

            # Предупреждение о дополнительных дубликатах
            if len(duplicate_match_ids) > DUPLICATE_DISPLAY_LIMIT:
                remaining = len(duplicate_match_ids) - DUPLICATE_DISPLAY_LIMIT
                print_info_line(
                    "И еще",
                    f"{remaining} дубликатов",
                    "⚠️",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_RED
                )

            self.issues.append(f"Дублирующиеся match_id: {len(duplicate_match_ids)}")
        else:
            print_status_message("Все match_id уникальны", "success")

    def _check_unique_sequence_numbers(self) -> None:
        """
        Проверяет уникальность match_seq_num в таблице матчей.

        match_seq_num — порядковый номер матча, присваиваемый Steam API.
        Дубликаты могут указывать на проблемы синхронизации с API или
        попытку загрузить один и тот же диапазон матчей дважды.

        Выводит:
        - Общее количество дубликатов
        - Первые DUPLICATE_DISPLAY_LIMIT дубликатов с количеством повторений

        Добавляет в issues: "Дублирующиеся match_seq_num: N"
        """

        print_info_line(
            "Проверка уникальности",
            "match_seq_num",
            "🔎",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_YELLOW
        )

        # Запрос дубликатов sequence numbers
        duplicate_sequence_numbers = self.session.query(
            self.config.match_model.match_seq_num,
            func.count(self.config.match_model.match_seq_num).label('count')
        ).group_by(self.config.match_model.match_seq_num) \
            .having(func.count(self.config.match_model.match_seq_num) > 1) \
            .all()

        if duplicate_sequence_numbers:
            print_status_message(
                f"Найдены дублирующиеся match_seq_num: {len(duplicate_sequence_numbers)}",
                "error"
            )

            # Вывод первых N дубликатов
            for seq_num, count in duplicate_sequence_numbers[:DUPLICATE_DISPLAY_LIMIT]:
                print_info_line(
                    f"Seq Num {seq_num}",
                    f"{count} дубликатов",
                    "⚠️",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_RED
                )

            self.issues.append(
                f"Дублирующиеся match_seq_num: {len(duplicate_sequence_numbers)}"
            )
        else:
            print_status_message("Все match_seq_num уникальны", "success")

    def _check_null_values_matches(self) -> None:
        """
        Проверяет наличие NULL значений в критических полях таблицы Match.

        Критические поля не должны содержать NULL, так как они необходимы
        для корректной работы анализа и обучения моделей. NULL значения
        могут возникать при ошибках загрузки данных или проблемах с API.

        Проверяемые поля:
        - match_seq_num: порядковый номер от Steam API
        - radiant_win: результат матча (победа Radiant)
        - duration: длительность матча в секундах
        - start_time: временная метка начала матча
        - tower_status_radiant: битовая маска башен Radiant
        - tower_status_dire: битовая маска башен Dire
        - barracks_status_radiant: битовая маска казарм Radiant
        - barracks_status_dire: битовая маска казарм Dire
        - lobby_type: тип лобби
        - game_mode: игровой режим
        - radiant_score: количество убийств команды Radiant
        - dire_score: количество убийств команды Dire

        Примечание: match_id не проверяется — первичный ключ,
        NULL невозможен на уровне БД.

        Выводит:
        - Список полей с NULL значениями
        - Количество NULL для каждого поля

        Добавляет в issues: "NULL в {field}: {count}" для каждого поля с NULL
        """

        print_info_line(
            "Проверка NULL значений",
            "таблица Match",
            "🔎",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_YELLOW
        )

        m = self.config.match_model

        # Подсчет NULL для каждого критического поля за один запрос
        result = self.session.query(
            func.sum(case((m.match_seq_num.is_(None), 1), else_=0)).label('match_seq_num'),
            func.sum(case((m.radiant_win.is_(None), 1), else_=0)).label('radiant_win'),
            func.sum(case((m.duration.is_(None), 1), else_=0)).label('duration'),
            func.sum(case((m.start_time.is_(None), 1), else_=0)).label('start_time'),
            func.sum(case((m.tower_status_radiant.is_(None), 1), else_=0)).label('tower_status_radiant'),
            func.sum(case((m.tower_status_dire.is_(None), 1), else_=0)).label('tower_status_dire'),
            func.sum(case((m.barracks_status_radiant.is_(None), 1), else_=0)).label('barracks_status_radiant'),
            func.sum(case((m.barracks_status_dire.is_(None), 1), else_=0)).label('barracks_status_dire'),
            func.sum(case((m.lobby_type.is_(None), 1), else_=0)).label('lobby_type'),
            func.sum(case((m.game_mode.is_(None), 1), else_=0)).label('game_mode'),
            func.sum(case((m.radiant_score.is_(None), 1), else_=0)).label('radiant_score'),
            func.sum(case((m.dire_score.is_(None), 1), else_=0)).label('dire_score'),
        ).one()

        # Фильтрация только полей с NULL
        null_issues: List[Tuple[str, int]] = [
            (field, count)
            for field, count in result._asdict().items()
            if count and count > 0
        ]

        if null_issues:
            print_status_message("Найдены NULL значения в таблице Match", "error")

            # Вывод каждого проблемного поля
            for field, count in null_issues:
                print_info_line(
                    field,
                    f"{count} записей",
                    "❌",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_RED
                )

            # Добавление в список проблем
            self.issues.extend([f"NULL в {field}: {count}" for field, count in null_issues])
        else:
            print_status_message("Нет NULL значений в критических полях Match", "success")

    def _check_null_values_players(self) -> None:
        """
        Проверяет наличие NULL значений в критических полях таблицы MatchPlayer.

        Критические поля необходимы для связи с матчами, идентификации
        героев, команд и корректного анализа статистики. NULL значения
        могут указывать на неполные данные от API или ошибки парсинга.

        Проверяемые поля:
        - match_id: связь с таблицей Match
        - account_id: идентификатор Steam аккаунта игрока
        - team_number: номер команды (0=Radiant, 1=Dire)
        - hero_id: идентификатор выбранного героя
        - hero_variant: вариант героя (facet)
        - role: роль игрока (core / support)
        - item_0–item_5: предметы в основных слотах
        - backpack_0–backpack_2: предметы в рюкзаке
        - item_neutral, item_neutral2: нейтральные предметы
        - kills, deaths, assists, kda: боевая статистика
        - last_hits, denies, gold_per_min, xp_per_min: экономическая статистика
        - level, net_worth: прогресс и ценность героя
        - aghanims_scepter, aghanims_shard, moonshard: бонусные предметы

        римечание: id не проверяется, так как является первичным ключом
        и не может содержать NULL на уровне базы данных.

        Выводит:
        - Список полей с NULL значениями
        - Количество NULL для каждого поля

        Добавляет в issues: "NULL в {field}: {count}" для каждого поля с NULL
        """

        print_info_line(
            "Проверка NULL значений",
            "таблица MatchPlayer",
            "🔎",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_YELLOW
        )

        p = self.config.player_model

        # Подсчет NULL для каждого критического поля за один запрос
        result = self.session.query(
            # Идентификация
            func.sum(case((p.match_id.is_(None), 1), else_=0)).label('match_id'),
            func.sum(case((p.account_id.is_(None), 1), else_=0)).label('account_id'),
            func.sum(case((p.team_number.is_(None), 1), else_=0)).label('team_number'),
            func.sum(case((p.hero_id.is_(None), 1), else_=0)).label('hero_id'),
            func.sum(case((p.hero_variant.is_(None), 1), else_=0)).label('hero_variant'),
            func.sum(case((p.role.is_(None), 1), else_=0)).label('role'),
            # Основные предметы
            func.sum(case((p.item_0.is_(None), 1), else_=0)).label('item_0'),
            func.sum(case((p.item_1.is_(None), 1), else_=0)).label('item_1'),
            func.sum(case((p.item_2.is_(None), 1), else_=0)).label('item_2'),
            func.sum(case((p.item_3.is_(None), 1), else_=0)).label('item_3'),
            func.sum(case((p.item_4.is_(None), 1), else_=0)).label('item_4'),
            func.sum(case((p.item_5.is_(None), 1), else_=0)).label('item_5'),
            # Рюкзак
            func.sum(case((p.backpack_0.is_(None), 1), else_=0)).label('backpack_0'),
            func.sum(case((p.backpack_1.is_(None), 1), else_=0)).label('backpack_1'),
            func.sum(case((p.backpack_2.is_(None), 1), else_=0)).label('backpack_2'),
            # Нейтральные предметы
            func.sum(case((p.item_neutral.is_(None), 1), else_=0)).label('item_neutral'),
            func.sum(case((p.item_neutral2.is_(None), 1), else_=0)).label('item_neutral2'),
            # Боевая статистика
            func.sum(case((p.kills.is_(None), 1), else_=0)).label('kills'),
            func.sum(case((p.deaths.is_(None), 1), else_=0)).label('deaths'),
            func.sum(case((p.assists.is_(None), 1), else_=0)).label('assists'),
            func.sum(case((p.kda.is_(None), 1), else_=0)).label('kda'),
            # Экономическая статистика
            func.sum(case((p.last_hits.is_(None), 1), else_=0)).label('last_hits'),
            func.sum(case((p.denies.is_(None), 1), else_=0)).label('denies'),
            func.sum(case((p.gold_per_min.is_(None), 1), else_=0)).label('gold_per_min'),
            func.sum(case((p.xp_per_min.is_(None), 1), else_=0)).label('xp_per_min'),
            func.sum(case((p.level.is_(None), 1), else_=0)).label('level'),
            func.sum(case((p.net_worth.is_(None), 1), else_=0)).label('net_worth'),
            # Бонусные предметы
            func.sum(case((p.aghanims_scepter.is_(None), 1), else_=0)).label('aghanims_scepter'),
            func.sum(case((p.aghanims_shard.is_(None), 1), else_=0)).label('aghanims_shard'),
            func.sum(case((p.moonshard.is_(None), 1), else_=0)).label('moonshard'),
        ).one()

        # Фильтрация только полей с NULL
        null_issues: List[Tuple[str, int]] = [
            (field, count)
            for field, count in result._asdict().items()
            if count and count > 0
        ]

        if null_issues:
            print_status_message("Найдены NULL значения в таблице MatchPlayer", "error")

            # Вывод каждого проблемного поля
            for field, count in null_issues:
                print_info_line(
                    field,
                    f"{count} записей",
                    "❌",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_RED
                )

            # Добавление в список проблем
            self.issues.extend([f"NULL в {field}: {count}" for field, count in null_issues])
        else:
            print_status_message(
                "Нет NULL значений в критических полях MatchPlayer",
                "success"
            )

    def _check_roles(self) -> None:
        """
        Проверяет корректность значений поля role.

        Допустимые роли определены в ROLE_MAPPING.
        Любые другие значения указывают на ошибки в логике
        классификации ролей или проблемы с данными.

        Выводит:
        - Список некорректных ролей
        - Количество записей для каждой некорректной роли

        Добавляет в issues: "Некорректные роли: N"
        """

        print_info_line(
            "Проверка корректности",
            "ролей игроков",
            "🔎",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_YELLOW
        )

        valid_roles = set(ROLE_MAPPING.keys())

        # Запрос ролей, не входящих в допустимый список
        invalid_roles = self.session.query(
            self.config.player_model.role,
            func.count(self.config.player_model.role)
        ).filter(~self.config.player_model.role.in_(valid_roles)) \
            .group_by(self.config.player_model.role) \
            .all()

        if invalid_roles:
            print_status_message("Найдены некорректные роли", "error")

            # Вывод каждой некорректной роли
            for role, count in invalid_roles:
                print_info_line(
                    f"Роль '{role}'",
                    f"{count} записей",
                    "❌",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_RED
                )

            # Добавление в список проблем
            total_invalid = sum(count for _, count in invalid_roles)
            self.issues.append(f"Некорректные роли: {total_invalid}")
        else:
            valid_roles_str = '/'.join(valid_roles)
            print_status_message(f"Все роли корректны ({valid_roles_str})", "success")

    def _check_team_numbers(self) -> None:
        """
        Проверяет корректность значений поля team_number.

        Допустимые значения: RADIANT_INDEX (0) и DIRE_INDEX (1).
        Любые другие значения указывают на ошибки в обработке
        данных API или проблемы с маппингом команд.

        Выводит:
        - Список некорректных team_number
        - Количество записей для каждого некорректного значения

        Добавляет в issues: "Некорректные team_number: N"
        """

        print_info_line(
            "Проверка корректности",
            "team_number",
            "🔎",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_YELLOW
        )

        valid_team_numbers = {RADIANT_INDEX, DIRE_INDEX}

        # Запрос team_number, не входящих в допустимый список
        invalid_team_numbers = self.session.query(
            self.config.player_model.team_number,
            func.count(self.config.player_model.team_number)
        ).filter(~self.config.player_model.team_number.in_(valid_team_numbers)) \
            .group_by(self.config.player_model.team_number) \
            .all()

        if invalid_team_numbers:
            print_status_message("Найдены некорректные team_number", "error")

            # Вывод каждого некорректного значения
            for team, count in invalid_team_numbers:
                print_info_line(
                    f"Team {team}",
                    f"{count} записей",
                    "❌",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_RED
                )

            # Добавление в список проблем
            total_invalid = sum(count for _, count in invalid_team_numbers)
            self.issues.append(f"Некорректные team_number: {total_invalid}")
        else:
            print_status_message(
                f"Все team_number корректны ({RADIANT_INDEX}/{DIRE_INDEX})",
                "success"
            )

    def _check_player_counts(self) -> None:
        """
        Проверяет количество игроков в каждом матче.

        Стандартный матч Dota 2 содержит ровно TEAM_SIZE * 2 игроков.
        Матчи с другим количеством могут указывать на:
        - Неполные данные от API
        - Досрочно завершенные матчи
        - Ошибки в загрузке данных игроков

        Выводит:
        - Общее количество матчей с неправильным числом игроков
        - Первые DUPLICATE_DISPLAY_LIMIT матчей с указанием фактического количества

        Добавляет в issues: "Матчи с неправильным количеством игроков: N"
        """

        print_info_line(
            "Проверка количества",
            "игроков в матчах",
            "🔎",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_YELLOW
        )

        expected_players = TEAM_SIZE * 2

        # Запрос матчей с неправильным количеством игроков (с лимитом вывода)
        matches_with_wrong_count = self.session.query(
            self.config.player_model.match_id,
            func.count(self.config.player_model.id).label('player_count')
        ).group_by(self.config.player_model.match_id) \
            .having(func.count(self.config.player_model.id) != expected_players) \
            .limit(DUPLICATE_DISPLAY_LIMIT).all()

        if matches_with_wrong_count:
            # Подсчет общего количества проблемных матчей (без лимита)
            total_wrong = self.session.query(self.config.player_model.match_id) \
                .group_by(self.config.player_model.match_id) \
                .having(func.count(self.config.player_model.id) != expected_players) \
                .count()

            print_status_message(
                f"Найдены матчи с неправильным количеством игроков: {total_wrong}",
                "error"
            )

            # Вывод первых N проблемных матчей
            for match_id, count in matches_with_wrong_count:
                print_info_line(
                    f"Match {match_id}",
                    f"{count} игроков",
                    "❌",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_RED
                )

            self.issues.append(f"Матчи с неправильным количеством игроков: {total_wrong}")
        else:
            print_status_message(
                f"Все матчи содержат {TEAM_SIZE * 2} игроков",
                "success"
            )

    def _check_data_consistency(self) -> None:
        """
        Проверяет связность данных между таблицами Match и MatchPlayer.

        Каждая запись в MatchPlayer должна иметь соответствующую запись в Match.
        Записи игроков без матча ("игроки-сироты") указывают на:
        - Ошибки в логике транзакций БД
        - Частичную загрузку данных
        - Удаление матчей без каскадного удаления игроков

        Использует LEFT OUTER JOIN для поиска игроков, у которых
        нет соответствующего матча в таблице Match.

        Выводит:
        - Количество "игроков-сирот"

        Добавляет в issues: "Игроки-сироты: N"
        """

        print_info_line(
            "Проверка связности",
            "данных",
            "🔎",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_YELLOW
        )

        # Запрос игроков без соответствующих матчей
        orphaned_players_count = self.session.query(
            func.count(self.config.player_model.id)
        ).outerjoin(
            self.config.match_model,
            self.config.player_model.match_id == self.config.match_model.match_id
        ).filter(self.config.match_model.match_id.is_(None)) \
            .scalar()

        if orphaned_players_count > 0:
            print_status_message(
                f"Найдены игроки без соответствующих матчей: {orphaned_players_count}",
                "error"
            )
            self.issues.append(f"Игроки-сироты: {orphaned_players_count}")
        else:
            print_status_message(
                "Все игроки связаны с существующими матчами",
                "success"
            )

    def _check_professional_data(self) -> None:
        """
        Делегирует проверку профессиональных данных в ProAnalyzer.

        Результаты проверок добавляются в общий список issues.
        """

        pro_analyzer = ProAnalyzer(self.session, self.config)
        pro_issues = pro_analyzer.check_integrity()
        self.issues.extend(pro_issues)

    def _display_summary(self) -> None:
        """
        Отображает итоговую сводку проверки целостности данных.

        Выводит количество найденных проблем с подробным списком,
        либо сообщение об успешном прохождении всех проверок.
        """

        print_subsection_header("Итоги проверки целостности", "📋", Colors.BRIGHT_PURPLE)

        if self.issues:
            print_status_message(
                f"Обнаружены проблемы целостности данных: {len(self.issues)}",
                "error"
            )

            # Вывод списка всех проблем
            for issue in self.issues:
                print_info_line(
                    "Проблема",
                    issue,
                    "•",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_RED
                )
        else:
            print_status_message("Все проверки целостности пройдены успешно!", "success")
            print_status_message("Данные готовы для использования в анализе", "info")
