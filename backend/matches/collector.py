"""
Сборщик live-матчей Dota 2: единственная точка наполнения БД.

Каждый вызов run() выполняет один проход по линии live-матчей. Обработка разделена на четыре стадии:
    1. Steam-клиент — получение сырых данных из публичного API.
    2. Обработка live-матчей — обход матчей по их состоянию ставки.
    3. Завершение матчей — поиск результата завершённых матчей по истории.
    4. Запись в БД — создание и обновление Match/Player/MatchPublication.

Источники данных:
    - GetLiveLeagueGames — основной источник, но с задержкой; отдаёт драфт и
      состав.
    - GetTopLiveGame (partner=1,2) — данные реального времени, но матч может
      отсутствовать; имеет приоритет при обновлении хода активного матча.

Состояния матча по полю bet_status:
    нет в БД — новый матч: ждём полный драфт, затем ищем событие в линии Winline.
    False    — события в линии Winline нет, ставок не будет, на следующих
               проходах матч пропускается.
    None     — событие найдено (winline_event_id записан), но коэффициенты ещё
               закрыты, каждый проход пробуем их получить.
    True     — коэффициенты получены, прогноз построен, матч активен; обновляем
               счёт и net worth по ходу игры.

Статические данные и модели поднимаются на старте сервера.

Публичные точки входа, вызываемые из views.py:
    - run()              — один проход опроса live-линии.
    - finish_completed() — один проход поиска результатов завершённых матчей.

Порядок функций в файле — сверху вниз по стадиям обработки; внутри стадии
вызывающая функция идёт перед вызываемыми, а маленькие помощники — сразу за
функцией, которая ими пользуется.
"""

# Стандартные библиотеки
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

# Сторонние библиотеки
import requests
from django.db import transaction
from django.utils import timezone

# Локальные импорты
from dota_core import config as core_config
from dota_core.config import RADIANT_INDEX, DIRE_INDEX, TEAM_SIZE

from . import shared_resources
from .inference import match_predictor
from .bookmakers import winline_parser
from .bookmakers.winline_parser import WinlineUnavailableError
from .models import Match, Player, MatchPublication

logger = logging.getLogger(__name__)

# Ключ Steam берётся из конфигурации окружения.
_STEAM_KEY = core_config.STEAM_API_KEY

# Полные адреса Steam API.
LIVE_LEAGUE_GAMES_URL = f"https://api.steampowered.com/IDOTA2Match_570/GetLiveLeagueGames/v1?key={_STEAM_KEY}"
TOP_LIVE_GAMES_URL_1 = f"https://api.steampowered.com/IDOTA2Match_570/GetTopLiveGame/v1/?partner=1&key={_STEAM_KEY}"
TOP_LIVE_GAMES_URL_2 = f"https://api.steampowered.com/IDOTA2Match_570/GetTopLiveGame/v1/?partner=2&key={_STEAM_KEY}"
MATCH_HISTORY_URL = f"https://api.steampowered.com/IDOTA2Match_570/GetMatchHistory/V001/?matches_requested=100&key={_STEAM_KEY}"
MATCH_HISTORY_BY_SEQ_URL = f"https://api.steampowered.com/IDOTA2Match_570/GetMatchHistoryBySequenceNum/V001/?key={_STEAM_KEY}"

# Сетевые параметры запросов к Steam.
REQUEST_TIMEOUT = 30   # секунд на ответ

# Ключи, без которых live-матч не имеет смысла обрабатывать.
REQUIRED_KEYS = frozenset((
    "match_id", "league_id", "radiant_team", "dire_team", "players",
    "stream_delay_s", "radiant_series_wins", "dire_series_wins",
))

# ────────────────────────────────────────────────────────────────────────────
# Стадия 1. Steam-клиент: получение сырых данных
# ────────────────────────────────────────────────────────────────────────────

def _redact_key(text: str) -> str:
    """Скрывает Steam API ключ в строке перед записью в лог."""
    return text.replace(_STEAM_KEY, "***")


def _get_json(url: str) -> Optional[dict]:
    """
    Выполняет GET-запрос и возвращает разобранный JSON либо None при неудаче.

    Args:
        url (str): Полный адрес запроса

    Returns:
        Optional[dict]: Тело ответа как dict или None
    """
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.warning("Steam API | запрос не удался: %s", _redact_key(str(exc)))
        return None


