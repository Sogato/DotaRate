"""
Инициализация и хранение состояния приложения в памяти процесса.

На старте сервера загружает всё, что нужно для работы и не меняется во время неё,
и держит это до остановки процесса:
    - HeroMapper   — сопоставление hero_id и плотного индекса
    - HeroCache    — справочник имён героев {hero_id: localized_name}
    - LeagueCache  — справочник названий лиг {leagueid: name}
    - Keras-модели — ансамбли по типам прогноза win/time/score

Загрузка выполняется один раз через load(), после чего view забирают готовые
объекты функциями доступа (get_hero_mapper(), get_models() и т.д.) без обращения
к БД и диску.

load() собирает состояние целиком либо бросает исключение — сервер не поднимется.
Частичный старт запрещён, чтобы не отдавать неполные данные.
"""

# Стандартные библиотеки
from typing import Dict, List

# Сторонние библиотеки
from keras import Model
from keras.models import load_model
from django.conf import settings

# Локальные импорты
from dota_core.utils.hero_mapper import HeroMapper
from dota_core.utils.hero_cache import HeroCache
from dota_core.utils.league_cache import LeagueCache
from dota_core.config import DOTA_VERSION, EXCLUDED_HERO_IDS
from dota_core.utils.console import (
    Colors,
    print_section_header,
    print_subsection_header,
    print_info_line,
    print_status_message,
)

# Шаблон базового имени файла модели.
MODEL_BASENAME = "model_{type}_{version}_{dota}"

# Состояние процесса. Наполняется в load(); до этого пустое, а функции доступа
# бросают ошибку.
_hero_mapper: HeroMapper | None = None
_hero_cache: HeroCache | None = None
_league_cache: LeagueCache | None = None
_models: Dict[str, List[Model]] | None = None

# Признак полной готовности. Истинен только после успешного завершения load().
_loaded: bool = False


def load() -> None:
    """
    Загружает всё состояние системы в память. Вызывается один раз на старте.

    Сначала выполняются лёгкие обращения к БД (маппер и справочники), затем
    тяжёлая загрузка моделей с диска, чтобы при проблемах с БД сервер прекращал
    запуск как можно раньше.

    Raises:
        RuntimeError: Справочник героев или лиг пуст либо недоступен
        FileNotFoundError: Не найдено ни одной модели какого-либо типа
        Exception: Ошибки БД при построении сопоставления пробрасываются наверх
    """
    global _hero_mapper, _hero_cache, _league_cache, _models, _loaded

    # Состояние строится строго один раз на процесс.
    if _loaded:
        print_status_message("state.load() вызван повторно — загрузка пропущена", "warning", "⚠️")
        return

    print_section_header("ИНИЦИАЛИЗАЦИЯ СОСТОЯНИЯ СИСТЕМЫ", "🤖", width=100, color=Colors.BRIGHT_GOLD)

    # === МАППЕР ГЕРОЕВ ===
    # Словари сопоставления строятся обращением к свойству, после чего соединение
    # с БД закрывается, а сами словари остаются в памяти объекта.
    print_subsection_header("Инициализация маппера героев", "🗺️", Colors.BRIGHT_PURPLE)
    mapper = HeroMapper(excluded_hero_ids=EXCLUDED_HERO_IDS)
    mapper.hero_to_index
    mapper.cleanup()
    _hero_mapper = mapper
    print_info_line("Загружено героев", f"{mapper.total_heroes}", "🧙‍♂️", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    print_info_line("Статус", "Маппер построен, соединение закрыто", "✅", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    print()

    # === СПРАВОЧНИКИ (печатают свои заголовки и статистику сами) ===
    hero_cache = HeroCache()
    if not hero_cache.initialize():
        raise RuntimeError(
            "HeroCache: справочник героев пуст или недоступен. Сервер не запущен."
        )
    _hero_cache = hero_cache

    league_cache = LeagueCache()
    if not league_cache.initialize():
        raise RuntimeError(
            "LeagueCache: справочник лиг пуст или недоступен. Сервер не запущен."
        )
    _league_cache = league_cache

    # === МОДЕЛИ (тяжёлая загрузка с диска — в конце) ===
    _models = _load_models()

    # Флаг выставляется в самом конце: при сбое на любом этапе состояние остаётся
    # неполным, а исключение не даёт серверу подняться.
    _loaded = True
    print_status_message("Состояние системы загружено успешно", "success", "🎊")
    print()


def _load_models() -> Dict[str, List[Model]]:
    """
    Загружает ансамбли всех типов согласно settings.MODEL_VERSIONS.

    Returns:
        Dict[str, List[Model]]: Сопоставление типа прогноза и списка моделей

    Raises:
        FileNotFoundError: Для какого-либо типа не найдено ни одной модели
    """
    print_subsection_header("Загрузка моделей", "🧠", Colors.BRIGHT_CYAN)
    return {
        model_type: _load_ensemble(model_type, version)
        for model_type, version in settings.MODEL_VERSIONS.items()
    }


def _load_ensemble(model_type: str, model_version: str) -> List[Model]:
    """
    Загружает все модели одного типа, упорядоченные по номеру.

    Args:
        model_type (str): Тип модели ('win', 'time', 'score')
        model_version (str): Версия модели (например, 'v1')

    Returns:
        List[Model]: Модели ансамбля в порядке возрастания номера

    Raises:
        FileNotFoundError: По ожидаемому шаблону не найдено ни одного файла
    """
    type_dir = settings.MODELS_DIR / model_type
    base_name = MODEL_BASENAME.format(type=model_type, version=model_version, dota=DOTA_VERSION)

    # Номер модели берётся из суффикса '_fold_N' в имени файла и задаёт порядок.
    model_paths = sorted(
        type_dir.glob(f"{base_name}_fold_*.keras"),
        key=lambda p: int(p.stem.split('_fold_')[-1])
    )

    if not model_paths:
        raise FileNotFoundError(
            f"Не найдено моделей типа '{model_type}' {model_version} в {type_dir} "
            f"(ожидался шаблон {base_name}_fold_*.keras). Сервер не будет запущен."
        )

    print_info_line(f"{model_type} {model_version}", f"загружено моделей: {len(model_paths)}", "📦",
                    Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
    return [load_model(str(path)) for path in model_paths]


def _ensure_loaded() -> None:
    """
    Проверяет, что состояние загружено, и даёт понятную ошибку вместо None.

    Raises:
        RuntimeError: Если load() ещё не вызывался или не завершился успешно
    """
    if not _loaded:
        raise RuntimeError(
            "Состояние не загружено. Вызовите state.load() при старте сервера."
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


def get_models() -> Dict[str, List[Model]]:
    """
    Загруженные ансамбли моделей по типам.

    Передаётся в match_predictor.predict() вместе с маппером.

    Returns:
        Dict[str, List[Model]]: Сопоставление типа прогноза и списка моделей
    """
    _ensure_loaded()
    return _models


def is_loaded() -> bool:
    """Готова ли система к работе. Удобно для health-check во view."""
    return _loaded
