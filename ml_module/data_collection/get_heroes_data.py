import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data_bases.heroes.models import Hero
from config import HEROES_DATABASE_URL, OPENDOTA_HEROES_API


# Настройка БД
engine = create_engine(HEROES_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_heroes_data_from_opendota_api():
    """
    Получает данные о героях Dota 2 из OpenDota API.

    :return: Список героев в формате JSON или None при ошибке.
    """
    try:
        print("Получение данных о героях из OpenDota API...")
        response = requests.get(OPENDOTA_HEROES_API)
        response.raise_for_status()
        data = response.json()
        print(f"Получено {len(data)} героев")
        return data
    except requests.HTTPError as http_err:
        print(f"Ошибка HTTP: {http_err}")
    except Exception as err:
        print(f"Ошибка: {err}")
    return None


def clear_heroes_table(session):
    """
    Очищает таблицу героев перед загрузкой новых данных.
    """
    try:
        session.query(Hero).delete()
        session.commit()
        print("Таблица героев очищена")
    except Exception as e:
        session.rollback()
        print(f"Ошибка при очистке таблицы: {e}")
        raise


def save_heroes_to_database(heroes_data):
    """
    Сохраняет данные о героях в базу данных.

    :param heroes_data: Список героев из API.
    """
    if not heroes_data:
        print("Нет данных для сохранения.")
        return

    session = SessionLocal()

    try:
        # Очищаем таблицу перед загрузкой новых данных
        clear_heroes_table(session)

        saved_count = 0
        for hero_data in heroes_data:
            # Обрабатываем роли - берем первую роль из списка
            roles = hero_data.get('roles', [])
            primary_role = roles[0] if roles else 'Support'  # дефолтная роль

            hero = Hero(
                id=hero_data.get('id'),
                name=hero_data.get('name'),
                localized_name=hero_data.get('localized_name'),
                primary_attr=hero_data.get('primary_attr'),
                attack_type=hero_data.get('attack_type'),
                roles=primary_role
            )

            session.add(hero)
            saved_count += 1

        session.commit()
        print(f"Данные успешно сохранены в базу данных: {saved_count} героев")

    except Exception as e:
        session.rollback()
        print(f"Ошибка при сохранении в базу данных: {e}")
        raise
    finally:
        session.close()


def main():
    """
    Основная функция для загрузки данных о героях.
    """
    print("Начинаем загрузку данных о героях...")

    # Получаем данные из API
    heroes_data = get_heroes_data_from_opendota_api()

    if heroes_data:
        # Сохраняем в базу данных
        save_heroes_to_database(heroes_data)
        print("Загрузка данных о героях завершена успешно!")
    else:
        print("Не удалось получить данные о героях.")


if __name__ == "__main__":
    main()