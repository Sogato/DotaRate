"""
Модуль загрузки данных о героях Dota 2 из OpenDota API.

Получает актуальную информацию о всех героях игры и сохраняет
в базу данных для использования в качестве справочника.
"""

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from data_bases.heroes.models import Hero
from config import HEROES_DATABASE_URL, OPENDOTA_HEROES_API
from utils.console import (
    print_section_header,
    print_subsection_header,
    print_status_message,
    print_info_line,
    print_progress_bar,
    Colors
)

# Создаем подключение к БД героев
engine = create_engine(HEROES_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_heroes_data_from_opendota_api():
    """
    Получает данные о героях Dota 2 из OpenDota API.

    Выполняет HTTP запрос к OpenDota API для получения информации
    о всех героях игры с их характеристиками.

    Returns:
        list: Список словарей с данными героев или None при ошибке

    Note:
        API возвращает полную информацию включая ID, имена, атрибуты и роли
    """
    print_status_message("Запрос к OpenDota API...", "info", "🌐")

    try:
        # Выполняем HTTP запрос к API
        response = requests.get(OPENDOTA_HEROES_API, timeout=30)
        response.raise_for_status()

        # Парсим JSON ответ
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


def clear_heroes_table(session):
    """
    Очищает таблицу героев перед загрузкой новых данных.
    Удаляет все существующие записи для обеспечения актуальности данных.

    Args:
        session: Активная сессия SQLAlchemy

    Raises:
        SQLAlchemyError: При ошибках работы с БД
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


def validate_hero_data(hero_data):
    """
    Проверяет корректность данных героя перед сохранением.

    Args:
        hero_data (dict): Данные героя из API

    Returns:
        bool: True если данные валидны, False иначе
    """
    required_fields = ['id', 'name', 'localized_name', 'primary_attr', 'attack_type']

    # Проверяем наличие обязательных полей
    for field in required_fields:
        if not hero_data.get(field):
            return False

    # Проверяем валидность значений атрибутов
    valid_attrs = ['agi', 'str', 'int', 'all']
    if hero_data.get('primary_attr') not in valid_attrs:
        return False

    valid_attack_types = ['Melee', 'Ranged']
    if hero_data.get('attack_type') not in valid_attack_types:
        return False

    return True


def save_heroes_to_database(heroes_data):
    """
    Сохраняет данные о героях в базу данных с валидацией.
    Выводит подробную статистику процесса сохранения.

    Args:
        heroes_data (list): Список словарей с данными героев из API

    Returns:
        bool: True если сохранение прошло успешно, False при ошибке
    """
    if not heroes_data:
        print_status_message("Нет данных для сохранения", "warning", "⚠️")
        return False

    session = SessionLocal()

    try:
        print_subsection_header("Сохранение в базу данных", "💾", Colors.BRIGHT_BLUE)

        # Очищаем таблицу перед загрузкой новых данных
        clear_heroes_table(session)

        saved_count = 0
        skipped_count = 0

        # Обрабатываем каждого героя
        for hero_data in heroes_data:
            # Валидируем данные героя
            if not validate_hero_data(hero_data):
                print_status_message(f"Пропущен герой с некорректными данными: {hero_data.get('name', 'Unknown')}",
                                     "warning", "⚠️")
                skipped_count += 1
                continue

            # Обрабатываем роли - сохраняем все роли через запятую
            roles = hero_data.get('roles', [])
            roles_str = ','.join(roles) if roles else 'Support'

            # Создаем объект Hero
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

        # Коммитим все изменения
        session.commit()

        # Выводим статистику сохранения
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


def display_database_sample():
    """
    Выводит примеры данных из базы данных для демонстрации результата.

    Показывает несколько случайных записей героев с их характеристиками
    для проверки корректности сохраненных данных.
    """
    print_subsection_header("Примеры данных в БД", "🎭", Colors.BRIGHT_MAGENTA)

    session = SessionLocal()

    try:
        # Получаем общую статистику
        total_heroes = session.query(Hero).count()

        if total_heroes == 0:
            print_status_message("База данных пуста", "warning", "⚠️")
            return

        # Получаем несколько разнообразных примеров
        sample_size = min(8, total_heroes)

        # Получаем героев разных типов для демонстрации разнообразия
        sample_heroes = []

        # Пытаемся получить по одному герою каждого основного атрибута
        for attr in ['agi', 'str', 'int']:
            hero = session.query(Hero).filter(Hero.primary_attr == attr).first()
            if hero:
                sample_heroes.append(hero)

        # Добавляем универсального героя если есть
        universal_hero = session.query(Hero).filter(Hero.primary_attr == 'all').first()
        if universal_hero:
            sample_heroes.append(universal_hero)

        # Дополняем до нужного количества случайными героями
        remaining_count = sample_size - len(sample_heroes)
        if remaining_count > 0:
            existing_ids = [h.id for h in sample_heroes]
            additional_heroes = session.query(Hero).filter(~Hero.id.in_(existing_ids)).limit(remaining_count).all()
            sample_heroes.extend(additional_heroes)

        print_status_message(f"Примеры героев ({len(sample_heroes)} записей):", "info", "📋")

        # Выводим детальную информацию о каждом герое
        for i, hero in enumerate(sample_heroes, 1):
            # Форматируем атрибут в читаемый вид
            attr_display = {
                'agi': 'Ловкость (AGI)',
                'str': 'Сила (STR)',
                'int': 'Интеллект (INT)',
                'all': 'Универсальный'
            }.get(hero.primary_attr, hero.primary_attr)

            # Форматируем тип атаки
            attack_display = {
                'Melee': 'Ближний бой',
                'Ranged': 'Дальний бой'
            }.get(hero.attack_type, hero.attack_type)

            # Форматируем роли
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

        # Статистика по атрибутам
        print()
        print_status_message("Статистика по основным атрибутам:", "info", "📊")

        attr_stats = {}
        for attr in ['agi', 'str', 'int', 'all']:
            count = session.query(Hero).filter(Hero.primary_attr == attr).count()
            if count > 0:
                attr_stats[attr] = count

        for attr, count in attr_stats.items():
            attr_name = {'agi': 'Ловкость', 'str': 'Сила', 'int': 'Интеллект', 'all': 'Универсальный'}[attr]
            print_info_line(attr_name, f"{count} героев", "🎯")

        # Статистика по типам атаки
        print()
        print_status_message("Статистика по типам атаки:", "info", "⚔️")

        melee_count = session.query(Hero).filter(Hero.attack_type == 'Melee').count()
        ranged_count = session.query(Hero).filter(Hero.attack_type == 'Ranged').count()

        print_info_line("Ближний бой", f"{melee_count} героев", "🗡️")
        print_info_line("Дальний бой", f"{ranged_count} героев", "🏹")

        # Статистика по самым популярным ролям
        print()
        print_status_message("Топ-5 самых популярных ролей:", "info", "🏆")

        # Собираем статистику по ролям
        role_counts = {}
        all_heroes = session.query(Hero).all()
        for hero in all_heroes:
            if hero.roles:
                roles_list = hero.roles.split(',')
                for role in roles_list:
                    role = role.strip()
                    role_counts[role] = role_counts.get(role, 0) + 1

        # Сортируем и выводим топ-5
        sorted_roles = sorted(role_counts.items(), key=lambda x: x[1], reverse=True)
        for role, count in sorted_roles[:5]:
            print_info_line(role, f"{count} героев", "🎭")

    except SQLAlchemyError as e:
        print_status_message(f"Ошибка при получении данных: {e}", "error", "❌")
    except Exception as e:
        print_status_message(f"Неожиданная ошибка: {e}", "error", "💥")
    finally:
        session.close()


def main():
    """
    Основная функция для полного цикла загрузки данных о героях.
    """
    print_section_header("ЗАГРУЗКА ДАННЫХ О ГЕРОЯХ DOTA 2", "⚔️", color=Colors.BRIGHT_PURPLE)

    # Получаем данные из API
    heroes_data = get_heroes_data_from_opendota_api()

    if heroes_data:
        print()  # Добавляем отступ

        # Сохраняем в базу данных
        if save_heroes_to_database(heroes_data):
            print()  # Добавляем отступ

            # Показываем примеры данных
            display_database_sample()

            print()
            print_status_message("Загрузка данных о героях завершена успешно!", "success", "🎉")
        else:
            print_status_message("Не удалось сохранить данные в базу", "error", "❌")
    else:
        print_status_message("Не удалось получить данные о героях из API", "error", "🌐")


if __name__ == "__main__":
    main()