def _fetch_sources() -> Optional[Tuple[List[dict], List[List[dict]]]]:
    """
    Параллельно запрашивает три источника live-данных.

    Returns:
        Optional[Tuple[List[dict], List[List[dict]]]]: Список live-league матчей
            и список вторичных источников (top-live), либо None, если основной
            источник недоступен
    """
    with ThreadPoolExecutor() as executor:
        f_live = executor.submit(_get_json, LIVE_LEAGUE_GAMES_URL)
        f_top1 = executor.submit(_get_json, TOP_LIVE_GAMES_URL_1)
        f_top2 = executor.submit(_get_json, TOP_LIVE_GAMES_URL_2)
        live_raw, top1_raw, top2_raw = f_live.result(), f_top1.result(), f_top2.result()

    if live_raw is None:
        logger.error("Основной источник live-league недоступен — проход прерван")
        return None

    live_games = live_raw.get("result", {}).get("games", [])
    secondary = [
        (top1_raw or {}).get("game_list", []),
        (top2_raw or {}).get("game_list", []),
    ]
    return live_games, secondary


# ────────────────────────────────────────────────────────────────────────────
# Стадия 2. Проход live-линии: маршрутизация матчей по состоянию ставки
# ────────────────────────────────────────────────────────────────────────────

def run() -> Dict[str, int]:
    """
    Выполняет один проход опроса live-линии Dota 2.

    Returns:
        Dict[str, int]: Краткая сводка прохода для ответа view
    """
    sources = _fetch_sources()
    if sources is None:
        return {"processed": 0, "touched": 0}

    live_games, secondary = sources
    processed = touched = 0

    for game in live_games:
        processed += 1
        try:
            if _process_live_game(game, secondary):
                touched += 1
        except Exception:
            # Ошибка на одном матче не должна прерывать весь проход.
            logger.exception("Сбой обработки матча %s", game.get("match_id"))

    logger.info("Проход завершён: получено %d, обработано %d", processed, touched)
    return {"processed": processed, "touched": touched}


def _process_live_game(game: dict, secondary: List[List[dict]]) -> bool:
    """
    Направляет матч в ветку нового или уже известного.

    Returns:
        bool: True, если матч был записан или обновлён
    """
    if not REQUIRED_KEYS.issubset(game.keys()) or game["match_id"] == 0:
        return False

    match = Match.objects.filter(match_id=game["match_id"]).first()
    if match is None:
        return _process_new_match(game)
    return _process_known_match(match, game, secondary)


def _process_known_match(match: Match, game: dict, secondary: List[List[dict]]) -> bool:
    """
    Обрабатывает уже известный БД матч в зависимости от его bet_status.

    Returns:
        bool: True, если матч обработан
    """
    # Ставок не будет — матч больше не трогаем.
    if match.bet_status is False:
        return False
    # Активный матч — обновляем ход игры.
    if match.bet_status is True:
        _update_progress(match.match_id, game, secondary)
        return True
    # bet_status is None — событие найдено, ждём коэффициенты.
    _try_activate(match, game)
    return True


def _process_new_match(game: dict) -> bool:
    """
    Обрабатывает матч, которого ещё нет в БД.

    Запись делается только после полного драфта. По названиям команд ищется
    событие в линии Winline: если события нет — матч сохраняется с
    bet_status=False; если есть — фиксируется winline_event_id, заводится
    предварительная запись (bet_status=None) и сразу же делается попытка получить
    коэффициенты, чтобы при удаче активировать матч в этом же проходе.

    Returns:
        bool: True, если матч записан
    """
    if not _is_full_draft(game):
        return False

    radiant_name = game["radiant_team"]["team_name"]
    dire_name = game["dire_team"]["team_name"]
    try:
        event_id = winline_parser.resolve_event_id(radiant_name, dire_name)
    except WinlineUnavailableError as exc:
        # Линию не удалось проверить — матч не записываем вовсе, следующий проход увидит его как новый.
        logger.warning("Матч %s: Winline недоступен, отложено до следующего прохода (%s)",
                       game["match_id"], exc)
        return False

    if event_id is None:
        # Матча нет в линии Winline — ставок не будет.
        _persist_skeleton(game, radiant_name, dire_name, event_id=None, bet_status=False)
        return True

    # Событие найдено: фиксируем его и сразу пробуем активировать матч.
    match = _persist_skeleton(game, radiant_name, dire_name, event_id=event_id, bet_status=None)
    _try_activate(match, game)
    return True


