"""
Модели SQLAlchemy для базы данных профессиональных матчей Dota 2.

Модуль определяет ORM модели для хранения и управления данными
о профессиональных матчах и статистике игроков в базе данных PRO матчей.

Назначение БД:
- Хранение данных профессиональных турнирных матчей
- Анализ мета-игры на высоком уровне

Структура данных:
- ProMatch: основная информация о профессиональном матче (результат, команды, турнир, серия)
- ProMatchPlayer: детальная статистика каждого из 10 игроков в профессиональном матче
- Связь: один матч (ProMatch) имеет ровно 10 связанных записей игроков (ProMatchPlayer)
"""
from sqlalchemy import Column, Integer, BigInteger, Boolean, Float, String, Enum, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()  # Базовый класс для всех моделей


class ProMatch(Base):
    """
    Модель профессионального матча Dota 2 для базы данных PRO матчей.

    Представляет один завершенный профессиональный турнирный матч с полной информацией:
    идентификаторы, результат, временные параметры, информация о командах и турнире,
    данные о серии матчей, состояние построек и итоговый счет.

    Связи:
    - players (relationship): один-ко-многим с ProMatchPlayer (10 игроков на матч)

    Attributes:
        match_id (int): Уникальный 64-битный ID матча от Steam
        match_seq_num (int): Порядковый номер для инкрементальных API запросов
        radiant_win (bool): Флаг победы команды Radiant (True) или Dire (False)
        duration (int): Длительность матча в секундах (не включает pre-game)
        start_time (int): Unix timestamp начала матча (UTC)
        radiant_team_id (int): Уникальный ID команды Radiant от Valve
        radiant_name (str): Название команды Radiant
        dire_team_id (int): Уникальный ID команды Dire от Valve
        dire_name (str): Название команды Dire
        leagueid (int): ID турнира/лиги от Valve
        league_name (str): Название турнира/лиги
        series_id (int): ID серии матчей
        series_type (int): Тип серии (1=Best of 1, 2=Best of 3, 3=Best of 5)
        tower_status_radiant (int): Битовая маска состояния башен Radiant (11 бит)
        tower_status_dire (int): Битовая маска состояния башен Dire (11 бит)
        barracks_status_radiant (int): Битовая маска состояния казарм Radiant (6 бит)
        barracks_status_dire (int): Битовая маска состояния казарм Dire (6 бит)
        lobby_type (int): Тип лобби (0=Normal, 1=Practice, 7=Ranked, и т.д.)
        game_mode (int): Игровой режим (1=All Pick, 2=Captains Mode, и т.д.)
        radiant_score (int): Количество убийств (kills) команды Radiant
        dire_score (int): Количество убийств (kills) команды Dire
        players (List[ProMatchPlayer]): Список из 10 игроков данного матча
    """
    __tablename__ = 'pro_matches'

    # === ОСНОВНАЯ ИНФОРМАЦИЯ ===
    match_id = Column(BigInteger, primary_key=True)  # Уникальный ID матча от Steam
    match_seq_num = Column(BigInteger, unique=True, nullable=False)  # Порядковый номер для API
    radiant_win = Column(Boolean, nullable=False)  # Победа команды Radiant
    duration = Column(Integer, nullable=False)  # Длительность в секундах
    start_time = Column(BigInteger, nullable=False, index=True)  # Unix timestamp начала матча

    # === ИНФОРМАЦИЯ О КОМАНДАХ ===
    radiant_team_id = Column(BigInteger, nullable=True, index=True)  # ID команды Radiant
    radiant_name = Column(String(255), nullable=True)  # Название команды Radiant
    dire_team_id = Column(BigInteger, nullable=True, index=True)  # ID команды Dire
    dire_name = Column(String(255), nullable=True)  # Название команды Dire

    # === ИНФОРМАЦИЯ О ТУРНИРЕ ===
    leagueid = Column(Integer, nullable=True, index=True)  # ID лиги/турнира
    league_name = Column(String(255), nullable=True)  # Название лиги/турнира

    # === ИНФОРМАЦИЯ О СЕРИИ ===
    series_id = Column(BigInteger, nullable=True, index=True)  # ID серии матчей
    series_type = Column(Integer, nullable=True)  # Тип серии (1=bo1, 2=bo3, 3=bo5)

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
    players = relationship("ProMatchPlayer", back_populates="match")  # Игроки матча


