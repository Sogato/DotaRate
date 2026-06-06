"""
Модуль тестирования ансамбля моделей Score v1 предсказания счёта матчей Dota 2.

Модуль реализует единый сценарий оценки ансамбля из N моделей (фолдов K-fold CV):
адаптивное обнаружение моделей, получение данных из взаимозаменяемых источников,
приведение к единому формату признаков, усреднение предсказаний (soft-voting),
разворот их в исходный масштаб (убийства) и расчёт регрессионных метрик отдельно
по каждой команде и суммарно по обоим счётам, с сравнением с baseline.

Ключевые особенности:
- Источники: HDF5 файл с тестовой выборкой и БД профессиональных матчей (взаимозаменяемы)
- Предсказания: вычисляются батчами средствами Keras для контроля потребления памяти
- Два выхода: модель предсказывает счёт Radiant и Dire совместно, метрики считаются
  по каждой команде и в среднем по обеим
- Нормализация: модели обучены в стандартизованном пространстве цели, поэтому их
  предсказания разворачиваются обратно в убийства по сохранённым тренером параметрам
  (mean/std из TARGET_NORM_FILENAME в папке прогона)

Две оцениваемые сущности:
- Ансамбль (soft-voting): усреднение предсказаний всех моделей.
- Отдельные модели фолдов: метрика каждой по отдельности — их разброс (mean ± std).

Сценарий тестирования:
1. Интерактивный выбор прогона (ансамбля) для тестирования
2. Интерактивный выбор источника данных (HDF5 или БД)
3. Адаптивное обнаружение моделей ансамбля внутри папки прогона
4. Загрузка параметров нормализации цели прогона
5. Загрузка данных и приведение к единому Dict формату через загрузчик
6. Получение предсказаний по каждой модели, усреднение и разворот в убийства
7. Расчёт метрик ансамбля (общих и по командам), метрик отдельных моделей и baseline

Метрики ансамбля считаются один раз за прогон: сводка по командам выводится в начале
отчёта, а основные показатели качества (MAE/RMSE/R²) — в конце. Baseline предсказания
среднего считается из истинных меток тестовой выборки и доступен одинаково для обоих
источников данных.

Структура хранения моделей:
Модели лежат в папках прогонов, созданных тренером:

    models / <DOTA_VERSION> / <MODEL_TYPE> / <MODEL_VERSION> / run_<NNN> / model_*_fold_N.keras
"""
import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '1'

# Стандартные библиотеки
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Сторонние библиотеки
import numpy as np
from keras.models import load_model
from sklearn.metrics import r2_score

# Локальные импорты
from ml_training.data_loader_v1 import DataLoaderV1
from config import DOTA_VERSION
from utils.console import (
    Colors,
    print_info_line,
    print_progress_bar,
    print_section_header,
    print_status_message,
    print_subsection_header,
)

# === КОНСТАНТЫ ФАЙЛОВ ===
HDF5_FILENAME = "HDF5_dataset_{version}_test.h5"    # Имя HDF5 файла с тестовой выборкой
MODEL_FILENAME = "model_score_v1_{version}.keras"   # Базовое имя моделей (суффикс _fold_N в именах фолдов)
TARGET_NORM_FILENAME = "target_norm.json"           # Файл параметров нормализации цели (рядом с моделями)

# === КОНСТАНТЫ СТРУКТУРЫ ХРАНЕНИЯ ===
MODEL_TYPE = "score"                                    # Тип модели (служит и типом цели загрузчика)
MODEL_VERSION = "v1"                                    # Версия модели данного типа
RUN_DIR_PREFIX = "run_"                                 # Префикс папки прогона
RUN_DIR_PATTERN = re.compile(r"run_(\d+)(?:_.*)?$")     # Папка прогона: run_<номер> с опциональной меткой

# === КОНСТАНТЫ СТРУКТУРЫ ЦЕЛИ ===
RADIANT_COL = 0                     # Столбец счёта Radiant в метках (N, 2)
DIRE_COL = 1                        # Столбец счёта Dire в метках (N, 2)

# === КОНСТАНТЫ ТЕСТИРОВАНИЯ ===
DEFAULT_LEAGUE_IDS: Set[int] = set()        # Фильтр по ID лиг для БД, например {5401, 4266} (пустое множество = все лиги)
DEFAULT_MAX_MATCHES = 10_000                # Лимит матчей при тестировании на БД (None = все)
DEFAULT_BATCH_SIZE = 256                    # Размер батча для предсказаний


