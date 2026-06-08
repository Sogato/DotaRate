"""
Модуль анализа статистики героев Dota 2.

Модуль предоставляет комплексный анализ игровой статистики героев на основе данных
из базы данных матчей. Включает анализ популярности, винрейтов, аспектов героев
(hero_variant) и их статистических показателей.

Виды анализа:
1. Популярность героев:
   - Частота выбора героев в матчах
   - Процент от общего количества игр
   - Средний KDA (Kill/Death/Assist ratio)
   - Топ наиболее играемых героев

2. Винрейт героев (базовая версия):
   - Процент побед для каждого героя
   - Фильтрация по минимальному количеству игр
   - Расчет с учетом стороны (Radiant/Dire)
   - Топ героев по проценту побед

3. Винрейт героев с аспектами:
   - Отдельная статистика для каждого аспекта
   - Пониженный порог минимальных игр
   - Топ аспектов по проценту побед

4. Статистика аспектов:
   - Распределение игр по аспектам
   - Процентное соотношение
   - Анализ популярности аспектов

Все расчеты контекстно учитывают сторону игрока (Radiant/Dire) и применяют
пороги минимального количества игр для статистической значимости.
"""

# Сторонние библиотеки
from sqlalchemy import func, case
from sqlalchemy.orm import Session

# Локальные импорты
from data_validation.components.database_config import DatabaseConfig
from utils.hero_cache import HeroCache
from utils.console import (
    Colors,
    print_subsection_header,
    print_info_line,
    print_status_message,
    print_section_header
)

# === КОНСТАНТЫ СТАТИСТИЧЕСКОЙ ЗНАЧИМОСТИ ===
MINIMUM_GAMES_FOR_HERO_WINRATE = 100            # Минимум матчей для расчёта винрейта героя
MINIMUM_GAMES_FOR_HERO_VARIANT_WINRATE = 50     # Минимум матчей для винрейта аспекта героя
                                                # Порог снижен, т.к. аспекты дробят выборку

# === КОНСТАНТЫ ОТОБРАЖЕНИЯ ===
TOP_HEROES_DISPLAY_COUNT = 15                   # Количество героев для отображения в топах


