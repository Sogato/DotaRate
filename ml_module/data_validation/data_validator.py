"""
Главный файл комплексной валидации и анализа данных Dota 2.

Модуль предоставляет класс DataValidator для проверки качества и анализа
статистики уже собранных данных. Поддерживает два типа баз данных:
публичные ranked матчи (Dataset) и профессиональные турнирные матчи (Pro).

DataValidator сам по себе анализ не выполняет — он является главным координатором и
запускает нужные компоненты из data_validation.components в правильном порядке
и собирает итоговый отчёт. Часть компонентов запускается только для Pro БД.

Основные этапы работы:
1. Инициализация — подключение к БД, загрузка кэша героев
2. Отображение информации о размере и весе данных (DatabaseAnalyzer)
3. Проверки целостности данных (IntegrityChecker)
4. Анализ матчей (MatchAnalyzer) и профессиональной статистики (ProAnalyzer)
5. Анализ игроков (PlayerAnalyzer) и героев (HeroAnalyzer)
6. Генерация итогового отчёта о состоянии данных
7. Очистка ресурсов — закрытие сессии БД
"""

# Стандартные библиотеки
import time
from typing import List, Optional

# Сторонние библиотеки
from sqlalchemy import create_engine, func, distinct
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError

# Локальные импорты
from data_validation.components.database_config import DatabaseConfig
from data_validation.components.database_analyzer import DatabaseAnalyzer
from data_validation.components.integrity_checker import IntegrityChecker
from data_validation.components.match_analyzer import MatchAnalyzer
from data_validation.components.player_analyzer import PlayerAnalyzer
from data_validation.components.hero_analyzer import HeroAnalyzer
from data_validation.components.pro_analyzer import ProAnalyzer
from data_validation.components.intersection_checker import IntersectionChecker
from config import PRIVATE_ACCOUNT_ID
from dota_core.hero_cache import HeroCache
from utils.console import (
    Colors,
    print_section_header,
    print_status_message,
    print_subsection_header
)


