"""Утилиты форматирования данных матчей."""

from datetime import datetime
from typing import Union

DEFAULT_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"   # Формат по умолчанию для отображения дат


def format_timestamp(timestamp: Union[int, float], fmt: str = DEFAULT_DATETIME_FORMAT) -> str:
    """
    Форматирует Unix timestamp в читаемую строку даты и времени.

    Args:
        timestamp: Unix timestamp (секунды с epoch).
                   Принимает int и float.
        fmt: Строка формата strftime. По умолчанию 'YYYY-MM-DD HH:MM:SS'.

    Returns:
        Сообщение об ошибке при некорректном значении.
    """

    try:
        return datetime.fromtimestamp(timestamp).strftime(fmt)
    except (ValueError, OSError, TypeError):
        return f"Invalid timestamp: {timestamp}"
