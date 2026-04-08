"""
Модуль анализа статистики игроков Dota 2.

Модуль предоставляет комплексный анализ индивидуальной статистики игроков
на основе данных из БД. Включает анализ боевых показателей, экономических метрик,
распределения по ролям и использования специальных предметов.

Виды анализа:
1. Обзорная статистика:
   - Общее количество записей игроков
   - Количество приватных аккаунтов и их процент
   - Количество уникальных публичных игроков
   - Предупреждения при аномально высоком проценте приватных аккаунтов

2. Статистика KDA (Kill/Death/Assist):
   - Минимальные, максимальные и средние значения убийств, смертей, ассистов
   - Средний коэффициент KDA

3. Статистика по ролям (Core/Support):
   - Распределение игроков по ролям
   - Средние боевые показатели (K/D/A) для каждой роли
   - Экономические метрики по ролям (GPM, XPM)
   - Фарм-метрики (Last Hits, Net Worth)

4. Экономическая статистика (диапазон и среднее):
   - GPM (Gold Per Minute)
   - XPM (Experience Per Minute)
   - Last Hits
   - Net Worth

5. Статистика уровней:
   - Распределение игроков на каждом уровне
   - Отдельный подсчёт для ролей Core и Support

6. Статистика специальных предметов:
   - Частота покупки Aghanim's Scepter, Aghanim's Shard, Moon Shard

Приватные аккаунты:
Steam позволяет скрывать игровой профиль, при этом OpenDota API возвращает
специальный ID = PRIVATE_ACCOUNT_ID. Высокий процент таких аккаунтов (>75%) может
указывать на проблемы с качеством данных или специфику выборки матчей.
"""

# Стандартные библиотеки
from typing import cast, Any

# Сторонние библиотеки
from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

# Локальные импорты
from data_validation.components.database_config import DatabaseConfig
from config import PRIVATE_ACCOUNT_ID
from utils.console import (
    Colors,
    print_section_header,
    print_subsection_header,
    print_info_line,
    print_status_message
)

# === КОНСТАНТЫ ПРЕДУПРЕЖДЕНИЙ ===
PRIVATE_ACCOUNTS_WARNING_THRESHOLD = 75    # Процент приватных аккаунтов для предупреждения


