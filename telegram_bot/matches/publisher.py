"""
Основной цикл публикации матчей в Telegram-канале.

Каждые несколько секунд бот запускает сбор данных на backend, забирает
список сопровождаемых матчей и для каждого решает: опубликовать новое
сообщение, отредактировать существующее или пропустить (см. _route).

Матч ведётся, пока у него стоит refresh_flag. Когда известен исход
(radiant_win), бот вносит финальную правку и снимает флаг — дальше
матч не трогается.

Последний отправленный текст каждого матча хранится в памяти процесса
(_last_text), чтобы не обращаться к Telegram с правкой без изменений. После
перезапуска словарь пуст, поэтому первая правка каждого матча проходит всегда:
текст считается изменившимся. Это безопасно — правка идентичным текстом не
создаёт дубликата.
"""

# Стандартные библиотеки
import logging
import time
from typing import Dict

# Сторонние библиотеки
from telebot.apihelper import ApiException

# Локальные импорты
from .. import api_client
from ..config import BOT, TELEGRAM_CHAT_ID, PUBLISH_WIN_THRESHOLD
from . import message

logger = logging.getLogger(__name__)

# Пауза в секундах между проходами цикла.
POLL_INTERVAL = 5

# Последний отправленный в Telegram текст по каждому матчу: {match_id: text}.
_last_text: Dict[int, str] = {}


def run() -> None:
    """Запускает бесконечный цикл публикации матчей. Возврата нет — выход по прерыванию."""
    logger.info("Бот запущен, вход в цикл публикации матчей")
    while True:
        try:
            _poll()
        except Exception:
            # Цикл должен пережить любой неожиданный сбой прохода.
            logger.exception("Неожиданный сбой в проходе цикла")
        time.sleep(POLL_INTERVAL)


def _poll() -> None:
    """Один проход: запуск сбора, чтение матчей, обработка каждого."""
    # Если основной проход сбора не удался, читать матчи в этом проходе смысла нет.
    if api_client.trigger_live_collection() is None:
        return
    api_client.trigger_completion_check()

    matches = api_client.get_active_matches()
    if matches is None:
        return

    for match in matches:
        try:
            _route(match)
        except Exception:
            # Ошибка на одном матче не должна прерывать весь проход.
            logger.exception("Сбой обработки матча %s", match.get('match_id'))


def _route(match: dict) -> None:
    """Направляет матч в публикацию или редактирование по его состоянию."""
    publication = match.get('publication') or {}
    message_id = publication.get('telegram_message_id')

    # Ещё не опубликован и не завершён — кандидат на публикацию.
    if message_id is None and match['radiant_win'] is None:
        if _should_publish(match):
            _publish(match)
        return

    # Опубликован и помечен на обновление — редактируем при изменении.
    if publication.get('refresh_flag'):
        _edit(match)


def _publish(match: dict) -> None:
    """
    Публикует новое сообщение о матче и сохраняет его id на backend.

    refresh_flag=True переводит матч в режим обновления: на следующих прохода он попадёт в ветку редактирования.
    """
    match_id = match['match_id']
    text = message.build(match)

    try:
        sent = BOT.send_message(TELEGRAM_CHAT_ID, text, parse_mode="HTML")
    except ApiException as exc:
        logger.warning("Матч %s: не удалось отправить сообщение: %s", match_id, exc)
        return

    saved = api_client.set_telegram_message_id(match_id, sent.message_id, refresh_flag=True)
    if saved:
        _last_text[match_id] = text
        logger.info("Матч %s опубликован (message_id=%s)", match_id, sent.message_id)
    else:
        # Сообщение в канале есть, но backend не сохранил его id. На следующем
        # проходе матч снова окажется без telegram_message_id — если backend не
        # поднимется, возможна повторная публикация.
        logger.warning(
            "Матч %s: сообщение отправлено, но backend не сохранил message_id", match_id
        )


def _edit(match: dict) -> None:
    """
    Редактирует сообщение при изменении текста.

    У завершённого матча снимает refresh_flag — независимо от того, была ли правка в этом проходе.
    """
    match_id = match['match_id']
    publication = match.get('publication') or {}
    message_id = publication.get('telegram_message_id')

    # _route отбирает матчи по одному refresh_flag, message_id он не смотрит.
    # Флаг без message_id — противоречивое состояние на backend: редактировать
    # нечего, остаётся заметить это в логе и выйти.
    if message_id is None:
        logger.warning("Матч %s: стоит refresh_flag, но нет message_id", match_id)
        return

    text = message.build(match)

    if _last_text.get(match_id) != text:
        try:
            BOT.edit_message_text(text, TELEGRAM_CHAT_ID, message_id, parse_mode="HTML")
            _last_text[match_id] = text
            logger.info("Матч %s: сообщение обновлено", match_id)
        except ApiException as exc:
            if _is_not_modified(exc):
                # В канале уже висит ровно этот текст, а память о нём отстала —
                # так бывает после перезапуска. Запоминаем текст и продолжаем.
                logger.debug("Матч %s: Telegram счёл сообщение неизменным", match_id)
                _last_text[match_id] = text
            else:
                # Правка не дошла до Telegram. Выходим, не трогая флаг:
                # следующий проход попробует снова.
                logger.warning("Матч %s: не удалось отредактировать: %s", match_id, exc)
                return

    # Исход известен — финальная правка сделана, матч больше не ведётся.
    if match['radiant_win'] is not None:
        if api_client.reset_refresh_flag(match_id):
            _last_text.pop(match_id, None)
            logger.info("Матч %s завершён, флаг обновления снят", match_id)


def _should_publish(match: dict) -> bool:
    """
    Проходит ли матч порог уверенности прогноза для публикации.

    PUBLISH_WIN_THRESHOLD=None публикует все матчи. Иначе матч публикуется
    только при уверенном прогнозе в любую сторону (см. описание константы
    в config).
    """
    if PUBLISH_WIN_THRESHOLD is None:
        return True
    predict_win = match['predict_win']
    return predict_win >= PUBLISH_WIN_THRESHOLD or predict_win <= 1 - PUBLISH_WIN_THRESHOLD


def _is_not_modified(exc: ApiException) -> bool:
    """Является ли ошибка Telegram безобидным «message is not modified»."""
    return "message is not modified" in str(exc).lower()