class ModelTesterScoreV1:
    """
    Координатор тестирования ансамбля моделей Score v1.

    Класс управляет полным циклом оценки: адаптивное обнаружение моделей фолдов,
    загрузка параметров нормализации и самого ансамбля, приведение источника
    (БД или HDF5) к единому Dict формату через DataLoaderV1, получение предсказаний
    по каждой модели, усреднение в ансамбль (soft-voting), разворот в убийства и
    расчёт регрессионных метрик ансамбля (общих и по командам), метрик отдельных
    моделей и сравнения с baseline предсказания среднего.

    Attributes:
        loader (DataLoaderV1): Загрузчик данных
        batch_size (int): Размер батча для предсказаний
        max_matches (Optional[int]): Лимит матчей для БД (None = все)
        league_ids (Set[int]): Фильтр по ID лиг для БД (пустое множество = все лиги)
        target_mean (float): Среднее цели из параметров нормализации прогона
        target_std (float): Стандартное отклонение цели из параметров нормализации
    """

    def __init__(self,
                 batch_size: int = DEFAULT_BATCH_SIZE,
                 max_matches: Optional[int] = DEFAULT_MAX_MATCHES,
                 league_ids: Optional[Set[int]] = None):
        """
        Инициализирует тестер с заданной конфигурацией.

        Создает загрузчик данных и фиксирует параметры тестирования.

        Args:
            batch_size (int): Размер батча для предсказаний
            max_matches (Optional[int]): Лимит матчей для БД (None = все)
            league_ids (Optional[Set[int]]): Фильтр по лигам для БД
                (None → все лиги, пустое множество)
        """

        self.loader = DataLoaderV1()

        # Параметры тестирования
        self.batch_size = batch_size
        self.max_matches = max_matches
        self.league_ids = league_ids if league_ids is not None else set(DEFAULT_LEAGUE_IDS)

        # Параметры нормализации цели загружаются из прогона в test()
        self.target_mean = 0.0
        self.target_std = 1.0

    def test(self,
             model_base_path: str,
             data_source: str,
             hdf5_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Координирует сценарий тестирования ансамбля на выбранном источнике данных.

        Последовательность действий:
        1. Адаптивное обнаружение моделей ансамбля по шаблону имени фолдов
        2. Загрузка параметров нормализации цели прогона
        3. Загрузка моделей и приведение источника (БД или HDF5) к единому формату
        4. Получение предсказаний по каждой модели, усреднение и разворот в убийства
        5. Расчёт метрик ансамбля (общих и по командам), метрик моделей и baseline

        Метрики ансамбля рассчитываются один раз: сводка по командам выводится в начале
        отчёта, а основные показатели качества — в конце. Baseline предсказания среднего
        считается из истинных меток тестовой выборки и доступен одинаково для обоих
        источников данных.

        Args:
            model_base_path (str): Базовый путь к моделям внутри папки прогона;
                суффикс _fold_N.keras обнаруживается автоматически сканированием директории
            data_source (str): Источник данных ('db' или 'hdf5')
            hdf5_path (Optional[str]): Путь к HDF5 файлу (требуется для data_source='hdf5')

        Returns:
            Dict[str, Any]: Результаты тестирования. При успехе содержит метрики
                ансамбля (общие и по командам), метрики отдельных моделей, baseline
                и метаданные; при ошибке — {'success': False, 'error': ...}
        """

        # Формирование заголовка с учётом источника и фильтра лиг
        title = f"ТЕСТИРОВАНИЕ АНСАМБЛЯ (SCORE) НА {'БАЗЕ ДАННЫХ' if data_source == 'db' else 'HDF5'}"
        if data_source == 'db' and self.league_ids:
            leagues_str = ", ".join(map(str, sorted(self.league_ids)))
            title += f" (ЛИГИ {leagues_str})"
        print_section_header(title, "🧪", 80, Colors.BRIGHT_GOLD)

        try:
            # Вывод конфигурации
            self._print_test_configuration(data_source)

            # Адаптивное обнаружение моделей: сканируем директорию по шаблону имени фолдов
            model_paths = self._discover_model_paths(model_base_path)
            if not model_paths:
                model_dir = Path(model_base_path).parent
                model_name_prefix = Path(model_base_path).stem + "_fold_"
                error_msg = (f"Не найдено моделей в директории {model_dir} "
                             f"с префиксом {model_name_prefix}*.keras")
                print_status_message(error_msg, "error", "❌")
                return {'success': False, 'error': error_msg}

            print_info_line("Найдено моделей в ансамбле", f"{len(model_paths)}", "🤖",
                            Colors.BLUE_3, Colors.BRIGHT_GREEN)

            # Загрузка параметров нормализации цели (нужны для разворота предсказаний)
            normalization = self._load_target_normalization(model_base_path)
            if normalization is None:
                norm_path = Path(model_base_path).parent / TARGET_NORM_FILENAME
                error_msg = f"Не найдены параметры нормализации: {norm_path}"
                print_status_message(error_msg, "error", "❌")
                return {'success': False, 'error': error_msg}

            self.target_mean, self.target_std = normalization
            print_info_line("Нормализация цели",
                            f"mean {self.target_mean:.1f} / std {self.target_std:.1f} убийств",
                            "📊", Colors.BLUE_3, Colors.BRIGHT_CYAN)

            # Ранняя проверка наличия HDF5 файла до загрузки моделей
            if data_source == 'hdf5' and (not hdf5_path or not Path(hdf5_path).exists()):
                error_msg = f"HDF5 файл не найден: {hdf5_path}"
                print_status_message(error_msg, "error", "❌")
                return {'success': False, 'error': error_msg}

            print_info_line("Score v1 Data Loader инициализирован",
                            f"Героев для модели: {self.loader.hero_mapper.total_heroes}",
                            "🧙‍♂️", Colors.BLUE_3, Colors.BRIGHT_GREEN)

            # Загрузка моделей ансамбля
            models = self._load_models(model_paths)

            # Подготовка данных выбранного источника
            features, true_labels = self._load_test_data(data_source, hdf5_path)

            # Средний счёт команд в тестовой выборке
            print_info_line("Средний счёт теста",
                            f"Radiant {true_labels[:, RADIANT_COL].mean():.1f} / "
                            f"Dire {true_labels[:, DIRE_COL].mean():.1f} убийств",
                            "⚔️", Colors.BLUE_3, Colors.BRIGHT_CYAN)

            # Предсказания каждой модели (в нормированном пространстве); ансамбль = среднее
            print_subsection_header("Получение предсказаний", "🔮", Colors.BRIGHT_CYAN)
            model_predictions_norm = self._get_model_predictions(models, features)
            # Разворот усреднённого предсказания в убийства
            ensemble_predictions = self._denormalize(model_predictions_norm.mean(axis=0))

            print_info_line("Обработано матчей", f"{ensemble_predictions.shape[0]:,}", "📊",
                            Colors.BLUE_3, Colors.BRIGHT_GREEN)
            print_status_message("Предсказания получены!", "success", "✅")

            # Метрики ансамбля рассчитываются один раз: общие и по командам
            metrics = self._calculate_metrics(ensemble_predictions, true_labels)

            # Сводка по командам (MAE/RMSE/R² раздельно для Radiant и Dire)
            print_subsection_header("Сводка по командам", "📊", Colors.BRIGHT_BLUE)
            self._print_team_summary(metrics)

            # Метрики каждой модели по отдельности (общие по обоим счётам)
            per_model_metrics = self._print_per_model_metrics(
                model_paths, model_predictions_norm, true_labels
            )

            # Сравнение с baseline предсказания среднего
            print_subsection_header("Сравнение с baseline", "⚖️", Colors.BRIGHT_ORANGE)
            baseline_comparison = self._print_baseline_comparison(metrics, true_labels)

            # Средние метрики по моделям
            print_subsection_header("Средние по моделям (стабильность фолдов)", "📋", Colors.BRIGHT_CYAN)
            self._print_fold_stability(per_model_metrics)

            # Метрики ансамбля (soft-voting), общие по обоим счётам
            print_subsection_header("Метрики ансамбля (soft-voting)", "🎯", Colors.BRIGHT_GOLD)
            self._print_ensemble_metrics(metrics)

            # Сборка итогового результата
            results = {
                'success': True,
                'model_paths': model_paths,
                'data_source': data_source,
                'test_samples': int(ensemble_predictions.shape[0]),
                'target_mean': self.target_mean,
                'target_std': self.target_std,
                'metrics': metrics,
                'per_model_metrics': per_model_metrics,
                'baseline_comparison': baseline_comparison,
                'league_filter': set(self.league_ids)
            }

            print_section_header("ТЕСТИРОВАНИЕ ЗАВЕРШЕНО", "🏆", 80, Colors.BRIGHT_GREEN)
            return results

        except Exception as e:
            error_msg = f"Ошибка при тестировании: {str(e)}"
            print_status_message(error_msg, "error", "💥")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': error_msg}

        finally:
            # Освобождение ресурсов маппера героев
            self.loader.hero_mapper.cleanup()

    def _denormalize(self, predictions_norm: np.ndarray) -> np.ndarray:
        """
        Разворачивает предсказания из нормированного пространства в убийства.

        Обратно к стандартизации тренера: pred_убийств = pred_норм * std + mean.
        Применяется поэлементно, поэтому одинаково работает для обоих столбцов счёта.

        Args:
            predictions_norm (np.ndarray): Предсказания в нормированных единицах, (N, 2)

        Returns:
            np.ndarray: Предсказания счёта в убийствах, (N, 2)
        """

        return predictions_norm * self.target_std + self.target_mean

    def _print_test_configuration(self, data_source: str) -> None:
        """
        Выводит конфигурацию тестирования: источник, параметры предсказаний, фильтры.

        Args:
            data_source (str): Источник данных ('db' или 'hdf5')
        """

        print_subsection_header("Конфигурация тестирования", "🔧", Colors.BRIGHT_CYAN)

        source_label = "База данных (PostgreSQL)" if data_source == 'db' else "HDF5 файл"
        print_info_line("Источник данных", source_label, "🗃️",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("Размер батча", f"{self.batch_size:,}", "📦",
                        Colors.BLUE_3, Colors.BRIGHT_BLUE)

        # Параметры, относящиеся только к БД
        if data_source == 'db':
            limit_label = f"{self.max_matches:,}" if self.max_matches is not None else "все"
            print_info_line("Лимит матчей", limit_label, "🔢",
                            Colors.BLUE_3, Colors.BRIGHT_WHITE)
            leagues_label = (", ".join(map(str, sorted(self.league_ids)))
                             if self.league_ids else "все лиги")
            print_info_line("Фильтр лиг", leagues_label, "🏆",
                            Colors.BLUE_3, Colors.BRIGHT_PURPLE)
        print()

    @staticmethod
    def _discover_model_paths(model_base_path: str) -> List[str]:
        """
        Адаптивно обнаруживает модели ансамбля сканированием директории.

        Ищет файлы по шаблону имени фолдов (<stem>_fold_*.keras) и сортирует их по номеру фолда.

        Args:
            model_base_path (str): Базовый путь к моделям

        Returns:
            List[str]: Отсортированные по номеру фолда пути к моделям
                (пустой список, если ничего не найдено)
        """

        model_dir = Path(model_base_path).parent
        model_name_prefix = Path(model_base_path).stem + "_fold_"
        return sorted(
            [str(p) for p in model_dir.glob(f"{model_name_prefix}*.keras")],
            key=lambda x: int(Path(x).stem.split('_fold_')[-1])  # Сортировка по номеру фолда
        )

    @staticmethod
    def _load_target_normalization(model_base_path: str) -> Optional[Tuple[float, float]]:
        """
        Загружает параметры нормализации цели из JSON в папке прогона.

        Файл создаётся тренером рядом с моделями и хранит общие mean/std, которыми
        стандартизовался счёт обеих команд при обучении. Без него развернуть
        предсказания в убийства нельзя.

        Args:
            model_base_path (str): Базовый путь к моделям (определяет папку прогона)

        Returns:
            Optional[Tuple[float, float]]: (mean, std) или None, если файла нет
        """

        norm_path = Path(model_base_path).parent / TARGET_NORM_FILENAME
        if not norm_path.exists():
            return None

        with open(norm_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return float(data['mean']), float(data['std'])

    @staticmethod
    def _load_models(model_paths: List) -> List:
        """
        Загружает модели ансамбля из указанных путей.

        Args:
            model_paths (List): Пути к файлам моделей фолдов

        Returns:
            List: Список загруженных моделей Keras
        """

        print_subsection_header("Загрузка ансамбля моделей", "🤖", Colors.BRIGHT_GREEN)
        models = []
        for model_path in model_paths:
            model = load_model(model_path)
            models.append(model)
            print_status_message(f"Модель {Path(model_path).name} загружена!", "success", "✅")
            print_info_line("Параметров модели", f"{model.count_params():,}", "🔢",
                            Colors.BLUE_3, Colors.BRIGHT_BLUE)
        return models

    def _load_test_data(self, data_source: str, hdf5_path: Optional[str]):
        """
        Загружает тестовые данные выбранного источника в единый Dict формат.

        Доступ к источнику (БД или HDF5) полностью инкапсулирован в загрузчике,
        обе ветки возвращают единый Dict формат признаков и метки. Тип целевой
        переменной задаётся MODEL_TYPE — он же определяет, что загрузчик читает
        как метки (для score это счёт обеих команд, shape (N, 2)).

        Args:
            data_source (str): Источник данных ('db' или 'hdf5')
            hdf5_path (Optional[str]): Путь к HDF5 файлу (для data_source='hdf5')

        Returns:
            Tuple[Dict[str, np.ndarray], np.ndarray]: Признаки и истинные метки (N, 2)
        """

        if data_source == 'db':
            return self.loader.load_data_from_db(
                target=MODEL_TYPE,
                limit=self.max_matches,
                league_ids=self.league_ids
            )
        return self.loader.load_data_from_hdf5(hdf5_path, target=MODEL_TYPE)

    def _get_model_predictions(self, models: List, features: Dict[str, np.ndarray]) -> np.ndarray:
        """
        Вычисляет предсказания каждой модели ансамбля по отдельности, батчами.

        Возвращает массив предсказаний (в нормированном пространстве цели) без
        усреднения, чтобы вызывающий код мог получить и метрики ансамбля (среднее
        по моделям), и метрики каждой модели в отдельности за один прогон.

        Последовательность действий:
        1. Поочерёдный проход по моделям ансамбля
        2. Один вызов model.predict() на всю выборку с внутренней батчёвкой Keras
        3. Накопление предсказаний по каждой модели отдельно
        4. Обновление прогресс-бара по числу обработанных моделей

        Батчинг выполняется внутри Keras через аргумент batch_size: один вызов
        predict() на модель обходит всю выборку и ограничивает потребление памяти
        на больших выборках.

        Args:
            models (List): Список загруженных моделей Keras (фолды ансамбля)
            features (Dict[str, np.ndarray]): Признаки {'radiant_heroes', 'dire_heroes'}

        Returns:
            np.ndarray: Предсказания счёта в нормированных единицах, shape (n_models, N, 2).
                Усреднение по оси 0 даёт предсказание ансамбля.
        """

        n_models = len(models)

        # Накопитель предсказаний по каждой модели отдельно
        per_model_predictions = []

        print_status_message(f"Запуск: {n_models} моделей, батч {self.batch_size}...", "info", "🔮")

        for model_idx, model in enumerate(models):
            # Один вызов на модель: батчинг и обход выборки выполняет сам Keras.
            # Выход (N, 2) не сплющиваем — два столбца счёта сохраняются.
            model_preds = model.predict(features, batch_size=self.batch_size, verbose=0)
            per_model_predictions.append(model_preds)

            # Прогресс по числу обработанных моделей ансамбля
            print_progress_bar(model_idx + 1, n_models, "Предсказания:", 40,
                               Colors.BRIGHT_GREEN, Colors.DIM)

        return np.array(per_model_predictions)

    @classmethod
    def _calculate_metrics(cls, predictions: np.ndarray, true_labels: np.ndarray) -> Dict[str, Any]:
        """
        Вычисляет регрессионные метрики качества для предсказаний счёта (в убийствах).

        Метрики считаются на трёх срезах:
        - 'radiant': только столбец счёта Radiant
        - 'dire': только столбец счёта Dire
        - 'overall': оба счёта вместе (конкатенация столбцов)

        Для каждого среза считаются MAE, RMSE и R². Применяется как к предсказаниям
        ансамбля, так и к отдельной модели.

        Args:
            predictions (np.ndarray): Предсказания счёта в убийствах, shape (N, 2)
            true_labels (np.ndarray): Истинные метки счёта в убийствах, shape (N, 2)

        Returns:
            Dict[str, Any]: {'radiant': {...}, 'dire': {...}, 'overall': {...}},
                где каждый срез содержит mae, rmse, r2
        """

        return {
            'radiant': cls._metrics_for_slice(predictions[:, RADIANT_COL], true_labels[:, RADIANT_COL]),
            'dire': cls._metrics_for_slice(predictions[:, DIRE_COL], true_labels[:, DIRE_COL]),
            # Общий срез: оба счёта рассматриваются как один пул значений
            'overall': cls._metrics_for_slice(predictions.reshape(-1), true_labels.reshape(-1))
        }

    @staticmethod
    def _metrics_for_slice(predictions: np.ndarray, true_labels: np.ndarray) -> Dict[str, float]:
        """
        Считает MAE, RMSE и R² для одномерного среза предсказаний и меток.

        R² возвращает 0.0, если дисперсия истинных меток нулевая (метрика не определена).

        Args:
            predictions (np.ndarray): Предсказания (убийства), shape (M,)
            true_labels (np.ndarray): Истинные метки (убийства), shape (M,)

        Returns:
            Dict[str, float]: mae, rmse, r2
        """

        errors = predictions - true_labels
        mae = float(np.mean(np.abs(errors)))
        rmse = float(np.sqrt(np.mean(errors ** 2)))

        # R² не определён при нулевой дисперсии истинных меток
        try:
            r2 = float(r2_score(true_labels, predictions))
        except ValueError:
            r2 = 0.0

        return {'mae': mae, 'rmse': rmse, 'r2': r2}

    def _print_per_model_metrics(self,
                                 model_paths: List[str],
                                 model_predictions_norm: np.ndarray,
                                 true_labels: np.ndarray) -> List[Dict[str, Any]]:
        """
        Считает и выводит метрики каждой модели ансамбля по отдельности.

        Предсказания каждой модели разворачиваются в убийства индивидуально, после чего
        считаются те же метрики, что и для ансамбля. В строку выводится общий срез
        (overall); разброс этих метрик характеризует стабильность фолдов между собой.

        Args:
            model_paths (List[str]): Пути к моделям фолдов (для извлечения метки фолда)
            model_predictions_norm (np.ndarray): Предсказания (n_models, N, 2) в норм. ед.
            true_labels (np.ndarray): Истинные метки (N, 2)

        Returns:
            List[Dict[str, Any]]: Метрики по каждой модели в порядке model_paths
        """

        print_subsection_header("Метрики по моделям ансамбля", "🧩", Colors.BRIGHT_CYAN)
        per_model_metrics = []
        for model_path, model_preds_norm in zip(model_paths, model_predictions_norm):
            # Разворот предсказаний конкретной модели в убийства
            model_preds_kills = self._denormalize(model_preds_norm)
            fold_metrics = self._calculate_metrics(model_preds_kills, true_labels)
            per_model_metrics.append(fold_metrics)
            fold_label = Path(model_path).stem.split('_fold_')[-1]
            overall = fold_metrics['overall']
            fold_value = (
                f"{Colors.GOLD_4}MAE {Colors.RESET} "
                f"{Colors.BRIGHT_GREEN}{overall['mae']:.2f}{Colors.RESET}  "
                f"{Colors.GOLD_4}RMSE {Colors.RESET} "
                f"{Colors.BRIGHT_CYAN}{overall['rmse']:.2f}{Colors.RESET}  "
                f"{Colors.GOLD_4}R² {Colors.RESET} "
                f"{Colors.BRIGHT_YELLOW}{overall['r2']:.3f}{Colors.RESET}"
            )
            print_info_line(f"Модель fold_{fold_label}", fold_value, "🔹",
                            Colors.BLUE_3, Colors.RESET)
        return per_model_metrics

    @staticmethod
    def _print_team_summary(metrics: Dict[str, Any]) -> None:
        """
        Выводит метрики ансамбля раздельно по командам (Radiant и Dire).

        Раздельный разрез показывает, не предсказывается ли счёт одной команды
        заметно хуже другой.

        Args:
            metrics (Dict[str, Any]): Метрики ансамбля из _calculate_metrics
        """

        radiant = metrics['radiant']
        dire = metrics['dire']

        radiant_value = (
            f"{Colors.GOLD_4}MAE {Colors.RESET}{Colors.BRIGHT_GREEN}{radiant['mae']:.2f}{Colors.RESET}  "
            f"{Colors.GOLD_4}RMSE {Colors.RESET}{Colors.BRIGHT_CYAN}{radiant['rmse']:.2f}{Colors.RESET}  "
            f"{Colors.GOLD_4}R² {Colors.RESET}{Colors.BRIGHT_YELLOW}{radiant['r2']:.3f}{Colors.RESET}"
        )
        dire_value = (
            f"{Colors.GOLD_4}MAE {Colors.RESET}{Colors.BRIGHT_GREEN}{dire['mae']:.2f}{Colors.RESET}  "
            f"{Colors.GOLD_4}RMSE {Colors.RESET}{Colors.BRIGHT_CYAN}{dire['rmse']:.2f}{Colors.RESET}  "
            f"{Colors.GOLD_4}R² {Colors.RESET}{Colors.BRIGHT_YELLOW}{dire['r2']:.3f}{Colors.RESET}"
        )
        print_info_line("Radiant", radiant_value, "🌞",
                        Colors.BLUE_3, Colors.RESET)
        print_info_line("Dire   ", dire_value, "🌑",
                        Colors.BLUE_3, Colors.RESET)

    @staticmethod
    def _print_ensemble_metrics(metrics: Dict[str, Any]) -> None:
        """
        Выводит общие показатели качества ансамбля построчно (срез overall).

        Метрики по командам выводятся отдельно через _print_team_summary.

        Args:
            metrics (Dict[str, Any]): Метрики ансамбля из _calculate_metrics
        """

        overall = metrics['overall']
        print_info_line("MAE", f"{overall['mae']:.2f} убийств", "🎯",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("RMSE", f"{overall['rmse']:.2f} убийств", "📈",
                        Colors.BLUE_3, Colors.BRIGHT_CYAN)
        print_info_line("R²", f"{overall['r2']:.3f}", "🔍",
                        Colors.BLUE_3, Colors.BRIGHT_YELLOW)

    @staticmethod
    def _print_baseline_comparison(metrics: Dict[str, Any],
                                   true_labels: np.ndarray) -> Dict[str, float]:
        """
        Считает и выводит сравнение ансамбля с baseline предсказания среднего.

        Baseline — константное предсказание среднего счёта по каждой команде на
        тестовой выборке. Его MAE и RMSE (по обоим счётам вместе) задают планку
        «без модели»; прирост показывает, насколько ансамбль снижает ошибку. По
        построению R² baseline равен 0, поэтому положительный overall R² ансамбля
        и означает выигрыш над этой планкой.

        Args:
            metrics (Dict[str, Any]): Метрики ансамбля
            true_labels (np.ndarray): Истинные метки счёта (N, 2)

        Returns:
            Dict[str, float]: baseline_mae, baseline_rmse, mae_improvement, rmse_improvement
        """

        # Baseline предсказывает среднее по каждому столбцу отдельно
        column_means = true_labels.mean(axis=0)
        baseline_errors = true_labels - column_means
        baseline_mae = float(np.mean(np.abs(baseline_errors)))
        baseline_rmse = float(np.sqrt(np.mean(baseline_errors ** 2)))

        overall = metrics['overall']
        mae_improvement = (1 - overall['mae'] / baseline_mae) * 100 if baseline_mae > 0 else 0.0
        rmse_improvement = (1 - overall['rmse'] / baseline_rmse) * 100 if baseline_rmse > 0 else 0.0

        baseline_comparison = {
            'baseline_mae': baseline_mae,
            'baseline_rmse': baseline_rmse,
            'mae_improvement': mae_improvement,
            'rmse_improvement': rmse_improvement
        }

        print_info_line("Baseline MAE (среднее)", f"{baseline_mae:.2f} убийств", "📉",
                        Colors.BLUE_3, Colors.BRIGHT_PURPLE)
        print_info_line("MAE ансамбля", f"{overall['mae']:.2f} убийств", "🎯",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("Снижение MAE", f"{mae_improvement:+.1f}%", "📈",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("Снижение RMSE", f"{rmse_improvement:+.1f}%", "📈",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        return baseline_comparison

    @staticmethod
    def _print_fold_stability(per_model_metrics: List[Dict[str, Any]]) -> None:
        """
        Выводит средние метрики по моделям с разбросом (стабильность фолдов на тесте).

        Использует общий срез (overall) каждой модели.

        Args:
            per_model_metrics (List[Dict[str, Any]]): Метрики отдельных моделей
        """

        maes = [m['overall']['mae'] for m in per_model_metrics]
        rmses = [m['overall']['rmse'] for m in per_model_metrics]
        r2s = [m['overall']['r2'] for m in per_model_metrics]
        print_info_line("Средний MAE",
                        f"{float(np.mean(maes)):.2f} ± {float(np.std(maes)):.2f} убийств", "🎯",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("Средний RMSE",
                        f"{float(np.mean(rmses)):.2f} ± {float(np.std(rmses)):.2f} убийств", "📈",
                        Colors.BLUE_3, Colors.BRIGHT_CYAN)
        print_info_line("Средний R²",
                        f"{float(np.mean(r2s)):.3f} ± {float(np.std(r2s)):.3f}", "⚖️",
                        Colors.BLUE_3, Colors.BRIGHT_YELLOW)


def select_run(version_dir: Path) -> Path:
    """
    Определяет ансамбль для тестирования, при необходимости запрашивая выбор у пользователя.

    Сканирует version_dir на наличие папок ансамблей (run_*), извлекает их номера тем же
    регулярным выражением, что использует trainer.

    Поведение зависит от числа найденных ансамблей:
    - Ноль → сообщение об ошибке и завершение программы
    - Один → выбирается автоматически; строка о нём дописывается к стартовому блоку
    - Несколько → секция выбора со списком (число моделей в каждом) и запрос в цикле

    При нескольких ансамблях выбор делается по номеру, а не по позиции в списке: номер
    совпадает с тем, что видит пользователь, и устойчив к пропускам в нумерации.
    Пустой ввод (Enter) выбирает последний сохранённый ансамбль.

    Args:
        version_dir (Path): Директория версии модели (.../<dota>/<type>/<version>)

    Returns:
        Path: Путь к выбранной папке ансамбля
    """

    # Сбор доступных ансамблей
    runs: Dict[int, Path] = {}
    for path in version_dir.glob(f"{RUN_DIR_PREFIX}*"):
        if not path.is_dir():
            continue
        match = RUN_DIR_PATTERN.match(path.name)
        if match:
            runs[int(match.group(1))] = path

    if not runs:
        print_status_message(f"Не найдено ансамблей в {version_dir}", "error", "❌")
        sys.exit(1)

    latest = max(runs)

    # Единственный ансамбль: выбора нет, дописываем строку к стартовому блоку
    if len(runs) == 1:
        run_dir = runs[latest]
        print_info_line("Ансамбль", run_dir.name, "📦",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        return run_dir

    # Несколько ансамблей: выбор происходит
    print_section_header("ВЫБОР АНСАМБЛЯ", "🎯", 80, Colors.BRIGHT_CYAN)

    # Список доступных ансамблей с числом найденных моделей
    print_subsection_header("Доступные ансамбли", "📋", Colors.BRIGHT_YELLOW)
    for num in sorted(runs, reverse=True):
        model_count = len(list(runs[num].glob("*_fold_*.keras")))
        run_value = f"{Colors.BRIGHT_GREEN}{model_count} моделей{Colors.RESET}"
        if num == latest:
            run_value += f"  {Colors.DIM}← последний{Colors.RESET}"
        print_info_line(runs[num].name, run_value, "📦",
                        Colors.BLUE_3, Colors.RESET)
    print()

    # Цикл запроса до получения корректного ввода
    while True:
        choice = input(f"{Colors.BRIGHT_GOLD}Введите номер ансамбля "
                       f"(Enter — последний): {Colors.RESET}").strip()
        if choice == "":
            selected = runs[latest]
        elif choice.isdigit() and int(choice) in runs:
            selected = runs[int(choice)]
        else:
            print_status_message("Неверный выбор! Введите номер из списка", "error", "❌")
            continue

        print_status_message(f"Выбран ансамбль {selected.name}", "success", "✅")
        return selected


def select_data_source() -> str:
    """
    Интерактивно запрашивает у пользователя источник данных для тестирования.

    Выводит список доступных источников и в цикле ожидает корректный ввод,
    повторяя запрос при некорректном выборе.

    Returns:
        str: Код выбранного источника данных:
            - 'db' для базы данных PostgreSQL (ProMatch/ProMatchPlayer)
            - 'hdf5' для предобработанного HDF5 файла
    """

    print_section_header("ВЫБОР ИСТОЧНИКА ДАННЫХ", "🎯", 80, Colors.BRIGHT_CYAN)
    print_subsection_header("Доступные источники данных", "📋", Colors.BRIGHT_YELLOW)
    print_info_line("1", "База данных (PostgreSQL) — Профессиональные матчи", "💾",
                    Colors.BLUE_3, Colors.BRIGHT_GREEN)
    print_info_line("2", "HDF5 файл — Тестовые публичные матчи", "📦",
                    Colors.BLUE_3, Colors.BRIGHT_BLUE)
    print()

    # Цикл запроса до получения корректного ввода
    while True:
        choice = input(f"{Colors.BRIGHT_GOLD}Выберите источник (1 или 2): {Colors.RESET}").strip()
        if choice == '1':
            print_status_message("Выбрана база данных PostgreSQL", "success", "✅")
            return 'db'
        elif choice == '2':
            print_status_message("Выбран HDF5 файл", "success", "✅")
            return 'hdf5'
        else:
            print_status_message("Неверный выбор! Введите 1 или 2", "error", "❌")


def print_startup_header() -> None:
    """
    Выводит стартовый заголовок программы.
    """

    print_section_header("ЗАПУСК ТЕСТИРОВАНИЯ SCORE-PREDICTOR V1", "🚀", color=Colors.VIOLET_2)
    print_info_line("Версия модели", "Score Prediction v1", "🤖",
                    Colors.BLUE_3, Colors.BRIGHT_CYAN)
    print_info_line("Тип оценки", "Ансамбль (soft-voting) фолдов", "🧪",
                    Colors.BLUE_3, Colors.BRIGHT_GREEN)
    print_info_line("Архитектура", "Hero Embedding + MLP (регрессия ×2)", "🧠",
                    Colors.BLUE_3, Colors.LAVENDER)
    print_info_line("Фреймворк", "Keras (TensorFlow)", "🔧",
                    Colors.BLUE_3, Colors.GOLD_3)


def main() -> None:
    """Точка входа: интерактивный выбор прогона и источника данных, запуск тестирования."""

    # Стартовый заголовок
    print_startup_header()

    # Определение путей
    data_dir = Path(__file__).parent.parent / "datasets"
    models_dir = Path(__file__).parent.parent / "models"
    version_dir = models_dir / DOTA_VERSION / MODEL_TYPE / MODEL_VERSION    # Директория версии модели

    # Выбор ансамбля
    run_dir = select_run(version_dir)
    print()

    # Интерактивный выбор источника данных (на чём тестируем)
    data_source = select_data_source()
    print()

    hdf5_file_path = str(data_dir / HDF5_FILENAME.format(version=DOTA_VERSION))
    # Базовый путь к моделям внутри выбранной папки прогона
    model_base_path = str(run_dir / MODEL_FILENAME.format(version=DOTA_VERSION))

    # Создание тестера и запуск тестирования
    tester = ModelTesterScoreV1(
        batch_size=DEFAULT_BATCH_SIZE,
        max_matches=DEFAULT_MAX_MATCHES,
        league_ids=DEFAULT_LEAGUE_IDS
    )
    results = tester.test(
        model_base_path=model_base_path,
        data_source=data_source,
        hdf5_path=hdf5_file_path
    )

    # Финальный статус (детальные результаты выведены блоком метрик ансамбля выше)
    if results['success']:
        print_status_message("Тестирование успешно завершено!", "success", "🎉")
    else:
        print_status_message("Тестирование не удалось", "error", "❌")
        if 'error' in results:
            print_status_message(f"Детали ошибки: {results['error']}", "error", "💥")
        sys.exit(1)


if __name__ == "__main__":
    main()
