"""
Парсер коэффициентов букмекера Winline на матчи Dota 2 через страницы событий сайта.

Каждая публичная функция открывает собственный браузер Firefox (Selenium) на
время вызова и закрывает его по завершении. Просто, но медленно — браузер не
переиспользуется между вызовами. Разбор разметки выполняется через BeautifulSoup.

Контракт по командам:
- Имена подаются на вход в порядке (radiant, dire).
- Коэффициенты возвращаются кортежем в порядке аргументов: первым — коэффициент команды team1, вторым — команды team2.

Две публичные точки входа, вызываемые раздельно на разных стадиях:

1. resolve_event_id(team1, team2) → str | None
   Загружает линию Dota 2, нечётко сопоставляет команды и возвращает
   идентификатор события (event_id) матча либо None, если матча в линии нет.

2. get_coefficients(event_id, team1, team2, map_number) → tuple | None
   По известному идентификатору события открывает его страницу и извлекает тип
   ставки "<map_number> карта победитель". Возвращает пару коэффициентов команд
   team1 и team2 либо None, если ставки на этот тип ещё не выставлены.
"""

# Стандартные библиотеки
import re
from difflib import SequenceMatcher
from typing import Callable, List, Optional, Tuple

# Сторонние библиотеки
from bs4 import BeautifulSoup
from bs4.element import Tag
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

# === КОНСТАНТЫ URL ===
LIST_URL = 'https://winline.ru/stavki/sport/kibersport/dota_2'   # Общая линия Dota 2 (список матчей)
EVENT_URL = 'https://winline.ru/stavki/sport/event/{event_id}'   # Страница отдельного события

# === КОНСТАНТЫ ПАРСИНГА ===
SIMILARITY_THRESHOLD = 0.5   # Минимальная близость имён для нечёткого сопоставления команд (0..1)
PAGE_TIMEOUT = 30            # Таймаут ожидания загрузки ключевого элемента страницы, сек

# === КОНСТАНТЫ БРАУЗЕРА ===
HEADLESS = True              # Запускать Firefox в безоконном режиме


def _similar(a: str, b: str) -> float:
    """
    Возвращает меру схожести двух строк в диапазоне 0..1 (1.0 — полное совпадение).

    Args:
        a (str): Первая строка
        b (str): Вторая строка

    Returns:
        float: Коэффициент схожести
    """

    return SequenceMatcher(None, a, b).ratio()


def _has_class(needle: str) -> Callable[[Optional[str]], bool]:
    """
    Строит предикат для аргумента class_ в методах поиска BeautifulSoup.

    Предикат считает элемент совпавшим, если needle входит в строку его классов.
    Сравнение по вхождению устойчиво к служебным классам фреймворка
    (например, ng-star-inserted), при которых точное сравнение не сработало бы.

    Args:
        needle (str): Искомая подстрока имени класса

    Returns:
        Callable[[Optional[str]], bool]: Предикат для аргумента class_ в BeautifulSoup
    """

    return lambda c: c is not None and needle in c


def _norm(name: Optional[str]) -> str:
    """
    Приводит имя к каноничному виду: без крайних пробелов, в нижнем регистре.

    Args:
        name (Optional[str]): Исходная строка (допускается None)

    Returns:
        str: Нормализованная строка (пустая для None)
    """

    return (name or '').strip().lower()


def _to_float(txt: Optional[str]) -> float:
    """
    Преобразует текст коэффициента в число, заменяя запятую на точку.

    Возвращает 0.0 при пустой строке или нечисловом значении; вызывающий код
    трактует ноль как отсутствие коэффициента.

    Args:
        txt (Optional[str]): Текстовое представление коэффициента

    Returns:
        float: Числовой коэффициент или 0.0, если разбор не удался
    """

    txt = (txt or '').strip().replace(',', '.')
    try:
        return float(txt)
    except ValueError:
        return 0.0