class PlayerAnalyzer:
    """
    Анализатор статистики игроков Dota 2 на основе данных матчей.

    Класс выполняет анализ индивидуальных показателей игроков:
    боевые метрики (KDA), экономические показатели (GPM, XPM), распределение
    по ролям, уровни и использование специальных предметов. Обрабатывает
    приватные аккаунты и выдает предупреждения при аномальных данных.

    Attributes:
        session (Session): Активная сессия SQLAlchemy для запросов к БД
        config (DatabaseConfig): Конфигурация с моделями Match и Player
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
        Выполняет полный анализ статистики игроков.

        Последовательно запускает все виды анализа:
        1. Обзорная статистика (количество игроков, приватные аккаунты)
        2. Статистика KDA (убийства, смерти, ассисты)
        3. Статистика по ролям (Core/Support)
        4. Экономическая статистика (GPM, XPM, Last Hits, Net Worth)
        5. Статистика уровней (распределение и средние по ролям)
        6. Статистика специальных предметов (Aghanim's Scepter/Shard, Moon Shard)

        При отсутствии данных выводит сообщение об ошибке и прерывает анализ.
        """

        # Вывод заголовка с типом базы данных
        header = f"СТАТИСТИКА ИГРОКОВ ({self.config.database_type.upper()})"
        print_section_header(header, "👥", color=Colors.BRIGHT_GOLD)

        # Получение общего количества записей
        total_player_records = self.session.query(
            func.count(self.config.player_model.id)
        ).scalar()

        # Проверка на наличие данных
        if total_player_records == 0:
            print_status_message("Нет данных игроков для анализа", "error")
            return

        # Последовательное выполнение всех видов анализа
        self._display_overview(total_player_records)
        self._analyze_kda()
        self._analyze_roles(total_player_records)
        self._analyze_economic_stats()
        self._analyze_level_stats(total_player_records)
        self._analyze_special_items(total_player_records)

    def _display_overview(self, total_player_records: int) -> None:
        """
        Отображает обзорную информацию о записях игроков.

        Рассчитывает количество приватных аккаунтов, уникальных публичных
        игроков и выводит предупреждение при превышении порога
        PRIVATE_ACCOUNTS_WARNING_THRESHOLD.

        Args:
            total_player_records (int): Общее количество записей игроков
        """

        # Подсчет приватных аккаунтов
        private_accounts_count = self.session.query(
            func.count(self.config.player_model.id)
        ).filter(
            self.config.player_model.account_id == PRIVATE_ACCOUNT_ID
        ).scalar()

        # Подсчет уникальных публичных игроков
        unique_public_players = self.session.query(
            func.count(distinct(self.config.player_model.account_id))
        ).filter(
            self.config.player_model.account_id != PRIVATE_ACCOUNT_ID
        ).scalar()

        # Расчет процента приватных аккаунтов
        private_percentage = (
            (private_accounts_count / total_player_records) * 100
            if total_player_records > 0 else 0
        )

        # Вывод основных метрик
        print_info_line(
            "Общее количество записей игроков",
            f"{total_player_records:,}",
            "📊",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_GREEN
        )
        print_info_line(
            "Записей с приватными аккаунтами",
            f"{private_accounts_count:,} ({private_percentage:.1f}%)",
            "🔒",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_YELLOW
        )
        print_info_line(
            "Уникальных публичных игроков",
            f"{unique_public_players:,}",
            "👤",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_CYAN
        )

        # Предупреждение при аномально высоком проценте приватных аккаунтов
        if private_percentage > PRIVATE_ACCOUNTS_WARNING_THRESHOLD:
            print_status_message(
                f"Аномально высокий процент приватных аккаунтов ({private_percentage:.1f}%) "
                f"может влиять на точность анализа",
                "warning"
            )

    def _analyze_kda(self) -> None:
        """
        Анализирует статистику KDA (Kill/Death/Assist).

        Рассчитывает диапазоны (min/max) и средние значения для kills,
        deaths, assists, а также средний KDA коэффициент.
        """

        print_subsection_header("Статистика KDA", "⚔️", Colors.BRIGHT_RED)

        try:
            # Агрегирующий запрос для всех компонентов KDA
            kda_stats = cast(Any, self.session.query(
                func.min(self.config.player_model.kills).label('min_kills'),
                func.max(self.config.player_model.kills).label('max_kills'),
                func.avg(self.config.player_model.kills).label('avg_kills'),
                func.min(self.config.player_model.deaths).label('min_deaths'),
                func.max(self.config.player_model.deaths).label('max_deaths'),
                func.avg(self.config.player_model.deaths).label('avg_deaths'),
                func.min(self.config.player_model.assists).label('min_assists'),
                func.max(self.config.player_model.assists).label('max_assists'),
                func.avg(self.config.player_model.assists).label('avg_assists'),
                func.avg(self.config.player_model.kda).label('avg_kda')
            ).first())

            # Вывод статистики по каждому компоненту
            print_info_line(
                "Kills",
                f"{kda_stats.min_kills} - {kda_stats.max_kills} "
                f"(среднее: {kda_stats.avg_kills:.1f})",
                "🗡️",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_RED
            )
            print_info_line(
                "Deaths",
                f"{kda_stats.min_deaths} - {kda_stats.max_deaths} "
                f"(среднее: {kda_stats.avg_deaths:.1f})",
                "💀",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_YELLOW
            )
            print_info_line(
                "Assists",
                f"{kda_stats.min_assists} - {kda_stats.max_assists} "
                f"(среднее: {kda_stats.avg_assists:.1f})",
                "🤝",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_GREEN
            )
            print_info_line(
                "Средний KDA",
                f"{kda_stats.avg_kda:.2f}",
                "📈",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_BLUE
            )

        except Exception as e:
            print_status_message(f"Ошибка при анализе KDA: {e}", "error")

    def _analyze_roles(self, total_player_records: int) -> None:
        """
        Анализирует статистику по ролям игроков (Core/Support).

        Для каждой роли рассчитывает распределение, средние боевые
        показатели (K/D/A, KDA), экономические (GPM, XPM) и
        фарм-метрики (Last Hits, Net Worth).

        Args:
            total_player_records (int): Общее количество записей для расчёта процентов
        """

        print_subsection_header("Статистика по ролям", "🎭", Colors.BRIGHT_PURPLE)

        try:
            # Агрегирующий запрос с группировкой по ролям
            role_stats = self.session.query(
                self.config.player_model.role,
                func.count(self.config.player_model.role).label('count'),
                func.avg(self.config.player_model.kills).label('avg_kills'),
                func.avg(self.config.player_model.deaths).label('avg_deaths'),
                func.avg(self.config.player_model.assists).label('avg_assists'),
                func.avg(self.config.player_model.kda).label('avg_kda'),
                func.avg(self.config.player_model.gold_per_min).label('avg_gpm'),
                func.avg(self.config.player_model.xp_per_min).label('avg_xpm'),
                func.avg(self.config.player_model.last_hits).label('avg_lh'),
                func.avg(self.config.player_model.net_worth).label('avg_nw')
            ).group_by(self.config.player_model.role).all()

            # Вывод статистики для каждой роля
            for idx, role_data in enumerate(role_stats):
                role = role_data.role
                count = role_data.count
                percentage = (count / total_player_records) * 100

                # Визуальное оформление в зависимости от роли
                role_emoji = "⚔️" if role == "core" else "🛡️"
                role_color = Colors.BRIGHT_RED if role == "core" else Colors.BRIGHT_CYAN

                # Заголовок роли с количеством записей
                prefix = "\n" if idx > 0 else ""
                print(
                    f"{prefix}{role_emoji} {Colors.BOLD}{role_color}{role.upper()}{Colors.RESET} "
                    f"({Colors.BRIGHT_GREEN}{count:,} записей, {percentage:.1f}%{Colors.RESET}):"
                )

                # Боевые показатели
                print_info_line(
                    "K/D/A",
                    f"{role_data.avg_kills:.1f}/{role_data.avg_deaths:.1f}/"
                    f"{role_data.avg_assists:.1f} (KDA: {role_data.avg_kda:.2f})",
                    "⚔️",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_ORANGE
                )

                # Экономические метрики
                print_info_line(
                    "GPM | XPM",
                    f"{role_data.avg_gpm:.0f} | {role_data.avg_xpm:.0f}",
                    "💰",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_GOLD
                )

                # Фарм-метрики
                print_info_line(
                    "Last Hits | Net Worth",
                    f"{role_data.avg_lh:.0f} | {role_data.avg_nw:.0f}",
                    "🔪",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_MINT
                )

        except Exception as e:
            print_status_message(f"Ошибка при анализе ролей: {e}", "error")

    def _analyze_economic_stats(self) -> None:
        """
        Анализирует экономические показатели игроков.

        Рассчитывает диапазоны (min/max) и средние значения для
        GPM, XPM, Last Hits и Net Worth.
        """

        print_subsection_header("Экономическая статистика", "💰", Colors.BRIGHT_GOLD)

        try:
            # Агрегирующий запрос для экономических метрик
            economic_stats = cast(Any, self.session.query(
                func.min(self.config.player_model.gold_per_min).label('min_gpm'),
                func.max(self.config.player_model.gold_per_min).label('max_gpm'),
                func.avg(self.config.player_model.gold_per_min).label('avg_gpm'),
                func.min(self.config.player_model.xp_per_min).label('min_xpm'),
                func.max(self.config.player_model.xp_per_min).label('max_xpm'),
                func.avg(self.config.player_model.xp_per_min).label('avg_xpm'),
                func.min(self.config.player_model.last_hits).label('min_lh'),
                func.max(self.config.player_model.last_hits).label('max_lh'),
                func.avg(self.config.player_model.last_hits).label('avg_lh'),
                func.min(self.config.player_model.net_worth).label('min_nw'),
                func.max(self.config.player_model.net_worth).label('max_nw'),
                func.avg(self.config.player_model.net_worth).label('avg_nw')
            ).first())

            # Вывод статистики по каждой метрике
            print_info_line(
                "GPM",
                f"{economic_stats.min_gpm} - {economic_stats.max_gpm} "
                f"(среднее: {economic_stats.avg_gpm:.0f})",
                "💰",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_GOLD
            )
            print_info_line(
                "XPM",
                f"{economic_stats.min_xpm} - {economic_stats.max_xpm} "
                f"(среднее: {economic_stats.avg_xpm:.0f})",
                "⭐",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_YELLOW
            )
            print_info_line(
                "Last Hits",
                f"{economic_stats.min_lh} - {economic_stats.max_lh} "
                f"(среднее: {economic_stats.avg_lh:.0f})",
                "🔪",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_GREEN
            )
            print_info_line(
                "Net Worth",
                f"{economic_stats.min_nw:,} - {economic_stats.max_nw:,} "
                f"(среднее: {economic_stats.avg_nw:,.0f})",
                "💎",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_CYAN
            )

        except Exception as e:
            print_status_message(f"Ошибка при анализе экономической статистики: {e}", "error")

    def _analyze_level_stats(self, total_player_records: int) -> None:
        """
        Анализирует статистику уровней игроков.

        Выводит распределение игроков по достигнутым уровням
        и средние уровни с диапазонами по ролям (Core/Support).

        Цветовое кодирование процентов для распределения уровней:
        - Зеленый: ≥8% (доминирующий)
        - Желтый: ≥5% (частый)
        - Оранжевый: ≥2% (умеренный)
        - Белый: <2% (редкий)

        Args:
            total_player_records (int): Общее количество записей для расчёта процентов
        """

        print_subsection_header("Статистика уровней", "📊", Colors.BRIGHT_BLUE)

        try:
            # Запрос распределения по уровням
            level_distribution = self.session.query(
                self.config.player_model.level,
                func.count(self.config.player_model.level).label('count')
            ).group_by(self.config.player_model.level) \
                .order_by(self.config.player_model.level).all()

            # Проверка на наличие данных
            if not level_distribution:
                print_status_message("Нет данных об уровнях игроков", "warning")
                return

            # Преобразование в словарь для удобного доступа
            level_dict = {level: count for level, count in level_distribution}
            min_level = min(level_dict.keys())
            max_level = max(level_dict.keys())

            # === РАСПРЕДЕЛЕНИЕ ПО УРОВНЯМ ===
            print(
                f"{Colors.BRIGHT_WHITE}🎯 Распределение игроков по уровням "
                f"({min_level}-{max_level}):{Colors.RESET}"
            )

            # Вывод статистики для каждого уровня
            for level in range(min_level, max_level + 1):
                count = level_dict.get(level, 0)
                if count > 0:
                    percentage = (count / total_player_records) * 100

                    # Цветовое кодирование на основе популярности уровня
                    if percentage >= 8.0:
                        color = Colors.BRIGHT_GREEN
                    elif percentage >= 5.0:
                        color = Colors.BRIGHT_YELLOW
                    elif percentage >= 2.0:
                        color = Colors.BRIGHT_ORANGE
                    else:
                        color = Colors.BRIGHT_WHITE

                    print(
                        f"    {Colors.BRIGHT_BLUE}Уровень {level:2d}:{Colors.RESET} "
                        f"{color}{count:>9,}{Colors.RESET} игроков "
                        f"({color}{percentage:4.2f}%{Colors.RESET})"
                    )

            # === СТАТИСТИКА ПО РОЛЯМ ===
            role_level_stats = self.session.query(
                self.config.player_model.role,
                func.avg(self.config.player_model.level).label('avg_level'),
                func.min(self.config.player_model.level).label('min_level'),
                func.max(self.config.player_model.level).label('max_level')
            ).group_by(self.config.player_model.role).all()

            print()
            print(f"{Colors.BRIGHT_WHITE}🎭 Статистика по ролям:{Colors.RESET}")

            for role_data in role_level_stats:
                role = role_data.role
                avg_level = role_data.avg_level
                min_level = role_data.min_level
                max_level = role_data.max_level

                # Визуальное оформление в зависимости от роли
                role_emoji = "⚔️" if role == "core" else "🛡️"
                role_color = Colors.BRIGHT_RED if role == "core" else Colors.BRIGHT_CYAN

                print(
                    f"    {role_emoji} {role_color}{role.upper():<8}{Colors.RESET} "
                    f"среднее {Colors.BRIGHT_YELLOW}{avg_level:5.1f}{Colors.RESET}, "
                    f"диапазон {min_level}-{max_level}"
                )

        except Exception as e:
            print_status_message(f"Ошибка при анализе уровней: {e}", "error")

    def _analyze_special_items(self, total_player_records: int) -> None:
        """
        Анализирует частоту покупки специальных предметов.

        Рассчитывает количество и процент игр с покупкой
        Aghanim's Scepter, Aghanim's Shard и Moon Shard.

        Args:
            total_player_records (int): Общее количество записей для расчёта процентов
        """

        print_subsection_header("Статистика Aghanim's предметов", "🔮", Colors.BRIGHT_MAGENTA)

        try:
            # Один запрос вместо трёх: подсчёт всех предметов за один проход
            item_stats = self.session.query(
                func.count(self.config.player_model.id).filter(
                    self.config.player_model.aghanims_scepter > 0
                ).label('scepter_count'),
                func.count(self.config.player_model.id).filter(
                    self.config.player_model.aghanims_shard > 0
                ).label('shard_count'),
                func.count(self.config.player_model.id).filter(
                    self.config.player_model.moonshard > 0
                ).label('moonshard_count')
            ).first()

            if item_stats is None:
                print_status_message("Нет данных о предметах для анализа", "warning")
                return

            scepter_count = item_stats.scepter_count
            shard_count = item_stats.shard_count
            moonshard_count = item_stats.moonshard_count

            # Расчет процентов для каждого предмета
            scepter_percentage = (
                (scepter_count / total_player_records) * 100
                if scepter_count else 0
            )
            shard_percentage = (
                (shard_count / total_player_records) * 100
                if shard_count else 0
            )
            moonshard_percentage = (
                (moonshard_count / total_player_records) * 100
                if moonshard_count else 0
            )

            # Вывод статистики для каждого предмета
            print_info_line(
                "Aghanim's Scepter",
                f"{scepter_count:,} ({scepter_percentage:.1f}%)",
                "🔱",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_PURPLE
            )
            print_info_line(
                "Aghanim's Shard",
                f"{shard_count:,} ({shard_percentage:.1f}%)",
                "💎",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_BLUE
            )
            print_info_line(
                "Moon Shard",
                f"{moonshard_count:,} ({moonshard_percentage:.1f}%)",
                "🌙",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_YELLOW
            )

        except Exception as e:
            print_status_message(f"Ошибка при анализе Aghanim's предметов: {e}", "error")
