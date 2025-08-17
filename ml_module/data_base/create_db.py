from sqlalchemy import create_engine
from models import Base
from config import DATABASE_URL

# Создаём engine
engine = create_engine(DATABASE_URL)

# Создаём таблицы (если не существуют)
Base.metadata.create_all(engine)

print("База данных и таблицы созданы успешно.")
