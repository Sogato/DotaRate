"""
Конфигурационный файл для системы сбора и анализа данных матчей Dota 2.
"""

import os

from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
# .env должен находиться в корневой директории проекта
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# === КОНФИГУРАЦИЯ БАЗЫ ДАННЫХ ===
DB_USER = os.getenv('DB_USER')  # Имя пользователя БД
DB_PASSWORD = os.getenv('DB_PASSWORD')  # Пароль пользователя БД
DB_HOST = os.getenv('DB_HOST')  # Хост БД
DB_PORT = os.getenv('DB_PORT')  # Порт БД

# Названия отдельных баз данных
DATASET_DB_NAME = os.getenv('DATASET_DB_NAME')  # БД для хранения матчей и игроков
HEROES_DB_NAME = os.getenv('HEROES_DB_NAME')  # БД для справочной информации о героях

# Строки подключения к базам данных
DATASET_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DATASET_DB_NAME}"
HEROES_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{HEROES_DB_NAME}"

# === КОНФИГУРАЦИЯ STEAM API ===
STEAM_API_KEY = os.getenv('STEAM_API_KEY')  # Ключ Steam API (получается на steamcommunity.com/dev)
STEAM_API_MATCH_HISTORY_URL = "https://api.steampowered.com/IDOTA2Match_570/GetMatchHistoryBySequenceNum/V001/"

# API для получения информации о героях (используется для инициализации БД HEROES)
OPENDOTA_HEROES_API = "https://api.opendota.com/api/heroes"

# Параметры сбора данных
STARTING_MATCH_SEQUENCE_NUMBER = 7_063_000_002  # Стартовый sequence number для начала сбора
TARGET_MATCHES_COUNT = 1000000  # Целевое количество матчей для сбора
DATABASE_SAVE_CHUNK_SIZE = 100000  # Размер чанка для пакетного сохранения в БД
BURST_TIME_TIMESTAMP = 1754697600  # Unix timestamp - матчи меньше этого времени игнорируются
MATCHES_PER_API_REQUEST = 100  # Количество матчей, запрашиваемых за один API вызов (1 - 100)
API_REQUEST_DELAY_SECONDS = 3  # Задержка между запросами к Steam API (для избежания rate limit)

# === НАСТРОЙКИ ЛОГИРОВАНИЯ (DEBUG) ===
ENABLE_DETAILED_STATISTICS = False  # Включает детальную статистику обработки каждого API вызова
ENABLE_RUINER_LOGGING = False  # Включает логирование подробностей обнаружения руинеров
ENABLE_ROLE_LOGGING = False  # Включает логирование процесса назначения ролей игрокам

# === ПАРАМЕТРЫ ВАЛИДАЦИИ ДАННЫХ ===
# Обязательные поля для матчей (если любое отсутствует - матч отклоняется)
REQUIRED_MATCH_FIELDS = {
    "match_id",  # Уникальный идентификатор матча
    "match_seq_num",  # Последовательный номер матча для API запросов
    "radiant_win",  # Победила ли команда Radiant (True/False)
    "duration",  # Длительность матча в секундах
    "start_time",  # Unix timestamp начала матча
    "tower_status_radiant",  # Битовая маска состояния башен Radiant
    "tower_status_dire",  # Битовая маска состояния башен Dire
    "barracks_status_radiant",  # Битовая маска состояния казарм Radiant
    "barracks_status_dire",  # Битовая маска состояния казарм Dire
    "lobby_type",  # Тип лобби (публичный, приватный и т.д.)
    "game_mode",  # Игровой режим (All Pick, Captain's Mode и т.д.)
    "radiant_score",  # Количество убийств команды Radiant
    "dire_score",  # Количество убийств команды Dire
    "players"  # Список игроков матча
}

# Обязательные поля для игроков (если любое отсутствует - матч отклоняется)
REQUIRED_PLAYER_FIELDS = {
    "account_id",  # Уникальный ID аккаунта игрока
    "hero_id",  # ID выбранного героя
    "hero_variant",  # Вариант героя (обычно 0, для Arcana и т.д.)
    "team_number",  # Номер команды (0 = Radiant, 1 = Dire)
    "net_worth",  # Общая стоимость предметов игрока
    "last_hits",  # Количество добитых крипов
    "denies",  # Количество заблокированных крипов
    "gold_per_min",  # Золото в минуту
    "xp_per_min",  # Опыт в минуту
    "item_0",  # Предмет в слоте 0 (верхний левый)
    "item_1",  # Предмет в слоте 1
    "item_2",  # Предмет в слоте 2
    "item_3",  # Предмет в слоте 3
    "item_4",  # Предмет в слоте 4
    "item_5",  # Предмет в слоте 5 (нижний правый)
    "backpack_0",  # Предмет в рюкзаке слот 0
    "backpack_1",  # Предмет в рюкзаке слот 1
    "backpack_2",  # Предмет в рюкзаке слот 2
    "kills",  # Количество убийств
    "deaths",  # Количество смертей
    "assists",  # Количество помощи в убийствах
    "item_neutral",  # Нейтральный предмет (основной)
    "item_neutral2",  # Нейтральный предмет (дополнительный)
    "level",  # Уровень героя на конец игры
    "aghanims_scepter",  # Есть ли Aghanim's Scepter (1/0)
    "aghanims_shard",  # Есть ли Aghanim's Shard (1/0)
    "moonshard"  # Есть ли съеденный Moon Shard (1/0)
}

