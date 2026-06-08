"""
Конфигурационный файл для системы сбора и анализа данных матчей Dota 2.

Содержит все настройки для:
 - Подключения к базам данных и внешним API
 - Параметров сбора и фильтрации матчей
 - Алгоритмов определения ролей и детекции руинеров
 - Настроек логирования и отладки
"""

import os
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
# .env должен находиться в корневой директории проекта
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))


# ═══════════════════════════════════════════════════════════════════════════
# БАЗЫ ДАННЫХ
# ═══════════════════════════════════════════════════════════════════════════

# Параметры подключения
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')

# Названия баз данных
DATASET_DB_NAME = os.getenv('DATASET_DB_NAME')  # Основная БД с матчами
HEROES_DB_NAME = os.getenv('HEROES_DB_NAME')    # Справочник героев
PRO_DB_NAME = os.getenv('PRO_DB_NAME')          # БД профессиональных матчей

# Строки подключения (PostgreSQL URI)
DATASET_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DATASET_DB_NAME}"
HEROES_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{HEROES_DB_NAME}"
PRO_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{PRO_DB_NAME}"


# ═══════════════════════════════════════════════════════════════════════════
# ВНЕШНИЕ API
# ═══════════════════════════════════════════════════════════════════════════

# Steam API
STEAM_API_KEY = os.getenv('STEAM_API_KEY')
STEAM_API_MATCH_HISTORY_URL = "https://api.steampowered.com/IDOTA2Match_570/GetMatchHistoryBySequenceNum/V001/"

# OpenDota API
OPENDOTA_HEROES_API = "https://api.opendota.com/api/heroes"
OPENDOTA_PRO_MATCHES_URL = "https://api.opendota.com/api/proMatches"
OPENDOTA_MATCH_DETAILS_URL = "https://api.opendota.com/api/matches"


# ═══════════════════════════════════════════════════════════════════════════
# ⚠️ ОБНОВЛЯЕМЫЕ ПАРАМЕТРЫ (изменяются при обновлении Dota 2)
# ═══════════════════════════════════════════════════════════════════════════

# Версия патча Dota 2
DOTA_VERSION = "741d"   # Используется в названиях файлов и моделей

# Начальный номер последовательности матчей для сбора данных
STARTING_MATCH_SEQUENCE_NUMBER = 7429675065   # Обновляется при переходе на новый патч для сбора актуальных матчей

# Unix timestamp даты выхода патча для анализа данных
BURST_TIME_TIMESTAMP = 1780714995   # Sat Jun 06 2026 03:03:15 GMT+0000


# ═══════════════════════════════════════════════════════════════════════════
# КОНСТАНТЫ ИГРОВОЙ ЛОГИКИ DOTA 2
# ═══════════════════════════════════════════════════════════════════════════

# Базовые игровые константы
TEAM_SIZE = 5            # Количество игроков в одной команде
RADIANT_INDEX = 0        # Индекс команды Radiant
DIRE_INDEX = 1           # Индекс команды Dire

# Битовые маски для игровых структур
TOWERS_BITMASK = 0x7FF    # 11 бит для 11 башен (0b11111111111)
BARRACKS_BITMASK = 0x3F   # 6 бит для 6 казарм (0b111111)

# Количество игровых структур на одну сторону
TOWERS_COUNT = 11          # 3 линии × 3 яруса + 2 у Ancient
BARRACKS_COUNT = 6         # 3 линии × 2 типа (Melee + Ranged)

# Названия казарм: 3 линии × 2 типа (Melee, Ranged)
BARRACKS_NAMES = [
    "Top Melee", "Top Ranged",
    "Middle Melee", "Middle Ranged",
    "Bottom Melee", "Bottom Ranged",
]

# Названия башен, сгруппированные по ярусам (Tier 1–3 + Ancient)
TOWER_NAMES = [
    "Tier 1 Top", "Tier 1 Middle", "Tier 1 Bottom",
    "Tier 2 Top", "Tier 2 Middle", "Tier 2 Bottom",
    "Tier 3 Top", "Tier 3 Middle", "Tier 3 Bottom",
    "Ancient Top", "Ancient Bottom",
]

# Маппинг индекса башен из TOWER_NAMES → битовая позиция в маске.
# API хранит биты по линиям (Top: 0,1,2; Mid: 3,4,5; Bot: 6,7,8), поэтому порядок переставлен.
TOWER_BIT_POSITIONS = [0, 3, 6, 1, 4, 7, 2, 5, 8, 9, 10]

# Битовые маски для player_slot
PLAYER_SLOT_TEAM_BITMASK = 0x80       # 1 бит для команды (0b10000000)
PLAYER_SLOT_POSITION_BITMASK = 0x07   # 3 бита для позиции в команде (0b00000111)

# Существующие типы турнирных серий
# Ключи используются для валидации, значения — для отображения
SERIES_TYPES = {
    0: "Best of 1 (BO1)",    # Bo1
    1: "Best of 3 (BO3)",    # Bo3
    2: "Best of 5 (BO5)",    # Bo5
}

