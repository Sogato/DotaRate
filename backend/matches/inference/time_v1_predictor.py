"""
Предсказатель длительности Time v1 для backend.

Обслуживает один матч: по плотным индексам героев двух команд считает длительность
матча в секундах. Внутри держит ансамбль моделей и усредняет их предсказания.

Модель отдаёт длительность сразу в секундах. Формат входа Time v1 совпадает с обучением.
"""

# Стандартные библиотеки
from typing import Dict, List, Sequence

# Сторонние библиотеки
import numpy as np
from keras import Model


class TimeV1Predictor:
    """
    Ансамбль моделей Time v1.

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
                dire_indices: Sequence[int]) -> float:
        """
        Считает длительность матча (в секундах) для одного матча.

        Прогоняет составы через каждую модель ансамбля и усредняет предсказания.

        Args:
            radiant_indices (Sequence[int]): Плотные индексы героев Radiant
            dire_indices (Sequence[int]): Плотные индексы героев Dire

        Returns:
            float: Длительность матча в секундах
        """

        features = self._prepare_input(radiant_indices, dire_indices)

        # Предсказание от каждой модели ансамбля.
        durations = []
        for model in self.models:
            # Прямой вызов модели (а не .predict) — лёгкий путь для одиночного матча:
            # .predict рассчитан на большие батчи и имеет лишние накладные расходы.
            # training=False обязателен: переводит BatchNorm/Dropout в режим инференса.
            output = model(features, training=False)
            durations.append(float(np.asarray(output).reshape(-1)[0]))

        # Soft-voting.
        return float(np.mean(durations))

    @staticmethod
    def _prepare_input(radiant_indices: Sequence[int],
                       dire_indices: Sequence[int]) -> Dict[str, np.ndarray]:
        """
        Подготавливает входные данные под модель Time v1.

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