class HeroAnalyzer:
    """
    Анализатор статистики героев Dota 2 на основе данных матчей.

    Класс выполняет комплексный анализ игровых данных: популярность героев,
    винрейты (общие и по аспектам), статистику аспектов героев.
    Все расчёты учитывают сторону игрока и применяют пороги статистической
    значимости.

    Attributes:
        session (Session): Активная сессия SQLAlchemy для запросов к БД
        config (DatabaseConfig): Конфигурация с моделями Match и Player
        heroes_cache (HeroCache): Кеш данных о героях для получения имён
    """

    def __init__(self, session: Session, config: DatabaseConfig, heroes_cache: HeroCache) -> None:
        """
        Инициализирует модуль статистики героев для выбранного типа базы данных.

        Args:
            session (Session): Активная сессия SQLAlchemy
            config (DatabaseConfig): Конфигурация с моделями и типом БД
            heroes_cache (HeroCache): Кеш для получения имён героев по ID
        """

        self.session = session
        self.config = config
        self.heroes_cache = heroes_cache

    def analyze(self, total_player_records: int) -> None:
        """
        Выполняет полный анализ статистики героев.

        Последовательно запускает анализ популярности, винрейтов (базовых
        и по аспектам) и статистику распределения аспектов.

        Args:
            total_player_records (int): Общее количество записей игроков в БД.
                Используется для расчёта процентов популярности.
        """

        header = f"СТАТИСТИКА ГЕРОЕВ ({self.config.database_type.upper()})"
        print_section_header(header, "👥", color=Colors.BRIGHT_GOLD)

        # Последовательное выполнение всех видов анализа
        self._analyze_popularity(total_player_records)
        self._analyze_winrates()
        self._analyze_variant_winrates()
        self._analyze_variants_stats(total_player_records)

    def _analyze_popularity(self, total_player_records: int) -> None:
        """
        Анализирует популярность героев по частоте выбора.

        Для каждого героя рассчитывает количество игр, процент от общего
        числа и средний KDA. Результаты сортируются по убыванию частоты выбора
        и выводятся с цветовым кодированием рангов.

        Args:
            total_player_records (int): Общее количество записей для расчёта процентов
        """

        print_subsection_header(
            f"Топ-{TOP_HEROES_DISPLAY_COUNT} популярных героев",
            "🦸",
            Colors.BRIGHT_BLUE
        )

        try:
            # Запрос статистики популярности с агрегацией
            hero_popularity_stats = self.session.query(
                self.config.player_model.hero_id,
                func.count(self.config.player_model.hero_id).label('count'),
                func.avg(self.config.player_model.kda).label('avg_kda')
            ).group_by(self.config.player_model.hero_id) \
                .order_by(func.count(self.config.player_model.hero_id).desc()) \
                .limit(TOP_HEROES_DISPLAY_COUNT).all()

            # Вывод топа с цветовым кодированием рангов
            for rank, (hero_id, count, avg_kda) in enumerate(hero_popularity_stats, 1):
                hero_name = self.heroes_cache.get_hero_name(hero_id)
                percentage = (count / total_player_records) * 100

                # Топ-3 золото, 4-10 серебро, остальные белый
                rank_color = (Colors.BRIGHT_GOLD if rank <= 3
                              else Colors.BRIGHT_SILVER if rank <= 10
                else Colors.BRIGHT_WHITE)

                print(f"{rank_color}{rank:2d}.{Colors.RESET} "
                      f"{Colors.BRIGHT_BLUE}{hero_name}{Colors.RESET} "
                      f"(ID: {Colors.BRIGHT_YELLOW}{hero_id}{Colors.RESET}): "
                      f"{Colors.BRIGHT_GREEN}{count:,}{Colors.RESET} игр "
                      f"({Colors.BRIGHT_CYAN}{percentage:.1f}%{Colors.RESET}), "
                      f"KDA: {Colors.BRIGHT_ORANGE}{avg_kda:.2f}{Colors.RESET}")

            # Проверка на пустой результат
            if not hero_popularity_stats:
                print_status_message("Нет данных о популярности героев", "warning")

        except Exception as e:
            print_status_message(f"Ошибка при расчете популярности героев: {e}", "error")

    def _analyze_winrates(self) -> None:
        """
        Анализирует винрейт героев без учёта вариантов.

        Рассчитывает процент побед для каждого героя с учётом стороны игрока.
        Победой считается: team_number=0 и radiant_win=True, либо team_number=1 и
        radiant_win=False. Герои с количеством игр ниже
        MINIMUM_GAMES_FOR_HERO_WINRATE отфильтровываются.

        Результаты фильтруются по минимальному количеству игр и сортируются
        по убыванию винрейта с цветовым кодированием
        """

        print_subsection_header(
            f"Топ-{TOP_HEROES_DISPLAY_COUNT} героев по винрейту "
            f"(мин. {MINIMUM_GAMES_FOR_HERO_WINRATE} игр)",
            "🏆",
            Colors.BRIGHT_GOLD
        )

        try:
            # Запрос винрейтов с условным подсчётом побед
            hero_winrates = self.session.query(
                self.config.player_model.hero_id,
                func.count(self.config.player_model.id).label('total_games'),
                func.sum(
                    case(
                        (
                            ((self.config.player_model.team_number == 0) &
                             (self.config.match_model.radiant_win == True)) |
                            ((self.config.player_model.team_number == 1) &
                             (self.config.match_model.radiant_win == False)),
                            1
                        ),
                        else_=0
                    )
                ).label('wins')
            ).join(
                self.config.match_model,
                self.config.player_model.match_id == self.config.match_model.match_id
            ).group_by(self.config.player_model.hero_id) \
                .having(func.count(self.config.player_model.id) >= MINIMUM_GAMES_FOR_HERO_WINRATE) \
                .all()

            # Расчёт процента винрейта для каждого героя
            hero_winrates_with_percentage = []
            for hero_id, total_games, wins in hero_winrates:
                winrate = (wins * 100.0) / total_games if total_games > 0 else 0
                hero_winrates_with_percentage.append((hero_id, total_games, wins, winrate))

            # Сортировка по винрейту (убывание)
            hero_winrates_with_percentage.sort(key=lambda x: x[3], reverse=True)

            # Вывод топа с цветовым кодированием
            for rank, (hero_id, total_games, wins, winrate) in enumerate(
                    hero_winrates_with_percentage[:TOP_HEROES_DISPLAY_COUNT], 1):
                hero_name = self.heroes_cache.get_hero_name(hero_id)

                # Топ-3 золото, 4-10 серебро, остальные белый
                rank_color = (Colors.BRIGHT_GOLD if rank <= 3
                              else Colors.BRIGHT_SILVER if rank <= 10
                              else Colors.BRIGHT_WHITE)

                # >=55% зелёный, >=50% жёлтый, <50% красный
                winrate_color = (Colors.BRIGHT_GREEN if winrate >= 55
                                 else Colors.BRIGHT_YELLOW if winrate >= 50
                                 else Colors.BRIGHT_RED)

                print(f"{rank_color}{rank:2d}.{Colors.RESET} "
                      f"{Colors.BRIGHT_BLUE}{hero_name}{Colors.RESET}: "
                      f"{winrate_color}{winrate:.1f}%{Colors.RESET} "
                      f"({Colors.BRIGHT_WHITE}{wins}/{total_games}{Colors.RESET})")

            # Проверка на пустой результат
            if not hero_winrates_with_percentage:
                print_status_message(
                    f"Нет героев с минимум {MINIMUM_GAMES_FOR_HERO_WINRATE} играми",
                    "warning"
                )

        except Exception as e:
            print_status_message(f"Ошибка при расчете винрейта героев: {e}", "error")

    def _analyze_variant_winrates(self) -> None:
        """
        Анализирует винрейт героев с учётом аспектов (hero_variant).

        Аналогично _analyze_winrates(), но группировка выполняется по паре
        (hero_id, hero_variant). Использует пониженный порог
        MINIMUM_GAMES_FOR_HERO_VARIANT_WINRATE, т.к. аспекты дробят выборку.

        Результаты сортируются по убыванию винрейта с цветовым кодированием.
        """

        print_subsection_header(
            f"Топ-{TOP_HEROES_DISPLAY_COUNT} героев по винрейту "
            f"(с вариантами, мин. {MINIMUM_GAMES_FOR_HERO_VARIANT_WINRATE} игр)",
            "🎨",
            Colors.BRIGHT_PURPLE
        )

        try:
            # Запрос винрейтов с группировкой по hero_id и hero_variant
            hero_variant_winrates = self.session.query(
                self.config.player_model.hero_id,
                self.config.player_model.hero_variant,
                func.count(self.config.player_model.id).label('total_games'),
                func.sum(
                    case(
                        (
                            ((self.config.player_model.team_number == 0) &
                             (self.config.match_model.radiant_win == True)) |
                            ((self.config.player_model.team_number == 1) &
                             (self.config.match_model.radiant_win == False)),
                            1
                        ),
                        else_=0
                    )
                ).label('wins')
            ).join(
                self.config.match_model,
                self.config.player_model.match_id == self.config.match_model.match_id
            ).group_by(self.config.player_model.hero_id, self.config.player_model.hero_variant) \
                .having(func.count(self.config.player_model.id) >= MINIMUM_GAMES_FOR_HERO_VARIANT_WINRATE) \
                .all()

            # Расчёт процента винрейта для каждого варианта
            hero_variant_winrates_with_percentage = []
            for hero_id, hero_variant, total_games, wins in hero_variant_winrates:
                winrate = (wins * 100.0) / total_games if total_games > 0 else 0
                hero_variant_winrates_with_percentage.append(
                    (hero_id, hero_variant, total_games, wins, winrate)
                )

            # Сортировка по винрейту (убывание)
            hero_variant_winrates_with_percentage.sort(key=lambda x: x[4], reverse=True)

            # Вывод топа с цветовым кодированием
            for rank, (hero_id, hero_variant, total_games, wins, winrate) in enumerate(
                    hero_variant_winrates_with_percentage[:TOP_HEROES_DISPLAY_COUNT], 1):
                hero_name = self.heroes_cache.get_hero_name(hero_id)
                variant_text = f" (вариант {hero_variant})" if hero_variant > 0 else ""

                # Топ-3 золото, 4-10 серебро, остальные белый
                rank_color = (Colors.BRIGHT_GOLD if rank <= 3
                              else Colors.BRIGHT_SILVER if rank <= 10
                              else Colors.BRIGHT_WHITE)

                # >=55% зелёный, >=50% жёлтый, <50% красный
                winrate_color = (Colors.BRIGHT_GREEN if winrate >= 55
                                 else Colors.BRIGHT_YELLOW if winrate >= 50
                                 else Colors.BRIGHT_RED)

                print(f"{rank_color}{rank:2d}.{Colors.RESET} "
                      f"{Colors.BRIGHT_BLUE}{hero_name}{Colors.BRIGHT_PURPLE}{variant_text}{Colors.RESET}: "
                      f"{winrate_color}{winrate:.1f}%{Colors.RESET} "
                      f"({Colors.BRIGHT_WHITE}{wins}/{total_games}{Colors.RESET})")

            # Проверка на пустой результат
            if not hero_variant_winrates_with_percentage:
                print_status_message(
                    f"Нет героев с вариантами с минимум {MINIMUM_GAMES_FOR_HERO_VARIANT_WINRATE} играми",
                    "warning"
                )

        except Exception as e:
            print_status_message(f"Ошибка при расчете винрейта героев с вариантами: {e}", "error")

    def _analyze_variants_stats(self, total_player_records: int) -> None:
        """
        Анализирует распределение аспектов героев.

        Рассчитывает частоту использования каждого аспекта и процент
        от общего количества игр. Вариант "Неизвестный" — незадокументированный аспект
        героя, варианты >0 — существующие аспекты, модифицирующие способности.

        Args:
            total_player_records (int): Общее количество записей для расчёта процентов
        """

        print_subsection_header("Статистика вариантов героев", "🎨", Colors.BRIGHT_PINK)

        try:
            # Запрос статистики вариантов с агрегацией
            variant_stats = self.session.query(
                self.config.player_model.hero_variant,
                func.count(self.config.player_model.hero_variant).label('count')
            ).group_by(self.config.player_model.hero_variant) \
                .order_by(func.count(self.config.player_model.hero_variant).desc()).all()

            # Вывод общего количества различных вариантов
            print_info_line(
                "Всего различных вариантов",
                f"{len(variant_stats)}",
                "🎭",
                Colors.BRIGHT_WHITE,
                Colors.BRIGHT_PURPLE
            )

            # Сортировка: "Неизвестный" первым, остальные по убыванию count
            unknown = [(v, c) for v, c in variant_stats if v == 0]
            known = [(v, c) for v, c in variant_stats if v != 0]
            variant_stats_sorted = unknown + known

            # Вывод топа вариантов с процентами
            for variant, count in variant_stats_sorted:
                percentage = (count / total_player_records) * 100
                variant_text = f"Вариант {variant}" if variant > 0 else "Неизвестный"

                print_info_line(
                    variant_text,
                    f"{count:,} ({percentage:.1f}%)",
                    "🎨",
                    Colors.BRIGHT_WHITE,
                    Colors.BRIGHT_MINT
                )

            # Проверка на пустой результат
            if not variant_stats:
                print_status_message("Нет данных о вариантах героев", "warning")

        except Exception as e:
            print_status_message(f"Ошибка при расчете статистики вариантов: {e}", "error")
