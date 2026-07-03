"""
Инициализация и хранение состояния приложения в памяти процесса.

На старте сервера загружает всё, что нужно для работы и не меняется во время неё,
и держит это до остановки процесса:
    - HeroMapper   — сопоставление hero_id и плотного индекса
    - HeroCache    — справочник имён героев {hero_id: localized_name}
    - LeagueCache  — справочник названий лиг {leagueid: name}
    - ModelCache   — ансамбли Keras-моделей по типам прогноза win/time/score

Каждый кэш инициализируется вызовом initialize() и печатает свою статистику в консоль.

Загрузка выполняется один раз через load(), после чего view забирают готовые
объекты функциями доступа (get_hero_mapper(), get_models() и т.д.) без обращения
к БД и диску.

load() собирает состояние целиком либо бросает исключение — сервер не поднимется.
Частичный старт запрещён, чтобы не отдавать неполные данные.
"""

# Стандартные библиотеки

# Сторонние библиотеки

# Локальные импорты
from .inference.model_cache import ModelCache
from dota_core.config import EXCLUDED_HERO_IDS
from dota_core.utils.hero_mapper import HeroMapper
from dota_core.utils.hero_cache import HeroCache
from dota_core.utils.league_cache import LeagueCache
from dota_core.utils.console import (
    Colors,
    print_section_header,
    print_status_message,
)

# Состояние процесса. Наполняется в load(); до этого пустое, а функции доступа
# бросают ошибку.
_hero_mapper: HeroMapper | None = None
_hero_cache: HeroCache | None = None
_league_cache: LeagueCache | None = None
_model_cache: ModelCache | None = None

# Признак полной готовности. Истинен только после успешного завершения load().
_loaded: bool = False


def load() -> None:
    """
    Загружает всё состояние системы в память. Вызывается один раз на старте.

    Сначала выполняются лёгкие обращения к БД (маппер и справочники), затем
    тяжёлая загрузка моделей с диска, чтобы при проблемах с БД сервер прекращал
    запуск как можно раньше.

    Если initialize() какого-либо кэша возвращает False, load() бросает
    RuntimeError и сервер не поднимается.

    Raises:
        RuntimeError: Какой-либо из кэшей не смог инициализироваться
            (пустая/недоступная БД, не найдены файлы моделей и т.д.)
    """
    global _hero_mapper, _hero_cache, _league_cache, _model_cache, _loaded

    # Состояние строится строго один раз на процесс.
    if _loaded:
        print_status_message("shared_resources.load() вызван повторно, загрузка пропущена", "warning", "⚠️")
        return

    print_section_header("ИНИЦИАЛИЗАЦИЯ СОСТОЯНИЯ СИСТЕМЫ", "🤖", width=100, color=Colors.BRIGHT_GOLD)

    # === МАППЕР ГЕРОЕВ ===
    hero_mapper = HeroMapper(excluded_hero_ids=EXCLUDED_HERO_IDS)
    if not hero_mapper.initialize():
        raise RuntimeError(
            "HeroMapper: не удалось построить маппинг героев. Сервер не запущен."
        )
    _hero_mapper = hero_mapper

    # === СПРАВОЧНИК ГЕРОЕВ ===
    hero_cache = HeroCache()
    if not hero_cache.initialize():
        raise RuntimeError(
            "HeroCache: справочник героев пуст или недоступен. Сервер не запущен."
        )
    _hero_cache = hero_cache

    # === СПРАВОЧНИК ЛИГ ===
    league_cache = LeagueCache()
    if not league_cache.initialize():
        raise RuntimeError(
            "LeagueCache: справочник лиг пуст или недоступен. Сервер не запущен."
        )
    _league_cache = league_cache

    # === МОДЕЛИ (тяжёлая загрузка с диска — в конце) ===
    model_cache = ModelCache()
    if not model_cache.initialize():
        raise RuntimeError(
            "ModelCache: не удалось загрузить модели. Сервер не запущен."
        )
    _model_cache = model_cache

    # Флаг выставляется в самом конце: при сбое на любом этапе состояние остаётся
    # неполным, а исключение не даёт серверу подняться.
    _loaded = True
    print_status_message("Состояние системы загружено успешно", "success", "🎊")
    print()


def _ensure_loaded() -> None:
    """
    Проверяет, что состояние загружено, и даёт понятную ошибку вместо None.

    Raises:
        RuntimeError: Если load() ещё не вызывался или не завершился успешно
    """
    if not _loaded:
        raise RuntimeError(
            "Состояние не загружено. Вызовите shared_resources.load() при старте сервера."
        )


def get_hero_mapper() -> HeroMapper:
    """Готовый HeroMapper (словари в памяти, соединение с БД закрыто)."""
    _ensure_loaded()
    return _hero_mapper


def get_hero_cache() -> HeroCache:
    """Готовый справочник имён героев."""
    _ensure_loaded()
    return _hero_cache


def get_league_cache() -> LeagueCache:
    """Готовый справочник названий лиг."""
    _ensure_loaded()
    return _league_cache


def get_model_cache() -> ModelCache:
    """Готовый кэш моделей."""
    _ensure_loaded()
    return _model_cache


def is_loaded() -> bool:
    """Готова ли система к работе. Удобно для health-check во view."""
    return _loaded
