"""
Модуль загрузки данных о лигах Dota 2 из OpenDota API.

Модуль обеспечивает полный цикл работы со справочником лиг:
получение актуальных данных из OpenDota API, валидацию, сохранение
в базу данных и отображение статистики.

Основные компоненты:
- Получение данных через OpenDota API (/api/leagues endpoint)
- Валидация обязательных полей (leagueid, name)
- Сохранение в БД с полной заменой (clear -> insert) и дедупликацией
- Статистика по уровням (tier)
"""

# Стандартные библиотеки
from typing import List, Dict, Optional

# Сторонние библиотеки
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError

# Локальные импорты
from dota_core.data_bases.leagues.models import League
from dota_core.config import LEAGUES_DATABASE_URL, OPENDOTA_LEAGUES_API
from dota_core.utils.console import (
    print_section_header,
    print_subsection_header,
    print_status_message,
    print_info_line,
    Colors
)

# Создаем подключение к БД лиг
engine = create_engine(LEAGUES_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# === КОНСТАНТЫ ОТОБРАЖЕНИЯ ===
LEAGUES_DISPLAY_COUNT = 20  # 0 - показывать все лиги, N > 0 - показывать N лиг

# Порог отсечения легаси-блока в ВЫБОРКЕ для показа.
# У OpenDota исторический казус: самые старые лиги (The International 2012/2013
# и пр.) получили id в районе 65000+, а современные нумеруются с низких значений
# и растут со временем (сейчас ~19000-20000). Чтобы в выборке не висел древний
# блок, лиги с id выше порога в листинг не попадают.
LEAGUES_DISPLAY_MAX_ID = 60000

# Читаемые названия уровней (tier) для вывода.
TIER_DISPLAY_MAP = {
    'premium': 'Премиальные (крупнейшие турниры)',
    'professional': 'Профессиональные',
    'amateur': 'Любительские',
    'excluded': 'Служебные (не настоящие турниры)',
}

# Подпись для лиг без указанного tier (в API tier == null)
TIER_UNKNOWN_DISPLAY = 'Без категории (уровень не задан)'


def get_leagues_data_from_opendota_api() -> Optional[List[Dict]]:
    """
    Получает данные о всех лигах Dota 2 из OpenDota API.

    Returns:
        Optional[List[Dict]]: Список лиг с полями [leagueid, ticket, banner,
            tier, name] или None при ошибке запроса

    Note:
        Timeout установлен 30 секунд. При сетевых ошибках возвращает None
        с выводом соответствующего статуса в консоль
    """
    print_status_message("Запрос к OpenDota API...", "info", "🌐")

    try:
        response = requests.get(OPENDOTA_LEAGUES_API, timeout=30)
        response.raise_for_status()
        data = response.json()

        print_info_line("Получено лиг", f"{len(data)}", "🏆")
        print_status_message("Данные успешно получены!", "success", "✅")

        return data

    except requests.exceptions.Timeout:
        print_status_message("Превышено время ожидания ответа API", "error", "⏱️")
        return None
    except requests.exceptions.HTTPError as http_err:
        print_status_message(f"HTTP ошибка: {http_err}", "error", "🌐")
        return None
    except requests.exceptions.RequestException as req_err:
        print_status_message(f"Ошибка запроса: {req_err}", "error", "📡")
        return None
    except ValueError as json_err:
        print_status_message(f"Ошибка парсинга JSON: {json_err}", "error", "📄")
        return None
    except Exception as err:
        print_status_message(f"Неожиданная ошибка: {err}", "error", "💥")
        return None


def clear_leagues_table(session: Session) -> None:
    """
    Очищает таблицу лиг перед загрузкой новых данных.

    Args:
        session (Session): Активная сессия SQLAlchemy

    Raises:
        SQLAlchemyError: При ошибках работы с БД (rollback выполняется автоматически)
    """
    try:
        deleted_count = session.query(League).count()
        session.query(League).delete()
        session.commit()

        print_info_line("Удалено старых записей", f"{deleted_count}", "🗑️")

    except SQLAlchemyError as e:
        session.rollback()
        print_status_message(f"Ошибка при очистке таблицы: {e}", "error", "❌")
        raise


def validate_league_data(league_data: Dict) -> bool:
    """
    Проверяет корректность данных лиги перед сохранением.

    Валидация включает:
    - Наличие leagueid (проверка через is None, чтобы пропустить id == 0)
    - Наличие непустого name

    Args:
        league_data (Dict): Данные лиги из API

    Returns:
        bool: True если данные валидны, False иначе
    """
    # leagueid обязателен (0 - валидное значение, поэтому проверяем на None)
    if league_data.get('leagueid') is None:
        return False

    # name обязателен и не должен быть пустым
    name = league_data.get('name')
    if not name or not str(name).strip():
        return False

    return True


def save_leagues_to_database(leagues_data: List[Dict]) -> bool:
    """
    Сохраняет данные о лигах в базу данных с предварительной валидацией.

    Процесс сохранения:
    1. Очистка таблицы (удаление старых записей)
    2. Валидация каждой лиги
    3. Дедупликация по leagueid (защита от дублей в данных API)
    4. Нормализация tier (пустая строка -> None)
    5. Batch insert всех валидных лиг
    6. Вывод статистики (сохранено/пропущено/дублей)

    Args:
        leagues_data (List[Dict]): Список словарей с данными лиг из API

    Returns:
        bool: True если сохранение успешно, False при ошибках БД или пустых данных
    """
    if not leagues_data:
        print_status_message("Нет данных для сохранения", "warning", "⚠️")
        return False

    session = SessionLocal()

    try:
        print_subsection_header("Сохранение в базу данных", "💾", Colors.BRIGHT_BLUE)

        clear_leagues_table(session)

        saved_count = 0
        skipped_count = 0
        duplicate_count = 0
        seen_ids = set()  # Для отсечения дублей по leagueid в рамках одной загрузки

        for league_data in leagues_data:
            if not validate_league_data(league_data):
                skipped_count += 1
                continue

            leagueid = league_data.get('leagueid')

            # Защита от дублей: первичный ключ leagueid должен быть уникален
            if leagueid in seen_ids:
                duplicate_count += 1
                continue
            seen_ids.add(leagueid)

            # Нормализация tier: пустую строку приводим к None
            tier = league_data.get('tier')
            if isinstance(tier, str):
                tier = tier.strip() or None

            league = League(
                leagueid=leagueid,
                name=str(league_data.get('name')).strip(),
                tier=tier
            )

            session.add(league)
            saved_count += 1

        session.commit()

        print_info_line("Сохранено лиг", f"{saved_count}", "✅")
        if skipped_count > 0:
            print_info_line("Пропущено записей", f"{skipped_count}", "⚠️")
        if duplicate_count > 0:
            print_info_line("Отброшено дублей", f"{duplicate_count}", "♻️")

        print_status_message("Данные успешно сохранены в базу данных!", "success", "🎉")
        return True

    except SQLAlchemyError as e:
        session.rollback()
        print_status_message(f"Ошибка при сохранении в базу данных: {e}", "error", "❌")
        return False
    except Exception as e:
        session.rollback()
        print_status_message(f"Неожиданная ошибка при сохранении: {e}", "error", "💥")
        return False
    finally:
        session.close()


def display_database_sample() -> None:
    """
    Выводит лиги из БД с детальной статистикой.

    Режимы листинга (контролируется через LEAGUES_DISPLAY_COUNT):
    - 0 или отрицательное: показывает все лиги (кроме легаси-блока)
    - N > 0: показывает N последних лиг по id (кроме легаси-блока)

    Статистика по tier при этом считается по ВСЕЙ БД, включая легаси-блок.

    Выводимая информация:
    - Детали лиг: name, leagueid, tier
    - Статистика по уровням (tier), отсортировано по популярности
    """
    session = SessionLocal()

    try:
        total_leagues = session.query(League).count()

        if total_leagues == 0:
            print_status_message("База данных пуста", "warning", "⚠️")
            return

        # Базовый запрос для отображения: отсекаем легаси-блок (id 65000+).
        # На статистику ниже это не влияет она считается по всей БД.
        listing_query = session.query(League).filter(League.leagueid <= LEAGUES_DISPLAY_MAX_ID)
        listing_total = listing_query.count()

        # Определение режима отображения на основе LEAGUES_DISPLAY_COUNT
        show_all_leagues = (LEAGUES_DISPLAY_COUNT <= 0) or (LEAGUES_DISPLAY_COUNT >= listing_total)

        if show_all_leagues:
            display_count = listing_total
            header_text = "Все лиги в БД"
            header_icon = "👑"
            status_text = f"Лиги ({display_count} в листинге из {total_leagues} в БД):"
        else:
            display_count = LEAGUES_DISPLAY_COUNT
            header_text = "Примеры данных в БД"
            header_icon = "🎭"
            status_text = f"Последние лиги по id ({display_count} из {total_leagues} в БД):"

        # Самые свежие лиги имеют наибольший id (легаси-блок уже отфильтрован)
        leagues_to_show = (listing_query
                           .order_by(League.leagueid.desc())
                           .limit(display_count)
                           .all())

        print_subsection_header(header_text, header_icon, Colors.BRIGHT_MAGENTA)
        print_status_message(status_text, "info", "📋")

        # Вывод детальной информации о каждой лиге
        for i, league in enumerate(leagues_to_show, 1):
            tier_display = TIER_DISPLAY_MAP.get(league.tier, league.tier or TIER_UNKNOWN_DISPLAY)

            print(f"  {Colors.BRIGHT_YELLOW}{i}.{Colors.RESET} "
                  f"{Colors.BRIGHT_CYAN}{Colors.BOLD}{league.name}{Colors.RESET}")
            print(f"     {Colors.DIM}ID: {league.leagueid}{Colors.RESET} "
                  f"{Colors.BRIGHT_WHITE}| Категория:{Colors.RESET} "
                  f"{Colors.BRIGHT_GREEN}{tier_display}{Colors.RESET}")

        print()
        print_status_message("Статистика по категориям (по всей БД):", "info", "📊")

        # Сбор статистики по tier (по всей БД)
        tier_counts = {}
        all_leagues = session.query(League).all()
        for league in all_leagues:
            key = league.tier if league.tier else 'unknown'
            tier_counts[key] = tier_counts.get(key, 0) + 1

        # Сортировка по популярности
        sorted_tiers = sorted(tier_counts.items(), key=lambda x: x[1], reverse=True)
        for tier, count in sorted_tiers:
            if tier == 'unknown':
                tier_name = TIER_UNKNOWN_DISPLAY
            else:
                tier_name = TIER_DISPLAY_MAP.get(tier, tier)
            print_info_line(tier_name, f"{count} лиг", "🎯")

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при получении данных: {e}", "error", "❌")
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
    finally:
        session.close()


def main() -> None:
    """
    Выполняет полный цикл загрузки данных о лигах.

    Последовательность:
    1. Получение данных из OpenDota API
    2. Сохранение в БД с валидацией и дедупликацией
    3. Вывод данных и статистики
    """
    print_section_header("ЗАГРУЗКА ДАННЫХ О ЛИГАХ DOTA 2", "🏆", color=Colors.BRIGHT_PURPLE)

    leagues_data = get_leagues_data_from_opendota_api()

    if leagues_data:
        print()

        if save_leagues_to_database(leagues_data):
            print()

            display_database_sample()

            print()
            print_status_message("Загрузка данных о лигах завершена успешно!", "success", "🎉")
        else:
            print_status_message("Не удалось сохранить данные в базу", "error", "❌")
    else:
        print_status_message("Не удалось получить данные о лигах из API", "error", "🌐")


if __name__ == "__main__":
    main()