# Полный справочник названий игровых режимов
GAME_MODES = {
    0:  "Unknown",                 # Неизвестный режим
    1:  "All Pick",                # Свободный выбор героев (Неактуально)
    2:  "Captains Mode",           # Капитанский режим (драфт капитаном)
    3:  "Random Draft",            # Каждый игрок выбирает из ограниченного пула героев
    4:  "Single Draft",            # Каждому игроку доступны 3 случайных героя
    5:  "All Random",              # Полностью случайный выбор героев
    6:  "Intro",                   # Вступительный режим
    7:  "Diretide",                # Ивент Diretide
    8:  "Reverse Captains Mode",   # Обратный капитанский режим
    9:  "Greeviling",              # Ивент Greeviling
    10: "Tutorial",                # Обучение
    11: "Mid Only",                # Игра только на центральной линии
    12: "Least Played",            # Наименее играемые герои
    13: "Limited Heroes",          # Ограниченный набор героев
    14: "Compendium Matchmaking",  # Поиск матчей через компендиум
    15: "Custom",                  # Кастомная игра
    16: "Captains Draft",          # Драфт капитанов из ограниченного пула
    17: "Balanced Draft",          # Сбалансированный драфт
    18: "Ability Draft",           # Драфт способностей героев
    19: "Event",                   # Ивент
    20: "All Random Deathmatch",   # Случайный герой после каждой смерти
    21: "1v1 Mid",                 # Дуэль 1 на 1 на миде
    22: "All Draft",               # Актуальный All Pick (с банами)
    23: "Turbo",                   # Ускоренный режим
    24: "Mutation",                # Мутации
    25: "Coaches Challenge",       # Игра с тренером
}

# Полный справочник названий типов лобби
LOBBY_TYPES = {
    0:  "Public Matchmaking",      # Обычные публичные матчи
    1:  "Practice",                # Тренировочное лобби
    2:  "Tournament",              # Турнирное лобби
    3:  "Tutorial",                # Обучение
    4:  "Co-op Bots",              # Кооп против ботов
    5:  "Ranked Team MM",          # Рейтинговый командный поиск
    6:  "Ranked Solo MM",          # Рейтинговый соло-поиск
    7:  "Ranked",                  # Рейтинговые матчи
    8:  "1v1 Mid",                 # Дуэль 1 на 1
    9:  "Battle Cup",              # Баттл Кап (мини-турнир)
    10: "Local Bots",              # Локальная игра с ботами
    11: "Spectator",               # Наблюдение
    12: "Event",                   # Ивент
    13: "Gauntlet",                # [Я не знаю что это]
    14: "New Player",              # Режим для новичков
    15: "Featured",                # Рекомендуемый режим
}

# Специальные значения
PRIVATE_ACCOUNT_ID = 4294967295  # ID приватных аккаунтов (max uint32)


# ═══════════════════════════════════════════════════════════════════════════
# ФИЛЬТРАЦИЯ МАТЧЕЙ
# ═══════════════════════════════════════════════════════════════════════════

# Обязательные поля для матчей
REQUIRED_MATCH_FIELDS = {
    "match_id",                   # Уникальный идентификатор матча (64-bit integer)
    "match_seq_num",              # Последовательный номер матча для API запросов
    "radiant_win",                # Победила ли команда Radiant (True/False)
    "duration",                   # Длительность матча в секундах
    "start_time",                 # Unix timestamp начала матча
    "tower_status_radiant",       # Битовая маска состояния башен команды Radiant
    "tower_status_dire",          # Битовая маска состояния башен команды Dire
    "barracks_status_radiant",    # Битовая маска состояния казарм команды Radiant
    "barracks_status_dire",       # Битовая маска состояния казарм команды Dire
    "lobby_type",                 # Тип лобби (публичный, рейтинговый, приватный и т.д.)
    "game_mode",                  # Игровой режим (All Pick, Captain's Mode, Random Draft и т.д.)
    "radiant_score",              # Количество убийств команды Radiant
    "dire_score",                 # Количество убийств команды Dire
    "players"                     # Список игроков матча (массив из 10 элементов)
}

