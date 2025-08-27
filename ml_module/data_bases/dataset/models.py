"""
Модели SQLAlchemy для базы данных матчей Dota 2.

Определяет структуру таблиц для хранения данных о матчах и игроках.
Включает связи между таблицами и индексы для оптимизации запросов.
"""

from sqlalchemy import Column, Integer, BigInteger, Boolean, Float, Enum, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

# Базовый класс для всех моделей
Base = declarative_base()


class Match(Base):
    """
    Модель матча Dota 2.

    Хранит основную информацию о матче: результат, длительность, состояние построек,
    игровые параметры и счет команд. Связана с игроками через отношение один-ко-многим.
    """
    __tablename__ = 'matches'

    # === ОСНОВНАЯ ИНФОРМАЦИЯ ===
    match_id = Column(BigInteger, primary_key=True)  # Уникальный ID матча от Steam
    match_seq_num = Column(BigInteger, unique=True, nullable=False)  # Порядковый номер для API
    radiant_win = Column(Boolean, nullable=False)  # Победа команды Radiant
    duration = Column(Integer, nullable=False)  # Длительность в секундах
    start_time = Column(BigInteger, nullable=False, index=True)  # Unix timestamp начала

    # === СОСТОЯНИЕ ПОСТРОЕК ===
    tower_status_radiant = Column(Integer, nullable=False)  # Битовая маска башен Radiant
    tower_status_dire = Column(Integer, nullable=False)  # Битовая маска башен Dire
    barracks_status_radiant = Column(Integer, nullable=False)  # Битовая маска казарм Radiant
    barracks_status_dire = Column(Integer, nullable=False)  # Битовая маска казарм Dire

    # === ИГРОВЫЕ ПАРАМЕТРЫ ===
    lobby_type = Column(Integer, nullable=False)  # Тип лобби
    game_mode = Column(Integer, nullable=False)  # Игровой режим

    # === СЧЕТ КОМАНД ===
    radiant_score = Column(Integer, nullable=False)  # Убийства команды Radiant
    dire_score = Column(Integer, nullable=False)  # Убийства команды Dire

    # === СВЯЗИ ===
    players = relationship("MatchPlayer", back_populates="match")  # Игроки матча


class MatchPlayer(Base):
    """
    Модель игрока в матче.

    Хранит детальную статистику игрока в конкретном матче: герой, роль,
    предметы, боевую и экономическую статистику. Связана с матчем через внешний ключ.
    """
    __tablename__ = 'match_players'

    # === ИДЕНТИФИКАЦИЯ ===
    id = Column(BigInteger, primary_key=True, autoincrement=True)  # Суррогатный ключ
    match_id = Column(BigInteger, ForeignKey('matches.match_id', ondelete='CASCADE'),
                      nullable=False, index=True)  # Ссылка на матч
    account_id = Column(BigInteger, nullable=False, index=True)  # Steam ID игрока

    # === ИГРОВЫЕ ПАРАМЕТРЫ ===
    team_number = Column(Integer, nullable=False, index=True)  # 0=Radiant, 1=Dire
    hero_id = Column(Integer, nullable=False, index=True)  # ID героя
    hero_variant = Column(Integer, nullable=False, index=True)  # Вариант героя
    role = Column(Enum("core", "support", name="role_enum"), nullable=False, index=True)  # Роль

    # === ПРЕДМЕТЫ ===
    # Основные слоты предметов (0-5)
    item_0 = Column(Integer, nullable=False)
    item_1 = Column(Integer, nullable=False)
    item_2 = Column(Integer, nullable=False)
    item_3 = Column(Integer, nullable=False)
    item_4 = Column(Integer, nullable=False)
    item_5 = Column(Integer, nullable=False)

    # Рюкзак (backpack)
    backpack_0 = Column(Integer, nullable=False)
    backpack_1 = Column(Integer, nullable=False)
    backpack_2 = Column(Integer, nullable=False)

    # Нейтральные предметы
    item_neutral = Column(Integer, nullable=False)
    item_neutral2 = Column(Integer, nullable=False)

    # === БОЕВАЯ СТАТИСТИКА ===
    kills = Column(Integer, nullable=False)  # Убийства
    deaths = Column(Integer, nullable=False)  # Смерти
    assists = Column(Integer, nullable=False)  # Помощи в убийствах
    kda = Column(Float, nullable=False)  # KDA коэффициент

    # === ЭКОНОМИЧЕСКАЯ СТАТИСТИКА ===
    last_hits = Column(Integer, nullable=False)  # Добивания крипов
    denies = Column(Integer, nullable=False)  # Блокирования добиваний
    gold_per_min = Column(Integer, nullable=False)  # Золото в минуту
    xp_per_min = Column(Integer, nullable=False)  # Опыт в минуту
    level = Column(Integer, nullable=False)  # Уровень героя
    net_worth = Column(Integer, nullable=False)  # Чистая стоимость

    # === ДОПОЛНИТЕЛЬНЫЕ ПРЕДМЕТЫ ===
    aghanims_scepter = Column(Integer, nullable=False)  # Скипетр Аганима
    aghanims_shard = Column(Integer, nullable=False)  # Осколок Аганима
    moonshard = Column(Integer, nullable=False)  # Лунный осколок

    # === СВЯЗИ ===
    match = relationship("Match", back_populates="players")  # Обратная связь с матчем
