"""
Координатор предсказаний по составу матча Dota 2.

По сырым hero_id двух команд возвращает словарь под поля модели Match:
вероятность победы, длительность матча и счёт каждой команды.

Модуль не хранит состояния и не грузит модели: готовый HeroMapper и модели
передаются аргументами на каждый вызов predict().
"""

# Стандартные библиотеки
from typing import Dict, List, Sequence, Type

# Сторонние библиотеки
from keras import Model

# Локальные импорты
from dota_core.utils.hero_mapper import HeroMapper
from dota_core.config import TEAM_SIZE

from .win_v1_predictor import WinV1Predictor
from .time_v1_predictor import TimeV1Predictor
from .score_v1_predictor import ScoreV1Predictor

# Классы, реализующие каждый тип прогноза.
PREDICTOR_CLASSES: Dict[str, Type] = {
    'win': WinV1Predictor,
    'time': TimeV1Predictor,
    'score': ScoreV1Predictor,
}


def predict(radiant_hero_ids: Sequence[int],
            dire_hero_ids: Sequence[int],
            mapper: HeroMapper,
            models: Dict[str, List[Model]]) -> Dict[str, float]:
    """
    Считает прогнозы для одного матча по составам двух команд.

    Args:
        radiant_hero_ids (Sequence[int]): hero_id команды Radiant (TEAM_SIZE штук)
        dire_hero_ids (Sequence[int]): hero_id команды Dire (TEAM_SIZE штук)
        mapper (HeroMapper): Готовый маппер из app_state
        models (Dict[str, List[Model]]): Ансамбли моделей по типам прогноза

    Returns:
        Dict[str, float]: Словарь под поля модели Match:
            predict_win           — вероятность победы Radiant (0–1)
            predict_time          — длительность матча, секунды
            predict_radiant_score — счёт Radiant (убийства)
            predict_dire_score    — счёт Dire (убийства)

    Raises:
        ValueError: В команде не TEAM_SIZE героев или неизвестный hero_id
    """

    # Преобразование hero_id в плотные индексы — общий шаг для всех моделей.
    radiant_indices = _team_to_indices(radiant_hero_ids, mapper)
    dire_indices = _team_to_indices(dire_hero_ids, mapper)

    # Счёт обеих команд возвращает одна модель за один вызов.
    score_predictor = PREDICTOR_CLASSES['score'](models['score'])
    radiant_score, dire_score = score_predictor.predict(radiant_indices, dire_indices)

    return {
        'predict_win': PREDICTOR_CLASSES['win'](models['win']).predict(radiant_indices, dire_indices),
        'predict_time': PREDICTOR_CLASSES['time'](models['time']).predict(radiant_indices, dire_indices),
        'predict_radiant_score': radiant_score,
        'predict_dire_score': dire_score,
    }


def _team_to_indices(hero_ids: Sequence[int], mapper: HeroMapper) -> List[int]:
    """
    Переводит hero_id одной команды в плотные индексы с сохранением порядка.

    Args:
        hero_ids (Sequence[int]): hero_id команды
        mapper (HeroMapper): Готовый маппер из app_state

    Returns:
        List[int]: Плотные индексы героев

    Raises:
        ValueError: Если героев не TEAM_SIZE или hero_id отсутствует в маппинге
    """

    if len(hero_ids) != TEAM_SIZE:
        raise ValueError(f"Ожидалось {TEAM_SIZE} героев в команде, получено {len(hero_ids)}")

    hero_to_index = mapper.hero_to_index

    indices = []
    for hero_id in hero_ids:
        if hero_id not in hero_to_index:
            raise ValueError(f"Герой с ID {hero_id} отсутствует в маппинге модели")
        indices.append(hero_to_index[hero_id])

    return indices
