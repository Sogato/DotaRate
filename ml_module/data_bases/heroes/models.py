"""
Модель SQLAlchemy для базы данных героев Dota 2.

Определяет структуру таблицы heroes для хранения справочной информации
о всех героях игры: имена, атрибуты, тип атаки и роли.
"""

from sqlalchemy import Column, Integer, String, Enum
from sqlalchemy.ext.declarative import declarative_base

# Базовый класс для модели героев
Base = declarative_base()


class Hero(Base):
    """
    Модель героя Dota 2.

    Справочная таблица, содержащая статическую информацию о всех героях:
    идентификаторы, названия, основные характеристики и игровые роли (указанные в самой игре).
    Используется как справочник.
    """
    __tablename__ = 'heroes'

    # === ИДЕНТИФИКАЦИЯ ===
    id = Column(Integer, primary_key=True)  # Уникальный ID героя от Valve
    name = Column(String(255), nullable=False, unique=True)  # Внутреннее имя (npc_dota_hero_antimage)
    localized_name = Column(String(255), nullable=False)  # Отображаемое имя (Anti-Mage)

    # === ОСНОВНОЙ АТРИБУТ ===
    # Определяет, какая характеристика является основной для героя
    primary_attr = Column(
        Enum("agi", "str", "int", "all", name="primary_attr_enum"),
        nullable=False
    )  # agi=Ловкость, str=Сила, int=Интеллект, all=Универсальный

    # === ТИП АТАКИ ===
    # Определяет дальность атаки героя
    attack_type = Column(
        Enum("Melee", "Ranged", name="attack_type_enum"),
        nullable=False
    )  # Ближний или дальний бой

    # === ИГРОВЫЕ РОЛИ ===
    # Роли героя в команде (множественные роли)
    roles = Column(String(500), nullable=False)  # Роли разделенные запятыми: "Carry,Escape,Nuker"