# Разрешенные игровые режимы (остальные режимы фильтруются)
ALLOWED_GAME_MODES = {
    1,  # All Pick
    2,  # Captain's Mode
    3,  # Random Draft
    4,  # Single Draft
    5,  # All Random
    8,  # Reverse Captain's Mode
    16,  # Captain's Draft
    22  # All Pick (Ranked)
}

# Дополнительные параметры фильтрации
MINIMUM_MATCH_DURATION = 1200  # Минимальная длительность матча в секундах (20 минут)
NUMBER_OF_PLAYERS_PER_TEAM = 5  # Ожидаемое количество игроков в каждой команде

# === КОНФИГУРАЦИЯ АЛГОРИТМА ОПРЕДЕЛЕНИЯ РОЛЕЙ ===
# ID предметов, характерных для роли поддержки
SUPPORT_ITEM_IDS = {
    37,  # Ghost Scepter
    43,  # Sentry Ward
    102,  # Force Staff
    214,  # Tranquil Boots
    218,  # Observer and Sentry Wards
    229,  # Solar Crest
    254,  # Glimmer Cape
    256,  # Aeon Disk
    269,  # Holy Locket
    931,  # Boots of Bearing
    1128  # Pavise
}

GPM_CALCULATION_START_MINUTE = 20  # Минута, с которой начинается рост ожидаемого GPM

# Веса для расчета support_score (влияют на точность определения ролей)
SUPPORT_ITEMS_WEIGHT = 5  # Вес количества support предметов
NET_WORTH_WEIGHT = 10  # Вес net worth (инвертированный - меньше = больше support)
LAST_HITS_WEIGHT = 5  # Вес last hits (инвертированный)
GPM_WEIGHT = 5  # Вес GPM (инвертированный)
XPM_WEIGHT = 5  # Вес XPM (инвертированный)

# === ПАРАМЕТРЫ ДЕТЕКЦИИ РУИНЕРОВ ===
# Ожидаемые KDA значения для расчета отклонений
CORE_EXPECTED_KDA = 4.8  # Ожидаемый KDA для core игроков
CORE_KDA_TOLERANCE = 4.3  # Допустимое отклонение KDA для core
SUPPORT_EXPECTED_KDA = 3.5  # Ожидаемый KDA для support игроков
SUPPORT_KDA_TOLERANCE = 3.0  # Допустимое отклонение KDA для support

# Параметры расчета ожидаемого GPM (Gold Per Minute)
CORE_BASE_GPM = 500  # Базовый GPM для core игроков
CORE_GPM_GROWTH_RATE = 6.0  # Скорость роста GPM у core (за минуту после 20-й минуты)
CORE_MAX_GPM = 800  # Максимальный ожидаемый GPM для core
SUPPORT_BASE_GPM = 300  # Базовый GPM для support игроков
SUPPORT_GPM_GROWTH_RATE = 5.0  # Скорость роста GPM у support
SUPPORT_MAX_GPM = 600  # Максимальный ожидаемый GPM для support

# Пороговые значения для классификации руинеров
INCOME_MINIMUM_THRESHOLD = 0.3  # Минимальный процент от ожидаемого дохода (30%)
RUINER_DETECTION_THRESHOLD = 0.6  # Порог ruiner_index для классификации как руинер
RUINER_LOGGING_THRESHOLD = 0.5  # Порог для логирования подозрительных игроков
RUINER_EMPTY_SLOTS_LIMIT = 4  # Максимальное количество пустых слотов предметов
RUINER_SAME_ITEMS_LIMIT = 4  # Максимальное количество одинаковых предметов

# Веса компонентов в формуле ruiner_index (сумма должна быть 1.0)
TEAM_DEATH_RATIO_WEIGHT = 0.25  # Вес компонента "доля смертей от командных"
KDA_SCORE_WEIGHT = 0.25  # Вес компонента "отклонение KDA от ожидаемого"
INCOME_SCORE_WEIGHT = 0.3  # Вес компонента "экономические показатели"
CONTRIBUTION_SCORE_WEIGHT = 0.2  # Вес компонента "вклад в убийства команды"

# Исключения для определения support предметов у конкретных героев
# Формат: {hero_id: {set_of_item_ids_to_ignore}}
HERO_ITEM_EXCEPTIONS = {
    97: {102},  # Magnus (ID: 97) - Ring of Basilius (ID: 102) не считается support предметом
    # Можно добавить другие исключения по мере необходимости
    # Например: 5: {214, 218},  # Crystal Maiden - игнорировать Mekansm и Pipe
}

# === КОНСТАНТЫ ДЛЯ АНАЛИЗА ДАТАСЕТА ===
PRIVATE_ACCOUNT_ID = 4294967295  # ID приватных аккаунтов Steam
MINIMUM_GAMES_FOR_HERO_WINRATE = 100  # Минимум игр для анализа винрейта героев
MINIMUM_GAMES_FOR_HERO_VARIANT_WINRATE = 50  # Минимум игр для анализа винрейта героев с аспектом
TOP_HEROES_DISPLAY_COUNT = 20  # Количество героев для отображения в рейтинге

# Битовые маски для анализа структур
TOWERS_BITMASK = 0x7FF  # 11 единиц для 11 башен
BARRACKS_BITMASK = 0x3F  # 6 единиц для 6 казарм