def _is_full_draft(game: dict) -> bool:
    """Завершён ли драфт: выбрано по TEAM_SIZE героев на каждую сторону."""
    picked = sum(1 for p in game["players"] if p.get("hero_id", 0) != 0)
    return picked == TEAM_SIZE * 2


# ────────────────────────────────────────────────────────────────────────────
# Стадия 2a. Активация ставки: коэффициенты, прогноз, состав
# ────────────────────────────────────────────────────────────────────────────

def _try_activate(match: Match, game: dict) -> None:
    """
    Пытается перевести матч из ожидания коэффициентов в активный.

    Номер карты в серии определяется по сохранённым победам обеих команд. Если
    коэффициенты ещё закрыты, матч остаётся в ожидании до следующего прохода.
    """
    map_number = match.radiant_series_wins + match.dire_series_wins + 1
    try:
        coefs = winline_parser.get_coefficients(
            match.winline_event_id, match.radiant_team_name, match.dire_team_name, map_number,
        )
    except WinlineUnavailableError as exc:
        # Страницу события не удалось проверить; bet_status остаётся None, следующий проход повторит попытку.
        logger.warning("Матч %s: Winline недоступен, активация отложена (%s)",
                       match.match_id, exc)
        return
    if coefs is None:
        return  # коэффициенты ещё не выставлены

    radiant_players, dire_players = _split_players(game["players"])
    predictions = _predict(radiant_players, dire_players)
    if predictions is None:
        # Коэффициенты есть, но прогноз не строится из-за исключённого или
        # неизвестного героя. Для исключённого героя это ожидание не завершится.
        logger.warning("Матч %s: коэффициенты есть, но прогноз не построен", match.match_id)
        return

    _activate_match(match, coefs, predictions, radiant_players, dire_players)


def _split_players(players: List[dict]) -> Tuple[List[dict], List[dict]]:
    """
    Разбивает игроков матча на составы Radiant и Dire.

    Кастеры и админы (team не равен 0 или 1) отбрасываются.

    Returns:
        Tuple[List[dict], List[dict]]: Составы (radiant, dire)
    """
    radiant: List[dict] = []
    dire: List[dict] = []
    for p in players:
        team = p.get("team")
        if team == 0:
            side = RADIANT_INDEX
        elif team == 1:
            side = DIRE_INDEX
        else:
            continue  # кастеры и админы

        record = {
            "account_id": p["account_id"],
            "nickname": p["name"],
            "team_number": side,
            "hero_id": p["hero_id"],
            "hero_variant": p.get("hero_variant", 0),
        }
        (radiant if side == RADIANT_INDEX else dire).append(record)
    return radiant, dire


def _predict(radiant_players: List[dict], dire_players: List[dict]) -> Optional[Dict[str, float]]:
    """
    Считает прогнозы по составам через match_predictor.

    Маппер и модели берутся из shared_resources.

    Returns:
        Optional[Dict[str, float]]: Прогнозы под поля Match, либо None, если
            состав не удалось перевести в индексы (неизвестный hero_id)
    """
    radiant_ids = [p["hero_id"] for p in radiant_players]
    dire_ids = [p["hero_id"] for p in dire_players]
    try:
        return match_predictor.predict(
            radiant_ids,
            dire_ids,
            shared_resources.get_hero_mapper(),
            shared_resources.get_models(),
        )
    except ValueError as exc:
        logger.warning("Прогноз пропущен: %s", exc)
        return None


# ────────────────────────────────────────────────────────────────────────────
# Стадия 2b. Обновление хода активного матча (счёт и net worth)
# ────────────────────────────────────────────────────────────────────────────

def _update_progress(match_id: int, game: dict, secondary: List[List[dict]]) -> None:
    """
    Обновляет счёт и net worth активного матча.

    Приоритет у top-live (реальное время); если матча там нет, берётся
    scoreboard основного источника (с задержкой).
    """
    if _update_from_secondary(match_id, secondary):
        return
    _update_from_scoreboard(match_id, game)