class DataValidator:
    """
    Главный координатор. Запускает все компоненты валидации и собирает итоговый отчёт.

    Класс управляет полным циклом валидации данных: инициализация соединения с БД,
    загрузка кеша героев, последовательный запуск всех видов проверок и анализа,
    генерация итогового отчета. Поддерживает два типа баз данных с автоматическим
    выбором конфигурации.

    Attributes:
        database_type (str): Тип базы данных ("dataset" или "pro")
        config (DatabaseConfig): Конфигурация активной БД.
        session (Session): Активная сессия SQLAlchemy
        start_time (float): Время начала выполнения для измерения производительности
        heroes_cache (HeroCache): Кэш данных о героях
        integrity_issues (List[str]): Список обнаруженных проблем целостности
        matches_count (int): Общее количество матчей в БД
        players_count (int): Общее количество записей игроков в БД
    """

    def __init__(self, database_type: str = "dataset") -> None:
        """
        Инициализирует валидатор для выбранного типа базы данных.

        Выбирает конфигурацию БД (Dataset или Pro) на основе переданного типа
        и подготавливает структуры для хранения результатов анализа.

        Args:
            database_type (str): Тип базы данных.
                "dataset" — публичные ranked матчи → DatabaseConfig.for_dataset()
                "pro" — профессиональные турнирные матчи → DatabaseConfig.for_pro()
                По умолчанию "dataset".
        """

        # Защита от случайного верхнего регистра на входе
        self.database_type = database_type.lower()

        # Выбор конфигурации в зависимости от типа БД
        if self.database_type == "pro":
            self.config = DatabaseConfig.for_pro()
        else:
            self.config = DatabaseConfig.for_dataset()

        # Инициализация атрибутов
        self.session: Session
        self.start_time: Optional[float] = None
        self.heroes_cache = HeroCache()
        self.integrity_issues: List[str] = []
        self.matches_count: int = 0
        self.players_count: int = 0

    def run(self) -> None:
        """
        Выполняет полный цикл валидации и анализа данных.

        Последовательность действий:
        1. Фиксация времени начала выполнения
        2. Инициализация подключения к БД и кэша героев
        3. Отображение информации о размере и весе данных
        4. Проверки целостности данных
        5. Анализ матчей и профессиональной статистики
        6. Анализ игроков и героев
        7. Генерация и вывод итогового отчёта
        8. Очистка ресурсов

        Ошибки БД и подключения перехватываются внутри метода и выводятся
        в консоль. Сессия закрывается в блоке finally.
        """

        # Фиксируем время старта до начала любых операций
        self.start_time = time.time()

        try:
            # === ИНИЦИАЛИЗАЦИЯ ===
            self._initialize()
            self._display_database_info()

            # === ПРОВЕРКИ ЦЕЛОСТНОСТИ ===
            self._run_integrity_checks()

            # === АНАЛИЗ ДАННЫХ ===
            self._run_match_analysis()
            self._run_player_analysis()

            # === ИТОГОВЫЙ ОТЧЁТ ===
            self._generate_final_report()

        except SQLAlchemyError as e:
            print_status_message(f"Ошибка базы данных: {e}", "error")
        except ConnectionError as e:
            print_status_message(f"Ошибка подключения к базе данных: {e}", "error")
        except Exception as e:
            print_status_message(f"Ошибка выполнения: {e}", "error")
        finally:
            # Закрываем сессию в любом случае, даже при исключении
            self._cleanup()

    def _initialize(self) -> None:
        """
        Инициализирует подключение к базе данных и загружает кэш героев.

        Raises:
            RuntimeError: Если не удалось загрузить данные героев из справочной БД
        """

        # === СОЗДАНИЕ ПОДКЛЮЧЕНИЯ К БД ===
        engine = create_engine(self.config.database_url)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        self.session = SessionLocal()

        # === ИНИЦИАЛИЗАЦИЯ КЭША ГЕРОЕВ ===
        if not self.heroes_cache.initialize():
            raise RuntimeError("Не удалось загрузить данные героев!")

    def _display_database_info(self) -> None:
        """
        Получает и отображает информацию о базе данных.

        Сохраняет счётчики matches_count и players_count
        для использования в последующих этапах анализа и финальном отчёте.

        Raises:
            RuntimeError: Если база данных пуста (нет данных для анализа)
        """

        # Получаем полную информацию о размере и содержимом БД
        analyzer = DatabaseAnalyzer(self.session, self.config)

        # Сохраняем счётчики для использования в финальном отчёте
        self.matches_count, self.players_count = analyzer.collect()

        # Проверяем что БД не пуста
        if self.matches_count == 0 and self.players_count == 0:
            raise RuntimeError("База данных пуста, нет данных для анализа")


    def _run_integrity_checks(self) -> None:
        """
        Запускает проверки целостности данных через IntegrityChecker.

        Делегирует все проверки классу IntegrityChecker и сохраняет
        результаты в self.integrity_issues для финального отчёта.
        """

        integrity_checker = IntegrityChecker(self.session, self.config)
        self.integrity_issues = integrity_checker.check()

    def _run_match_analysis(self) -> None:
        """
        Запускает анализ матчей и профессиональной статистики.

        Базовый анализ через MatchAnalyzer выполняется всегда.
        Дополнительный анализ через ProAnalyzer запускается только
        если конфигурация поддерживает профессиональные данные
        (supports_teams, supports_leagues или supports_series).
        """

        # Базовый анализ матчей — выполняется всегда
        match_analyzer = MatchAnalyzer(self.session, self.config)
        match_analyzer.analyze()

        # Дополнительный анализ для профессиональной БД
        if (self.config.supports_teams or
            self.config.supports_leagues or
            self.config.supports_series):
            pro_analyzer = ProAnalyzer(self.session, self.config)
            pro_analyzer.analyze(self.matches_count)

    def _run_player_analysis(self) -> None:
        """
        Запускает анализ игроков и героев.

        Анализ игроков через PlayerAnalyzer выполняется всегда.
        Анализ героев через HeroAnalyzer запускается только если
        есть записи игроков (players_count > 0). HeroAnalyzer
        использует players_count для расчёта относительных метрик.
        """

        # Анализ игроков выполняется всегда
        player_analyzer = PlayerAnalyzer(self.session, self.config)
        player_analyzer.analyze()

        # Анализ героев запускаем только если есть данные
        if self.players_count > 0:
            hero_analyzer = HeroAnalyzer(self.session, self.config, self.heroes_cache)
            hero_analyzer.analyze(self.players_count)


    def _generate_final_report(self) -> None:
        """
        Генерирует и выводит итоговый отчёт о валидации и анализе.

        Отчёт включает: объём данных (матчи, игроки, герои),
        профессиональную статистику (для Pro БД), баланс сторон
        (винрейты Radiant/Dire), производительность анализа и
        список проблем целостности.

        При ошибках сбора статистики использует нулевые значения
        по умолчанию — отчёт формируется в любом случае.
        """

        # Фиксируем время завершения для подсчёта длительности анализа
        end_time = time.time()
        execution_time = end_time - self.start_time

        try:
            # === ПОЛУЧЕНИЕ КЛЮЧЕВЫХ СТАТИСТИЧЕСКИХ ДАННЫХ ===
            # Подсчёт побед Radiant и Dire для расчёта винрейтов
            radiant_wins = self.session.query(
                func.count(self.config.match_model.match_id)
            ).filter(
                self.config.match_model.radiant_win == True
            ).scalar()

            dire_wins = self.session.query(
                func.count(self.config.match_model.match_id)
            ).filter(
                self.config.match_model.radiant_win == False
            ).scalar()

            # Вычисляем процентные винрейты с защитой от деления на ноль
            radiant_winrate = (
                (radiant_wins / self.matches_count * 100)
                if self.matches_count > 0 else 0
            )
            dire_winrate = (
                (dire_wins / self.matches_count * 100)
                if self.matches_count > 0 else 0
            )

            # Временной диапазон данных в днях
            min_time, max_time = self.session.query(
                func.min(self.config.match_model.start_time),
                func.max(self.config.match_model.start_time)
            ).first()

            days_covered = 0
            if min_time and max_time:
                days_covered = (max_time - min_time) / (24 * 3600)

            # Количество уникальных игроков (исключаем приватные аккаунты)
            unique_players = self.session.query(
                func.count(distinct(self.config.player_model.account_id))
            ).filter(
                self.config.player_model.account_id != PRIVATE_ACCOUNT_ID
            ).scalar()

            # Средняя длительность матчей в формате MM:SS
            avg_duration = self.session.query(
                func.avg(self.config.match_model.duration)
            ).scalar()
            avg_duration_str = (
                f"{int(avg_duration) // 60}:{int(avg_duration) % 60:02d}"
                if avg_duration else "N/A"
            )

            # === ДОПОЛНИТЕЛЬНАЯ СТАТИСТИКА ДЛЯ ПРОФЕССИОНАЛЬНОЙ БД ===
            pro_stats = {}

            if self.config.supports_teams:
                # Подсчёт уникальных команд через UNION по radiant и dire team_id
                pro_stats['unique_teams'] = self.session.query(
                    self.config.match_model.radiant_team_id.label('team_id')
                ).filter(
                    self.config.match_model.radiant_team_id.isnot(None)
                ).union(
                    self.session.query(
                        self.config.match_model.dire_team_id.label('team_id')
                    ).filter(
                        self.config.match_model.dire_team_id.isnot(None)
                    )
                ).distinct().count()

            if self.config.supports_leagues:
                # Подсчёт уникальных лиг по leagueid
                pro_stats['unique_leagues'] = self.session.query(
                    func.count(distinct(self.config.match_model.leagueid))
                ).filter(
                    self.config.match_model.leagueid.isnot(None)
                ).scalar()

            if self.config.supports_series:
                # Подсчёт уникальных серий по series_id
                pro_stats['unique_series'] = self.session.query(
                    func.count(distinct(self.config.match_model.series_id))
                ).filter(
                    self.config.match_model.series_id.isnot(None)
                ).scalar()

        except Exception as e:
            print_status_message(f"Ошибка получения статистики для отчёта: {e}", "warning")

            # Нулевые значения по умолчанию — отчёт формируется в любом случае
            radiant_winrate = 0
            dire_winrate = 0
            days_covered = 0
            unique_players = 0
            avg_duration_str = "N/A"
            pro_stats = {}

        # === ОТОБРАЖЕНИЕ ИТОГОВОГО ОТЧЁТА ===
        header = f"ИТОГОВЫЙ ОТЧЁТ ({self.config.database_type.upper()})"
        print_section_header(header, "📋", 80, Colors.BRIGHT_GOLD)

        # Объём проанализированных данных
        print()
        print(f"{Colors.BRIGHT_WHITE}📊 Объём проанализированных данных:{Colors.RESET}")
        print(f"    ├─ Тип базы данных:       {Colors.BRIGHT_PURPLE}{self.config.database_type:>10}{Colors.RESET}")
        print(f"    ├─ Матчей обработано:     {Colors.BRIGHT_GREEN}{self.matches_count:>10,}{Colors.RESET}")
        print(f"    ├─ Записей игроков:       {Colors.BRIGHT_CYAN}{self.players_count:>10,}{Colors.RESET}")
        print(f"    ├─ Уникальных игроков:    {Colors.BRIGHT_BLUE}{unique_players:>10,}{Colors.RESET}")
        print(f"    └─ Героев в базе:         {Colors.BRIGHT_PURPLE}{len(self.heroes_cache):>10,}{Colors.RESET}")

        # Профессиональная статистика (только если есть данные Pro БД)
        if pro_stats:
            print()
            print(f"{Colors.BRIGHT_WHITE}🏆 Профессиональная статистика:{Colors.RESET}")
            if 'unique_teams' in pro_stats:
                print(f"    ├─ Уникальных команд:     {Colors.BRIGHT_GOLD}{pro_stats['unique_teams']:>10,}{Colors.RESET}")
            if 'unique_leagues' in pro_stats:
                print(f"    ├─ Уникальных лиг:        {Colors.BRIGHT_ORANGE}{pro_stats['unique_leagues']:>10,}{Colors.RESET}")
            if 'unique_series' in pro_stats:
                print(f"    └─ Уникальных серий:      {Colors.BRIGHT_CYAN}{pro_stats['unique_series']:>10,}{Colors.RESET}")

        # Баланс и качество данных
        print()
        print(f"{Colors.BRIGHT_WHITE}⚖️ Баланс и качество данных:{Colors.RESET}")
        print(f"    ├─ Radiant винрейт:       {Colors.BRIGHT_GREEN}{radiant_winrate:>9.1f}%{Colors.RESET}")
        print(f"    ├─ Dire винрейт:          {Colors.BRIGHT_RED}{dire_winrate:>9.1f}%{Colors.RESET}")
        print(f"    ├─ Средняя длительность:  {Colors.BRIGHT_ORANGE}{avg_duration_str:>10}{Colors.RESET}")
        print(f"    └─ Временной охват:       {Colors.BRIGHT_MAGENTA}{days_covered:>9.1f} дней{Colors.RESET}")

        # Производительность анализа
        print()
        print(f"{Colors.BRIGHT_WHITE}⏱️ Производительность анализа:{Colors.RESET}")
        print(f"    └─ Время выполнения:      {Colors.BRIGHT_YELLOW}{execution_time:>9.2f} сек{Colors.RESET}")

        # Проблемы целостности, выводим только если они есть
        if self.integrity_issues:
            print()
            print(f"{Colors.BRIGHT_WHITE}⚠️ Проблемы целостности данных:{Colors.RESET}")
            print(f"    Обнаружено проблем: {Colors.BRIGHT_RED}{len(self.integrity_issues)}{Colors.RESET}")
            for i, issue in enumerate(self.integrity_issues, 1):
                print(f"    {i}. {Colors.BRIGHT_RED}{issue}{Colors.RESET}")

        # === ЗАКЛЮЧЕНИЕ ===
        header = f"АНАЛИЗ ЗАВЕРШЁН ({self.config.database_type.upper()})"
        print_section_header(header, "🎉", 80, Colors.BRIGHT_GREEN)

        if not self.integrity_issues:
            print(f"✅ {Colors.BRIGHT_GREEN}Все проверки целостности пройдены успешно!{Colors.RESET}")
            if self.config.database_type == "Professional":
                print(f"✅ {Colors.BRIGHT_GREEN}Профессиональные данные включают команды, лиги и серии{Colors.RESET}")
        else:
            print(f"⚠️ {Colors.BRIGHT_YELLOW}Обнаружены проблемы целостности данных{Colors.RESET}")
            print(f"🔍 {Colors.BRIGHT_YELLOW}Рекомендуется исправить проблемы перед использованием данных{Colors.RESET}")

    def _cleanup(self) -> None:
        """
        Закрывает соединение с базой данных и освобождает ресурсы.

        Безопасно закрывает сессию SQLAlchemy с обработкой возможных ошибок.
        """

        if self.session is not None:
            try:
                # Закрываем сессию и освобождаем соединение с БД
                self.session.close()
                print_status_message("Соединение с базой данных закрыто", "success")
            except Exception as e:
                print_status_message(f"Ошибка при закрытии соединения: {e}", "warning")


