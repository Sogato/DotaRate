from sqlalchemy import create_engine, text
from config import HEROES_DATABASE_URL
from models import Hero

engine = create_engine(HEROES_DATABASE_URL)


def clear_database():
    hero_table = Hero.__tablename__

    with engine.begin() as conn:
        conn.execute(text(f'TRUNCATE TABLE "{hero_table}" RESTART IDENTITY CASCADE'))
    print("Данные о героях успешно очищены.")


def check_if_empty():
    hero_table = Hero.__tablename__

    with engine.connect() as conn:
        hero_count = conn.execute(text(f'SELECT COUNT(*) FROM "{hero_table}"')).scalar()

    if hero_count == 0:
        print("База данных героев пуста.")
        return True
    else:
        print(f"База данных героев не пуста: {hero_count} героев.")
        return False


if __name__ == "__main__":
    clear_database()
    check_if_empty()