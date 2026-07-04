"""
Построение графика отчёта статистики для Telegram-бота.

Рисует отчёт в тёмной теме: бейдж вида отчёта, заголовок, легенда и по
столбцу на каждый порог уверенности. У каждого порога три слоя — "слот"
на всю высоту шкалы (пустые пороги видны, а не исчезают), столбец всех
матчей порога и поверх него акцентный столбец верных прогнозов. Подписи
процентов и счётчиков стоят на общей базовой линии над слотами.

Как выглядит каждый вид отчёта, решает сам модуль: compute помечает
результат полем "kind", а реестр _STYLES сопоставляет виду акцентный цвет
и текст бейджа. Вся визуальная система — фон, трек, акценты, бейджи —
живёт здесь и меняется согласованно.

Возвращает PNG в памяти (BytesIO): на диск ничего не пишется, картинка
живёт только до отправки в канал.

Диаграмма строится на объектном API matplotlib (Figure + холст Agg).
Так построение потокобезопасно (график собирается в потоке планировщика)
и не накапливает фигуры в памяти долго работающего процесса.
"""

# Стандартные библиотеки
from io import BytesIO

# Сторонние библиотеки
import numpy as np
from matplotlib.path import Path
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.patches import PathPatch, FancyBboxPatch

# Шрифт всех надписей, DejaVu Sans поставляется вместе с matplotlib
_FONT = "DejaVu Sans"

# Базовая палитра тёмной темы — общая для всех видов отчётов.
_BG = "#0e1420"      # фон всего полотна
_PANEL = "#161f31"   # "слот" порога на всю высоту шкалы
_TRACK = "#26324b"   # столбец "все матчи порога"
_EDGE = "#232e44"    # обводка слота
_INK = "#eef2f8"     # основной текст
_SUB = "#7a86a0"     # вторичный текст
_MUTED = "#3a465f"   # подписи пустых порогов

# Оформление по видам отчётов (result["kind"] из compute). Вид различается
# двумя признаками: акцентным цветом столбцов верных прогнозов и текстом
# бейджа в шапке.
_STYLES = {
    "daily":         {"accent": "#22b0ff", "tag": "ДЕНЬ"},
    "weekly":        {"accent": "#f7c325", "tag": "НЕДЕЛЯ"},
    "monthly":       {"accent": "#ff4d73", "tag": "МЕСЯЦ"},
    "all_time":      {"accent": "#a06bf5", "tag": "ПАТЧ"},
    "league":        {"accent": "#2ee6a8", "tag": "ЛИГА"},
    "except_league": {"accent": "#ff8a3d", "tag": "БЕЗ ЛИГИ"},
}

# Оформление для неизвестного вида — нейтральное, чтобы новый вид отчёта,
# ещё не внесённый в _STYLES, рендерился, а не ронял публикацию.
_FALLBACK_STYLE = {"accent": "#8ea0c0", "tag": "ОТЧЁТ"}

# Геометрия: ширина столбца и радиус скругления углов в единицах данных.
_BAR_WIDTH = 0.5
_BAR_RADIUS = 0.09


def render(result: dict) -> BytesIO:
    """
    Рисует график отчёта и возвращает его в виде PNG в памяти.

    Args:
        result (dict): Результат compute.build — {"title", "kind", "thresholds"}.
            По "kind" из реестра _STYLES выбирается оформление вида отчёта.

    Returns:
        BytesIO: Буфер с PNG, перемотанный в начало. На диск не пишется.
    """
    style = _STYLES.get(result.get("kind"), _FALLBACK_STYLE)

    thresholds = result["thresholds"]
    x = np.arange(len(thresholds))
    top = max(t["total"] for t in thresholds) or 1

    fig = Figure(figsize=(10, 6), dpi=300)
    FigureCanvasAgg(fig)  # привязывает холст Agg к фигуре для вывода в PNG
    fig.patch.set_facecolor(_BG)

    _draw_header(fig, result["title"], style["accent"], style["tag"])
    _draw_bars(fig, thresholds, x, top, style["accent"])

    buffer = BytesIO()
    fig.savefig(buffer, format="png", facecolor=fig.get_facecolor())
    buffer.seek(0)
    return buffer


# ────────────────────────────────────────────────────────────────────────────
# Шапка
# ────────────────────────────────────────────────────────────────────────────