def _make_browser() -> webdriver.Firefox:
    """
    Создаёт новый экземпляр Firefox, настроенный на быстрый разбор разметки.

    Returns:
        webdriver.Firefox: Новый экземпляр браузера
    """

    options = FirefoxOptions()
    if HEADLESS:
        options.add_argument("--headless")

    # get() вернёт управление после DOMContentLoaded, не дожидаясь догрузки всех ресурсов
    # нужный элемент всё равно дождётся WebDriverWait в _load
    options.page_load_strategy = 'eager'

    # Отключаем уведомления
    options.set_preference("dom.webnotifications.enabled", False)
    # Не качаем картинки (2 = блокировать)
    options.set_preference("permissions.default.image", 2)
    # Запрещаем автозапуск медиа (5 = блокировать всё)
    options.set_preference("media.autoplay.default", 5)

    # Отключаем автообновление Firefox
    options.set_preference("app.update.enabled", False)
    # Не проверяем, является ли Firefox браузером по умолчанию
    options.set_preference("browser.shell.checkDefaultBrowser", False)
    # Глушим телеметрию, чтобы не плодить фоновые запросы
    options.set_preference("toolkit.telemetry.enabled", False)

    return webdriver.Firefox(options=options)


def _load(browser: webdriver.Firefox, url: str, wait_css: str) -> Optional[BeautifulSoup]:
    """
    Открывает страницу и ожидает появления элемента по CSS-селектору.

    Ожидание необходимо из-за динамической подгрузки содержимого. Возвращает
    разобранный документ либо None, если элемент не появился за таймаут.

    Args:
        browser (webdriver.Firefox): Активный экземпляр браузера
        url (str): Адрес загружаемой страницы
        wait_css (str): CSS-селектор ожидаемого элемента

    Returns:
        Optional[BeautifulSoup]: Разобранный документ или None при таймауте
    """

    browser.get(url)
    try:
        WebDriverWait(browser, PAGE_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, wait_css))
        )
    except TimeoutException:
        return None
    return BeautifulSoup(browser.page_source, 'html.parser')


def resolve_event_id(team1: str,
                     team2: str,
                     threshold: float = SIMILARITY_THRESHOLD) -> Optional[str]:
    """
    Находит идентификатор события (event_id) матча в линии Dota 2 по именам команд.

    Открывает собственный браузер на время вызова. Загружает линию, перебирает
    карточки матчей и нечётко сопоставляет переданные имена с парой команд
    карточки в обоих порядках. Из совпавшей карточки извлекает идентификатор
    события.

    Args:
        team1 (str): Имя первой команды (radiant)
        team2 (str): Имя второй команды (dire)
        threshold (float): Минимальная близость имён для совпадения (0..1)

    Returns:
        Optional[str]: Идентификатор найденного события либо None, если
            матч в линии отсутствует
    """

    with _make_browser() as browser:
        soup = _load(browser, LIST_URL, ".block-sport__champ-list")
        if soup is None:
            return None

        t1, t2 = _norm(team1), _norm(team2)
        for card in soup.select('div.block-sport__champ-list .card'):
            names_block = card.find('div', class_=_has_class('body-left__names'))
            if not names_block:
                continue
            teams = [_norm(n.get_text()) for n in names_block.find_all('div', class_='name')]
            if len(teams) < 2:
                continue

            # Сравнение в прямом и зеркальном порядке
            direct = _similar(t1, teams[0]) >= threshold and _similar(t2, teams[1]) >= threshold
            swapped = _similar(t1, teams[1]) >= threshold and _similar(t2, teams[0]) >= threshold
            if not (direct or swapped):
                continue

            link = card.find('a', href=re.compile(r'/event/\d+'))
            if not isinstance(link, Tag):
                continue
            m = re.search(r'/event/(\d+)', str(link.get('href', '')))
            if m:
                return m.group(1)

        return None


