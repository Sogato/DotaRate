"""
Модуль загрузки данных о героях Dota 2 из OpenDota API.

Модуль обеспечивает полный цикл работы со справочником героев:
получение актуальных данных из OpenDota API, валидацию, сохранение
в базу данных и отображение статистики.

Основные компоненты:
- Получение данных через OpenDota API (/api/heroes endpoint)
- Валидация обязательных полей (id, name, атрибуты, тип атаки)
- Сохранение в БД с полной заменой (clear → insert)
- Статистика по атрибутам, типам атаки и ролям
"""

# Стандартные библиотеки
from typing import List, Dict, Optional

# Сторонние библиотеки
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError

# Локальные импорты
from data_bases.heroes.models import Hero
from config import HEROES_DATABASE_URL, OPENDOTA_HEROES_API
from utils.console import (
    print_section_header,
    print_subsection_header,
    print_status_message,
    print_info_line,
    Colors
)

# === КОНСТАНТЫ ОТОБРАЖЕНИЯ ===
HEROES_DISPLAY_COUNT = 0  # 0 - показывать всех героев, N > 0 - показывать N героев

# Создаем подключение к БД героев
engine = create_engine(HEROES_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_heroes_data_from_opendota_api() -> Optional[List[Dict]]:
    """
    Получает данные о всех героях Dota 2 из OpenDota API.

    Returns:
        Optional[List[Dict]]: Список героев с полями [id, name, localized_name,
            primary_attr, attack_type, roles] или None при ошибке запроса

    Note:
        Timeout установлен 30 секунд. При сетевых ошибках возвращает None
        с выводом соответствующего статуса в консоль
    """
    print_status_message("Запрос к OpenDota API...", "info", "🌐")

    try:
        response = requests.get(OPENDOTA_HEROES_API, timeout=30)
        response.raise_for_status()
        data = response.json()

        print_info_line("Получено героев", f"{len(data)}", "⚔️")
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


def clear_heroes_table(session: Session) -> None:
    """
    Очищает таблицу героев перед загрузкой новых данных.

    Args:
        session (Session): Активная сессия SQLAlchemy

    Raises:
        SQLAlchemyError: При ошибках работы с БД (rollback выполняется автоматически)
    """
    try:
        deleted_count = session.query(Hero).count()
        session.query(Hero).delete()
        session.commit()

        print_info_line("Удалено старых записей", f"{deleted_count}", "🗑️")

    except SQLAlchemyError as e:
        session.rollback()
        print_status_message(f"Ошибка при очистке таблицы: {e}", "error", "❌")
        raise


def validate_hero_data(hero_data: Dict) -> bool:
    """
    Проверяет корректность данных героя перед сохранением.

    Валидация включает:
    - Наличие обязательных полей: id, name, localized_name, primary_attr, attack_type
    - Валидность primary_attr: ['agi', 'str', 'int', 'all']
    - Валидность attack_type: ['Melee', 'Ranged']

    Args:
        hero_data (Dict): Данные героя из API

    Returns:
        bool: True если данные валидны, False иначе
    """
    required_fields = ['id', 'name', 'localized_name', 'primary_attr', 'attack_type']

    # Проверка наличия обязательных полей
    for field in required_fields:
        if not hero_data.get(field):
            return False

    # Валидация значений атрибутов: agi/str/int/all
    valid_attrs = ['agi', 'str', 'int', 'all']
    if hero_data.get('primary_attr') not in valid_attrs:
        return False

    # Валидация типа атаки: Melee/Ranged
    valid_attack_types = ['Melee', 'Ranged']
    if hero_data.get('attack_type') not in valid_attack_types:
        return False

    return True


def save_heroes_to_database(heroes_data: List[Dict]) -> bool:
    """
    Сохраняет данные о героях в базу данных с предварительной валидацией.

    Процесс сохранения:
    1. Очистка таблицы (удаление старых записей)
    2. Валидация каждого героя
    3. Обработка ролей (конвертация списка в строку через запятую)
    4. Batch insert всех валидных героев
    5. Вывод статистики (сохранено/пропущено)

    Args:
        heroes_data (List[Dict]): Список словарей с данными героев из API

    Returns:
        bool: True если сохранение успешно, False при ошибках БД или пустых данных
    """
    if not heroes_data:
        print_status_message("Нет данных для сохранения", "warning", "⚠️")
        return False

    session = SessionLocal()

    try:
        print_subsection_header("Сохранение в базу данных", "💾", Colors.BRIGHT_BLUE)

        clear_heroes_table(session)

        saved_count = 0
        skipped_count = 0

        for hero_data in heroes_data:
            if not validate_hero_data(hero_data):
                print_status_message(f"Пропущен герой с некорректными данными: {hero_data.get('name', 'Unknown')}",
                                     "warning", "⚠️")
                skipped_count += 1
                continue

            # Обработка ролей - конвертация списка в строку через запятую
            roles = hero_data.get('roles', [])
            roles_str = ','.join(roles) if roles else 'Support'

            hero = Hero(
                id=hero_data.get('id'),
                name=hero_data.get('name'),
                localized_name=hero_data.get('localized_name'),
                primary_attr=hero_data.get('primary_attr'),
                attack_type=hero_data.get('attack_type'),
                roles=roles_str
            )

            session.add(hero)
            saved_count += 1

        session.commit()

        print_info_line("Сохранено героев", f"{saved_count}", "✅")
        if skipped_count > 0:
            print_info_line("Пропущено записей", f"{skipped_count}", "⚠️")

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
    Выводит героев из БД с детальной статистикой.

    Режимы отображения (контролируется через HEROES_DISPLAY_COUNT):
    - 0 или отрицательное: показывает всех героев (отсортированных по ID)
    - N > 0: показывает N героев (разнообразная выборка по атрибутам)

    Выводимая информация:
    - Детали героев: localized_name, ID, системное имя, атрибут, тип атаки, роли
    - Статистика по основным атрибутам (AGI/STR/INT/Universal)
    - Статистика по типам атаки (Melee/Ranged)
    - Статистика по ролям (отсортировано по популярности)
    """
    session = SessionLocal()

    try:
        total_heroes = session.query(Hero).count()

        if total_heroes == 0:
            print_status_message("База данных пуста", "warning", "⚠️")
            return

        # Определение режима отображения на основе HEROES_DISPLAY_COUNT
        show_all_heroes = (HEROES_DISPLAY_COUNT <= 0) or (HEROES_DISPLAY_COUNT >= total_heroes)

        if show_all_heroes:
            display_count = total_heroes
            header_text = "Все герои в БД"
            header_icon = "👑"
            status_text = f"Все герои ({display_count} записей):"
        else:
            display_count = HEROES_DISPLAY_COUNT
            header_text = "Примеры данных в БД"
            header_icon = "🎭"
            status_text = f"Примеры героев ({display_count} из {total_heroes} записей):"

        print_subsection_header(header_text, header_icon, Colors.BRIGHT_MAGENTA)

        # Получение героев для отображения
        if show_all_heroes:
            heroes_to_show = session.query(Hero).order_by(Hero.id).all()
        else:
            # Формирование разнообразной выборки для демонстрации
            heroes_to_show = []

            # Добавляем по одному герою каждого основного атрибута
            for attr in ['agi', 'str', 'int']:
                hero = session.query(Hero).filter(Hero.primary_attr == attr).first()
                if hero:
                    heroes_to_show.append(hero)

            # Добавляем универсального героя если есть
            universal_hero = session.query(Hero).filter(Hero.primary_attr == 'all').first()
            if universal_hero:
                heroes_to_show.append(universal_hero)

            # Дополняем до нужного количества
            remaining_count = display_count - len(heroes_to_show)
            if remaining_count > 0:
                existing_ids = [h.id for h in heroes_to_show]
                additional_heroes = session.query(Hero).filter(~Hero.id.in_(existing_ids)).limit(remaining_count).all()
                heroes_to_show.extend(additional_heroes)

        print_status_message(status_text, "info", "📋")

        # Вывод детальной информации о каждом герое
        for i, hero in enumerate(heroes_to_show, 1):
            # Форматирование атрибута в читаемый вид
            attr_display = {
                'agi': 'Ловкость (AGI)',
                'str': 'Сила (STR)',
                'int': 'Интеллект (INT)',
                'all': 'Универсальный'
            }.get(hero.primary_attr, hero.primary_attr)

            attack_display = {
                'Melee': 'Ближний бой',
                'Ranged': 'Дальний бой'
            }.get(hero.attack_type, hero.attack_type)

            roles_list = hero.roles.split(',') if hero.roles else []
            roles_display = ', '.join(roles_list)

            print(f"  {Colors.BRIGHT_YELLOW}{i}.{Colors.RESET} "
                  f"{Colors.BRIGHT_CYAN}{Colors.BOLD}{hero.localized_name}{Colors.RESET}")
            print(f"     {Colors.DIM}ID: {hero.id} | Системное имя: {hero.name}{Colors.RESET}")
            print(
                f"     {Colors.BRIGHT_WHITE}Основной атрибут:{Colors.RESET} {Colors.BRIGHT_GREEN}{attr_display}{Colors.RESET}")
            print(
                f"     {Colors.BRIGHT_WHITE}Тип атаки:{Colors.RESET} {Colors.BRIGHT_ORANGE}{attack_display}{Colors.RESET}")
            print(
                f"     {Colors.BRIGHT_WHITE}Роли:{Colors.RESET} {Colors.BRIGHT_PURPLE}{roles_display}{Colors.RESET}")

        print()
        print_status_message("Статистика по основным атрибутам:", "info", "📊")

        # Сбор статистики по атрибутам
        attr_stats = {}
        for attr in ['agi', 'str', 'int', 'all']:
            count = session.query(Hero).filter(Hero.primary_attr == attr).count()
            if count > 0:
                attr_stats[attr] = count

        for attr, count in attr_stats.items():
            attr_name = {'agi': 'Ловкость', 'str': 'Сила', 'int': 'Интеллект', 'all': 'Универсальный'}[attr]
            print_info_line(attr_name, f"{count} героев", "🎯")

        print()
        print_status_message("Статистика по типам атаки:", "info", "⚔️")

        melee_count = session.query(Hero).filter(Hero.attack_type == 'Melee').count()
        ranged_count = session.query(Hero).filter(Hero.attack_type == 'Ranged').count()

        print_info_line("Ближний бой", f"{melee_count} героев", "🗡️")
        print_info_line("Дальний бой", f"{ranged_count} героев", "🏹")

        print()
        print_status_message("Статистика по ролям:", "info", "🏆")

        # Сбор статистики по ролям из всех героев
        role_counts = {}
        all_heroes = session.query(Hero).all()
        for hero in all_heroes:
            if hero.roles:
                roles_list = hero.roles.split(',')
                for role in roles_list:
                    role = role.strip()
                    role_counts[role] = role_counts.get(role, 0) + 1

        # Сортировка по популярности
        sorted_roles = sorted(role_counts.items(), key=lambda x: x[1], reverse=True)
        for role, count in sorted_roles:
            print_info_line(role, f"{count} героев", "🎭")

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при получении данных: {e}", "error", "❌")
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
    finally:
        session.close()


def main() -> None:
    """
    Выполняет полный цикл загрузки данных о героях.

    Последовательность:
    1. Получение данных из OpenDota API
    2. Сохранение в БД с валидацией
    3. Вывод данных и статистики
    """
    print_section_header("ЗАГРУЗКА ДАННЫХ О ГЕРОЯХ DOTA 2", "⚔️", color=Colors.BRIGHT_PURPLE)

    heroes_data = get_heroes_data_from_opendota_api()

    if heroes_data:
        print()

        if save_heroes_to_database(heroes_data):
            print()

            display_database_sample()

            print()
            print_status_message("Загрузка данных о героях завершена успешно!", "success", "🎉")
        else:
            print_status_message("Не удалось сохранить данные в базу", "error", "❌")
    else:
        print_status_message("Не удалось получить данные о героях из API", "error", "🌐")


if __name__ == "__main__":
    main()
