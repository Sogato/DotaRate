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
DOTA_VERSION = "739e"   # Используется в названиях файлов и моделей

# Начальный номер последовательности матчей для сбора данных
STARTING_MATCH_SEQUENCE_NUMBER = 7133998434     # Обновляется при переходе на новый патч для сбора актуальных матчей

# Unix timestamp точки отсечки для анализа данных
BURST_TIME_TIMESTAMP = 1759510800   # Fri Oct 03 2025 17:00:00 GMT+0000


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

# Битовые маски для player_slot
PLAYER_SLOT_TEAM_BITMASK = 0x80       # 1 бит для команды (0b10000000)
PLAYER_SLOT_POSITION_BITMASK = 0x07   # 3 бита для позиции в команде (0b00000111)

# Существующие типы турнирных серий
SERIES_TYPES = {
    0,     # Bo1 (Best of 1)
    1,     # Bo3 (Best of 3)
    2,     # Bo5 (Best of 5)
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

# Специальные значения
PRIVATE_ACCOUNT_ID = 4294967295  # ID приватных аккаунтов (max uint32)


# ═══════════════════════════════════════════════════════════════════════════
# ФИЛЬТРАЦИЯ МАТЧЕЙ
# ═══════════════════════════════════════════════════════════════════════════

# Минимальная длительность матча в секундах
# Матчи короче этого времени не учитываются в анализе
MINIMUM_MATCH_DURATION = 1200   # 20 минут

# Исключаемые герои из анализа
# ID героев, которые нужно исключить из датасета и обучения моделей
EXCLUDED_HERO_IDS = {
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
