"""
Построение графика отчёта статистики для Telegram-бота.

Рисует одну столбчатую диаграмму точности прогнозов по порогам уверенности и
возвращает PNG в памяти (BytesIO): на диск ничего не пишется, картинка живёт
только до отправки в канал.

Диаграмма строится на объектном API matplotlib (Figure + холст Agg).
Так построение потокобезопасно (график собирается в потоке планировщика)
и не накапливает фигуры в памяти долго работающего процесса.
"""

# Стандартные библиотеки
from io import BytesIO

# Сторонние библиотеки
import matplotlib.patheffects as pe
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

# Шрифт заголовка.
_FONT_TITLE = {"family": "sans-serif", "fontname": "Arial", "weight": "normal", "size": 18}

# Шрифт подписей осей.
_FONT_LABEL = {"family": "sans-serif", "fontname": "Arial", "weight": "normal", "size": 14}

# Размер шрифта подписей на столбцах.
_ANNOTATE_FONT_SIZE = 18


def render(result: dict, colors: list) -> BytesIO:
    """
    Рисует график отчёта и возвращает его в виде PNG в памяти.

    Args:
        result (dict): Результат compute.build — {"title", "thresholds"}
        colors (list): Пара [фон, акцент] в HEX для конкретного вида отчёта

    Returns:
        BytesIO: Буфер с PNG, перемотанный в начало. На диск не пишется.
    """
    title = result["title"]
    categories = [t["label"] for t in result["thresholds"]]
    num_matches = [t["total"] for t in result["thresholds"]]
    percent_correct = [t["percent_correct"] for t in result["thresholds"]]

    matches_color, correct_color = colors[0], colors[1]

    fig = Figure(figsize=(10, 6))
    FigureCanvasAgg(fig)  # привязывает холст Agg к фигуре для вывода в PNG
    ax = fig.subplots()

    ax.set_title(title, fontdict=_FONT_TITLE)
    ax.set_xlabel("Уверенность в победе", fontdict=_FONT_LABEL)
    ax.set_ylabel("Количество матчей", fontdict=_FONT_LABEL)
    ax.tick_params(axis="both", labelsize=12)

    # На каждом пороге два наложенных столбца: фоновый во всю высоту, это все
    # матчи, поверх него столбец пониже, это сколько из них предсказано верно.
    correct_matches = [n * p / 100 for n, p in zip(num_matches, percent_correct)]
    ax.bar(categories, num_matches, color=matches_color, edgecolor="black", linewidth=2,
           zorder=2, label="Количество проанализированных игр")
    ax.bar(categories, correct_matches, color=correct_color, edgecolor="black", linewidth=2,
           zorder=2, label="Процент верных предсказаний")

    _annotate(ax, categories, num_matches, percent_correct)

    ax.legend(fontsize=12, loc="upper right")
    ax.grid(True, linestyle="-", alpha=0.3)

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=300)
    buffer.seek(0)
    return buffer


def _annotate(ax, categories: list, num_matches: list, percent_correct: list) -> None:
    """
    Подписывает на столбцах число матчей и процент верных прогнозов.

    Число матчей ставится у верхушки фонового столбца, а процент — внутри
    столбца верных прогнозов либо над ним, если тот слишком низкий для
    подписи внутри.
    """
    # Единица отступа подписей — 2% от высоты самого высокого столбца.
    offset = (max(num_matches) or 1) * 0.02

    for category, total, percent in zip(categories, num_matches, percent_correct):
        correct = total * percent / 100

        if total - correct > offset * 4:
            _label(ax, category, total - offset, f"{total}")

        if total <= 0:
            continue

        if correct > offset * 4:
            _label(ax, category, correct - offset, f"{percent}%")
        else:
            _label(ax, category, total + offset * 3, f"{percent}%")


def _label(ax, x, y: float, text: str) -> None:
    """
    Ставит одну подпись: белый текст с чёрной обводкой, выровненный по центру по
    горизонтали и прижатый верхним краем к точке (x, y).

    Вынесен отдельно, чтобы _annotate задавал стиль подписи в одном месте, а не
    повторял его в каждом вызове.
    """
    ax.text(x, y, text, ha="center", va="top", fontsize=_ANNOTATE_FONT_SIZE, color="white",
            path_effects=[pe.withStroke(linewidth=2.5, foreground="black")])
