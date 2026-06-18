"""
Модуль проверки пересечений между базами данных матчей Dota 2.

Модуль выполняет сравнение двух баз данных — Dataset (публичные матчи)
и Pro (турнирные матчи) — на предмет дублирования match_id.

По логике системы базы должны содержать непересекающиеся множества матчей.
Любое пересечение является недопустимым и указывает на проблему
в системе сбора или обработки данных.

Алгоритм работы:
1. Подключение к обеим базам данных
2. Извлечение множеств match_id из каждой БД
3. Анализ пересечений через операции множеств (intersection, difference)
4. Проверка консистентности данных в пересекающихся записях
   (сравнение start_time, duration, radiant_win)
5. Вывод статистики и примеров пересекающихся матчей
"""

# Стандартные библиотеки
from typing import Dict, Set

# Сторонние библиотеки
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError

# Локальные импорты
from data_validation.components.database_config import DatabaseConfig
from dota_core.utils.time_format import format_timestamp
from dota_core.utils.console import (
    Colors,
    print_section_header,
    print_subsection_header,
    print_info_line,
    print_status_message
)

# === КОНСТАНТЫ ОТОБРАЖЕНИЯ ===
SAMPLE_MATCHES_DISPLAY_LIMIT = 10           # Количество примеров пересекающихся матчей