def main() -> None:
    """
    Главная функция с интерактивным меню выбора режима анализа.

    Режимы:
    1 — Анализ публичных матчей (Dataset)
    2 — Анализ профессиональных матчей (Pro)
    3 — Быстрая проверка пересечений между базами
    0 — Выход
    """

    # Выводим заголовок и меню выбора режима
    print_section_header("ЗАПУСК ВАЛИДАТОРА", "🔍", color=Colors.BRIGHT_ORANGE)

    print_subsection_header("Выберите режим анализа:", "⚙️", Colors.BRIGHT_ORANGE)
    print(f"  {Colors.BRIGHT_ORANGE}1.{Colors.RESET} 🎮 Публичные матчи (Dataset)")
    print(f"  {Colors.BRIGHT_ORANGE}2.{Colors.RESET} 🏆 Профессиональные матчи (Pro)")
    print(f"  {Colors.BRIGHT_ORANGE}3.{Colors.RESET} 🚀 Быстрая проверка пересечений")
    print(f"  {Colors.BRIGHT_ORANGE}0.{Colors.RESET} 🚪 Выход")

    choice = input(f"{Colors.BRIGHT_CYAN}➤ Введите номер:{Colors.RESET} ").strip()

    # Создаём нужный анализатор и запускаем в зависимости от выбора
    if choice == "1":
        validator = DataValidator("dataset")
        validator.run()
    elif choice == "2":
        validator = DataValidator("pro")
        validator.run()
    elif choice == "3":
        intersection_checker = IntersectionChecker()
        intersection_checker.check()
    elif choice == "0":
        print(f"\n{Colors.BRIGHT_WHITE}Выход из программы.{Colors.RESET}")
    else:
        print(f"\n{Colors.BRIGHT_RED}Неверный выбор. Пожалуйста, попробуйте снова.{Colors.RESET}")


if __name__ == "__main__":
    main()
