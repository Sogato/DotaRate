"""
Модель SQLAlchemy для базы данных лиг Dota 2.

Модуль определяет ORM модель для хранения справочной информации
о турнирных лигах в базе данных leagues.

Назначение БД leagues:
- Хранение статических данных о лигах (справочник)

Структура данных:
- League: справочная таблица с информацией о лиге (id, название, уровень)
"""

from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()  # Базовый класс для всех моделей


class League(Base):
    """
    Модель лиги Dota 2 для базы данных leagues.

    Представляет справочную информацию о турнирной лиге: уникальный
    идентификатор, название и уровень (tier). Используется как статический
    справочник для других компонентов системы.

    Attributes:
        leagueid (int): Уникальный числовой ID лиги от OpenDota/Valve
        name (str): Название лиги (например, "The International 2023")
        tier (str | None): Уровень лиги (например, "professional", "amateur")
    """
    __tablename__ = 'leagues'

    # === ИДЕНТИФИКАЦИЯ ===
    leagueid = Column(Integer, primary_key=True)  # Уникальный ID лиги от OpenDota

    # === ОСНОВНЫЕ ДАННЫЕ ===
    name = Column(String(500), nullable=False)    # Название лиги
    tier = Column(String(50), nullable=True)      # Уровень лиги
