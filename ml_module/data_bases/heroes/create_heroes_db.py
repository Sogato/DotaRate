from sqlalchemy import create_engine
from models import Base
from config import HEROES_DATABASE_URL

# Создаём engine для БД героев
engine = create_engine(HEROES_DATABASE_URL)

# Создаём таблицы (если не существуют)
Base.metadata.create_all(engine)

print("База данных героев и таблицы созданы успешно.")