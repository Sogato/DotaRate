import os
from dotenv import load_dotenv

# Загружаем .env из корня (родительская директория ml_module)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# Общие константы для БД
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')

# Константы для отдельных БД
DATASET_DB_NAME = os.getenv('DATASET_DB_NAME')
HEROES_DB_NAME = os.getenv('HEROES_DB_NAME')

# Основная БД (датасет - матчи и игроки)
DATASET_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DATASET_DB_NAME}"
# БД для героев
HEROES_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{HEROES_DB_NAME}"

OPENDOTA_HEROES_API = "https://api.opendota.com/api/heroes"

# === КОНСТАНТЫ STEAM API ===
STEAM_API_KEY = os.getenv('STEAM_API_KEY')
STEAM_API_MATCH_HISTORY_URL = "https://api.steampowered.com/IDOTA2Match_570/GetMatchHistoryBySequenceNum/V001/"
STARTING_MATCH_SEQUENCE_NUMBER = 7_063_000_002
TARGET_MATCHES_COUNT = 10600
DATABASE_SAVE_CHUNK_SIZE = 1500
BURST_TIME_TIMESTAMP = 1754697600  # Временная метка для фильтрации старых матчей
MATCHES_PER_API_REQUEST = 100
API_REQUEST_DELAY_SECONDS = 3

# === НАСТРОЙКИ ЛОГИРОВАНИЯ ===
ENABLE_DETAILED_STATISTICS = False  # Детальная статистика каждого API вызова
ENABLE_RUINER_LOGGING = False  # Логирование найденных руинеров
ENABLE_ROLE_LOGGING = False  # Логирование распределения ролей

# === ОБЯЗАТЕЛЬНЫЕ ПОЛЯ ДЛЯ ВАЛИДАЦИИ ДАННЫХ ===
REQUIRED_MATCH_FIELDS = {
    "players", "radiant_win", "duration", "start_time", "tower_status_radiant",
    "tower_status_dire", "barracks_status_radiant", "barracks_status_dire",
    "radiant_score", "dire_score",
}
REQUIRED_PLAYER_FIELDS = {"account_id", "team_number", "hero_id"}

# === КОНСТАНТЫ ДЛЯ ОПРЕДЕЛЕНИЯ РОЛЕЙ И РУИНЕРОВ ===
SUPPORT_ITEM_IDS = {37, 43, 102, 185, 214, 218, 229, 254, 256, 269, 931, 1128}
ALLOWED_GAME_MODES = {1, 2, 3, 4, 5, 8, 16, 22}

# Параметры для расчета индекса руинера по KDA
CORE_EXPECTED_KDA = 4.8
CORE_KDA_TOLERANCE = 4.3
SUPPORT_EXPECTED_KDA = 3.5
SUPPORT_KDA_TOLERANCE = 3.0

# Параметры для расчета ожидаемого GPM (золото в минуту)
CORE_BASE_GPM = 500
CORE_GPM_GROWTH_RATE = 6.0
CORE_MAX_GPM = 800
SUPPORT_BASE_GPM = 300
SUPPORT_GPM_GROWTH_RATE = 5.0
SUPPORT_MAX_GPM = 600

# Пороговые значения для детекции руинеров
INCOME_MINIMUM_THRESHOLD = 0.3  # 30% от ожидаемого net_worth
RUINER_DETECTION_THRESHOLD = 0.6
RUINER_LOGGING_THRESHOLD = 0.5
RUINER_EMPTY_SLOTS_LIMIT = 4  # Максимальное количество пустых слотов предметов
RUINER_SAME_ITEMS_LIMIT = 4  # Максимальное количество одинаковых предметов

# Веса для расчета индекса руинера
TEAM_DEATH_RATIO_WEIGHT = 0.25
KDA_SCORE_WEIGHT = 0.25
INCOME_SCORE_WEIGHT = 0.3
CONTRIBUTION_SCORE_WEIGHT = 0.2

# Параметры для определения ролей игроков
GPM_CALCULATION_START_MINUTE = 20  # С какой минуты начинается рост GPM
SUPPORT_ITEMS_WEIGHT = 5
NET_WORTH_WEIGHT = 10
LAST_HITS_WEIGHT = 5
GPM_WEIGHT = 5
XPM_WEIGHT = 5

# Исключения для саппорт предметов (герой_id: {предмет_id})
HERO_ITEM_EXCEPTIONS = {
    97: {102},  # Magnus не считается саппортом за предмет 102
}