def _draw_header(fig: Figure, title: str, accent: str, tag: str) -> None:
    """
    Рисует шапку: бейдж вида отчёта, заголовок, подзаголовок и легенду.

    Бейдж — главный различитель видов отчётов наряду с акцентным цветом.
    """
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # Бейдж: ширина подстраивается под длину текста.
    chip_w = 0.022 + len(tag) * 0.0115
    ax.add_patch(FancyBboxPatch((0.06, 0.905), chip_w, 0.048,
                 boxstyle="round,pad=0.004,rounding_size=0.012",
                 mutation_aspect=0.6, facecolor=accent, edgecolor="none"))
    ax.text(0.06 + chip_w / 2, 0.9285, tag, ha="center", va="center",
            fontsize=10.5, fontweight="bold", color=_BG, family=_FONT)

    ax.text(0.06, 0.845, title, fontsize=20, fontweight="bold",
            va="center", color=_INK, family=_FONT)
    ax.text(0.06, 0.788, "Точность прогнозов по порогам уверенности",
            fontsize=11.5, va="center", color=_SUB, family=_FONT)

    # Легенда — в одну строку с подзаголовком, по правому краю.
    ax.text(0.94, 0.788, "верные", ha="right", va="center",
            fontsize=10.5, color=_SUB, family=_FONT)
    ax.add_patch(FancyBboxPatch((0.94 - 0.075, 0.7815), 0.013, 0.013,
                 boxstyle="round,pad=0,rounding_size=0.006",
                 facecolor=accent, edgecolor="none"))
    ax.text(0.94 - 0.095, 0.788, "все матчи порога", ha="right", va="center",
            fontsize=10.5, color=_SUB, family=_FONT)
    ax.add_patch(FancyBboxPatch((0.94 - 0.245, 0.7815), 0.013, 0.013,
                 boxstyle="round,pad=0,rounding_size=0.006",
                 facecolor=_TRACK, edgecolor="none"))


# ────────────────────────────────────────────────────────────────────────────
# Столбцы
# ────────────────────────────────────────────────────────────────────────────

def _draw_bars(fig: Figure, thresholds: list, x, top: int, accent: str) -> None:
    """
    Рисует зону столбцов: слоты порогов, столбцы матчей и подписи.

    Подписи всех порогов стоят на общей базовой линии над слотами; у пустых
    порогов на тех же местах — приглушённые прочерк и надпись "нет матчей", чтобы
    ряд подписей оставался ровным.
    """
    ax = fig.add_axes([0.06, 0.095, 0.88, 0.675])
    ax.set_facecolor(_BG)

    track_top = top * 1.0
    y_percent = track_top + top * 0.135
    y_count = track_top + top * 0.045

    for i, t in enumerate(thresholds):
        ax.add_patch(PathPatch(_rounded_bar(i, track_top),
                     facecolor=_PANEL, edgecolor=_EDGE, linewidth=1, zorder=2))
        if t["total"]:
            ax.add_patch(PathPatch(_rounded_bar(i, t["total"]),
                         facecolor=_TRACK, edgecolor="none", zorder=3))
        if t["correct"]:
            ax.add_patch(PathPatch(_rounded_bar(i, t["correct"]),
                         facecolor=accent, edgecolor="none", zorder=4))

        if t["total"]:
            ax.text(i, y_percent, f"{t['percent_correct']:.0f}%", ha="center",
                    va="bottom", fontsize=19, fontweight="bold",
                    color=_INK, family=_FONT)
            ax.text(i, y_count, f"{t['correct']} из {t['total']}", ha="center",
                    va="bottom", fontsize=10, color=_SUB, family=_FONT)
        else:
            ax.text(i, y_percent, "—", ha="center", va="bottom",
                    fontsize=19, fontweight="bold", color=_MUTED, family=_FONT)
            ax.text(i, y_count, "нет матчей", ha="center", va="bottom",
                    fontsize=10, color=_MUTED, family=_FONT)

    ax.set_xlim(-0.62, len(thresholds) - 0.38)
    ax.set_ylim(0, top * 1.40)
    ax.set_xticks(x, [t["label"] for t in thresholds], fontsize=13.5,
                  color=_INK, family=_FONT)
    ax.set_yticks([])
    ax.tick_params(length=0, pad=10)
    for spine in ax.spines.values():
        spine.set_visible(False)


def _rounded_bar(x: float, height: float) -> Path:
    """
    Path столбца со скруглёнными верхними углами и прямым низом.

    Углы скругляются квадратичными кривыми Безье; радиус ограничивается
    половиной высоты и ширины, чтобы низкие столбцы не деформировались.
    """
    r = min(_BAR_RADIUS, height / 2, _BAR_WIDTH / 2)
    left, right = x - _BAR_WIDTH / 2, x + _BAR_WIDTH / 2
    verts = [(left, 0), (left, height - r),
             (left, height), (left + r, height),
             (right - r, height),
             (right, height), (right, height - r),
             (right, 0), (left, 0)]
    codes = [Path.MOVETO, Path.LINETO,
             Path.CURVE3, Path.CURVE3,
             Path.LINETO,
             Path.CURVE3, Path.CURVE3,
             Path.LINETO, Path.CLOSEPOLY]
    return Path(verts, codes)