def _update_from_secondary(match_id: int, secondary: List[List[dict]]) -> bool:
    """
    Обновляет ход игры по вторичным источникам (top-live), если матч там есть.

    Top-live отдаёт не суммарную ценность команд, а перевес Radiant
    (radiant_lead), который может быть отрицательным при лидерстве Dire. Поскольку
    оба поля net_worth неотрицательны, перевес зеркалится по сторонам: при лидерстве
    Radiant он кладётся в net_worth_radiant (net_worth_dire=0), при лидерстве Dire —
    наоборот. Знак, таким образом, кодируется тем, какое из полей ненулевое, а
    их разность net_worth_radiant - net_worth_dire равна исходному radiant_lead.

    Returns:
        bool: True, если матч найден во вторичном источнике и обновлён
    """
    for source in secondary:
        for entry in source:
            if int(entry.get("match_id", 0)) != match_id:
                continue
            try:
                lead = entry["radiant_lead"]
                _write_progress(
                    match_id,
                    duration=entry["game_time"],
                    radiant_score=entry["radiant_score"],
                    dire_score=entry["dire_score"],
                    net_worth_radiant=max(lead, 0),
                    net_worth_dire=max(-lead, 0),
                )
            except KeyError as exc:
                logger.warning("Матч %s: неполные данные top-live, поле %s", match_id, exc)
            return True
    return False


def _update_from_scoreboard(match_id: int, game: dict) -> None:
    """Обновляет счёт и net worth по scoreboard основного источника."""
    try:
        board = game["scoreboard"]
        radiant, dire = board["radiant"], board["dire"]
        _write_progress(
            match_id,
            duration=board["duration"],
            radiant_score=radiant["score"],
            dire_score=dire["score"],
            net_worth_radiant=sum(p["net_worth"] for p in radiant["players"]),
            net_worth_dire=sum(p["net_worth"] for p in dire["players"]),
        )
    except KeyError as exc:
        logger.warning("Матч %s: неполный scoreboard, пропущено поле %s", match_id, exc)


# ────────────────────────────────────────────────────────────────────────────
# Стадия 3. Завершение матчей: поиск результата по истории Steam
# ────────────────────────────────────────────────────────────────────────────

def finish_completed() -> Dict[str, int]:
    """
    Выполняет один проход поиска результатов завершённых матчей.

    Берёт матчи со ставкой (bet_status=True) без известного исхода
    (radiant_win is null), находит их в истории Steam по seq_num и проставляет
    финальные данные.

    Returns:
        Dict[str, int]: Сводка прохода
    """
    pending = Match.objects.filter(bet_status=True, radiant_win__isnull=True)
    checked = finished = 0

    for match in pending:
        checked += 1
        try:
            if _try_finish_match(match):
                finished += 1
        except Exception:
            logger.exception("Сбой при завершении матча %s", match.match_id)

    logger.info("Поиск результатов завершён: проверено %d, закрыто %d", checked, finished)
    return {"checked": checked, "finished": finished}


def _try_finish_match(match: Match) -> bool:
    """
    Пытается найти и записать результат одного матча.

    Сначала по игрокам матча ищется его seq_num в истории лиги, затем по seq_num
    запрашиваются детали и обновляется матч.

    Returns:
        bool: True, если результат найден и сохранён
    """
    seq_num = _find_match_seq_num(match)
    if seq_num is None:
        logger.info("Матч %s ещё не найден в истории Steam", match.match_id)
        return False

    history = _get_json(f"{MATCH_HISTORY_BY_SEQ_URL}&start_at_match_seq_num={seq_num}")
    result = (history or {}).get("result", {})
    if result.get("status") != 1:
        return False

    matches = result.get("matches", [])
    if not matches:
        return False

    _write_completed(match, matches[0])
    return True


def _find_match_seq_num(match: Match) -> Optional[int]:
    """
    Ищет seq_num матча, перебирая историю по каждому из его игроков.

    Returns:
        Optional[int]: seq_num или None, если матча ещё нет ни в одной истории
    """
    for player in match.players.all():
        url = f"{MATCH_HISTORY_URL}&league_id={match.league_id}&account_id={player.account_id}"
        history = _get_json(url)
        result = (history or {}).get("result", {})
        if result.get("status") != 1:
            continue
        for entry in result.get("matches", []):
            if entry.get("match_id") == match.match_id:
                return entry.get("match_seq_num")
    return None


def _write_completed(match: Match, details: dict) -> None:
    """Проставляет финальные поля матча из деталей Steam."""
    if "players" not in details:
        return

    net_worth_radiant = sum(p["net_worth"] for p in details["players"] if p["team_number"] == 0)
    net_worth_dire = sum(p["net_worth"] for p in details["players"] if p["team_number"] == 1)

    match.radiant_win = details["radiant_win"]
    match.live_status = False
    match.duration = details["duration"]
    match.radiant_score = details["radiant_score"]
    match.dire_score = details["dire_score"]
    match.net_worth_radiant = net_worth_radiant
    match.net_worth_dire = net_worth_dire
    match.end_time = timezone.now()
    match.save(update_fields=(
        "radiant_win", "live_status", "duration", "radiant_score",
        "dire_score", "net_worth_radiant", "net_worth_dire", "end_time",
    ))
    logger.info("Матч %s закрыт (radiant_win=%s)", match.match_id, match.radiant_win)


