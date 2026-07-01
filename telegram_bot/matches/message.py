"""
Сборка текста сообщения о матче для публикации в Telegram.

Превращает карточку матча в готовую строку. Все функции чистые:
получают данные матча и справочник имён героев, возвращают строку без
сетевых вызовов, обращений к БД и побочных действий.

Сообщение состоит из независимых секций: шапка, ход игры, составы, коэффициенты,
прогноз, исход. Каждую секцию строит своя функция и возвращает блок без пустых
строк по краям. build склеивает блоки, разделяя пустой строкой.

Форматируются только активированные матчи (bet_status=True). У таких матчей
backend уже заполнил коэффициенты, прогнозы и поля хода игры, поэтому функции не
проверяют их на None. Имена героев приходят как hero_id и определяются через
переданный HeroCache.
"""

# Стандартные библиотеки
from typing import List

# Локальные импорты
from dota_core.utils.hero_cache import HeroCache


def _format_mmss(seconds: int) -> str:
    """Переводит секунды в строку вида MM:SS с ведущими нулями."""
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# ────────────────────────────────────────────────────────────────────────────
# Шапка: команды, турнир, победитель, статус
# ────────────────────────────────────────────────────────────────────────────

def _format_header(match: dict) -> str:
    """Заголовок сообщения: команды и номер карты, турнир, победитель, статус."""
    radiant_name = match['radiant_team_name'].strip()
    dire_name = match['dire_team_name'].strip()

    game_number = match['radiant_series_wins'] + match['dire_series_wins'] + 1
    lines = [f"🧀 {radiant_name} vs {dire_name} [Игра №{game_number}] 🧀"]

    if match['league_name']:
        lines.append(f"🏆 {match['league_name'].strip()} 🏆")

    # Победитель есть только у завершённого матча.
    if match['radiant_win'] is not None:
        winner = radiant_name if match['radiant_win'] else dire_name
        lines.append(f"🎉 Победитель {winner} 🎉")

    if match['live_status']:
        lines.append("🟡 Статус: LIVE 🟡")
    else:
        lines.append("🔴 Статус: Завершён 🔴")

    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# Ход игры: время, счёт, ценность/перевес
# ────────────────────────────────────────────────────────────────────────────

def _format_progress(match: dict) -> str:
    """
    Блок хода игры. Содержимое зависит от состояния матча и источника данных:

    - Матч завершён (radiant_win задан) — итоговые цифры.
    - Матч идёт, оба net_worth ненулевые — данные scoreboard: известна ценность
      обеих команд, дополнительно показывается задержка трансляции.
    - Матч идёт, хотя бы одно net_worth нулевое — данные top-live: известен
      только перевес Radiant, разнесённый по сторонам (положительный в
      net_worth_radiant, отрицательный по модулю в net_worth_dire).

    У scoreboard оба поля net_worth всегда ненулевые, поэтому нулевое значение
    однозначно отличает данные top-live от полного scoreboard.
    """
    duration = _format_mmss(match['duration'])
    score = f"{match['radiant_score']} vs {match['dire_score']}"

    # Завершённый матч: итоги.
    if match['radiant_win'] is not None:
        return (
            "[Итоги матча]\n"
            f"⏳ Время: {duration} мин.\n"
            f"⚔️ Счёт: {score}\n"
            f"💰 Ценность: {match['net_worth_radiant']} vs {match['net_worth_dire']}"
        )

    # Полный scoreboard: известна ценность обеих команд.
    if match['net_worth_radiant'] != 0 and match['net_worth_dire'] != 0:
        delay = _format_mmss(match['stream_delay_s'])
        return (
            f"[Задержка: {delay} мин.]\n"
            f"⏳ Время: {duration} мин.\n"
            f"⚔️ Счёт: {score}\n"
            f"💰 Ценность: {match['net_worth_radiant']} vs {match['net_worth_dire']}"
        )

    # Данные top-live: известен только перевес одной из сторон.
    lead = match['net_worth_radiant'] - match['net_worth_dire']
    if lead >= 0:
        advantage = f"💰 Преимущество ☀️ Radiant: {lead}"
    else:
        advantage = f"💰 Преимущество 🌑 Dire: {abs(lead)}"
    return (
        "[Данные из лобби]\n"
        f"⏳ Время: {duration} мин.\n"
        f"⚔️ Счёт: {score}\n"
        f"{advantage}"
    )


