"""
Модуль тестирования ансамбля моделей Win v1 предсказания исходов матчей Dota 2.

Модуль реализует единый сценарий оценки ансамбля из N моделей (фолдов K-fold CV):
адаптивное обнаружение моделей, получение данных из взаимозаменяемых источников,
приведение к единому формату признаков, усреднение предсказаний (soft-voting)
и расчёт итоговых метрик с разбором по уровням уверенности и сравнением с baseline.

Ключевые особенности:
- Источники: HDF5 файл с тестовой выборкой и БД профессиональных матчей (взаимозаменяемы)
- Предсказания: вычисляются батчами средствами Keras для контроля потребления памяти

Две оцениваемые сущности:
- Ансамбль (soft-voting): усреднение вероятностей всех моделей.
- Отдельные модели фолдов: метрика каждой по отдельности — их разброс (mean ± std).

Сценарий тестирования:
1. Интерактивный выбор прогона (ансамбля) для тестирования
2. Интерактивный выбор источника данных (HDF5 или БД)
3. Адаптивное обнаружение моделей ансамбля внутри папки прогона
4. Загрузка данных и приведение к единому Dict формату через загрузчик
5. Получение предсказаний по каждой модели и усреднение в ансамбль
6. Расчёт метрик ансамбля, метрик отдельных моделей и сравнение с baseline

Метрики ансамбля считаются один раз за прогон и используются дважды: компоненты матрицы
ошибок выводятся в начале отчёта, а основные показатели качества — в конце. Один и тот же
прогон моделей даёт и метрики ансамбля (среднее по моделям), и метрики отдельных фолдов.

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
from typing import Any, Dict, List, Optional, Set

# Сторонние библиотеки
import numpy as np
from keras.models import load_model
from sklearn.metrics import roc_auc_score

# Локальные импорты
from ml_training.data_loader_win_v1 import DataLoaderWinV1
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
MODEL_FILENAME = "model_win_v1_{version}.keras"     # Базовое имя моделей (суффикс _fold_N в именах фолдов)

# === КОНСТАНТЫ СТРУКТУРЫ ХРАНЕНИЯ ===
MODEL_TYPE = "win"                                      # Тип модели
MODEL_VERSION = "v1"                                    # Версия модели данного типа
RUN_DIR_PREFIX = "run_"                                 # Префикс папки прогона
RUN_DIR_PATTERN = re.compile(r"run_(\d+)(?:_.*)?$")     # Папка прогона: run_<номер> с опциональной меткой

# === КОНСТАНТЫ ТЕСТИРОВАНИЯ ===
DEFAULT_LEAGUE_IDS: Set[int] = set()        # Фильтр по ID лиг для БД, например {5401, 4266} (пустое множество = все лиги)
DEFAULT_MAX_MATCHES = 10_000                # Лимит матчей при тестировании на БД (None = все)
DEFAULT_BATCH_SIZE = 256                    # Размер батча для предсказаний

DEFAULT_CONFIDENCE_THRESHOLDS = [0.5, 0.55, 0.6, 0.65, 0.7] # Пороги уверенности для анализа точности предсказаний ансамбля.


class ModelTesterWinV1:
    """
    Координатор тестирования ансамбля моделей Win v1.

    Класс управляет полным циклом оценки: адаптивное обнаружение моделей фолдов,
    загрузка ансамбля, приведение источника (БД или HDF5) к единому Dict формату
    через DataLoaderWinV1, получение предсказаний по каждой модели, усреднение
    в ансамбль (soft-voting) и расчёт метрик ансамбля, метрик отдельных моделей
    и сравнения с baseline мажоритарного класса.

    Attributes:
        loader (DataLoaderWinV1): Загрузчик данных
        batch_size (int): Размер батча для предсказаний
        max_matches (Optional[int]): Лимит матчей для БД (None = все)
        league_ids (Set[int]): Фильтр по ID лиг для БД (пустое множество = все лиги)
        confidence_thresholds (List[float]): Пороги уверенности для анализа точности
    """

    def __init__(self,
                 batch_size: int = DEFAULT_BATCH_SIZE,
                 max_matches: Optional[int] = DEFAULT_MAX_MATCHES,
                 league_ids: Optional[Set[int]] = None,
                 confidence_thresholds: Optional[List[float]] = None):
        """
        Инициализирует тестер с заданной конфигурацией.

        Создает загрузчик данных и фиксирует параметры тестирования.

        Args:
            batch_size (int): Размер батча для предсказаний
            max_matches (Optional[int]): Лимит матчей для БД (None = все)
            league_ids (Optional[Set[int]]): Фильтр по лигам для БД
                (None → все лиги, пустое множество)
            confidence_thresholds (Optional[List[float]]): Пороги уверенности
                (None → DEFAULT_CONFIDENCE_THRESHOLDS)
        """

        self.loader = DataLoaderWinV1()

        # Параметры тестирования
        self.batch_size = batch_size
        self.max_matches = max_matches
        self.league_ids = league_ids if league_ids is not None else set(DEFAULT_LEAGUE_IDS)
        self.confidence_thresholds = (
            confidence_thresholds if confidence_thresholds is not None
            else DEFAULT_CONFIDENCE_THRESHOLDS.copy()
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
        3. Получение предсказаний по каждой модели и усреднение в ансамбль
        4. Расчёт метрик ансамбля, метрик отдельных моделей и сравнение с baseline

        Метрики ансамбля рассчитываются один раз: компоненты матрицы ошибок выводятся
        в начале отчёта, а основные показатели качества — в конце. Baseline мажоритарного
        класса считается из истинных меток тестовой выборки и доступен одинаково для
        обоих источников данных.

        Args:
            model_base_path (str): Базовый путь к моделям внутри папки прогона;
                суффикс _fold_N.keras обнаруживается автоматически сканированием директории
            data_source (str): Источник данных ('db' или 'hdf5')
            hdf5_path (Optional[str]): Путь к HDF5 файлу (требуется для data_source='hdf5')

        Returns:
            Dict[str, Any]: Результаты тестирования. При успехе содержит метрики
                ансамбля, метрики отдельных моделей, анализ уверенности, baseline
                и метаданные; при ошибке — {'success': False, 'error': ...}
        """

        # Формирование заголовка с учётом источника и фильтра лиг
        title = f"ТЕСТИРОВАНИЕ АНСАМБЛЯ НА {'БАЗЕ ДАННЫХ' if data_source == 'db' else 'HDF5'}"
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

            print_info_line("Win v1 Data Loader инициализирован",
                            f"Героев для модели: {self.loader.hero_mapper.total_heroes}",
                            "🧙‍♂️", Colors.BLUE_3, Colors.BRIGHT_GREEN)

            # Загрузка моделей ансамбля
            models = self._load_models(model_paths)

            # Подготовка данных выбранного источника: доступ инкапсулирован в загрузчике
            features, true_labels = self._load_test_data(data_source, hdf5_path)

            # Баланс классов тестовой выборки
            test_radiant_rate = float(true_labels.mean())
            print_info_line("Баланс выборки",
                            f"Radiant {test_radiant_rate:.1%} / Dire {1 - test_radiant_rate:.1%}",
                            "⚖️", Colors.BLUE_3, Colors.BRIGHT_CYAN)

            # Получение предсказаний по каждой модели; ансамбль = среднее по моделям
            print_subsection_header("Получение предсказаний", "🔮", Colors.BRIGHT_CYAN)
            model_predictions = self._get_model_predictions(models, features)
            ensemble_predictions = model_predictions.mean(axis=0)

            print_info_line("Обработано матчей", f"{ensemble_predictions.shape[0]:,}", "📊",
                            Colors.BLUE_3, Colors.BRIGHT_GREEN)
            print_status_message("Предсказания получены!", "success", "✅")

            # Метрики ансамбля рассчитываются один раз
            metrics = self._calculate_metrics(ensemble_predictions, true_labels)

            # Матрица ошибок ансамбля (Radiant = положительный класс)
            print_subsection_header("Матрица ошибок", "📊", Colors.BRIGHT_BLUE)
            self._print_confusion_matrix(metrics)

            # Метрики каждой модели по отдельности
            per_model_metrics = self._print_per_model_metrics(
                model_paths, model_predictions, true_labels
            )

            # Точность по уровням уверенности ансамбля
            print_subsection_header("Точность по уровням уверенности", "🎲", Colors.BRIGHT_GOLD)
            confidence_analysis = self._analyze_confidence_accuracy(ensemble_predictions, true_labels)
            self._print_confidence_table(confidence_analysis)

            # Сравнение с baseline мажоритарного класса
            print_subsection_header("Сравнение с baseline", "⚖️", Colors.BRIGHT_ORANGE)
            baseline_comparison = self._print_baseline_comparison(metrics, test_radiant_rate)

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
                'test_radiant_rate': test_radiant_rate,
                'metrics': metrics,
                'per_model_metrics': per_model_metrics,
                'confidence_analysis': confidence_analysis,
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

        thresholds_label = ", ".join(f"{int(t * 100)}%" for t in self.confidence_thresholds)
        print_info_line("Пороги уверенности", thresholds_label, "🎲",
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
        обе ветки возвращают единый Dict формат признаков и метки.

        Args:
            data_source (str): Источник данных ('db' или 'hdf5')
            hdf5_path (Optional[str]): Путь к HDF5 файлу (для data_source='hdf5')

        Returns:
            Tuple[Dict[str, np.ndarray], np.ndarray]: Признаки и истинные метки
        """

        if data_source == 'db':
            return self.loader.load_data_from_db(
                limit=self.max_matches,
                league_ids=self.league_ids
            )
        return self.loader.load_data_from_hdf5(hdf5_path)

    def _get_model_predictions(self, models: List, features: Dict[str, np.ndarray]) -> np.ndarray:
        """
        Вычисляет предсказания каждой модели ансамбля по отдельности, батчами.

        Возвращает матрицу предсказаний без усреднения, чтобы вызывающий код мог
        получить и метрики ансамбля (среднее по моделям), и метрики каждой модели
        в отдельности за один прогон.

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
            np.ndarray: Матрица вероятностей победы Radiant, shape (n_models, N).
                Усреднение по оси 0 даёт предсказание ансамбля (soft-voting).
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
        Вычисляет основные метрики качества и матрицу ошибок для набора предсказаний.

        Бинаризует вероятности по порогу 0.5 (ровно 0.5 → Radiant) и считает accuracy, precision,
        recall, F1 и компоненты матрицы ошибок. AUC вычисляется напрямую по вероятностям.
        Применяется как к предсказаниям ансамбля, так и к предсказаниям отдельной модели.

        Победа Radiant (метка 1) трактуется как положительный класс: precision и recall
        считаются относительно предсказания побед Radiant. AUC возвращает 0.0, если
        в выборке присутствует только один класс (roc_auc_score не определён).

        Args:
            predictions (np.ndarray): Вероятности победы Radiant (диапазон 0-1)
            true_labels (np.ndarray): Истинные метки (1 = победа Radiant, 0 = победа Dire)

        Returns:
            Dict[str, float]: Метрики и компоненты матрицы ошибок:
                - 'accuracy', 'precision', 'recall', 'f1_score', 'auc'
                - 'true_positives', 'true_negatives', 'false_positives', 'false_negatives'
        """

        # Бинаризация вероятностей по порогу 0.5
        binary_predictions = (predictions >= 0.5).astype(int)
        accuracy = float(np.mean(binary_predictions == true_labels))

        # Компоненты матрицы ошибок (Radiant = положительный класс)
        tp = int(np.sum((binary_predictions == 1) & (true_labels == 1)))
        tn = int(np.sum((binary_predictions == 0) & (true_labels == 0)))
        fp = int(np.sum((binary_predictions == 1) & (true_labels == 0)))
        fn = int(np.sum((binary_predictions == 0) & (true_labels == 1)))

        # Производные метрики с защитой от деления на ноль
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

        # AUC по вероятностям; не определён при единственном классе в выборке
        try:
            auc_score = float(roc_auc_score(true_labels, predictions))
        except ValueError:
            auc_score = 0.0

        return {
            'accuracy': accuracy,
            'precision': precision,
            'recall': recall,
            'f1_score': f1_score,
            'auc': auc_score,
            'true_positives': int(tp),
            'true_negatives': int(tn),
            'false_positives': int(fp),
            'false_negatives': int(fn)
        }

    def _analyze_confidence_accuracy(self, predictions: np.ndarray, true_labels: np.ndarray) -> Dict:
        """
        Анализирует точность предсказаний ансамбля в разрезе уровней уверенности.

        Пороги берутся из self.confidence_thresholds. Для каждого порога выделяет
        три группы предсказаний и считает их точность и долю от общего числа:
        1. Уверенные за Radiant: вероятность >= threshold
        2. Уверенные за Dire: вероятность <= (1 - threshold)
        3. Все уверенные предсказания: объединение групп 1 и 2

        Чем выше порог, тем меньше предсказаний попадает в уверенную группу, но тем
        выше ожидаемая точность для хорошо откалиброванной модели.

        Args:
            predictions (np.ndarray): Вероятности победы Radiant (диапазон 0-1)
            true_labels (np.ndarray): Истинные метки (1 = Radiant, 0 = Dire)

        Returns:
            Dict: Анализ по каждому порогу с ключами вида 'confidence_{N}', где для
                каждой группы указаны count, accuracy и percentage_of_total.
        """

        analysis = {}
        for threshold in self.confidence_thresholds:
            # Группа уверенных предсказаний за Radiant
            high_radiant_mask = predictions >= threshold
            high_radiant_count = np.sum(high_radiant_mask)
            high_radiant_accuracy = np.mean(true_labels[high_radiant_mask]) if high_radiant_count > 0 else 0.0

            # Группа уверенных предсказаний за Dire (зеркальный порог)
            high_dire_mask = predictions <= (1 - threshold)
            high_dire_count = np.sum(high_dire_mask)
            high_dire_accuracy = np.mean(1 - true_labels[high_dire_mask]) if high_dire_count > 0 else 0.0

            # Объединённая группа всех уверенных предсказаний
            confident_mask = (predictions >= threshold) | (predictions <= (1 - threshold))
            confident_count = np.sum(confident_mask)
            if confident_count > 0:
                confident_predictions = (predictions[confident_mask] >= 0.5).astype(int)
                confident_labels = true_labels[confident_mask]
                overall_confident_accuracy = np.mean(confident_predictions == confident_labels)
            else:
                overall_confident_accuracy = 0.0

            analysis[f"confidence_{int(threshold * 100)}"] = {
                'threshold': threshold,
                'radiant_confident': {
                    'count': int(high_radiant_count),
                    'accuracy': high_radiant_accuracy,
                    'percentage_of_total': high_radiant_count / len(predictions) if len(predictions) > 0 else 0.0
                },
                'dire_confident': {
                    'count': int(high_dire_count),
                    'accuracy': high_dire_accuracy,
                    'percentage_of_total': high_dire_count / len(predictions) if len(predictions) > 0 else 0.0
                },
                'overall_confident': {
                    'count': int(confident_count),
                    'accuracy': overall_confident_accuracy,
                    'percentage_of_total': confident_count / len(predictions) if len(predictions) > 0 else 0.0
                }
            }
        return analysis

    def _print_per_model_metrics(self,
                                 model_paths: List[str],
                                 model_predictions: np.ndarray,
                                 true_labels: np.ndarray) -> List[Dict[str, float]]:
        """
        Считает и выводит метрики каждой модели ансамбля по отдельности.

        Разброс этих метрик характеризует не качество ансамбля, а стабильность
        фолдов между собой на тестовой выборке.

        Args:
            model_paths (List[str]): Пути к моделям фолдов (для извлечения метки фолда)
            model_predictions (np.ndarray): Матрица предсказаний (n_models, N)
            true_labels (np.ndarray): Истинные метки

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
                f"{Colors.GOLD_4}Accuracy {Colors.RESET} "
                f"{Colors.BRIGHT_GREEN}{fold_metrics['accuracy']:.1%}{Colors.RESET}  "
                f"{Colors.GOLD_4}AUC {Colors.RESET} "
                f"{Colors.BRIGHT_CYAN}{fold_metrics['auc']:.1%}{Colors.RESET}  "
                f"{Colors.GOLD_4}F1 {Colors.RESET} "
                f"{Colors.BRIGHT_YELLOW}{fold_metrics['f1_score']:.1%}{Colors.RESET}"
            )
            print_info_line(f"Модель fold_{fold_label}", fold_value, "🔹",
                            Colors.BLUE_3, Colors.RESET)
        return per_model_metrics

    @staticmethod
    def _print_confusion_matrix(metrics: Dict[str, float]) -> None:
        """
        Выводит матрицу ошибок построчно (Radiant = положительный класс).

        Args:
            metrics (Dict[str, float]): Метрики ансамбля из _calculate_metrics
        """

        print_info_line("Radiant угадан верно (TP)", f"{metrics['true_positives']:,}", "✅",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("Dire угадан верно (TN)", f"{metrics['true_negatives']:,}", "✅",
                        Colors.BLUE_3, Colors.GOLD_3)
        print_info_line("Radiant вместо Dire (FP)", f"{metrics['false_positives']:,}", "❌",
                        Colors.BLUE_3, Colors.BRIGHT_ORANGE)
        print_info_line("Dire вместо Radiant (FN)", f"{metrics['false_negatives']:,}", "❌",
                        Colors.BLUE_3, Colors.BRIGHT_RED)

    @staticmethod
    def _print_ensemble_metrics(metrics: Dict[str, float]) -> None:
        """
        Выводит основные показатели качества ансамбля построчно.

        Матрица ошибок выводится отдельно через _print_confusion_matrix.

        Args:
            metrics (Dict[str, float]): Метрики ансамбля из _calculate_metrics
        """

        print_info_line("Accuracy", f"{metrics['accuracy']:.1%}", "🎯",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("Precision", f"{metrics['precision']:.1%}", "🔍",
                        Colors.BLUE_3, Colors.BRIGHT_BLUE)
        print_info_line("Recall", f"{metrics['recall']:.1%}", "🎣",
                        Colors.BLUE_3, Colors.BRIGHT_PURPLE)
        print_info_line("F1-Score", f"{metrics['f1_score']:.1%}", "⚖️",
                        Colors.BLUE_3, Colors.BRIGHT_YELLOW)
        print_info_line("AUC", f"{metrics['auc']:.1%}", "📈",
                        Colors.BLUE_3, Colors.BRIGHT_CYAN)

    @staticmethod
    def _print_confidence_table(confidence_analysis: Dict) -> None:
        """
        Выводит точность по уровням уверенности построчно (одна строка на порог).

        Каждая строка собирается в цветной value-стринг: счётчик уверенных предсказаний и их доля,
        общая точность, а также точность среди уверенных предсказаний каждой стороны.

        Args:
            confidence_analysis (Dict): Результат _analyze_confidence_accuracy
        """

        for conf_data in confidence_analysis.values():
            threshold = conf_data['threshold']
            overall = conf_data['overall_confident']
            radiant = conf_data['radiant_confident']
            dire = conf_data['dire_confident']

            # Сборка цветного value: уверенные / точность / точность по сторонам
            conf_value = (
                f"{Colors.TEAL_3}Предсказаний:{Colors.RESET} "
                f"{Colors.BRIGHT_WHITE}{overall['count']:,}{Colors.RESET} "
                f"{Colors.DIM}({overall['percentage_of_total']:.1%}){Colors.RESET}  "
                f"{Colors.GOLD_4}Точность:{Colors.RESET} "
                f"{Colors.GOLD_1}{overall['accuracy']:.1%}{Colors.RESET}  "
                f"{Colors.BRIGHT_BLUE}Radiant:{Colors.RESET} "
                f"{Colors.BLUE_4}{radiant['accuracy']:.1%}{Colors.RESET}  "
                f"{Colors.BRIGHT_RED}Dire:{Colors.RESET} "
                f"{Colors.BRIGHT_ORANGE}{dire['accuracy']:.1%}{Colors.RESET}"
            )
            print_info_line(f"Порог ≥{int(threshold * 100)}%", conf_value, "🎲",
                            Colors.BLUE_3, Colors.RESET)

    @staticmethod
    def _print_baseline_comparison(metrics: Dict[str, float],
                                   test_radiant_rate: float) -> Dict[str, float]:
        """
        Считает и выводит сравнение точности ансамбля с baseline мажоритарного класса.

        Args:
            metrics (Dict[str, float]): Метрики ансамбля
            test_radiant_rate (float): Доля побед Radiant в тестовой выборке

        Returns:
            Dict[str, float]: baseline_accuracy, model_improvement, improvement_percentage
        """

        majority_accuracy = max(test_radiant_rate, 1 - test_radiant_rate)
        improvement = (metrics['accuracy'] / majority_accuracy - 1) * 100 if majority_accuracy > 0 else 0.0
        baseline_comparison = {
            'baseline_accuracy': majority_accuracy,
            'model_improvement': metrics['accuracy'] - majority_accuracy,
            'improvement_percentage': improvement
        }
        print_info_line("Baseline (мажоритарный класс)", f"{majority_accuracy:.1%}", "📉",
                        Colors.BLUE_3, Colors.BRIGHT_PURPLE)
        print_info_line("Точность ансамбля", f"{metrics['accuracy']:.1%}", "🎯",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("Прирост над baseline", f"+{baseline_comparison['model_improvement'] * 100:.1f} п.п.", "📈",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        return baseline_comparison

    @staticmethod
    def _print_fold_stability(per_model_metrics: List[Dict[str, float]]) -> None:
        """
        Выводит средние метрики по моделям с разбросом (стабильность фолдов на тесте).

        Args:
            per_model_metrics (List[Dict[str, float]]): Метрики отдельных моделей
        """

        accuracies = [m['accuracy'] for m in per_model_metrics]
        aucs = [m['auc'] for m in per_model_metrics]
        f1_scores = [m['f1_score'] for m in per_model_metrics]
        print_info_line("Средняя Accuracy",
                        f"{float(np.mean(accuracies)):.1%} ± {float(np.std(accuracies)):.1%}", "🎯",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("Средний AUC",
                        f"{float(np.mean(aucs)):.1%} ± {float(np.std(aucs)):.1%}", "📈",
                        Colors.BLUE_3, Colors.BRIGHT_CYAN)
        print_info_line("Средний F1",
                        f"{float(np.mean(f1_scores)):.1%} ± {float(np.std(f1_scores)):.1%}", "⚖️",
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

    print_section_header("ЗАПУСК ТЕСТИРОВАНИЯ WIN-PREDICTOR V1", "🚀", color=Colors.VIOLET_2)
    print_info_line("Версия модели", "Win Prediction v1", "🤖",
                    Colors.BLUE_3, Colors.BRIGHT_CYAN)
    print_info_line("Тип оценки", "Ансамбль (soft-voting) фолдов", "🧪",
                    Colors.BLUE_3, Colors.BRIGHT_GREEN)
    print_info_line("Архитектура", "Hero Embedding + MLP", "🧠",
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
    tester = ModelTesterWinV1(
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
