"""
Клиент backend API для Telegram-бота.

Модуль, который знает адреса методов backend.
Остальные части бота работают с матчами через функции отсюда и не зависят ни от путей, ни от формата
запросов, при переименовании метода на backend правка нужна только здесь.

Сетевые ошибки наружу не пробрасываются: функция записывает сбой в лог и
возвращает None (при чтении) или False (при записи), чтобы один неудачный запрос
не прерывал проход бота. Реакцию на неуспех выбирает вызывающий код.
"""

# Стандартные библиотеки
import logging
from typing import Any, List, Optional

# Сторонние библиотеки
import requests

# Локальные импорты
from . import config

logger = logging.getLogger(__name__)

# Время ожидания соединения и ответа на обычный запрос, секунды
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 10

# Время ожидания ответа на запуск сбора, секунды
COLLECT_READ_TIMEOUT = 300

# Пути методов относительно API_BASE_URL
_COLLECT_LIVE       = 'collect/live/'
_COLLECT_COMPLETE   = 'collect/complete/'

_MATCHES            = 'matches/'
_MATCH_DETAIL       = 'matches/{match_id}/'

_SET_TELEGRAM_ID    = 'publication/telegram-id/'
_RESET_REFRESH_FLAG = 'publication/refresh-flag/'

_LAST_DAY           = 'matches/last-day/'
_LAST_WEEK          = 'matches/last-week/'
_LAST_MONTH         = 'matches/last-month/'
_ALL_TIME           = 'matches/all-time/'
_BY_LEAGUE          = 'matches/league/{league_id}/'
_EXCEPT_LEAGUE      = 'matches/except-league/{league_id}/'

_CLEANUP            = 'matches/cleanup/'

# Общая сессия — единый пул соединений для всех запросов к backend.
_session = requests.Session()


# ────────────────────────────────────────────────────────────────────────────
# Низкоуровневые помощники
# ────────────────────────────────────────────────────────────────────────────

def _url(path: str) -> str:
    """Собирает полный адрес метода из базового URL и относительного пути."""
    return f"{config.API_BASE_URL.rstrip('/')}/{path}"


def _get_json(path: str, read_timeout: Optional[float] = READ_TIMEOUT) -> Optional[Any]:
    """
    Выполняет GET-запрос и возвращает разобранный JSON, либо None при ошибке.

    Тип результата зависит от метода: список матчей приходит как list,
    карточка матча и сводка прохода как dict. Параметр read_timeout позволяет
    долгим методам ждать ответ дольше обычного.
    """
    url = _url(path)
    try:
        response = _session.get(url, timeout=(CONNECT_TIMEOUT, read_timeout))
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.warning("GET %s | запрос не удался: %s", url, exc)
        return None


def _post(path: str, data: dict) -> bool:
    """
    Выполняет POST-запрос с данными формы и возвращает признак успеха.

    Данные кодируются как application/x-www-form-urlencoded.
    """
    url = _url(path)
    try:
        response = _session.post(url, data=data, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("POST %s | запрос не удался: %s", url, exc)
        return False


# ────────────────────────────────────────────────────────────────────────────
# Запуск сбора
# ────────────────────────────────────────────────────────────────────────────

def trigger_live_collection() -> Optional[dict]:
    """Запускает на backend один проход live-линии."""
    return _get_json(_COLLECT_LIVE, read_timeout=COLLECT_READ_TIMEOUT)


def trigger_completion_check() -> Optional[dict]:
    """Запускает на backend поиск результатов завершённых матчей."""
    return _get_json(_COLLECT_COMPLETE, read_timeout=COLLECT_READ_TIMEOUT)


# ────────────────────────────────────────────────────────────────────────────
# Чтение матчей
# ────────────────────────────────────────────────────────────────────────────

def get_matches() -> Optional[List[dict]]:
    """Возвращает список всех матчей, либо None при ошибке запроса."""
    return _get_json(_MATCHES)


def get_match(match_id: int) -> Optional[dict]:
    """Возвращает карточку одного матча по его id, либо None."""
    return _get_json(_MATCH_DETAIL.format(match_id=match_id))


# ────────────────────────────────────────────────────────────────────────────
# Публикация в Telegram
# ────────────────────────────────────────────────────────────────────────────

def set_telegram_message_id(match_id: int,
                            telegram_message_id: int,
                            refresh_flag: Optional[bool] = None) -> bool:
    """
    Сохраняет id опубликованного сообщения в публикацию матча.

    refresh_flag передаётся, только если задан явно: метод принимает его
    опционально и не трогает текущее значение поля, если оно не пришло.
    """
    data = {
        'match_id': match_id,
        'telegram_message_id': telegram_message_id,
    }
    if refresh_flag is not None:
        data['refresh_flag'] = refresh_flag
    return _post(_SET_TELEGRAM_ID, data)


def reset_refresh_flag(match_id: int) -> bool:
    """Снимает у публикации матча признак необходимости обновления."""
    return _post(_RESET_REFRESH_FLAG, {'match_id': match_id})


# ────────────────────────────────────────────────────────────────────────────
# Списки за период и по лиге (для статистики)
# ────────────────────────────────────────────────────────────────────────────

def get_last_day() -> Optional[List[dict]]:
    """Матчи, завершившиеся за последние сутки."""
    return _get_json(_LAST_DAY)


def get_last_week() -> Optional[List[dict]]:
    """Матчи, завершившиеся за последнюю неделю."""
    return _get_json(_LAST_WEEK)


def get_last_month() -> Optional[List[dict]]:
    """Матчи, завершившиеся в предыдущем календарном месяце."""
    return _get_json(_LAST_MONTH)


def get_all_time() -> Optional[List[dict]]:
    """Все матчи со ставкой за всё время."""
    return _get_json(_ALL_TIME)


def get_by_league(league_id: int) -> Optional[List[dict]]:
    """Матчи со ставкой по указанной лиге."""
    return _get_json(_BY_LEAGUE.format(league_id=league_id))


def get_except_league(league_id: int) -> Optional[List[dict]]:
    """Матчи со ставкой во всех лигах, кроме указанной."""
    return _get_json(_EXCEPT_LEAGUE.format(league_id=league_id))


# ────────────────────────────────────────────────────────────────────────────
# Обслуживание БД
# ────────────────────────────────────────────────────────────────────────────

def trigger_cleanup() -> Optional[dict]:
    """
    Запускает на backend удаление старых матчей без ставки.

    Returns:
        Optional[dict]: Сводка вида {"status", "deleted"} либо None при ошибке
    """
    return _get_json(_CLEANUP)
