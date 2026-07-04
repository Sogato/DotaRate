"""
Сборка текстовой подписи отчёта статистики для публикации в Telegram.

Превращает результат compute.build в готовую подпись к графику. Функции
чистые: получают данные отчёта, возвращают строку без сетевых вызовов
и побочных действий.

Подпись размечена HTML (жирный заголовок и суммы), поэтому отправлять её
нужно с parse_mode="HTML". Заголовок экранируется: в него попадают
названия лиг — произвольный текст.
"""

# Стандартные библиотеки
import html

# Локальные импорты
from .. import config

# Эмодзи заголовка подписи по видам отчётов.
_KIND_EMOJI = {
    "daily": "☀️",
    "weekly": "🗓",
    "monthly": "🌕",
    "all_time": "⚙️",
    "league": "🏆",
    "except_league": "🎭",
}

# Эмодзи для неизвестного вида.
_FALLBACK_EMOJI = "🎊"


def format_caption(result: dict) -> str:
    """
    Собирает текстовую подпись отчёта.

    Итог фиксированной стратегии — это дельта, она выводится со знаком.
    У банковской главное — итоговая сумма (жирным), её дельта от стартового
    банка уточняется в скобках.

    Пороги без движения денег в подпись не попадают: нулевой итог
    фиксированной стратегии и нетронутый банк означают, что ставок
    на этом пороге не было — показывать нечего.

    Args:
        result (dict): Результат compute.build — {"title", "kind", "thresholds"}

    Returns:
        str: HTML-текст подписи к графику отчёта (parse_mode="HTML")
    """
    emoji = _KIND_EMOJI.get(result.get("kind"), _FALLBACK_EMOJI)
    lines = [f"{emoji} <b>{html.escape(result['title'])}</b> {emoji}"]

    fixed = [t for t in result['thresholds'] if t['fixed_profit'] != 0]
    if fixed:
        lines.append("")
        lines.append(f"💵 Фиксированная ставка {_money(config.FIXED_BID)}₽")
        lines.extend(f" — {t['label']}: <b>{_signed_money(t['fixed_profit'])}₽</b>"
                     for t in fixed)

    changed = [t for t in result['thresholds'] if round(t['bank']) != config.BANK_SIZE]
    if changed:
        lines.append("")
        lines.append(f"💰 Банк {_money(config.BANK_SIZE)}₽ при ставке {config.FIX_PERCENT}%")
        lines.extend(f" — {t['label']}: <b>{_money(t['bank'])}₽</b> "
                     f"({_signed_money(t['bank'] - config.BANK_SIZE)}₽)"
                     for t in changed)

    return "\n".join(lines)


def _money(value: float) -> str:
    """Округляет до рубля и разделяет тысячи пробелом: 12345.6 → '12 346'."""
    return format(round(value), ',').replace(',', ' ')


def _signed_money(value: float) -> str:
    """
    Как _money, но всегда со знаком: +520, −180.

    Минус — математический (U+2212): он одной ширины с плюсом, и колонка
    знаков в подписи не пляшет.
    """
    amount = _money(abs(value))
    return f"+{amount}" if round(value) >= 0 else f"−{amount}"