class ProMatchPlayer(Base):
    """
    Модель игрока в профессиональном матче для базы данных PRO матчей.

    Представляет статистику одного профессионального игрока в конкретном матче.
    Хранит информацию о выбранном герое, роли, финальных предметах,
    боевой статистике, экономике и дополнительных параметрах.

    Attributes:
        id (int): Суррогатный первичный ключ (автоинкремент)
        match_id (int): Foreign key на pro_matches.match_id
        account_id (int): Steam ID игрока (32-bit)
        team_number (int): Команда игрока (0=Radiant, 1=Dire)
        hero_id (int): ID героя из справочника Dota 2 (начинается с 1)
        hero_variant (int): Вариант героя (facet, начинается с 1)
        role (str): Роль игрока - "core" или "support" (Enum на уровне БД)
        item_0 to item_5 (int): ID предметов в основных слотах (0-5)
        backpack_0 to backpack_2 (int): ID предметов в рюкзаке
        item_neutral (int): Первый нейтральный предмет
        item_neutral2 (int): Второй нейтральный предмет
        kills (int): Количество убийств героев
        deaths (int): Количество смертей
        assists (int): Количество помощи в убийствах
        kda (float): Коэффициент (kills + assists) / deaths
        last_hits (int): Количество добиваний крипов
        denies (int): Количество добиваний союзных крипов
        gold_per_min (int): Среднее золото в минуту
        xp_per_min (int): Средний опыт в минуту
        level (int): Финальный уровень героя (минимум 1)
        net_worth (int): Чистая стоимость героя (золото + стоимость предметов)
        aghanims_scepter (int): Наличие Скипетра Аганима (0/1)
        aghanims_shard (int): Наличие Осколка Аганима (0/1)
        moonshard (int): Наличие Лунных осколков (0/1)
        match (ProMatch): Обратная связь с родительским матчем
    """
    __tablename__ = 'pro_match_players'

    # === ИДЕНТИФИКАЦИЯ ===
    id = Column(BigInteger, primary_key=True, autoincrement=True)  # Суррогатный ключ
    match_id = Column(BigInteger, ForeignKey('pro_matches.match_id', ondelete='CASCADE'),
                      nullable=False, index=True)  # Ссылка на матч
    account_id = Column(BigInteger, nullable=False, index=True)  # Steam ID игрока

    # === ИГРОВЫЕ ПАРАМЕТРЫ ===
    team_number = Column(Integer, nullable=False, index=True)  # 0=Radiant, 1=Dire
    hero_id = Column(Integer, nullable=False, index=True)  # ID героя
    hero_variant = Column(Integer, nullable=False, index=True)  # Вариант героя (facet)
    role = Column(Enum("core", "support", name="pro_role_enum"), nullable=False, index=True)  # Роль

    # === ПРЕДМЕТЫ: ОСНОВНЫЕ СЛОТЫ ===
    item_0 = Column(Integer, nullable=False)  # Основной слот 0
    item_1 = Column(Integer, nullable=False)  # Основной слот 1
    item_2 = Column(Integer, nullable=False)  # Основной слот 2
    item_3 = Column(Integer, nullable=False)  # Основной слот 3
    item_4 = Column(Integer, nullable=False)  # Основной слот 4
    item_5 = Column(Integer, nullable=False)  # Основной слот 5

    # === ПРЕДМЕТЫ: РЮКЗАК ===
    backpack_0 = Column(Integer, nullable=False)  # Рюкзак слот 0
    backpack_1 = Column(Integer, nullable=False)  # Рюкзак слот 1
    backpack_2 = Column(Integer, nullable=False)  # Рюкзак слот 2

    # === ПРЕДМЕТЫ: НЕЙТРАЛЬНЫЕ ===
    item_neutral = Column(Integer, nullable=False)  # Нейтральный предмет 1
    item_neutral2 = Column(Integer, nullable=False)  # Нейтральный предмет 2

    # === БОЕВАЯ СТАТИСТИКА ===
    kills = Column(Integer, nullable=False)  # Убийства
    deaths = Column(Integer, nullable=False)  # Смерти
    assists = Column(Integer, nullable=False)  # Помощи в убийствах
    kda = Column(Float, nullable=False)  # KDA коэффициент: (K+A)/D

    # === ЭКОНОМИЧЕСКАЯ СТАТИСТИКА ===
    last_hits = Column(Integer, nullable=False)  # Добивания крипов
    denies = Column(Integer, nullable=False)  # Добивания союзных крипов
    gold_per_min = Column(Integer, nullable=False)  # Золото в минуту
    xp_per_min = Column(Integer, nullable=False)  # Опыт в минуту
    level = Column(Integer, nullable=False)  # Уровень героя
    net_worth = Column(Integer, nullable=False)  # Ценность героя

    # === ДОПОЛНИТЕЛЬНЫЕ ПРЕДМЕТЫ ===
    aghanims_scepter = Column(Integer, nullable=False)  # Скипетр Аганима
    aghanims_shard = Column(Integer, nullable=False)  # Осколок Аганима
    moonshard = Column(Integer, nullable=False)  # Лунный осколок

    # === СВЯЗИ ===
    match = relationship("ProMatch", back_populates="players")  # Обратная связь с матчем
