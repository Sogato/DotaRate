"""
Предсказатель счёта Score v1 для backend.

Обслуживает один матч: по плотным индексам героев двух команд считает счёт обеих
команд (убийства Radiant и Dire). Внутри держит ансамбль моделей и усредняет их
предсказания.

Модель отдаёт оба счёта сразу в убийствах. В отличие от win/time, выход модели —
два числа: столбец 0 — счёт Radiant, столбец 1 — счёт Dire. Формат входа совпадает
с обучением.
"""

# Стандартные библиотеки
from typing import Dict, List, Sequence, Tuple

# Сторонние библиотеки
import numpy as np
from keras import Model

# Локальные импорты
from dota_core.config import DIRE_INDEX, RADIANT_INDEX


class ScoreV1Predictor:
    """
    Ансамбль моделей Score v1.

    Attributes:
        models (List[Model]): Загруженные модели Keras.
    """

    def __init__(self, models: List[Model]):
        """
        Сохраняет готовый ансамбль моделей.

        Args:
            models (List[Model]): Готовые модели Keras, загруженные координатором
        """

        self.models = models

    def predict(self,
                radiant_indices: Sequence[int],
                dire_indices: Sequence[int]) -> Tuple[float, float]:
        """
        Считает счёт обеих команд (в убийствах) для одного матча.

        Прогоняет составы через каждую модель ансамбля и усредняет предсказания
        покомпонентно (отдельно счёт Radiant и Dire).

        Args:
            radiant_indices (Sequence[int]): Плотные индексы героев Radiant
            dire_indices (Sequence[int]): Плотные индексы героев Dire

        Returns:
            Tuple[float, float]: (счёт Radiant, счёт Dire) в убийствах
        """

        features = self._prepare_input(radiant_indices, dire_indices)

        # Предсказание от каждой модели ансамбля.
        team_scores = []
        for model in self.models:
            # Прямой вызов модели (а не .predict) — лёгкий путь для одиночного матча:
            # .predict рассчитан на большие батчи и имеет лишние накладные расходы.
            # training=False обязателен: переводит BatchNorm/Dropout в режим инференса.
            output = model(features, training=False)
            team_scores.append(np.asarray(output).reshape(-1))  # форма (2,): [radiant, dire]

        # Soft-voting.
        # Столбцы выхода идут в порядке Radiant→Dire — том же, что и RADIANT_INDEX/DIRE_INDEX.
        mean_scores = np.mean(team_scores, axis=0)
        return float(mean_scores[RADIANT_INDEX]), float(mean_scores[DIRE_INDEX])

    @staticmethod
    def _prepare_input(radiant_indices: Sequence[int],
                       dire_indices: Sequence[int]) -> Dict[str, np.ndarray]:
        """
        Подготавливает входные данные под модель Score v1.

        Args:
            radiant_indices (Sequence[int]): Плотные индексы героев Radiant
            dire_indices (Sequence[int]): Плотные индексы героев Dire

        Returns:
            Dict[str, np.ndarray]: {'radiant_heroes': (1, 5) int32, 'dire_heroes': (1, 5) int32}
        """

        return {
            'radiant_heroes': np.array([radiant_indices], dtype=np.int32),
            'dire_heroes': np.array([dire_indices], dtype=np.int32),
        }
