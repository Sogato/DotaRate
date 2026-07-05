"""
Ручной запуск отчётов статистики в боевой Telegram-чат.

Небольшая утилита для внепланового запуска любого отчёта.
Публикация идёт в тот же канал (TELEGRAM_CHAT_ID из config),
что и у планировщика, — это БОЕВОЙ чат.
"""

# Стандартные библиотеки
import logging
import sys
from typing import Callable, Optional, Tuple

# Локальные импорты
from telegram_bot.stats import publisher as stats_publisher

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """
    Логи публикации — рядом с меню, как обычные строки.

    Успех и причину неудачи сообщают сами модули (stats.publisher и др.)
    через logging. Пишем их в stdout тем же потоком, что и print, голым
    сообщением без служебного префикса — иначе лог уходит в stderr и
    перемешивается с меню. Болтливость сторонних библиотек глушим.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    logging.getLogger("telebot").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# ────────────────────────────────────────────────────────────────────────────
# Подтверждение
# ────────────────────────────────────────────────────────────────────────────

def _confirm(label: str) -> bool:
    """
    Спрашивает подтверждение перед публикацией в боевой чат.

    По умолчанию (пустой ввод, отмена по Ctrl+C/Ctrl+D) — «нет», чтобы
    случайный Enter ничего не отправил. При отказе сам печатает «Отменено».
    """
    try:
        answer = input(f'\nПубликуем "{label}" в БОЕВОЙ чат? [y/N]: ').strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        answer = ""

    if answer in {"y", "yes", "д", "да"}:
        return True
    print("Отменено.")
    return False


# ────────────────────────────────────────────────────────────────────────────
# Параметрические отчёты: спрашивают id лиги, затем подтверждают уже с ним
# ────────────────────────────────────────────────────────────────────────────

def _ask_league_id() -> Optional[int]:
    """Спрашивает id лиги; None — если ввод пуст, не число или отмена."""
    try:
        raw = input("id лиги (пусто — отмена): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        print("Нужно целое число — отмена.")
        return None


def _run_league() -> None:
    """Спрашивает id лиги, подтверждает уже с ним и публикует отчёт."""
    league_id = _ask_league_id()
    if league_id is None:
        return
    label = f"Отчёт по лиге {league_id}"
    if _confirm(label):
        print(f"Запуск: {label}…")
        stats_publisher.publish_league(league_id)


def _run_except_league() -> None:
    """Спрашивает id лиги, подтверждает уже с ним и публикует отчёт."""
    league_id = _ask_league_id()
    if league_id is None:
        return
    label = f"Отчёт по всем лигам, кроме лиги {league_id}"
    if _confirm(label):
        print(f"Запуск: {label}…")
        stats_publisher.publish_except_league(league_id)


# ────────────────────────────────────────────────────────────────────────────
# Меню
# ────────────────────────────────────────────────────────────────────────────

def _confirmed(label: str, publish: Callable[[], None]) -> Callable[[], None]:
    """Оборачивает непараметрическую публикацию подтверждением по её подписи."""
    def run() -> None:
        if _confirm(label):
            print(f"Запуск: {label}…")
            publish()
    return run


# Непараметрические отчёты: подпись и что публиковать.
_SIMPLE: Tuple[Tuple[str, Callable[[], None]], ...] = (
    ("Дневной отчёт",      stats_publisher.publish_daily),
    ("Недельный отчёт",    stats_publisher.publish_weekly),
    ("Месячный отчёт",     stats_publisher.publish_monthly),
    ("Отчёт за весь патч", stats_publisher.publish_all_time),
)

# Полное меню: непараметрические + параметрические по лиге.
# Порядок задаёт нумерацию на экране.
_MENU: Tuple[Tuple[str, Callable[[], None]], ...] = tuple(
    (label, _confirmed(label, publish)) for label, publish in _SIMPLE
) + (
    ("Отчёт по лиге",                    _run_league),
    ("Отчёт по всем лигам, кроме одной", _run_except_league),
)


def _print_menu() -> None:
    """Печатает пункты меню и вариант выхода."""
    print("\nОтчёты в БОЕВОЙ чат:")
    for number, (label, _) in enumerate(_MENU, start=1):
        print(f"  {number}. {label}")
    print("  0. Выход")


def main() -> None:
    """Показывает меню и запускает выбранный отчёт, пока не выберут выход."""
    _configure_logging()
    while True:
        _print_menu()
        try:
            choice = input("Выбор: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if choice in {"0", "q", "exit"}:
            break

        if not choice.isdigit() or not (1 <= int(choice) <= len(_MENU)):
            print("Нет такого пункта.")
            continue

        _, action = _MENU[int(choice) - 1]
        action()


if __name__ == "__main__":
    main()
