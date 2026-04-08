"""
Модуль анализа профессиональных данных Dota 2 (команды, лиги, серии).

Модуль предоставляет инструменты для анализа профессиональной сцены Dota 2:
проверку целостности данных, статистику команд, лиг и серий матчей.
Поддерживает различные типы баз данных с разным набором полей.

Виды проверок целостности:
1. Консистентность данных команд:
   - Соответствие team_id и team_name (оба заполнены или оба пусты)
   - Проверка для Radiant и Dire команд отдельно
   - Выявление записей с неполными данными

2. Консистентность данных лиг:
   - Соответствие leagueid и league_name
   - Выявление записей с частично заполненной информацией

3. Валидность типов серий:
   - Проверка на соответствие допустимым значениям
   - Выявление некорректных типов серий
   - Подсчет проблемных записей

Виды анализа:
1. Статистика команд:
   - Количество уникальных команд (всего, Radiant, Dire)
   - Топ наиболее активных команд
   - Процент матчей для каждой команды

2. Статистика лиг:
   - Количество уникальных лиг в данных
   - Покрытие матчей данными о лигах
   - Топ наиболее активных лиг

3. Статистика серий:
   - Количество уникальных серий
   - Покрытие матчей данными о сериях
   - Распределение по типам SERIES_TYPES
"""

# Сторонние библиотеки
from typing import List
from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

# Локальные импорты
from data_validation.components.database_config import DatabaseConfig
from config import SERIES_TYPES
from utils.console import (
    Colors,
    print_subsection_header,
    print_info_line,
    print_status_message
)

# === КОНСТАНТЫ ОТОБРАЖЕНИЯ ===
TOP_TEAMS_DISPLAY_COUNT = 5                     # Количество команд для отображения
TOP_LEAGUES_DISPLAY_COUNT = 15                  # Количество лиг для отображения