class IntersectionChecker:
    """
    Анализатор пересечений данных между базами Dataset и Pro.

    По логике системы базы данных должны содержать непересекающиеся
    множества матчей. Любое обнаруженное пересечение является недопустимым
    и указывает на проблему в системе сбора или обработки данных.

    Attributes:
        dataset_config (DatabaseConfig): Конфигурация для Dataset БД
        pro_config (DatabaseConfig): Конфигурация для Pro БД
    """

    def __init__(self) -> None:
        """
         Инициализирует модуль для анализа пересечений.

        Создаёт конфигурации для обеих баз данных через DatabaseConfig.
        """

        self.dataset_config = DatabaseConfig.for_dataset()
        self.pro_config = DatabaseConfig.for_pro()

    def check(self) -> Dict[str, int]:
        """
        Выполняет полную проверку пересечений между базами данных.

        Подключается к обеим БД, извлекает множества match_id,
        вычисляет пересечения и выводит статистику. При обнаружении
        пересечений отображает примеры с проверкой консистентности данных.

        Returns:
            Dict[str, int]: Словарь со статистикой пересечений:
                - total_dataset: количество матчей в Dataset БД
                - total_pro: количество матчей в Pro БД
                - intersection: количество пересекающихся матчей
                - dataset_only: матчи только в Dataset БД
                - pro_only: матчи только в Pro БД
            Возвращает пустой словарь при ошибках.
        """

        print_section_header(
            "ПРОВЕРКА ПЕРЕСЕЧЕНИЙ МЕЖДУ БАЗАМИ ДАННЫХ",
            "🔄",
            color=Colors.BRIGHT_MAGENTA
        )

        dataset_session = None
        pro_session = None

        try:
            # === СОЗДАНИЕ ПОДКЛЮЧЕНИЙ К БАЗАМ ДАННЫХ ===
            dataset_engine = create_engine(self.dataset_config.database_url)
            pro_engine = create_engine(self.pro_config.database_url)

            DatasetSessionLocal = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=dataset_engine
            )
            ProSessionLocal = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=pro_engine
            )

            dataset_session = DatasetSessionLocal()
            pro_session = ProSessionLocal()

            # Получение всех match_id из Dataset БД
            dataset_match_ids = set(
                match_id for (match_id,) in
                dataset_session.query(self.dataset_config.match_model.match_id).all()
            )

            # Получение всех match_id из Pro БД
            pro_match_ids = set(
                match_id for (match_id,) in
                pro_session.query(self.pro_config.match_model.match_id).all()
            )

            # === АНАЛИЗ ПЕРЕСЕЧЕНИЙ ЧЕРЕЗ ОПЕРАЦИИ МНОЖЕСТВ ===
            intersection = dataset_match_ids & pro_match_ids
            dataset_only = dataset_match_ids - pro_match_ids
            pro_only = pro_match_ids - dataset_match_ids

            # === ФОРМИРОВАНИЕ СТАТИСТИКИ ===
            total_dataset = len(dataset_match_ids)
            total_pro = len(pro_match_ids)
            intersection_count = len(intersection)
            dataset_only_count = len(dataset_only)
            pro_only_count = len(pro_only)

            result = {
                'total_dataset': total_dataset,
                'total_pro': total_pro,
                'intersection': intersection_count,
                'dataset_only': dataset_only_count,
                'pro_only': pro_only_count
            }

            # === ВЫВОД РЕЗУЛЬТАТОВ АНАЛИЗА ===
            self._display_statistics(result)

            # Примеры пересекающихся матчей (если есть)
            if intersection_count > 0:
                self._display_sample_matches(intersection, dataset_session, pro_session)

            return result

        except SQLAlchemyError as e:
            print_status_message(
                f"Ошибка базы данных при проверке пересечений: {e}",
                "error",
                "❌"
            )
            return {}

        except Exception as e:
            print_status_message(
                f"Неожиданная ошибка при проверке пересечений: {e}",
                "error",
                "💥"
            )
            return {}

        finally:
            # Гарантированное закрытие соединений
            if dataset_session:
                dataset_session.close()
            if pro_session:
                pro_session.close()

    @staticmethod
    def _display_statistics(result: Dict[str, int]) -> None:
        """
        Отображает детальную статистику пересечений между базами данных.

        Выводит размеры БД, количество пересечений, уникальные матчи
        и итоговый статус проверки с оценкой серьёзности проблемы.

        Args:
            Dict[str, int]: Словарь со статистикой пересечений:
                - total_dataset: количество матчей в Dataset БД
                - total_pro: количество матчей в Pro БД
                - intersection: количество пересекающихся матчей
                - dataset_only: матчи только в Dataset БД
                - pro_only: матчи только в Pro БД
        """

        # === РАСЧЕТ ДОПОЛНИТЕЛЬНЫХ МЕТРИК ===
        total_unique = result['dataset_only'] + result['pro_only']
        total_all = result['total_dataset'] + result['total_pro']

        # Процент каждой БД от общего количества
        dataset_percentage = (result['total_dataset'] / total_all * 100) if total_all > 0 else 0
        pro_percentage = (result['total_pro'] / total_all * 100) if total_all > 0 else 0

        # Процент уникальных матчей относительно своей БД
        dataset_unique_pct = (result['dataset_only'] / result['total_dataset'] * 100) if result[
                                                                                             'total_dataset'] > 0 else 0
        pro_unique_pct = (result['pro_only'] / result['total_pro'] * 100) if result['total_pro'] > 0 else 0

        # === ОСНОВНЫЕ МЕТРИКИ ===
        print_subsection_header("Размер баз данных", "💾", Colors.BRIGHT_CYAN)
        print_info_line(
            "Dataset БД (публичные матчи)",
            f"{result['total_dataset']:,} ({dataset_percentage:.1f}% от общего)",
            "🎮",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_GREEN
        )
        print_info_line(
            "Pro БД (турнирные матчи)",
            f"{result['total_pro']:,} ({pro_percentage:.1f}% от общего)",
            "🏆",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_PURPLE
        )
        print_info_line(
            "Всего матчей (сумма обеих БД)",
            f"{total_all:,}",
            "📊",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_BLUE
        )

        # === АНАЛИЗ ПЕРЕСЕЧЕНИЙ ===
        print_subsection_header("Анализ пересечений", "🔄", Colors.BRIGHT_ORANGE)

        # Расчет процента пересечений относительно меньшей БД
        min_db_size = min(result['total_dataset'], result['total_pro'])
        intersection_percentage = (
            (result['intersection'] / min_db_size * 100)
            if min_db_size > 0 else 0
        )

        print_info_line(
            "Дубликаты (матчи в обеих БД)",
            f"{result['intersection']:,} ({intersection_percentage:.1f}% от меньшей БД)",
            "🔄",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_RED if result['intersection'] > 0 else Colors.BRIGHT_GREEN
        )

        # === УНИКАЛЬНЫЕ МАТЧИ ===
        print_subsection_header("Уникальные матчи", "✨", Colors.BRIGHT_MINT)
        print_info_line(
            "Уникальные публичные матчи",
            f"{result['dataset_only']:,} ({dataset_unique_pct:.1f}% от Dataset БД)",
            "🎮",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_CYAN
        )
        print(f"    {Colors.DIM}↳ Матчи, которые есть только в Dataset БД и отсутствуют в Pro БД{Colors.RESET}")

        print_info_line(
            "Уникальные турнирные матчи",
            f"{result['pro_only']:,} ({pro_unique_pct:.1f}% от Pro БД)",
            "🏆",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_GOLD
        )
        print(f"    {Colors.DIM}↳ Матчи, которые есть только в Pro БД и отсутствуют в Dataset БД{Colors.RESET}")

        print_info_line(
            "Всего уникальных матчей",
            f"{total_unique:,}",
            "✅",
            Colors.BRIGHT_WHITE,
            Colors.BRIGHT_BLUE
        )
        print(f"    {Colors.DIM}↳ Сумма уникальных матчей из обеих баз без дубликатов{Colors.RESET}")

        # === СТАТУС ПРОВЕРКИ ===
        print_subsection_header("Результат проверки", "🔍", Colors.BRIGHT_YELLOW)

        if result['intersection'] == 0:
            print_status_message(
                "Пересечений не обнаружено - базы данных корректно разделены",
                "success",
                "✅"
            )
            print(f"{Colors.DIM}  Dataset БД и Pro БД содержат полностью независимые наборы матчей{Colors.RESET}")
        else:
            print_status_message(
                f"ОБНАРУЖЕНЫ НЕДОПУСТИМЫЕ ПЕРЕСЕЧЕНИЯ: {result['intersection']:,} дубликатов",
                "error",
                "❌"
            )
            print(
                f"{Colors.DIM}  Одни и те же матчи присутствуют в обеих базах данных - требуется исправление{Colors.RESET}")

            # Оценка серьёзности проблемы
            duplicate_impact = (result['intersection'] / min_db_size * 100)
            if duplicate_impact > 50:
                severity = "КРИТИЧЕСКАЯ"
                color = Colors.BRIGHT_RED
            elif duplicate_impact > 10:
                severity = "ВЫСОКАЯ"
                color = Colors.BRIGHT_ORANGE
            else:
                severity = "УМЕРЕННАЯ"
                color = Colors.BRIGHT_YELLOW

            print(f"{color}  Серьезность проблемы: {severity} ({duplicate_impact:.1f}% дублирования){Colors.RESET}")

    def _display_sample_matches(self,
                                intersection: Set[int],
                                dataset_session: Session,
                                pro_session: Session) -> None:
        """
        Отображает примеры пересекающихся матчей с проверкой консистентности.

        Для каждого примера сравнивает start_time, duration и radiant_win
        между двумя БД и выводит найденные расхождения.

        Args:
            intersection (Set[int]): Множество пересекающихся match_id
            dataset_session (Session): Активная сессия Dataset БД
            pro_session (Session): Активная сессия Pro БД
        """

        display_limit = min(SAMPLE_MATCHES_DISPLAY_LIMIT, len(intersection))
        print_subsection_header(
            f"Примеры пересекающихся матчей (топ-{display_limit})",
            "🎯",
            Colors.BRIGHT_MINT
        )

        # Берём первые N матчей (отсортированных по ID)
        sample_matches = sorted(intersection)[:display_limit]

        for i, match_id in enumerate(sample_matches, 1):
            try:
                # Получение записей матча из обеих БД
                dataset_match = dataset_session.query(
                    self.dataset_config.match_model
                ).filter(
                    self.dataset_config.match_model.match_id == match_id
                ).first()

                pro_match = pro_session.query(
                    self.pro_config.match_model
                ).filter(
                    self.pro_config.match_model.match_id == match_id
                ).first()

                if dataset_match and pro_match:
                    # Форматирование временных меток
                    dataset_date = format_timestamp(dataset_match.start_time)
                    pro_date = format_timestamp(pro_match.start_time)

                    # Форматирование длительности (MM:SS)
                    duration_ds = f"{dataset_match.duration // 60}:{dataset_match.duration % 60:02d}"
                    duration_pro = f"{pro_match.duration // 60}:{pro_match.duration % 60:02d}"

                    # Вывод информации о матче
                    print(
                        f"  {Colors.BRIGHT_YELLOW}{i:2d}.{Colors.RESET} "
                        f"Match ID {Colors.BRIGHT_CYAN}{match_id}{Colors.RESET}"
                    )
                    print(
                        f"      Dataset: {Colors.BRIGHT_WHITE}{dataset_date}{Colors.RESET}, "
                        f"длительность {duration_ds}"
                    )
                    print(
                        f"      Pro:     {Colors.BRIGHT_WHITE}{pro_date}{Colors.RESET}, "
                        f"длительность {duration_pro}"
                    )

                    # === ПРОВЕРКА КОНСИСТЕНТНОСТИ ДАННЫХ ===
                    inconsistencies = []

                    if dataset_match.start_time != pro_match.start_time:
                        inconsistencies.append("разное время начала")

                    if dataset_match.duration != pro_match.duration:
                        inconsistencies.append("разная длительность")

                    if dataset_match.radiant_win != pro_match.radiant_win:
                        inconsistencies.append("разный результат")

                    # Вывод результатов проверки
                    if inconsistencies:
                        print(
                            f"      {Colors.BRIGHT_RED}⚠️ Обнаружены различия: "
                            f"{', '.join(inconsistencies)}{Colors.RESET}"
                        )
                    else:
                        print(
                            f"      {Colors.BRIGHT_GREEN}✅ Данные идентичны{Colors.RESET}"
                        )

            except Exception as e:
                print(
                    f"  {i:2d}. Match ID {match_id} - "
                    f"ошибка получения деталей: {e}"
                )
