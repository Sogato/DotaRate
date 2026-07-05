"""
Утилита для оценки памяти, занимаемой данными кэшей.

Стандартный sys.getsizeof() для словаря считает только сам "каркас" словаря,
но не его ключи и значения. Для честной оценки нужно пройтись по содержимому.

Функции модуля используются всеми кэшами (HeroCache, LeagueCache, HeroMapper)
для вывода строки "Задействовано памяти" при инициализации.
"""

# Стандартные библиотеки
import sys
from typing import Dict


def dict_memory_bytes(*dicts: Dict) -> int:
    """
    Считает суммарную память, занимаемую словарями вместе с их содержимым.

    Учитывает сам словарь, все ключи и все значения. Подходит для плоских
    словарей вида {int: str} или {int: int} — вложенные структуры не обходит.

    Args:
        *dicts: Один или несколько словарей (например, оба словаря маппера)

    Returns:
        int: Суммарный размер в байтах
    """
    total = 0

    for d in dicts:
        # Каркас самого словаря
        total += sys.getsizeof(d)

        # Плюс каждый ключ и каждое значение
        for key, value in d.items():
            total += sys.getsizeof(key)
            total += sys.getsizeof(value)

    return total


def format_memory(num_bytes: int) -> str:
    """
    Переводит размер в байтах в человекочитаемую строку.

    Examples:
        format_memory(512)      # "512 Б"
        format_memory(2048)     # "2.0 КБ"
        format_memory(5242880)  # "5.0 МБ"

    Args:
        num_bytes (int): Размер в байтах

    Returns:
        str: Строка вида "512 Б", "2.0 КБ", "5.0 МБ" или "1.25 ГБ"
    """
    if num_bytes < 1024:
        return f"{num_bytes} Б"

    kilobytes = num_bytes / 1024
    if kilobytes < 1024:
        return f"{kilobytes:.1f} КБ"

    megabytes = kilobytes / 1024
    if megabytes < 1024:
        return f"{megabytes:.1f} МБ"

    return f"{megabytes / 1024:.2f} ГБ"
