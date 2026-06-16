"""
Модель SQLAlchemy для базы данных героев Dota 2.

Модуль определяет ORM модель для хранения справочной информации
о всех героях игры в базе данных heroes.

Назначение БД heroes:
- Хранение статических данных о героях (справочник)

Структура данных:
- Hero: справочная таблица с информацией о герое (имена, атрибуты, роли)
"""

from sqlalchemy import Column, Integer, String, Enum
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()  # Базовый класс для всех моделей


class Hero(Base):
    """
    Модель героя Dota 2 для базы данных heroes.

    Представляет справочную информацию о герое: уникальные идентификаторы,
    названия, основной атрибут, тип атаки и игровые роли.
    Используется как статический справочник для других компонентов системы.

    Attributes:
        id (int): Уникальный числовой ID героя от Valve (начинается с 1)
        name (str): Внутреннее системное имя героя (например, npc_dota_hero_antimage)
        localized_name (str): Локализованное отображаемое имя (например, Anti-Mage)
        primary_attr (str): Основной атрибут героя - "agi", "str", "int" или "all"
        attack_type (str): Тип атаки героя - "Melee" (ближний) или "Ranged" (дальний)
        roles (str): Игровые роли героя, разделенные запятыми (например, "Carry,Escape,Nuker")
    """
    __tablename__ = 'heroes'

    # === ИДЕНТИФИКАЦИЯ ===
    id = Column(Integer, primary_key=True)  # Уникальный ID героя от Valve
    name = Column(String(255), nullable=False, unique=True)  # Системное имя
    localized_name = Column(String(255), nullable=False)  # Отображаемое имя

    # === ОСНОВНОЙ АТРИБУТ ===
    primary_attr = Column(
        Enum("agi", "str", "int", "all", name="primary_attr_enum"),
        nullable=False
    )

    # === ТИП АТАКИ ===
    attack_type = Column(
        Enum("Melee", "Ranged", name="attack_type_enum"),
        nullable=False
    )

    # === ИГРОВЫЕ РОЛИ ===
    roles = Column(String(500), nullable=False)  # Множественные роли через запятую
