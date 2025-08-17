import os
from dotenv import load_dotenv

# Загружаем .env из корня (родительская директория ml_module)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# Константы для БД
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')
DB_NAME = os.getenv('DB_NAME')
# Формируем URL подключения к БД
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Steam API константы
STEAM_API_KEY = os.getenv('STEAM_API_KEY')
STEAM_GET_MATCH_HISTORY_API = "https://api.steampowered.com/IDOTA2Match_570/GetMatchHistoryBySequenceNum/V001/"

# Параметры сбора данных
LAST_MATCH_SEQ_NUM = 7003463459  # Начальный match_seq_num для сбора данных
MAX_ID = 8320561182  # Максимальный match_id для остановки сбора
MAX_MATCHES = 1000000000  # Максимальное количество матчей для сбора
CHUNK_SIZE = 1000  # Шаг сохранения матчей в БД
BURST_TIME = 1754697600  # Временная граница для матчей
MATCHES_PER_REQUEST = 100  # Количество матчей за один запрос к Steam API

7063000000
7063000000
7063000000

# Константы для фильтрации
REQUIRED_MATCH_KEYS = {
    "players", "radiant_win", "duration", "start_time", "tower_status_radiant",
    "tower_status_dire", "barracks_status_radiant", "barracks_status_dire",
    "radiant_score", "dire_score",
}
REQUIRED_PLAYER_KEYS = {"account_id", "team_number", "hero_id"}
SUPPORT_ITEMS = {43, 185, 188, 214, 218, 229, 254, 269, 931, 1128}
GAME_MODS = {22}

# Настройки API запросов
API_REQUEST_DELAY = 4  # Задержка между запросами к API (секунды)
API_ERROR_DELAY = 15  # Задержка при ошибках API (секунды)
API_EXCEPTION_DELAY = 10  # Задержка при RequestException (секунды)
MAX_API_ATTEMPTS = 5  # Максимальное количество попыток получения матчей

# Настройки определения руинеров
RUINER_THRESHOLD = 0.5  # Порог для определения руинера

DS_NORMALIZATION_FACTOR = 3.0  # Фактор нормализации Death Score

# Настройки GPM для ролей
CORE_BASE_GPM = 500  # Базовый GPM для кор-героев (для 15 минут)
CORE_GPM_GROWTH_RATE = 6.5  # Рост GPM на минуту после 15 минут для коров
CORE_MAX_GPM = 800  # Максимальный ожидаемый GPM для коров

SUPPORT_BASE_GPM = 300  # Базовый GPM для саппортов (для 15 минут)
SUPPORT_GPM_GROWTH_RATE = 4.5  # Рост GPM на минуту после 15 минут для саппортов
SUPPORT_MAX_GPM = 500  # Максимальный ожидаемый GPM для саппортов

# Коэффициенты для расчета Ruiner Index
RUINER_DS_WEIGHT = 0.4  # Вес Death Score
RUINER_IS_WEIGHT = 0.3  # Вес Item Score
RUINER_CS_WEIGHT = 0.3  # Вес Contribution Score

# Настройки отладки и вывода
ENABLE_RUINER_DEBUG = True  # Включить отладку руинеров

# OpenDota API для получения стартового match_seq_num
OPENDOTA_PUBLIC_MATCHES_API = "https://api.opendota.com/api/publicMatches"
