"""
Расчёт статистики точности прогнозов и симуляция ставок.

По набору завершённых матчей для каждого порога уверенности
(DEFAULT_CONFIDENCE_THRESHOLDS) считает долю верных прогнозов и моделирует две
стратегии ставок: фиксированную (постоянная ставка) и банковскую (процент от
текущего банка, с капитализацией). Результат возвращается словарём — рисует
график и постит его в Telegram уже stats_publisher.

Расчёт выполняет compute_stats; обёртки daily/weekly/monthly/all_time/league/
except_league получают для него матчи через api_client и подписывают отчёт
заголовком своего периода. Даты в заголовках выводятся из даты сервера;
когда именно запускать отчёты — забота расписания, не этого модуля.
"""

# Стандартные библиотеки
import logging
from datetime import date, timedelta
from typing import List, Optional

# Локальные импорты
from dota_core.config import DOTA_VERSION

from . import api_client
from . import config

logger = logging.getLogger(__name__)

# Месяцы в родительном падеже — для дат («за 5 января»).
_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

# Месяцы в именительном падеже — для месяца целиком («за Январь»).
_MONTHS_NOMINATIVE = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)

# Пороги уверенности для анализа точности прогнозов.
DEFAULT_CONFIDENCE_THRESHOLDS = [0.5, 0.55, 0.6, 0.65, 0.7]


# ════════════════════════════════════════════════════════════════════════════
# Обёртки по периодам и лигам (точки входа для планировщика и ручных вызовов)
# ════════════════════════════════════════════════════════════════════════════

def daily() -> Optional[dict]:
    """Статистика за прошедшие сутки."""
    matches = api_client.get_last_day()
    if matches is None:
        logger.warning("Дневная статистика: матчи не получены")
        return None
    day = date.today() - timedelta(days=1)
    title = f"Статистика за {day.day} {_MONTHS_GENITIVE[day.month - 1]} {day.year} года"
    return compute_stats(matches, title)


def weekly() -> Optional[dict]:
    """Статистика за прошедшую неделю."""
    matches = api_client.get_last_week()
    if matches is None:
        logger.warning("Недельная статистика: матчи не получены")
        return None
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=6)
    title = (
        f"Статистика с {start.day} {_MONTHS_GENITIVE[start.month - 1]} {start.year} года "
        f"по {end.day} {_MONTHS_GENITIVE[end.month - 1]} {end.year} года"
    )
    return compute_stats(matches, title)


def monthly() -> Optional[dict]:
    """Статистика за прошедший календарный месяц."""
    matches = api_client.get_last_month()
    if matches is None:
        logger.warning("Месячная статистика: матчи не получены")
        return None
    # Последний день предыдущего месяца задаёт месяц и год для заголовка.
    last_day_prev_month = date.today().replace(day=1) - timedelta(days=1)
    month_name = _MONTHS_NOMINATIVE[last_day_prev_month.month - 1].title()
    title = f"Статистика за {month_name} {last_day_prev_month.year} года"
    return compute_stats(matches, title)


def all_time() -> Optional[dict]:
    """Статистика за всё время текущего патча."""
    matches = api_client.get_all_time()
    if matches is None:
        logger.warning("Статистика за всё время: матчи не получены")
        return None
    title = f"Статистика патча {DOTA_VERSION}"
    return compute_stats(matches, title)


def league(league_id: int) -> Optional[dict]:
    """Статистика по указанной лиге."""
    matches = api_client.get_by_league(league_id)
    if matches is None:
        logger.warning("Статистика лиги %s: матчи не получены", league_id)
        return None
    league_name = matches[0]['league_name'] if matches else None
    title = f"Статистика {league_name}" if league_name else f"Статистика лиги {league_id}"
    return compute_stats(matches, title)


def except_league(league_id: int) -> Optional[dict]:
    """
    Статистика по всем лигам, кроме указанной.

    Название исключённой лиги недоступно: в выборку входят матчи других лиг,
    поэтому в заголовке используется её id.
    """
    matches = api_client.get_except_league(league_id)
    if matches is None:
        logger.warning("Статистика без лиги %s: матчи не получены", league_id)
        return None
    title = f"Статистика всех лиг, кроме лиги {league_id}"
    return compute_stats(matches, title)