def get_coefficients(event_id: str,
                     team1: str,
                     team2: str,
                     map_number: int,
                     threshold: float = SIMILARITY_THRESHOLD) -> Optional[Tuple[float, float]]:
    """
    Извлекает коэффициенты на победителя указанной карты по идентификатору события.

    Открывает собственный браузер на время вызова. Открывает страницу события,
    находит тип ставки "<map_number> карта победитель", собирает пары "подпись
    исхода — коэффициент" и сопоставляет их с командами как единое назначение:
    два исхода распределяются между team1 и team2 без переиспользования.
    Коэффициенты возвращаются в порядке аргументов.

    Args:
        event_id (str): Идентификатор события из resolve_event_id
        team1 (str): Имя первой команды (radiant)
        team2 (str): Имя второй команды (dire)
        map_number (int): Номер карты, для которой нужен тип ставки
        threshold (float): Минимальная близость имён для совпадения (0..1)

    Returns:
        Optional[Tuple[float, float]]: Пара коэффициентов команд team1 и team2
            в порядке аргументов либо None, если ставки ещё не выставлены или
            исходы не сопоставились с командами
    """

    with _make_browser() as browser:
        url = EVENT_URL.format(event_id=event_id)
        soup = _load(browser, url, "[class*='market']")
        if soup is None:
            return None

        bet_type = _find_bet_type(soup, f"{map_number} карта победитель")
        if bet_type is None:
            return None

        # Пары "подпись исхода — коэффициент"
        outcomes: List[Tuple[str, float]] = []
        for row in bet_type.find_all(class_=_has_class('row-btn')):
            label = str(row.get('title') or '').strip()
            coef_el = row.find(class_=_has_class('row-btn__coef'))
            coef_txt = coef_el.get_text(strip=True) if coef_el else ''
            coef = _to_float(coef_txt)
            if label and coef:
                outcomes.append((_norm(label), coef))

        # Тип ставки найден, но коэффициенты ещё не выставлены
        if len(outcomes) < 2:
            return None

        return _assign_coefficients(outcomes, _norm(team1), _norm(team2), threshold)


def _find_bet_type(soup: BeautifulSoup, title_text: str) -> Optional[Tag]:
    """
    Находит блок типа ставки по точному совпадению заголовка.

    Заголовок нормализуется (схлопывание пробелов, нижний регистр) и
    сравнивается с целевым на полное равенство.

    Args:
        soup (BeautifulSoup): Разобранный документ страницы события
        title_text (str): Искомый заголовок типа ставки

    Returns:
        Optional[Tag]: Элемент блока типа ставки или None, если не найден
    """

    target = title_text.lower()
    for m in soup.find_all(class_='market'):
        te = m.find(class_=_has_class('market-title'))
        if not te:
            continue
        title = ' '.join(te.get_text(strip=True).split()).lower()
        if title == target:
            return m
    return None


def _assign_coefficients(outcomes: List[Tuple[str, float]],
                         team1: str,
                         team2: str,
                         threshold: float) -> Optional[Tuple[float, float]]:
    """
    Распределяет исходы между двумя командами как единое назначение.

    Перебирает все пары различных исходов и для каждой считает суммарную
    схожесть подписей с (team1, team2). Удерживает назначение с максимальной
    суммой при условии, что обе схожести не ниже порога. Один исход не может
    быть назначен обеим командам, что исключает возврат одного коэффициента
    дважды.

    Args:
        outcomes (List[Tuple[str, float]]): Пары "подпись исхода — коэффициент"
        team1 (str): Нормализованное имя первой команды
        team2 (str): Нормализованное имя второй команды
        threshold (float): Минимальная близость для совпадения (0..1)

    Returns:
        Optional[Tuple[float, float]]: Коэффициенты (team1, team2) при удачном
            назначении либо None, если ни одно назначение не прошло порог
    """

    best: Optional[Tuple[float, float]] = None
    best_score = -1.0
    n = len(outcomes)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            s1 = _similar(team1, outcomes[i][0])
            s2 = _similar(team2, outcomes[j][0])
            if s1 < threshold or s2 < threshold:
                continue
            score = s1 + s2
            if score > best_score:
                best_score = score
                best = (outcomes[i][1], outcomes[j][1])
    return best