class ProAnalyzer:
    """
    Анализатор профессиональных данных Dota 2 (команды, лиги, серии).

    Класс работает в двух режимах:
    1. Валидация — проверка целостности профессиональных данных (check_integrity).
    2. Анализ — статистика команд, лиг и серий (analyze).

    Attributes:
        session (Session): Активная сессия SQLAlchemy для запросов к БД
        config (DatabaseConfig): Конфигурация с моделями и флагами поддержки функций
    """

    def __init__(self, session: Session, config: DatabaseConfig) -> None:
        """
        Инициализирует анализатор профессиональных данных.

        Args:
            session (Session): Активная сессия SQLAlchemy
            config (DatabaseConfig): Конфигурация с моделями БД (match_model) и флагами:
                         - supports_teams: наличие полей команд
                         - supports_leagues: наличие полей лиг
                         - supports_series: наличие полей серий
        """
        self.session = session
        self.config = config

    def check_integrity(self) -> List[str]:
        """
        Выполняет проверку целостности профессиональных данных.

        Последовательно проверяет:
        1. Консистентность данных команд (если supports_teams)
        2. Консистентность данных лиг (если supports_leagues)
        3. Валидность типов серий (если supports_series)

        Returns:
            List[str]: Список текстовых описаний найденных проблем
        """

        issues = []

        print_subsection_header("Проверки профессиональных данных", "🏆", Colors.BRIGHT_GOLD)

        # Проверка данных команд (если поддерживается)
        if self.config.supports_teams:
            issues.extend(self._check_team_consistency())

        # Проверка данных лиг (если поддерживается)
        if self.config.supports_leagues:
            issues.extend(self._check_league_consistency())

        # Проверка типов серий (если поддерживается)
        if self.config.supports_series:
            issues.extend(self._check_series_types())

        return issues

    def _check_team_consistency(self) -> List[str]:
        """
        Проверяет консистентность данных команд.

        Выявляет записи, где team_id и team_name заполнены не одновременно
        (одно поле есть, другого нет). Проверка выполняется отдельно
        для Radiant и Dire команд.

        Консистентные данные:
        - Оба поля заполнены: team_id=123, team_name="Team Secret"
        - Оба поля пусты: team_id=NULL, team_name=NULL

        Неконсистентные данные:
        - team_id=123, team_name=NULL
        - team_id=NULL, team_name="Team Secret"

        Returns:
            List[str]: Список описаний найденных проблем
        """

        issues = []

        print_info_line(
            "Проверка консистентности",
            "данных команд",
            "🔎",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_YELLOW
        )

        # Проверка Radiant команд: team_id XOR team_name
        inconsistent_radiant = self.session.query(
            func.count(self.config.match_model.match_id)
        ).filter(
            ((self.config.match_model.radiant_team_id.isnot(None)) &
             (self.config.match_model.radiant_name.is_(None))) |
            ((self.config.match_model.radiant_team_id.is_(None)) &
             (self.config.match_model.radiant_name.isnot(None)))
        ).scalar()

        # Проверка Dire команд: team_id XOR team_name
        inconsistent_dire = self.session.query(
            func.count(self.config.match_model.match_id)
        ).filter(
            ((self.config.match_model.dire_team_id.isnot(None)) &
             (self.config.match_model.dire_name.is_(None))) |
            ((self.config.match_model.dire_team_id.is_(None)) &
             (self.config.match_model.dire_name.isnot(None)))
        ).scalar()

        total_inconsistent = inconsistent_radiant + inconsistent_dire

        # Вывод результатов проверки
        if total_inconsistent > 0:
            print_status_message(
                f"Матчи с неконсистентными данными команд: {total_inconsistent}",
                "warning"
            )

            if inconsistent_radiant > 0:
                print_info_line(
                    "Radiant команды",
                    f"{inconsistent_radiant} неконсистентных",
                    "⚠️",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_ORANGE
                )

            if inconsistent_dire > 0:
                print_info_line(
                    "Dire команды",
                    f"{inconsistent_dire} неконсистентных",
                    "⚠️",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_ORANGE
                )

            issues.append(f"Неконсистентные данные команд: {total_inconsistent}")
        else:
            print_status_message("Данные команд консистентны", "success")

        return issues

    def _check_league_consistency(self) -> List[str]:
        """
        Проверяет консистентность данных лиг.

        Выявляет записи, где leagueid и league_name заполнены не одновременно
        (одно поле есть, другого нет).

        Консистентные данные:
        - Оба поля заполнены: leagueid=12345, league_name="The International"
        - Оба поля пусты: leagueid=NULL, league_name=NULL

        Неконсистентные данные:
        - leagueid=12345, league_name=NULL
        - leagueid=NULL, league_name="The International"

        Returns:
            List[str]: Список описаний найденных проблем
        """

        issues = []

        print_info_line(
            "Проверка консистентности",
            "данных лиг",
            "🔎",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_YELLOW
        )

        # Проверка лиг: leagueid XOR league_name
        inconsistent_leagues = self.session.query(
            func.count(self.config.match_model.match_id)
        ).filter(
            ((self.config.match_model.leagueid.isnot(None)) &
             (self.config.match_model.league_name.is_(None))) |
            ((self.config.match_model.leagueid.is_(None)) &
             (self.config.match_model.league_name.isnot(None)))
        ).scalar()

        # Вывод результатов проверки
        if inconsistent_leagues > 0:
            print_status_message(
                f"Матчи с неконсистентными данными лиг: {inconsistent_leagues}",
                "warning"
            )
            issues.append(f"Неконсистентные данные лиг: {inconsistent_leagues}")
        else:
            print_status_message("Данные лиг консистентны", "success")

        return issues

    def _check_series_types(self) -> List[str]:
        """
        Проверяет корректность типов серий.

        Выявляет записи с недопустимыми значениями series_type,
        не входящими в SERIES_TYPES.

        Returns:
            List[str]: Список описаний найденных проблем
        """

        issues = []

        print_info_line(
            "Проверка корректности",
            "типов серий",
            "🔎",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_YELLOW
        )

        # Поиск серий с недопустимыми типами
        invalid_series_types = self.session.query(
            self.config.match_model.series_type,
            func.count(self.config.match_model.series_type)
        ).filter(self.config.match_model.series_type.isnot(None)) \
            .filter(~self.config.match_model.series_type.in_(SERIES_TYPES)) \
            .group_by(self.config.match_model.series_type) \
            .all()

        # Вывод результатов проверки
        if invalid_series_types:
            print_status_message("Найдены некорректные типы серий", "error")

            for series_type, count in invalid_series_types:
                print_info_line(
                    f"Тип серии {series_type}",
                    f"{count} записей",
                    "❌",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_RED
                )

            total_invalid = sum(count for _, count in invalid_series_types)
            issues.append(f"Некорректные типы серий: {total_invalid}")
        else:
            print_status_message("Все типы серий корректны", "success")

        return issues

    def analyze(self, total_matches: int) -> None:
        """
        Выполняет полный анализ профессиональных данных.

        Последовательно запускает анализ команд, лиг и серий
        в зависимости от флагов конфигурации.

        Args:
            total_matches (int): Общее количество матчей в БД для расчёта процентов
        """

        # Анализ команд (если поддерживается)
        if self.config.supports_teams:
            self._analyze_teams(total_matches)

        # Анализ лиг (если поддерживается)
        if self.config.supports_leagues:
            self._analyze_leagues(total_matches)

        # Анализ серий (если поддерживается)
        if self.config.supports_series:
            self._analyze_series(total_matches)

    def _analyze_teams(self, total_matches: int) -> None:
        """
        Анализирует статистику профессиональных команд.

        Рассчитывает количество уникальных команд (всего, по сторонам)
        и выводит топ наиболее активных команд. Статистика объединяет
        данные Radiant и Dire через UNION.

        Args:
            total_matches (int): Общее количество матчей для расчёта процентов
        """

        print_subsection_header("Статистика команд", "👥", Colors.BRIGHT_BLUE)

        # Подсчет уникальных команд по сторонам
        unique_radiant_teams = self.session.query(
            func.count(distinct(self.config.match_model.radiant_team_id))
        ).filter(self.config.match_model.radiant_team_id.isnot(None)).scalar()

        unique_dire_teams = self.session.query(
            func.count(distinct(self.config.match_model.dire_team_id))
        ).filter(self.config.match_model.dire_team_id.isnot(None)).scalar()

        # Подсчет общего количества уникальных команд через UNION
        all_team_ids = self.session.query(
            self.config.match_model.radiant_team_id.label('team_id')
        ).filter(self.config.match_model.radiant_team_id.isnot(None)) \
            .union(
            self.session.query(self.config.match_model.dire_team_id.label('team_id'))
            .filter(self.config.match_model.dire_team_id.isnot(None))
        )

        unique_total_teams = self.session.query(
            func.count(distinct(all_team_ids.subquery().c.team_id))
        ).scalar()

        # Вывод основных метрик
        print_info_line(
            "Уникальных команд (всего)",
            f"{unique_total_teams:,}",
            "🏟️",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_GREEN
        )
        print_info_line(
            "Команд играло Radiant",
            f"{unique_radiant_teams:,}",
            "🌅",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_YELLOW
        )
        print_info_line(
            "Команд играло Dire",
            f"{unique_dire_teams:,}",
            "🌙",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_PURPLE
        )

        # === ТОП КОМАНД ПО АКТИВНОСТИ ===
        print()
        print(f"🏆 Топ-{TOP_TEAMS_DISPLAY_COUNT} команд по активности:")

        # Статистика Radiant команд
        radiant_stats = self.session.query(
            self.config.match_model.radiant_team_id.label('team_id'),
            self.config.match_model.radiant_name.label('team_name'),
            func.count(self.config.match_model.match_id).label('matches_count')
        ).filter(self.config.match_model.radiant_team_id.isnot(None)) \
            .group_by(
            self.config.match_model.radiant_team_id,
            self.config.match_model.radiant_name
        )

        # Статистика Dire команд
        dire_stats = self.session.query(
            self.config.match_model.dire_team_id.label('team_id'),
            self.config.match_model.dire_name.label('team_name'),
            func.count(self.config.match_model.match_id).label('matches_count')
        ).filter(self.config.match_model.dire_team_id.isnot(None)) \
            .group_by(
            self.config.match_model.dire_team_id,
            self.config.match_model.dire_name
        )

        # Объединение через UNION ALL (сохраняет все строки без дедупликации)
        # и группировка только по team_id (избегает дублей при переименовании)
        subquery = radiant_stats.union_all(dire_stats).subquery()
        combined_team_stats = self.session.query(
            subquery.c.team_id,
            func.max(subquery.c.team_name).label('team_name'),
            func.sum(subquery.c.matches_count).label('total_matches')
        ).group_by(subquery.c.team_id) \
            .order_by(func.sum(subquery.c.matches_count).desc()) \
            .limit(TOP_TEAMS_DISPLAY_COUNT).all()

        # Вывод топа команд
        for rank, (team_id, team_name, matches) in enumerate(combined_team_stats, 1):
            team_display_name = team_name or f"Team {team_id}"
            percentage = (matches / total_matches) * 100 if total_matches > 0 else 0

            print_info_line(
                f"{rank}. {team_display_name}",
                f"{matches:,} матчей ({percentage:.1f}%)",
                "⚔️",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_GREEN
            )

    def _analyze_leagues(self, total_matches: int) -> None:
        """
        Анализирует статистику профессиональных лиг.

        Рассчитывает количество уникальных лиг, покрытие матчей
        данными о лигах и выводит топ наиболее активных лиг.

        Args:
            total_matches (int): Общее количество матчей для расчёта процентов
        """

        print_subsection_header("Статистика лиг", "🏟️", Colors.BRIGHT_PURPLE)

        # Подсчет уникальных лиг
        unique_leagues = self.session.query(
            func.count(distinct(self.config.match_model.leagueid))
        ).filter(self.config.match_model.leagueid.isnot(None)).scalar()

        # Подсчет матчей с данными о лигах
        matches_with_league = self.session.query(
            func.count(self.config.match_model.match_id)
        ).filter(self.config.match_model.leagueid.isnot(None)).scalar()

        # Расчет покрытия данными о лигах
        league_coverage = (
            (matches_with_league / total_matches) * 100
            if total_matches > 0 else 0
        )

        # Вывод основных метрик
        print_info_line(
            "Уникальных лиг",
            f"{unique_leagues:,}",
            "🏆",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_PURPLE
        )
        print_info_line(
            "Матчей с данными лиг",
            f"{matches_with_league:,} ({league_coverage:.1f}%)",
            "📊",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_GOLD
        )

        # === ТОП ЛИГ ПО АКТИВНОСТИ ===
        print()
        print(f"🏆 Топ-{TOP_LEAGUES_DISPLAY_COUNT} лиг по активности:")

        # Запрос топа лиг
        top_leagues = self.session.query(
            self.config.match_model.leagueid,
            self.config.match_model.league_name,
            func.count(self.config.match_model.match_id).label('matches_count')
        ).filter(self.config.match_model.leagueid.isnot(None)) \
            .group_by(
            self.config.match_model.leagueid,
            self.config.match_model.league_name
        ).order_by(func.count(self.config.match_model.match_id).desc()) \
            .limit(TOP_LEAGUES_DISPLAY_COUNT).all()

        # Вывод топа лиг
        for rank, (league_id, league_name, matches) in enumerate(top_leagues, 1):
            league_display_name = league_name or f"League {league_id}"
            percentage = (matches / total_matches) * 100 if total_matches > 0 else 0

            print_info_line(
                f"{rank}. {league_display_name} ({league_id})",
                f"{matches:,} матчей ({percentage:.1f}%)",
                "🏟️",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_GOLD
            )

    def _analyze_series(self, total_matches: int) -> None:
        """
        Анализирует статистику серий матчей.

        Рассчитывает количество уникальных серий, покрытие матчей
        данными о сериях и распределение по типам серий.

        Args:
            total_matches (int): Общее количество матчей для расчёта процентов
        """

        print_subsection_header("Статистика серий", "📚", Colors.BRIGHT_ORANGE)

        # Подсчет матчей в сериях
        matches_with_series = self.session.query(
            func.count(self.config.match_model.match_id)
        ).filter(self.config.match_model.series_id.isnot(None)).scalar()

        # Подсчет уникальных серий
        unique_series = self.session.query(
            func.count(distinct(self.config.match_model.series_id))
        ).filter(self.config.match_model.series_id.isnot(None)).scalar()

        # Расчет покрытия данными о сериях
        series_coverage = (
            (matches_with_series / total_matches) * 100
            if total_matches > 0 else 0
        )

        # Вывод основных метрик
        print_info_line(
            "Уникальных серий",
            f"{unique_series:,}",
            "📚",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_ORANGE
        )
        print_info_line(
            "Матчей в сериях",
            f"{matches_with_series:,} ({series_coverage:.1f}%)",
            "🎯",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_CYAN
        )

        # === РАСПРЕДЕЛЕНИЕ ПО ТИПАМ СЕРИЙ ===
        print()
        print("📊 Распределение по типам серий:")

        # Запрос распределения по типам
        series_types = self.session.query(
            self.config.match_model.series_type,
            func.count(self.config.match_model.match_id).label('matches_count')
        ).filter(self.config.match_model.series_type.isnot(None)) \
            .group_by(self.config.match_model.series_type) \
            .order_by(self.config.match_model.series_type).all()

        # Вывод распределения
        for series_type, matches in series_types:
            type_name = SERIES_TYPES.get(series_type, f"Тип {series_type}")
            percentage = (matches / total_matches) * 100 if total_matches > 0 else 0

            print_info_line(
                type_name,
                f"{matches:,} матчей ({percentage:.1f}%)",
                "📚",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_ORANGE
            )