# ────────────────────────────────────────────────────────────────────────────
# Составы команд
# ────────────────────────────────────────────────────────────────────────────

def _format_roster(emoji: str,
                   side: str,
                   team_name: str,
                   players: List[dict],
                   hero_cache: HeroCache) -> str:
    """
    Состав одной стороны: заголовок и строки "герой (никнейм)".

    Имя героя берётся по hero_id из HeroCache. Для неизвестного id кэш сам
    возвращает заглушку, поэтому проверять id здесь не нужно.
    """
    lines = [f"{emoji} {side} ({team_name.strip()}):"]
    for player in players:
        hero_name = hero_cache.get_hero_name(player['hero_id'])
        lines.append(f" — {hero_name} ({player['nickname'].strip()})")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# Коэффициенты
# ────────────────────────────────────────────────────────────────────────────

def _format_coefficients(match: dict) -> str:
    """Букмекерские коэффициенты на обе команды."""
    return (
        "📈 Коэффициенты:\n"
        f" ● {match['radiant_team_name'].strip()}: {match['radiant_team_coefficient']}\n"
        f" ● {match['dire_team_name'].strip()}: {match['dire_team_coefficient']}"
    )


# ────────────────────────────────────────────────────────────────────────────
# Прогноз модели: время и счёт
# ────────────────────────────────────────────────────────────────────────────

def _format_prediction(match: dict) -> str:
    """Прогноз длительности матча и счёта обеих команд."""
    predict_time = _format_mmss(match['predict_time'])
    radiant_score = int(match['predict_radiant_score'])
    dire_score = int(match['predict_dire_score'])
    return (
        f"⏳ Предполагаемое время: {predict_time} мин.\n"
        f"⚔️ Предполагаемый счёт: {radiant_score} vs {dire_score}"
    )


# ────────────────────────────────────────────────────────────────────────────
# Исход: вероятность победы и верность прогноза
# ────────────────────────────────────────────────────────────────────────────

def _format_outcome(match: dict) -> str:
    """
    Вероятность победы фаворита, а для завершённого матча отметка о том, сбылся ли прогноз.
    """
    predict_win = match['predict_win']
    radiant_name = match['radiant_team_name'].strip()
    dire_name = match['dire_team_name'].strip()

    if predict_win >= 0.5:
        favourite, probability = radiant_name, predict_win * 100
    else:
        favourite, probability = dire_name, (1 - predict_win) * 100
    lines = [f"💊 Вероятность победы {favourite}: {probability:.2f}% 💊"]

    # Оценка появляется только когда исход известен.
    if match['radiant_win'] is not None:
        predicted_radiant = predict_win >= 0.5
        if predicted_radiant == match['radiant_win']:
            lines.append("🍻 Предсказание верно! 🍻")
        else:
            lines.append("🙀 Предсказание не верно... 🙀")

    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# Сборка сообщения
# ────────────────────────────────────────────────────────────────────────────

def build(match: dict, hero_cache: HeroCache) -> str:
    """
    Собирает полный текст сообщения о матче.

    Args:
        match (dict): Карточка матча из MatchSerializer
        hero_cache (HeroCache): Готовый справочник имён героев

    Returns:
        str: Текст сообщения для отправки или редактирования в Telegram
    """
    sections = [
        _format_header(match),
        _format_progress(match),
        _format_roster("☀️", "Radiant", match['radiant_team_name'], match['radiant_players'], hero_cache),
        _format_roster("🌑", "Dire", match['dire_team_name'], match['dire_players'], hero_cache),
        _format_coefficients(match),
        _format_prediction(match),
        _format_outcome(match),
    ]

    # Между секциями остаётся пустая строка, блоки возвращаются без неё по краям.
    return "\n\n".join(sections)