# Обязательные поля для игроков
REQUIRED_PLAYER_FIELDS = {
    "account_id",                 # Уникальный ID аккаунта игрока Steam (может быть анонимным)
    "hero_id",                    # ID выбранного героя (числовой идентификатор)
    "hero_variant",               # Вариант героя (1 - 6)
    "team_number",                # Номер команды (0 = Radiant, 1 = Dire)
    "net_worth",                  # Общая стоимость предметов игрока на конец матча
    "last_hits",                  # Количество добитых крипов (основной показатель фарма)
    "denies",                     # Количество заблокированных союзных крипов
    "gold_per_min",               # Среднее золото в минуту за весь матч
    "xp_per_min",                 # Средний опыт в минуту за весь матч

    # Предметы в основных слотах (6 основных слотов инвентаря)
    "item_0",                     # Предмет в слоте 0 (верхний левый)
    "item_1",                     # Предмет в слоте 1 (верхний средний)
    "item_2",                     # Предмет в слоте 2 (верхний правый)
    "item_3",                     # Предмет в слоте 3 (нижний левый)
    "item_4",                     # Предмет в слоте 4 (нижний средний)
    "item_5",                     # Предмет в слоте 5 (нижний правый)

    # Предметы в рюкзаке (дополнительное хранилище)
    "backpack_0",                 # Предмет в рюкзаке слот 0
    "backpack_1",                 # Предмет в рюкзаке слот 1
    "backpack_2",                 # Предмет в рюкзаке слот 2

    # Боевая статистика
    "kills",                      # Количество убийств героев противника
    "deaths",                     # Количество смертей
    "assists",                    # Количество помощи в убийствах (ассисты)

    # Дополнительные предметы и характеристики
    "item_neutral",               # Основной нейтральный предмет
    "item_neutral2",              # Дополнительный нейтральный предмет
    "level",                      # Уровень героя на конец игры (1-30)
    "aghanims_scepter",           # Есть ли Aghanim's Scepter (1/0)
    "aghanims_shard",             # Есть ли Aghanim's Shard (1/0)
    "moonshard"                   # Есть ли съеденный Moon Shard (1/0)
}

# Разрешенные игровые режимы
ALLOWED_GAME_MODES = {
    3:  "Random Draft",            # Каждый игрок выбирает из ограниченного пула героев
    4:  "Single Draft",            # Каждому игроку доступны 3 случайных героя
    5:  "All Random",              # Полностью случайный выбор героев
    22: "All Draft",               # Актуальный All Pick (с банами)
}

# Разрешенные типы лобби
ALLOWED_LOBBY_TYPES = {
    0:  "Public Matchmaking",      # Обычные публичные матчи
    7:  "Ranked",                  # Рейтинговые матчи
}

# Допустимые статусы игроков, покинувших матч
VALID_LEAVER_STATUSES = {
    0,     # Завершил матч нормально
    1,     # Отключился, но без наказания (переподключился)
    # 2,     # Отключился более чем на 5 минут (abandon)
    # 3,     # Покинул игру намеренно (abandon)
    # 4,     # Бездействие/AFK (abandon)
    # 5,     # Не подключился к матчу
    # 6,     # Слишком долго подключался
}

# Минимальная длительность матча в секундах
# Матчи короче этого времени не учитываются в анализе
MINIMUM_MATCH_DURATION = 1200   # 20 минут

# Исключаемые герои из анализа
# ID героев, которые нужно исключить из датасета и обучения моделей
EXCLUDED_HERO_IDS = {
    # 155 # Largo
    # 145,  # Пример: раскомментируйте для исключения героя
}


# ═══════════════════════════════════════════════════════════════════════════
# ОПРЕДЕЛЕНИЕ РОЛЕЙ ИГРОКОВ
# ═══════════════════════════════════════════════════════════════════════════

# Support-предметы
# Наличие этих предметов увеличивает support_score игрока
SUPPORT_ITEM_IDS = {
    37,    # Ghost Scepter
    43,    # Sentry Ward
    102,   # Force Staff
    214,   # Tranquil Boots
    218,   # Observer and Sentry Wards
    229,   # Solar Crest
    254,   # Glimmer Cape
    256,   # Aeon Disk
    269,   # Holy Locket
    931,   # Boots of Bearing
    1128   # Pavise
}

# Исключения для предметов по героям
# Формат: {hero_id: {item_ids}}
# Указанные предметы НЕ будут считаться support-предметами для этих героев
HERO_ITEM_EXCEPTIONS = {
    97: {102}  # Magnus (ID: 73) - Force Staff
}

# Бонус support_score для определённых героев
# Формат: {(hero_id, hero_variant)} или {hero_id}
# Эти герои/аспекты получают дополнительный бонус к support_score
HERO_SUPPORT_SCORE_EXCEPTIONS = {
    (73, 3)  # Alchemist (ID: 73) variant 3
}

# Маппинг ролей в числовые значения для ML-моделей
ROLE_MAPPING = {
    "core": 0,
    "support": 1
}


# ═══════════════════════════════════════════════════════════════════════════
# ОПРЕДЕЛЕНИЕ РУИНЕРОВ
# ═══════════════════════════════════════════════════════════════════════════

# Исключения компонентов ruiner_index по героям
# Формат: {(hero_id, hero_variant): {'component_name'}} или {hero_id: {'component_name'}}
# Доступные компоненты: 'team_death_ratio', 'kda_score', 'income_score', 'contribution_score'

# Позволяет игнорировать определённые компоненты при расчёте ruiner_index для героев с нестандартной игровой механикой
HERO_RUINER_EXCEPTIONS = {
    (73, 3): {'income_score'}  # Alchemist (ID: 73) variant 3 - игнорирует income_score
}
