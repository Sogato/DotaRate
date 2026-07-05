"""
Сборка текста сообщения о матче для публикации в Telegram.

Превращает карточку матча в готовую строку. Все функции чистые:
получают данные матча, возвращают строку без сетевых вызовов, обращений
к БД и побочных действий.

Текст размечен HTML, поэтому отправлять и редактировать его нужно
с parse_mode="HTML". Всё, что приходит из данных матча текстом — названия
команд и лиг, имена героев, ники, — экранируется через _esc, чтобы
произвольный символ '<' в нике не ломал разметку.

Сообщение состоит из независимых секций: шапка, ход игры, составы, коэффициенты,
прогноз, исход. Каждую секцию строит своя функция и возвращает блок без пустых
строк по краям. build склеивает блоки, разделяя пустой строкой.

Форматируются только активированные матчи (bet_status=True). У таких матчей
backend уже заполнил коэффициенты, прогнозы и поля хода игры, поэтому функции не
проверяют их на None.
"""

# Стандартные библиотеки
import html
from typing import List


def _esc(text: str) -> str:
    """Экранирует HTML в произвольном тексте (команды, лиги, герои, ники)."""
    return html.escape(str(text).strip())


def _format_mmss(seconds: int) -> str:
    """Переводит секунды в строку вида MM:SS с ведущими нулями."""
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _format_money(value: float) -> str:
    """Разделяет тысячи пробелом: 130939 → '130 939'."""
    return format(int(value), ',').replace(',', ' ')


# ────────────────────────────────────────────────────────────────────────────
# Шапка: команды, турнир, победитель, статус
# ────────────────────────────────────────────────────────────────────────────

def _format_header(match: dict) -> str:
    """Заголовок сообщения: команды и номер карты, турнир, победитель, статус."""
    radiant_name = _esc(match['radiant_team_name'])
    dire_name = _esc(match['dire_team_name'])

    map_number = match['radiant_series_wins'] + match['dire_series_wins'] + 1
    lines = [f"🧀 <b>{radiant_name} vs {dire_name}</b> [Карта {map_number}] 🧀"]

    if match['league_name']:
        lines.append(f"🏆 {_esc(match['league_name'])}")

    # Победитель есть только у завершённого матча.
    if match['radiant_win'] is not None:
        winner = radiant_name if match['radiant_win'] else dire_name
        lines.append(f"🎉 Победитель <b>{winner}</b>")
        lines.append("🏁 <b>Матч окончен</b> 🏁")
    else:
        # Задержка трансляции известна только у данных полного scoreboard
        # (у top-live поле нулевое) и уточняет статус внутри эмодзи-рамки.
        delay = (f" (задержка {_format_mmss(match['stream_delay_s'])})"
                 if match['stream_delay_s'] else "")
        lines.append(f"🔴 Статус: LIVE{delay} 🔴")

    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# Ход игры: время, счёт, нетворс/перевес
# ────────────────────────────────────────────────────────────────────────────

def _format_progress(match: dict) -> str:
    """
    Блок хода игры: время, счёт и третья строка по источнику данных.

    - Матч завершён (radiant_win задан) или идёт с полным scoreboard
      (оба net_worth ненулевые) — нетворс обеих команд.
    - Матч идёт на данных top-live (хотя бы одно net_worth нулевое) —
      известен только перевес Radiant, разнесённый по сторонам
      (положительный в net_worth_radiant, отрицательный по модулю в
      net_worth_dire); показывается перевес лидирующей стороны.

    У scoreboard оба поля net_worth всегда ненулевые, поэтому нулевое значение
    однозначно отличает данные top-live от полного scoreboard.
    """
    lines = [
        f"⌛️ Время: <b>{_format_mmss(match['duration'])}</b>",
        f"⚔️ Счёт: <b>{match['radiant_score']} vs {match['dire_score']}</b>",
    ]

    finished = match['radiant_win'] is not None
    scoreboard = match['net_worth_radiant'] != 0 and match['net_worth_dire'] != 0
    if finished or scoreboard:
        lines.append(f"💰 Нетворс: <b>{_format_money(match['net_worth_radiant'])} / "
                     f"{_format_money(match['net_worth_dire'])}</b>")
    else:
        lead = match['net_worth_radiant'] - match['net_worth_dire']
        side = "☀️ Radiant" if lead >= 0 else "🌑 Dire"
        lines.append(f"💰 Перевес {side}: {_format_money(abs(lead))}")

    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# Составы команд
# ────────────────────────────────────────────────────────────────────────────

def _format_roster(emoji: str,
                   side: str,
                   team_name: str,
                   players: List[dict]) -> str:
    """
    Состав одной стороны: заголовок и строки "герой (никнейм)".

    Ник набран моноширинным (<code>): визуально отделяется от героя, а тап по нему в Telegram копирует ник.
    """
    lines = [f"{emoji} {side} (<b>{_esc(team_name)}</b>):"]
    for player in players:
        lines.append(f" — {_esc(player['hero_name'])} "
                     f"(<code>{_esc(player['nickname'])}</code>)")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# Коэффициенты
# ────────────────────────────────────────────────────────────────────────────

def _format_coefficients(match: dict) -> str:
    """Букмекерские коэффициенты на обе команды."""
    return (
        "🎲 Коэффициенты:\n"
        f" ● {_esc(match['radiant_team_name'])}: <b>{match['radiant_team_coefficient']}</b>\n"
        f" ● {_esc(match['dire_team_name'])}: <b>{match['dire_team_coefficient']}</b>"
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
        f"⌛️ Ожидаемое время: <b>{predict_time}</b>\n"
        f"⚔️ Ожидаемый счёт: <b>{radiant_score} vs {dire_score}</b>"
    )


# ────────────────────────────────────────────────────────────────────────────
# Исход: вероятность победы и верность прогноза
# ────────────────────────────────────────────────────────────────────────────

def _format_outcome(match: dict) -> str:
    """
    Вероятность победы фаворита, а для завершённого матча отметка о том, сбылся ли прогноз.
    """
    predict_win = match['predict_win']
    radiant_name = _esc(match['radiant_team_name'])
    dire_name = _esc(match['dire_team_name'])

    if predict_win >= 0.5:
        favourite, probability = radiant_name, predict_win * 100
    else:
        favourite, probability = dire_name, (1 - predict_win) * 100
    lines = [f"🔮 Вероятность победы <b>{favourite}</b>: <b>{probability:.0f}%</b>"]

    # Оценка появляется только когда исход известен.
    if match['radiant_win'] is not None:
        predicted_radiant = predict_win >= 0.5
        if predicted_radiant == match['radiant_win']:
            lines.append("🍻 Предсказание верно!")
        else:
            lines.append("🫠 Предсказание не верно...")

    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# Сборка сообщения
# ────────────────────────────────────────────────────────────────────────────

def build(match: dict) -> str:
    """
    Собирает полный текст сообщения о матче.

    Args:
        match (dict): Карточка матча из MatchSerializer

    Returns:
        str: HTML-текст сообщения для отправки или редактирования в Telegram
    """
    sections = [
        _format_header(match),
        _format_progress(match),
        _format_roster("☀️", "Radiant", match['radiant_team_name'], match['radiant_players']),
        _format_roster("🌑", "Dire", match['dire_team_name'], match['dire_players']),
        _format_coefficients(match),
        _format_prediction(match),
        _format_outcome(match),
    ]

    # Между секциями остаётся пустая строка, блоки возвращаются без неё по краям.
    return "\n\n".join(sections)
