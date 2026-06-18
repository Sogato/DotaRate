"""
Общий конфигурационный модуль системы Dota Rate (dota_core).

Содержит настройки и константы, общие для всех модулей системы (ML-модуль,
backend и т.д.): подключения к базам данных и внешним API, обновляемые при
смене патча параметры, игровые константы Dota 2 и список исключаемых героев.

Загрузка переменных окружения из .env выполняется единожды на всю
систему. Локальные config-модули отдельных пакетов импортируют этот модуль
и дополняют его своими специфичными константами.
"""

import os as _os
from dotenv import load_dotenv as _load_dotenv

# Загрузка переменных окружения из .env в корне репозитория (на уровень выше
# dota_core). Единая точка загрузки .env для всей системы.
_load_dotenv(dotenv_path=_os.path.join(_os.path.dirname(__file__), '..', '.env'))


# ═══════════════════════════════════════════════════════════════════════════
# БАЗЫ ДАННЫХ
# ═══════════════════════════════════════════════════════════════════════════

# Параметры подключения
DB_USER = _os.getenv('DB_USER')
DB_PASSWORD = _os.getenv('DB_PASSWORD')
DB_HOST = _os.getenv('DB_HOST')
DB_PORT = _os.getenv('DB_PORT')

# Названия баз данных
BACKEND_DB_NAME = _os.getenv('BACKEND_DB_NAME')  # БД backend
HEROES_DB_NAME = _os.getenv('HEROES_DB_NAME')    # Справочник героев
LEAGUES_DB_NAME = _os.getenv('LEAGUES_DB_NAME')  # Справочник лиг
DATASET_DB_NAME = _os.getenv('DATASET_DB_NAME')  # Основная БД с матчами
PRO_DB_NAME = _os.getenv('PRO_DB_NAME')          # БД профессиональных матчей

# Строки подключения (PostgreSQL URI)
HEROES_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{HEROES_DB_NAME}"
LEAGUES_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{LEAGUES_DB_NAME}"
DATASET_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DATASET_DB_NAME}"
PRO_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{PRO_DB_NAME}"

# ═══════════════════════════════════════════════════════════════════════════
# ВНЕШНИЕ API
# ═══════════════════════════════════════════════════════════════════════════

# Steam API
STEAM_API_KEY = _os.getenv('STEAM_API_KEY')
STEAM_API_MATCH_HISTORY_URL = "https://api.steampowered.com/IDOTA2Match_570/GetMatchHistoryBySequenceNum/V001/"

# OpenDota API
OPENDOTA_HEROES_API = "https://api.opendota.com/api/heroes"
OPENDOTA_LEAGUES_API = "https://api.opendota.com/api/leagues"
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
# ИСКЛЮЧАЕМЫЕ ГЕРОИ
# ═══════════════════════════════════════════════════════════════════════════

# ID героев, исключаемых из анализа, датасета и обучения моделей.
# Используется HeroMapper'ом, поэтому должен быть общим: индексы героев обязаны
# совпадать между препроцессингом, обучением и инференсом.
EXCLUDED_HERO_IDS = {
    # 155 # Largo
    # 145,  # Пример: раскомментируйте для исключения героя
}