# ────────────────────────────────────────────────────────────────────────────
# Стадия 4. Запись в БД: предзапись, активация, игроки, ход игры
# ────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def _persist_skeleton(game: dict,
                      radiant_name: str,
                      dire_name: str,
                      *,
                      event_id: Optional[str],
                      bet_status: Optional[bool]) -> Match:
    """
    Создаёт или обновляет предварительную запись матча до получения коэффициентов.

    Покрывает оба неактивных состояния: bet_status=False (ставок не будет) и
    bet_status=None (ждём коэффициенты, winline_event_id уже известен). Прогнозы
    и коэффициенты заполняются позже, в _activate_match.

    Поля, фиксируемые однократно при создании (start_time, нулевые счётчики хода
    игры), передаются через create_defaults и не перезаписываются при обновлении.
    """

    # Доменные поля, нужные и при создании, и при обновлении. Важно: при наличии
    # create_defaults Django на вставке берёт только его, а defaults применяется
    # лишь при обновлении. Поэтому общие поля кладём в оба словаря, а в
    # create_defaults добавляем сверху лишь то, что фиксируется один раз.
    common = {
        "league_id": game["league_id"],
        "league_name": shared_resources.get_league_cache().get_league_name(game["league_id"]),
        "radiant_team_id": game["radiant_team"]["team_id"],
        "radiant_team_name": radiant_name,
        "dire_team_id": game["dire_team"]["team_id"],
        "dire_team_name": dire_name,
        "stream_delay_s": game["stream_delay_s"],
        "radiant_series_wins": game["radiant_series_wins"],
        "dire_series_wins": game["dire_series_wins"],
        "live_status": None,
        "bet_status": bet_status,
        "winline_event_id": event_id,
        "radiant_team_coefficient": None,
        "dire_team_coefficient": None,
        "predict_win": None,
        "predict_time": None,
        "predict_radiant_score": None,
        "predict_dire_score": None,
    }
    match, _ = Match.objects.update_or_create(
        match_id=game["match_id"],
        create_defaults={
            **common,
            "start_time": timezone.now(),
            "end_time": None,
            "duration": 0,
            "radiant_score": 0,
            "dire_score": 0,
            "net_worth_radiant": 0,
            "net_worth_dire": 0,
        },
        defaults=common,
    )

    # Запись публикации заводится сразу, чтобы телеграм-эндпоинты обновляли
    # существующий объект, а не искали отсутствующий.
    MatchPublication.objects.get_or_create(match=match)
    return match


@transaction.atomic
def _activate_match(match: Match,
                    coefs: Tuple[float, float],
                    predictions: Dict[str, float],
                    radiant_players: List[dict],
                    dire_players: List[dict]) -> None:
    """
    Переводит матч в активное состояние: проставляет коэффициенты, прогнозы и
    состав, ставит bet_status=True и live_status=True.
    """
    radiant_coef, dire_coef = coefs
    Match.objects.filter(match_id=match.match_id).update(
        live_status=True,
        bet_status=True,
        radiant_team_coefficient=radiant_coef,
        dire_team_coefficient=dire_coef,
        predict_win=predictions["predict_win"],
        predict_time=predictions["predict_time"],
        predict_radiant_score=predictions["predict_radiant_score"],
        predict_dire_score=predictions["predict_dire_score"],
    )
    for record in (*radiant_players, *dire_players):
        _persist_player(match, record)
    logger.info("Матч %s активирован (ставка открыта)", match.match_id)


def _persist_player(match: Match, record: dict) -> None:
    """
    Создаёт или обновляет одного игрока матча.

    Ключ поиска — (match, account_id), остальные поля идут в defaults и
    обновляются, чтобы смена героя в драфте не создавала дубликат.
    """
    Player.objects.update_or_create(
        match=match,
        account_id=record["account_id"],
        defaults={
            "nickname": record["nickname"],
            "team_number": record["team_number"],
            "hero_id": record["hero_id"],
            "hero_variant": record["hero_variant"],
        },
    )


def _write_progress(match_id: int, **fields: int) -> None:
    """Точечно обновляет поля хода игры у существующего матча."""
    Match.objects.filter(match_id=match_id).update(**fields)
