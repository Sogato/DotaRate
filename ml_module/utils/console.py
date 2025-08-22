"""
Утилиты для красивого форматированного вывода в консоль
"""


class Colors:
    """Расширенная палитра ANSI цветов для элегантного форматированного вывода"""

    # Основные цвета
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'

    # Яркие цвета
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'

    # Специальные стили
    BOLD = '\033[1m'
    DIM = '\033[2m'
    ITALIC = '\033[3m'
    UNDERLINE = '\033[4m'
    BLINK = '\033[5m'
    REVERSE = '\033[7m'
    STRIKETHROUGH = '\033[9m'

    # Цвета фона
    BG_BLACK = '\033[40m'
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'
    BG_MAGENTA = '\033[45m'
    BG_CYAN = '\033[46m'
    BG_WHITE = '\033[47m'

    # 256-цветная палитра (некоторые основные)
    ORANGE = '\033[38;5;208m'
    PURPLE = '\033[38;5;129m'
    PINK = '\033[38;5;205m'
    LIME = '\033[38;5;154m'
    GOLD = '\033[38;5;220m'
    SILVER = '\033[38;5;250m'
    CORAL = '\033[38;5;203m'
    TEAL = '\033[38;5;37m'
    LAVENDER = '\033[38;5;183m'
    MINT = '\033[38;5;121m'

    # Яркие версии дополнительных цветов
    BRIGHT_ORANGE = '\033[38;5;214m'
    BRIGHT_PURPLE = '\033[38;5;135m'
    BRIGHT_PINK = '\033[38;5;213m'
    BRIGHT_LIME = '\033[38;5;155m'
    BRIGHT_GOLD = '\033[38;5;226m'
    BRIGHT_SILVER = '\033[38;5;255m'
    BRIGHT_CORAL = '\033[38;5;210m'
    BRIGHT_TEAL = '\033[38;5;51m'
    BRIGHT_LAVENDER = '\033[38;5;189m'
    BRIGHT_MINT = '\033[38;5;122m'

    # Сброс форматирования
    RESET = '\033[0m'


def print_section_header(title: str, icon: str = "📊", width: int = 80, color: str = Colors.BRIGHT_CYAN):
    """Печатает красивый заголовок секции"""
    print(f"\n{color}{'═' * width}{Colors.RESET}")
    centered_title = f"{icon} {title} {icon}".center(width)
    print(f"{color}{centered_title}{Colors.RESET}")
    print(f"{color}{'═' * width}{Colors.RESET}")


def print_subsection_header(title: str, icon: str = "▶", color: str = Colors.BRIGHT_YELLOW):
    """Печатает заголовок подсекции"""
    print(f"\n{color}{icon} {Colors.BOLD}{title}{Colors.RESET}")
    print(f"{color}{'─' * (len(title) + 3)}{Colors.RESET}")


def print_info_line(label: str, value: str, icon: str = "•",
                    label_color: str = Colors.BRIGHT_WHITE,
                    value_color: str = Colors.BRIGHT_GREEN,
                    icon_color: str = Colors.BRIGHT_BLUE):
    """Печатает информационную строку с иконкой"""
    print(f"  {icon_color}{icon}{Colors.RESET} {label_color}{label}:{Colors.RESET} {value_color}{value}{Colors.RESET}")


def print_progress_bar(current: int, total: int, prefix: str = "", width: int = 40,
                       fill_color: str = Colors.BRIGHT_GREEN, empty_color: str = Colors.DIM):
    """Печатает прогресс-бар"""
    if total == 0:
        percentage = 0
    else:
        percentage = min(100, (current / total) * 100)

    filled_length = int(width * current // total) if total > 0 else 0
    bar = f"{fill_color}{'█' * filled_length}{empty_color}{'░' * (width - filled_length)}{Colors.RESET}"
    print(f"  {prefix} [{bar}] {Colors.BRIGHT_YELLOW}{percentage:.1f}%{Colors.RESET} "
          f"({Colors.BRIGHT_WHITE}{current:,}/{total:,}{Colors.RESET})")


def print_status_message(message: str, status: str = "info", icon: str = ""):
    """Печатает статусное сообщение с соответствующим цветом"""
    status_colors = {
        "success": Colors.BRIGHT_GREEN,
        "error": Colors.BRIGHT_RED,
        "warning": Colors.BRIGHT_YELLOW,
        "info": Colors.BRIGHT_BLUE
    }

    status_icons = {
        "success": "✅",
        "error": "❌",
        "warning": "⚠️",
        "info": "ℹ️"
    }

    color = status_colors.get(status, Colors.BRIGHT_WHITE)
    status_icon = icon or status_icons.get(status, "•")

    print(f"{color}{status_icon} {message}{Colors.RESET}")
