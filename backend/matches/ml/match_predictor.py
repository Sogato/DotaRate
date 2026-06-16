"""
Фасад предсказаний по составу матча Dota 2.

view передаёт сюда сырые hero_id двух команд и получает словарь под поля модели
Match (predict_win, predict_time, predict_radiant_score, predict_dire_score).

Координатор (MatchPredictor) делает общее для всех моделей: маппинг
hero_id -> плотный индекс и загрузку моделей с диска. Предсказатели win/time/score
сами готовят вход под свою модель и усредняют фолды (soft-voting).

load() вызывается один раз на старте (MatchesConfig.ready()) и строит всё
состояние; если моделей нет — падает, и сервер не стартует. predict() работает
только с тем, что уже в памяти.
"""

# Стандартные библиотеки
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# Сторонние библиотеки
from keras import Model
from keras.models import load_model

# Локальные импорты
from dota_core.hero_mapper import HeroMapper
from dota_core.config import DOTA_VERSION, EXCLUDED_HERO_IDS, TEAM_SIZE

# Текущие боевые версии предсказателей.
from .win_v1_predictor import WinV1Predictor
from .time_v1_predictor import TimeV1Predictor
from .score_v1_predictor import ScoreV1Predictor


# Папка с обученными моделями: ml/models/<тип>/*.keras.
MODELS_DIR = Path(__file__).resolve().parent / 'models'

# Базовое имя модели.
MODEL_BASENAME = "model_{type}_{version}_{dota}"

# Боевые версии моделей: тип -> версия.
MODEL_VERSIONS: Dict[str, str] = {
    'win': 'v1',
    'time': 'v1',
    'score': 'v1',
}


class MatchPredictor:
    """
    Координатор предсказаний: маппинг героев и три ансамбля.

    Наполняется через load(); до него predict_match() поднимет RuntimeError.
    Backend использует один экземпляр, но класс к этому не привязан — в тестах
    можно создавать свои.
    """

    def __init__(self):
        # Состояние заполняется в load(); до этого координатор не готов к работе.
        self._hero_to_index: Optional[Dict[int, int]] = None
        self._predictors: Optional[Dict[str, object]] = None

    def load(self) -> None:
        """
        Строит маппинг героев и загружает три ансамбля.

        Тяжёлая операция, вызывается один раз на старте сервера. Если файлов
        модели нет, _load_ensemble бросает FileNotFoundError и сервер не поднимется.
        """

        mapper = HeroMapper(excluded_hero_ids=EXCLUDED_HERO_IDS)
        self._hero_to_index = mapper.hero_to_index  # Строит и кеширует словарь
        mapper.cleanup()                            # Словарь в памяти, БД больше не нужна

        self._predictors = {
            'win': WinV1Predictor(self._load_ensemble('win', MODEL_VERSIONS['win'])),
            'time': TimeV1Predictor(self._load_ensemble('time', MODEL_VERSIONS['time'])),
            'score': ScoreV1Predictor(self._load_ensemble('score', MODEL_VERSIONS['score'])),
        }

    def predict_match(self,
                      radiant_hero_ids: Sequence[int],
                      dire_hero_ids: Sequence[int]) -> Dict[str, float]:
        """
        Считает прогнозы для одного матча по составам двух команд.

        Returns:
            Словарь под поля модели Match:
                predict_win           — вероятность победы Radiant (0–1)
                predict_time          — длительность матча, секунды
                predict_radiant_score — счёт Radiant (убийства)
                predict_dire_score    — счёт Dire (убийства)

        Raises:
            RuntimeError: load() ещё не вызван.
            ValueError: в команде не TEAM_SIZE героев или неизвестный hero_id.
        """

        if self._predictors is None:
            raise RuntimeError("Предсказатели не загружены. Вызовите load() при старте сервера.")

        # hero_id -> плотные индексы (общее для всех моделей).
        radiant_indices = self._team_to_indices(radiant_hero_ids)
        dire_indices = self._team_to_indices(dire_hero_ids)

        # score отдаёт два числа.
        radiant_score, dire_score = self._predictors['score'].predict(radiant_indices, dire_indices)

        return {
            'predict_win': self._predictors['win'].predict(radiant_indices, dire_indices),
            'predict_time': self._predictors['time'].predict(radiant_indices, dire_indices),
            'predict_radiant_score': radiant_score,
            'predict_dire_score': dire_score,
        }

    @staticmethod
    def _load_ensemble(model_type: str, model_version: str) -> List[Model]:
        """
        Загружает все фолды одного типа из models/<тип>, отсортированные по номеру.

        Raises:
            FileNotFoundError: если не найдено ни одной модели.
        """

        type_dir = MODELS_DIR / model_type
        base_name = MODEL_BASENAME.format(type=model_type, version=model_version, dota=DOTA_VERSION)

        fold_paths = sorted(
            type_dir.glob(f"{base_name}_fold_*.keras"),
            key=lambda p: int(p.stem.split('_fold_')[-1])
        )

        if not fold_paths:
            raise FileNotFoundError(
                f"Не найдено моделей типа '{model_type}' {model_version} в {type_dir} "
                f"(ожидался шаблон {base_name}_fold_*.keras). Сервер не будет запущен."
            )

        return [load_model(str(path)) for path in fold_paths]

    def _team_to_indices(self, hero_ids: Sequence[int]) -> List[int]:
        """
        Переводит hero_id одной команды в плотные индексы (порядок сохраняется).

        Raises:
            ValueError: если героев не TEAM_SIZE или hero_id нет в маппинге.
        """

        if len(hero_ids) != TEAM_SIZE:
            raise ValueError(f"Ожидалось {TEAM_SIZE} героев в команде, получено {len(hero_ids)}")

        indices = []
        for hero_id in hero_ids:
            if hero_id not in self._hero_to_index:
                raise ValueError(f"Герой с ID {hero_id} отсутствует в маппинге модели")
            indices.append(self._hero_to_index[hero_id])

        return indices


# Модульный фасад: один экземпляр на процесс + load()/predict().
# apps.py зовёт load() на старте, view зовёт predict() на каждый запрос.
_predictor: Optional[MatchPredictor] = None


def load() -> None:
    """
    Создаёт и наполняет singleton-координатор. Вызывается из MatchesConfig.ready().

    Присваивается в _predictor только после полной загрузки: при сбое на полпути
    singleton остаётся None, а исключение не даёт серверу подняться с неполным
    набором моделей.
    """

    global _predictor
    predictor = MatchPredictor()
    predictor.load()
    _predictor = predictor


def predict(radiant_hero_ids: Sequence[int],
            dire_hero_ids: Sequence[int]) -> Dict[str, float]:
    """
    Считает прогнозы для одного матча. Точка входа для view.

    Raises:
        RuntimeError: load() ещё не вызван.
    """

    if _predictor is None:
        raise RuntimeError("Предсказатели не загружены. Вызовите load() при старте сервера.")
    return _predictor.predict_match(radiant_hero_ids, dire_hero_ids)
