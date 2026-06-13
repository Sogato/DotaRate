"""
Модуль тестирования ансамбля моделей Time v1 предсказания длительности матчей Dota 2.

Модуль реализует единый сценарий оценки ансамбля из N моделей (фолдов K-fold CV):
адаптивное обнаружение моделей, получение данных из взаимозаменяемых источников,
приведение к единому формату признаков, усреднение предсказаний (soft-voting)
и расчёт регрессионных метрик с разбором ошибки по диапазонам длительности и
сравнением с baseline.

Ключевые особенности:
- Источники: HDF5 файл с тестовой выборкой и БД профессиональных матчей (взаимозаменяемы)
- Предсказания: вычисляются батчами средствами Keras для контроля потребления памяти
- Масштаб предсказаний: модели выдают длительность сразу в секундах. Разворот из
  нормированного пространства встроен в сами модели тренером (фиксированным слоем
  денормализации), поэтому тестеру не нужны никакие внешние параметры нормализации

Две оцениваемые сущности:
- Ансамбль (soft-voting): усреднение предсказаний всех моделей.
- Отдельные модели фолдов: метрика каждой по отдельности — их разброс (mean ± std).

Сценарий тестирования:
1. Интерактивный выбор прогона (ансамбля) для тестирования
2. Интерактивный выбор источника данных (HDF5 или БД)
3. Адаптивное обнаружение моделей ансамбля внутри папки прогона
4. Загрузка данных и приведение к единому Dict формату через загрузчик
5. Получение предсказаний по каждой модели (в секундах) и усреднение в ансамбль
6. Расчёт метрик ансамбля, метрик отдельных моделей и сравнение с baseline

Метрики ансамбля считаются один раз за прогон: сводка остатков выводится в начале
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
MODEL_FILENAME = "model_time_v1_{version}.keras"    # Базовое имя моделей (суффикс _fold_N в именах фолдов)

# === КОНСТАНТЫ СТРУКТУРЫ ХРАНЕНИЯ ===
MODEL_TYPE = "time"                                     # Тип модели (служит и типом цели загрузчика)
MODEL_VERSION = "v1"                                    # Версия модели данного типа
RUN_DIR_PREFIX = "run_"                                 # Префикс папки прогона
RUN_DIR_PATTERN = re.compile(r"run_(\d+)(?:_.*)?$")     # Папка прогона: run_<номер> с опциональной меткой

# === КОНСТАНТЫ ТЕСТИРОВАНИЯ ===
DEFAULT_LEAGUE_IDS: Set[int] = set()        # Фильтр по ID лиг для БД, например {5401, 4266} (пустое множество = все лиги)
DEFAULT_MAX_MATCHES = 10_000                # Лимит матчей при тестировании на БД (None = все)
DEFAULT_BATCH_SIZE = 256                    # Размер батча для предсказаний

DEFAULT_DURATION_BUCKETS_MIN = [30, 40, 50] # Границы диапазонов длительности (минуты) для разбора ошибки


class ModelTesterTimeV1:
    """
    Координатор тестирования ансамбля моделей Time v1.

    Класс управляет полным циклом оценки: адаптивное обнаружение моделей фолдов,
    загрузка ансамбля, приведение источника (БД или HDF5) к единому Dict формату
    через DataLoaderV1, получение предсказаний по каждой модели (в секундах),
    усреднение в ансамбль (soft-voting) и расчёт регрессионных метрик ансамбля,
    метрик отдельных моделей и сравнения с baseline предсказания среднего.

    Attributes:
        loader (DataLoaderV1): Загрузчик данных
        batch_size (int): Размер батча для предсказаний
        max_matches (Optional[int]): Лимит матчей для БД (None = все)
        league_ids (Set[int]): Фильтр по ID лиг для БД (пустое множество = все лиги)
        duration_buckets_min (List[int]): Границы диапазонов длительности (минуты)
    """

    def __init__(self,
                 batch_size: int = DEFAULT_BATCH_SIZE,
                 max_matches: Optional[int] = DEFAULT_MAX_MATCHES,
                 league_ids: Optional[Set[int]] = None,
                 duration_buckets_min: Optional[List[int]] = None):
        """
        Инициализирует тестер с заданной конфигурацией.

        Создает загрузчик данных и фиксирует параметры тестирования.

        Args:
            batch_size (int): Размер батча для предсказаний
            max_matches (Optional[int]): Лимит матчей для БД (None = все)
            league_ids (Optional[Set[int]]): Фильтр по лигам для БД
                (None → все лиги, пустое множество)
            duration_buckets_min (Optional[List[int]]): Границы диапазонов длительности
                (None → DEFAULT_DURATION_BUCKETS_MIN)
        """

        self.loader = DataLoaderV1()

        # Параметры тестирования
        self.batch_size = batch_size
        self.max_matches = max_matches
        self.league_ids = league_ids if league_ids is not None else set(DEFAULT_LEAGUE_IDS)
        self.duration_buckets_min = (
            duration_buckets_min if duration_buckets_min is not None
            else DEFAULT_DURATION_BUCKETS_MIN.copy()
        )

    def test(self,
             model_base_path: str,
             data_source: str,
             hdf5_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Координирует сценарий тестирования ансамбля на выбранном источнике данных.

        Последовательность действий:
        1. Адаптивное обнаружение моделей ансамбля по шаблону имени фолдов
        2. Загрузка моделей и приведение источника (БД или HDF5) к единому формату
        3. Получение предсказаний по каждой модели (в секундах) и усреднение в ансамбль
        4. Расчёт метрик ансамбля, метрик отдельных моделей и сравнение с baseline

        Метрики ансамбля рассчитываются один раз: сводка остатков выводится в начале
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
                ансамбля, метрики отдельных моделей, разбор по диапазонам, baseline
                и метаданные; при ошибке — {'success': False, 'error': ...}
        """

        # Формирование заголовка с учётом источника и фильтра лиг
        title = f"ТЕСТИРОВАНИЕ АНСАМБЛЯ (TIME) НА {'БАЗЕ ДАННЫХ' if data_source == 'db' else 'HDF5'}"
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

            # Ранняя проверка наличия HDF5 файла до загрузки моделей
            if data_source == 'hdf5' and (not hdf5_path or not Path(hdf5_path).exists()):
                error_msg = f"HDF5 файл не найден: {hdf5_path}"
                print_status_message(error_msg, "error", "❌")
                return {'success': False, 'error': error_msg}

            print_info_line("Time v1 Data Loader инициализирован",
                            f"Героев для модели: {self.loader.hero_mapper.total_heroes}",
                            "🧙‍♂️", Colors.BLUE_3, Colors.BRIGHT_GREEN)

            # Загрузка моделей ансамбля
            models = self._load_models(model_paths)

            # Подготовка данных выбранного источника
            features, true_labels = self._load_test_data(data_source, hdf5_path)

            # Распределение длительности тестовой выборки
            print_info_line("Длительность теста",
                            f"среднее {true_labels.mean() / 60:.1f} мин / "
                            f"диапазон {true_labels.min() / 60:.1f}–{true_labels.max() / 60:.1f} мин",
                            "⏱️", Colors.BLUE_3, Colors.BRIGHT_CYAN)

            # Предсказания каждой модели (в секундах); ансамбль = среднее по моделям
            print_subsection_header("Получение предсказаний", "🔮", Colors.BRIGHT_CYAN)
            model_predictions = self._get_model_predictions(models, features)
            ensemble_predictions = model_predictions.mean(axis=0)

            print_info_line("Обработано матчей", f"{ensemble_predictions.shape[0]:,}", "📊",
                            Colors.BLUE_3, Colors.BRIGHT_GREEN)
            print_status_message("Предсказания получены!", "success", "✅")

            # Метрики ансамбля рассчитываются один раз
            metrics = self._calculate_metrics(ensemble_predictions, true_labels)

            # Сводка остатков (систематическое смещение, попадание в окно)
            print_subsection_header("Сводка ошибок", "📊", Colors.BRIGHT_BLUE)
            self._print_error_summary(metrics)

            # Метрики каждой модели по отдельности
            per_model_metrics = self._print_per_model_metrics(
                model_paths, model_predictions, true_labels
            )

            # Ошибка по диапазонам длительности
            print_subsection_header("Ошибка по диапазонам длительности", "🎲", Colors.BRIGHT_GOLD)
            range_analysis = self._analyze_error_by_range(ensemble_predictions, true_labels)
            self._print_error_by_range_table(range_analysis)

            # Сравнение с baseline предсказания среднего
            print_subsection_header("Сравнение с baseline", "⚖️", Colors.BRIGHT_ORANGE)
            baseline_comparison = self._print_baseline_comparison(metrics, true_labels)

            # Средние метрики по моделям
            print_subsection_header("Средние по моделям (стабильность фолдов)", "📋", Colors.BRIGHT_CYAN)
            self._print_fold_stability(per_model_metrics)

            # Метрики ансамбля (soft-voting)
            print_subsection_header("Метрики ансамбля (soft-voting)", "🎯", Colors.BRIGHT_GOLD)
            self._print_ensemble_metrics(metrics)

            # Сборка итогового результата
            results = {
                'success': True,
                'model_paths': model_paths,
                'data_source': data_source,
                'test_samples': int(ensemble_predictions.shape[0]),
                'metrics': metrics,
                'per_model_metrics': per_model_metrics,
                'range_analysis': range_analysis,
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

        buckets_label = ", ".join(f"{b}" for b in self.duration_buckets_min)
        print_info_line("Границы диапазонов (мин)", buckets_label, "🎲",
                        Colors.BLUE_3, Colors.BRIGHT_YELLOW)
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
        как метки (для time это длительность в секундах).

        Args:
            data_source (str): Источник данных ('db' или 'hdf5')
            hdf5_path (Optional[str]): Путь к HDF5 файлу (для data_source='hdf5')

        Returns:
            Tuple[Dict[str, np.ndarray], np.ndarray]: Признаки и истинные метки (секунды)
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

        Возвращается матрица предсказаний без усреднения, чтобы вызывающий код мог получить и метрики
        ансамбля (среднее по моделям), и метрики каждой модели в отдельности за один прогон.

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
            np.ndarray: Матрица предсказаний длительности в секундах, shape (n_models, N).
                Усреднение по оси 0 даёт предсказание ансамбля.
        """

        n_models = len(models)

        # Накопитель предсказаний по каждой модели отдельно
        per_model_predictions = []

        print_status_message(f"Запуск: {n_models} моделей, батч {self.batch_size}...", "info", "🔮")

        for model_idx, model in enumerate(models):
            # Один вызов на модель: батчинг и обход выборки выполняет сам Keras
            model_preds = model.predict(features, batch_size=self.batch_size, verbose=0).flatten()
            per_model_predictions.append(model_preds)

            # Прогресс по числу обработанных моделей ансамбля
            print_progress_bar(model_idx + 1, n_models, "Предсказания:", 40,
                               Colors.BRIGHT_GREEN, Colors.DIM)

        return np.array(per_model_predictions)

    @staticmethod
    def _calculate_metrics(predictions: np.ndarray, true_labels: np.ndarray) -> Dict[str, float]:
        """
        Вычисляет регрессионные метрики качества для набора предсказаний (в секундах).

        Считает MAE, RMSE, R², медианную абсолютную ошибку, систематическое смещение
        (bias = среднее pred − факт) и долю предсказаний в пределах 5 и 10 минут от
        истины. Применяется как к предсказаниям ансамбля, так и к отдельной модели.
        Метрики ошибки дополнительно приводятся к минутам для читаемости.

        R² возвращает 0.0, если дисперсия истинных меток нулевая (метрика не определена).

        Args:
            predictions (np.ndarray): Предсказания длительности в секундах
            true_labels (np.ndarray): Истинные метки длительности в секундах

        Returns:
            Dict[str, float]: Метрики ошибки (в секундах и минутах) и доли попаданий
        """

        errors = predictions - true_labels  # знак: + переоценка, − недооценка
        mae = float(np.mean(np.abs(errors)))
        rmse = float(np.sqrt(np.mean(errors ** 2)))
        median_ae = float(np.median(np.abs(errors)))
        bias = float(np.mean(errors))

        # Доля предсказаний в пределах окна точности
        within_5min = float(np.mean(np.abs(errors) <= 5 * 60))
        within_10min = float(np.mean(np.abs(errors) <= 10 * 60))

        # R² не определён при нулевой дисперсии истинных меток
        try:
            r2 = float(r2_score(true_labels, predictions))
        except ValueError:
            r2 = 0.0

        return {
            'mae': mae,
            'rmse': rmse,
            'r2': r2,
            'median_ae': median_ae,
            'bias': bias,
            'mae_min': mae / 60.0,
            'rmse_min': rmse / 60.0,
            'median_ae_min': median_ae / 60.0,
            'bias_min': bias / 60.0,
            'within_5min': within_5min,
            'within_10min': within_10min
        }

    def _analyze_error_by_range(self, predictions: np.ndarray, true_labels: np.ndarray) -> List[Dict[str, Any]]:
        """
        Разбирает ошибку ансамбля по диапазонам истинной длительности.

        Границы берутся из self.duration_buckets_min (минуты) и задают диапазоны:
        до первой границы, между соседними границами и после последней. Для каждого
        диапазона считается число матчей, их доля и средняя абсолютная ошибка (MAE).

        Разрез показывает, где модель ошибается сильнее — обычно на хвостах
        распределения (очень короткие и очень длинные матчи).

        Args:
            predictions (np.ndarray): Предсказания длительности в секундах
            true_labels (np.ndarray): Истинные метки длительности в секундах

        Returns:
            List[Dict[str, Any]]: По каждому диапазону label, count, percentage, mae_min
        """

        abs_errors = np.abs(predictions - true_labels)
        total = len(true_labels)

        # Построение границ диапазонов в минутах: (None, e0), (e0, e1), ..., (e_last, None)
        edges = sorted(self.duration_buckets_min)
        ranges: List[Tuple[Optional[int], Optional[int]]] = []
        prev: Optional[int] = None
        for edge in edges:
            ranges.append((prev, edge))
            prev = edge
        ranges.append((prev, None))

        analysis = []
        for low_min, high_min in ranges:
            # Маска истинной длительности в границах диапазона (в секундах)
            mask = np.ones(total, dtype=bool)
            if low_min is not None:
                mask &= true_labels >= low_min * 60
            if high_min is not None:
                mask &= true_labels < high_min * 60

            count = int(np.sum(mask))
            mae_min = float(np.mean(abs_errors[mask]) / 60.0) if count > 0 else 0.0

            # Человекочитаемая метка диапазона
            if low_min is None:
                label = f"< {high_min} мин"
            elif high_min is None:
                label = f"≥ {low_min} мин"
            else:
                label = f"{low_min}–{high_min} мин"

            analysis.append({
                'label': label,
                'count': count,
                'percentage': count / total if total > 0 else 0.0,
                'mae_min': mae_min
            })

        return analysis

    def _print_per_model_metrics(self,
                                 model_paths: List[str],
                                 model_predictions: np.ndarray,
                                 true_labels: np.ndarray) -> List[Dict[str, float]]:
        """
        Считает и выводит метрики каждой модели ансамбля по отдельности.

        Разброс этих метрик характеризует не качество ансамбля, а стабильность фолдов между собой на тесте.

        Args:
            model_paths (List[str]): Пути к моделям фолдов (для извлечения метки фолда)
            model_predictions (np.ndarray): Матрица предсказаний (n_models, N) в секундах
            true_labels (np.ndarray): Истинные метки (секунды)

        Returns:
            List[Dict[str, float]]: Метрики по каждой модели в порядке model_paths
        """

        print_subsection_header("Метрики по моделям ансамбля", "🧩", Colors.BRIGHT_CYAN)
        per_model_metrics = []
        for model_path, model_preds in zip(model_paths, model_predictions):
            fold_metrics = self._calculate_metrics(model_preds, true_labels)
            per_model_metrics.append(fold_metrics)
            fold_label = Path(model_path).stem.split('_fold_')[-1]
            fold_value = (
                f"{Colors.GOLD_4}MAE {Colors.RESET} "
                f"{Colors.BRIGHT_GREEN}{fold_metrics['mae_min']:.2f}м{Colors.RESET}  "
                f"{Colors.GOLD_4}RMSE {Colors.RESET} "
                f"{Colors.BRIGHT_CYAN}{fold_metrics['rmse_min']:.2f}м{Colors.RESET}  "
                f"{Colors.GOLD_4}R² {Colors.RESET} "
                f"{Colors.BRIGHT_YELLOW}{fold_metrics['r2']:.3f}{Colors.RESET}"
            )
            print_info_line(f"Модель fold_{fold_label}", fold_value, "🔹",
                            Colors.BLUE_3, Colors.RESET)
        return per_model_metrics

    @staticmethod
    def _print_error_summary(metrics: Dict[str, float]) -> None:
        """
        Выводит сводку остатков ансамбля построчно.

        Систематическое смещение (bias) показывает, склонна ли модель в среднем
        завышать (+) или занижать (−) длительность. Доли попаданий в окно дают
        практическое ощущение точности.

        Args:
            metrics (Dict[str, float]): Метрики ансамбля из _calculate_metrics
        """

        bias_sign = "переоценка" if metrics['bias'] >= 0 else "недооценка"
        print_info_line("Смещение (pred − факт)", f"{metrics['bias_min']:+.2f} мин ({bias_sign})", "🧭",
                        Colors.BLUE_3, Colors.BRIGHT_PURPLE)
        print_info_line("Медианная ошибка", f"{metrics['median_ae_min']:.2f} мин", "📍",
                        Colors.BLUE_3, Colors.GOLD_3)
        print_info_line("Точность в ±5 мин", f"{metrics['within_5min']:.1%}", "🎯",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("Точность в ±10 мин", f"{metrics['within_10min']:.1%}", "🎯",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)

    @staticmethod
    def _print_ensemble_metrics(metrics: Dict[str, float]) -> None:
        """
        Выводит основные показатели качества ансамбля построчно.

        Сводка остатков выводится отдельно через _print_error_summary.

        Args:
            metrics (Dict[str, float]): Метрики ансамбля из _calculate_metrics
        """

        print_info_line("MAE", f"{metrics['mae_min']:.2f} мин", "🎯",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("RMSE", f"{metrics['rmse_min']:.2f} мин", "📈",
                        Colors.BLUE_3, Colors.BRIGHT_CYAN)
        print_info_line("R²", f"{metrics['r2']:.3f}", "🔍",
                        Colors.BLUE_3, Colors.BRIGHT_YELLOW)

    @staticmethod
    def _print_error_by_range_table(range_analysis: List[Dict[str, Any]]) -> None:
        """
        Выводит ошибку по диапазонам длительности построчно (одна строка на диапазон).

        Args:
            range_analysis (List[Dict[str, Any]]): Результат _analyze_error_by_range
        """

        for bucket in range_analysis:
            range_value = (
                f"{Colors.TEAL_3}Матчей:{Colors.RESET} "
                f"{Colors.BRIGHT_WHITE}{bucket['count']:,}{Colors.RESET} "
                f"{Colors.DIM}({bucket['percentage']:.1%}){Colors.RESET}  "
                f"{Colors.GOLD_4}MAE:{Colors.RESET} "
                f"{Colors.GOLD_1}{bucket['mae_min']:.2f} мин{Colors.RESET}"
            )
            print_info_line(bucket['label'], range_value, "🎲",
                            Colors.BLUE_3, Colors.RESET)

    @staticmethod
    def _print_baseline_comparison(metrics: Dict[str, float],
                                   true_labels: np.ndarray) -> Dict[str, float]:
        """
        Считает и выводит сравнение ансамбля с baseline предсказания среднего.

        Baseline — константное предсказание средней длительности по тестовой выборке.
        Его MAE и RMSE задают планку «без модели»; прирост показывает, насколько
        ансамбль снижает ошибку относительно неё. По построению R² baseline равен 0,
        поэтому положительный R² ансамбля и означает выигрыш над этой планкой.

        Args:
            metrics (Dict[str, float]): Метрики ансамбля
            true_labels (np.ndarray): Истинные метки длительности (секунды)

        Returns:
            Dict[str, float]: baseline_mae_min, baseline_rmse_min, mae_improvement, rmse_improvement
        """

        mean_label = float(np.mean(true_labels))
        baseline_errors = true_labels - mean_label
        baseline_mae = float(np.mean(np.abs(baseline_errors)))
        baseline_rmse = float(np.sqrt(np.mean(baseline_errors ** 2)))

        mae_improvement = (1 - metrics['mae'] / baseline_mae) * 100 if baseline_mae > 0 else 0.0
        rmse_improvement = (1 - metrics['rmse'] / baseline_rmse) * 100 if baseline_rmse > 0 else 0.0

        baseline_comparison = {
            'baseline_mae_min': baseline_mae / 60.0,
            'baseline_rmse_min': baseline_rmse / 60.0,
            'mae_improvement': mae_improvement,
            'rmse_improvement': rmse_improvement
        }

        print_info_line("Baseline MAE (среднее)", f"{baseline_mae / 60:.2f} мин", "📉",
                        Colors.BLUE_3, Colors.BRIGHT_PURPLE)
        print_info_line("MAE ансамбля", f"{metrics['mae_min']:.2f} мин", "🎯",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("Снижение MAE", f"{mae_improvement:+.1f}%", "📈",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("Снижение RMSE", f"{rmse_improvement:+.1f}%", "📈",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        return baseline_comparison

    @staticmethod
    def _print_fold_stability(per_model_metrics: List[Dict[str, float]]) -> None:
        """
        Выводит средние метрики по моделям с разбросом (стабильность фолдов на тесте).

        Args:
            per_model_metrics (List[Dict[str, float]]): Метрики отдельных моделей
        """

        maes = [m['mae_min'] for m in per_model_metrics]
        rmses = [m['rmse_min'] for m in per_model_metrics]
        r2s = [m['r2'] for m in per_model_metrics]
        print_info_line("Средний MAE",
                        f"{float(np.mean(maes)):.2f} ± {float(np.std(maes)):.2f} мин", "🎯",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("Средний RMSE",
                        f"{float(np.mean(rmses)):.2f} ± {float(np.std(rmses)):.2f} мин", "📈",
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

    print_section_header("ЗАПУСК ТЕСТИРОВАНИЯ TIME-PREDICTOR V1", "🚀", color=Colors.VIOLET_2)
    print_info_line("Версия модели", "Time Prediction v1", "🤖",
                    Colors.BLUE_3, Colors.BRIGHT_CYAN)
    print_info_line("Тип оценки", "Ансамбль (soft-voting) фолдов", "🧪",
                    Colors.BLUE_3, Colors.BRIGHT_GREEN)
    print_info_line("Архитектура", "Hero Embedding + MLP (регрессия)", "🧠",
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
    tester = ModelTesterTimeV1(
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