# ════════════════════════════════════════════════════════════════════════════
# Расчётное ядро
# ════════════════════════════════════════════════════════════════════════════

def compute_stats(matches: List[dict], title: str) -> dict:
    """
    Считает точность прогнозов и симулирует ставки по всем порогам.

    Прогнозом матча считается сторона фаворита: predict_win — вероятность
    победы Radiant, при значении ниже 0.5 фаворит Dire, и уверенность
    отсчитывается в его сторону. Матч учитывается на каждом пороге, который
    его уверенность превышает.

    Матчи без исхода или без прогноза пропускаются, поэтому на вход годится
    и список с live-матчами.

    Args:
        matches (List[dict]): Карточки матчей из MatchSerializer
        title (str): Заголовок отчёта

    Returns:
        dict: Результат отчёта:
            {
                "title": str,                  # заголовок отчёта
                "thresholds": [                # срез по каждому порогу
                    {
                        "threshold": float,        # порог долей, например 0.6
                        "label": str,              # подпись, например ">60%"
                        "total": int,              # сколько матчей прошло порог
                        "correct": int,            # из них с верным прогнозом
                        "percent_correct": float,  # доля верных, проценты (0..100)
                        "fixed_profit": float,     # итог фиксированной стратегии, ₽
                        "bank": float,             # итог банковской стратегии, ₽
                    },
                    ...
                ],
            }
    """
    thresholds = DEFAULT_CONFIDENCE_THRESHOLDS
    n = len(thresholds)

    total = [0] * n
    correct = [0] * n
    fixed_profit = [0.0] * n
    bank = [float(config.BANK_SIZE)] * n

    for match in matches:
        predict = match['predict_win']
        win = match['radiant_win']

        # Только завершённые матчи с прогнозом; незавершённые пропускаем.
        if predict is None or win is None:
            continue

        favored_radiant = predict >= 0.5
        confidence = predict if favored_radiant else 1 - predict
        coefficient = (match['radiant_team_coefficient'] if favored_radiant
                       else match['dire_team_coefficient'])
        hit = favored_radiant == win

        for i, threshold in enumerate(thresholds):
            if confidence < threshold:
                continue
            total[i] += 1
            stake = bank[i] * (config.FIX_PERCENT / 100)
            if hit:
                correct[i] += 1
                fixed_profit[i] += config.FIXED_BID * (coefficient - 1)
                bank[i] += stake * (coefficient - 1)
            else:
                fixed_profit[i] -= config.FIXED_BID
                bank[i] -= stake

    threshold_stats = [
        {
            "threshold": threshold,
            "label": f">{round(threshold * 100)}%",
            "total": total[i],
            "correct": correct[i],
            "percent_correct": round(correct[i] / total[i] * 100, 1) if total[i] else 0.0,
            "fixed_profit": fixed_profit[i],
            "bank": bank[i],
        }
        for i, threshold in enumerate(thresholds)
    ]
    return {"title": title, "thresholds": threshold_stats}


# ════════════════════════════════════════════════════════════════════════════
# Текстовая подпись
# ════════════════════════════════════════════════════════════════════════════

def format_caption(result: dict) -> str:
    """
    Собирает текстовую подпись отчёта.

    Пороги без движения денег в подпись не попадают: нулевой итог
    фиксированной стратегии и нетронутый банк означают, что ставок
    на этом пороге не было — показывать нечего.
    """
    lines = [f"🎊 {result['title']} 🎊"]

    fixed = [t for t in result['thresholds'] if t['fixed_profit'] != 0]
    if fixed:
        lines.append("")
        lines.append(f"💵 Фиксированная ставка {_money(config.FIXED_BID)}₽ 💵")
        lines.extend(f" — {t['label']}:  {_money(t['fixed_profit'])}₽" for t in fixed)

    changed = [t for t in result['thresholds'] if round(t['bank']) != config.BANK_SIZE]
    if changed:
        lines.append("")
        lines.append(f"💰 Банк {_money(config.BANK_SIZE)}₽ при ставке {config.FIX_PERCENT}% 💰")
        lines.extend(f" — {t['label']}:  {_money(t['bank'])}₽" for t in changed)

    return "\n".join(lines)


def _money(value: float) -> str:
    """Округляет до рубля и разделяет тысячи пробелом: 12345.6 → '12 346'."""
    return format(round(value), ',').replace(',', ' ')
