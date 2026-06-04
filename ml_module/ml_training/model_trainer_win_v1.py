"""
Модуль обучения модели Win v1 предсказания исходов матчей Dota 2 с K-fold кросс-валидацией.

Модуль реализует полный цикл обучения ансамбля из N моделей (N_FOLDS):
разбиение данных, создание архитектуры нейросети, компиляцию, обучение
отдельных моделей и сбор итоговой статистики.

Win v1 представляет собой архитектуру, основанную на условном подходе
"bag-of-heroes" (мешок героев), где модель обучается предсказывать исход матча
исключительно по составам команд без явного моделирования синергий или контр-пиков.

Ключевые особенности:
- Вход: плотные индексы героев (5 Radiant + 5 Dire)
- Embedding слой: общий для обеих команд, преобразует индексы в векторы
- Average Pooling: агрегация через GlobalAveragePooling1D (усреднение векторов команды)
- Представление команды: единый вектор без учёта взаимодействий между героями
- MLP: последовательность Dense → BatchNorm → ReLU → Dropout слоёв
- Выходной слой: sigmoid активация для вероятности победы Radiant
- Воспроизводимость: единый сид RANDOM_SEED фиксирует все источники случайности

Архитектурные ограничения:
- Модель не различает связи между героями (синергии внутри команды или контр-пиков между командами)
- Модель не учитывает позиции/роли героев или их порядок в пике
- Общий embedding делает представления героев одинаковыми вне зависимости от стороны (Radiant/Dire)
- Подход основан на усреднении "силы" состава без явных попарных взаимодействий

Эта архитектура служит отправной точкой для оценки предсказательной способности
моделей на основе только составов героев с минимальной архитектурной сложностью.

Структура хранения моделей:
Каждое обучение сохраняется в отдельную папку прогона:

    models / <DOTA_VERSION> / <MODEL_TYPE> / <MODEL_VERSION> / run_<NNN> / model_*_fold_N.keras
"""
import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '1'

# Стандартные библиотеки
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Сторонние библиотеки
import numpy as np
from keras import Input, Model
from keras.backend import clear_session as keras_clear_session
from keras.callbacks import Callback, EarlyStopping, ReduceLROnPlateau
from keras.layers import (
    Activation,
    BatchNormalization,
    Concatenate,
    Dense,
    Dropout,
    Embedding,
    GlobalAveragePooling1D,
)
from keras.metrics import AUC, Precision, Recall
from keras.optimizers import Adam
from keras.regularizers import L2
from keras.utils import set_random_seed
from sklearn.model_selection import StratifiedKFold

# Локальные импорты
from ml_training.data_loader_win_v1 import DataLoaderWinV1
from config import DOTA_VERSION, TEAM_SIZE
from utils.console import (
    Colors,
    print_info_line,
    print_section_header,
    print_status_message,
    print_subsection_header,
)

# === КОНСТАНТЫ ФАЙЛОВ ===
HDF5_FILENAME = "HDF5_dataset_{version}_main.h5"    # Имя HDF5 файла с данными
MODEL_FILENAME = "model_win_v1_{version}.keras"     # Базовое имя сохраняемой модели

# === КОНСТАНТЫ СТРУКТУРЫ ХРАНЕНИЯ ===
MODEL_TYPE = "win"                                      # Тип модели
MODEL_VERSION = "v1"                                    # Версия модели данного типа
RUN_DIR_PREFIX = "run_"                                 # Префикс папки прогона
RUN_DIR_PATTERN = re.compile(r"run_(\d+)(?:_.*)?$")     # Папка прогона: run_<номер> с опциональной меткой

# === КОНСТАНТЫ АРХИТЕКТУРЫ ===
DEFAULT_HIDDEN_UNITS = [256, 128]   # Размеры скрытых Dense слоев
DEFAULT_EMBEDDING_DIM = 32          # Размерность embedding векторов героев
DEFAULT_DROPOUT_RATE = 0.2          # Коэффициент dropout для регуляризации
DEFAULT_L2_REG = 0.001              # Коэффициент L2 регуляризации весов

# === КОНСТАНТЫ ОБУЧЕНИЯ ===
RANDOM_SEED = 42                    # Единый сид воспроизводимости
DEFAULT_EPOCHS = 1000               # Максимальное количество эпох обучения
DEFAULT_BATCH_SIZE = 512            # Размер батча при обучении
DEFAULT_LEARNING_RATE = 0.0005      # Начальная скорость обучения оптимизатора
N_FOLDS = 5                         # Количество фолдов для кросс-валидации

# === КОНСТАНТЫ CALLBACKS ===
# EarlyStopping - остановка обучения при отсутствии улучшений
EARLY_STOPPING_PATIENCE = 25        # Количество эпох без улучшения до остановки
EARLY_STOPPING_MIN_DELTA = 0.0001   # Минимальное изменение для учета как улучшение
EARLY_STOPPING_MONITOR = 'val_loss' # Метрика для отслеживания
EARLY_STOPPING_MODE = 'min'         # Режим отслеживания

# ReduceLROnPlateau - снижение learning rate при плато
REDUCE_LR_PATIENCE = 5          # Количество эпох без улучшения до снижения LR
REDUCE_LR_FACTOR = 0.5          # Коэффициент снижения LR (новый LR = старый × factor)
REDUCE_LR_COOLDOWN = 1          # Количество эпох ожидания после снижения LR
REDUCE_LR_MIN_LR = 0.000001     # Минимальное значение LR
REDUCE_LR_MONITOR = 'val_loss'  # Метрика для отслеживания плато


class ModelTrainerWinV1:
    """
    Координатор обучения ансамбля моделей Win v1 с K-fold кросс-валидацией.

    Класс управляет полным циклом обучения: загрузка данных через DataLoaderWinV1,
    разбиение на фолды, создание архитектуры, компиляция, обучение отдельных моделей
    и сбор итоговой статистики по всем фолдам.

    Attributes:
        loader (DataLoaderWinV1): Загрузчик данных
        batch_size (int): Размер батча при обучении
        epochs (int): Максимальное количество эпох
        learning_rate (float): Начальная скорость обучения
        embedding_dim (int): Размерность embedding векторов
        hidden_units (List[int]): Размеры скрытых Dense слоев
        dropout_rate (float): Коэффициент dropout
        l2_reg (float): Коэффициент L2 регуляризации
    """

    def __init__(self,
                 batch_size: int = DEFAULT_BATCH_SIZE,
                 epochs: int = DEFAULT_EPOCHS,
                 learning_rate: float = DEFAULT_LEARNING_RATE,
                 embedding_dim: int = DEFAULT_EMBEDDING_DIM,
                 hidden_units: Optional[List[int]] = None,
                 dropout_rate: float = DEFAULT_DROPOUT_RATE,
                 l2_reg: float = DEFAULT_L2_REG):
        """
        Инициализирует тренер с заданными гиперпараметрами.

        Создает загрузчик данных и определяет количество героев
        для корректной настройки embedding слоя.

        Args:
            batch_size (int): Размер батча при обучении
            epochs (int): Максимальное количество эпох
            learning_rate (float): Начальная скорость обучения
            embedding_dim (int): Размерность embedding векторов
            hidden_units (List[int]): Размеры скрытых Dense слоев
            dropout_rate (float): Коэффициент dropout
            l2_reg (float): Коэффициент L2 регуляризации
        """

        self.loader = DataLoaderWinV1()

        # Параметры обучения
        self.batch_size = batch_size
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.embedding_dim = embedding_dim
        self.hidden_units = hidden_units if hidden_units is not None else DEFAULT_HIDDEN_UNITS.copy()
        self.dropout_rate = dropout_rate
        self.l2_reg = l2_reg

    def train(self, data_file_path: str, base_model_path: str) -> None:
        """
        Координирует полный цикл обучения ансамбля с K-fold кросс-валидацией.

        Последовательность действий:
        1. Фиксация сида (RANDOM_SEED) для всех источников случайности
        2. Загрузка данных из HDF5 файла (в Dict формате)
        3. Разбиение на N_FOLDS стратифицированных фолдов
        4. Обучение отдельной модели на каждом фолде
        5. Сохранение каждой модели с суффиксом _fold_N
        6. Вывод итоговой статистики по всем фолдам

        Args:
            data_file_path (str): Путь к HDF5 файлу с данными
            base_model_path (str): Базовый путь для сохранения моделей

        Raises:
            FileNotFoundError: Если HDF5 файл не найден
            OSError: Если не удается получить доступ к файлу
            ValueError: Если данные некорректны (пустой датасет и т.д.)
        """

        print_section_header("ПОДГОТОВКА ДАННЫХ К ОБУЧЕНИЮ", "📦", color=Colors.BLUE_2)

        try:
            # Фиксация сида до создания моделей: один вызов покрывает Python random, numpy и бэкенд TF
            set_random_seed(RANDOM_SEED)

            # Вывод конфигурации
            self._print_model_configuration()

            # Загрузка данных напрямую через DataLoader
            features_all, labels_all = self.loader.load_data_from_hdf5(data_file_path)

            # Инициализация кросс-валидации (разбиение завязано на тот же RANDOM_SEED)
            total_samples = features_all['radiant_heroes'].shape[0]
            skf = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=RANDOM_SEED)

            fold_results = []

            print_section_header(f"ОБУЧЕНИЕ {N_FOLDS} МОДЕЛЕЙ (K-FOLD CV)", "🧠", color=Colors.GREEN_1)

            # Обучение отдельных фолдов
            for fold_num, (train_idx, val_idx) in enumerate(skf.split(np.zeros(total_samples), labels_all), 1):
                fold_result = self._train_single_fold(
                    fold_num=fold_num,
                    train_idx=train_idx,
                    val_idx=val_idx,
                    features_all=features_all,
                    labels_all=labels_all,
                    save_path=base_model_path
                )
                fold_results.append(fold_result)

            # Итоговая статистика
            self._print_ensemble_summary(fold_results)
            print_status_message("Обучение ансамбля успешно завершено!", "success")

        except (FileNotFoundError, OSError) as e:
            error_msg = f"Ошибка доступа к файлам: {str(e)}"
            print_status_message(error_msg, "error")
            raise

        except ValueError as e:
            error_msg = f"Ошибка данных: {str(e)}"
            print_status_message(error_msg, "error")
            raise

        except Exception as e:
            error_msg = f"Непредвиденная ошибка при обучении: {str(e)}"
            print_status_message(error_msg, "error")
            import traceback
            traceback.print_exc()
            raise

        finally:
            self.loader.hero_mapper.cleanup()

    def _print_model_configuration(self) -> None:
        """
        Выводит конфигурацию модели: архитектуру, регуляризацию, параметры обучения.
        """

        print_subsection_header("Конфигурация модели", "🔧", Colors.TEAL_2)

        # Архитектура
        print_info_line("Количество героев", f"{self.loader.hero_mapper.total_heroes:,}", "🧙‍♂️",
                        Colors.BLUE_3, Colors.BRIGHT_CYAN)
        print_info_line("Размер команды", f"{TEAM_SIZE}", "👥",
                        Colors.BLUE_3, Colors.BRIGHT_WHITE)
        print_info_line("Размерность embedding", f"{self.embedding_dim}", "📐",
                        Colors.BLUE_3, Colors.LAVENDER)
        print_info_line("Скрытые слои", f"{self.hidden_units}", "🧠",
                        Colors.BLUE_3, Colors.BRIGHT_PURPLE)

        # Регуляризация
        print_info_line("Dropout rate", f"{self.dropout_rate}", "🔢",
                        Colors.BLUE_3, Colors.ORANGE)
        print_info_line("L2 регуляризация", f"{self.l2_reg}", "⚙️",
                        Colors.BLUE_3, Colors.CORAL)

        # Параметры обучения
        print_info_line("Фиксированный сид", f"{RANDOM_SEED}", "🎲",
                        Colors.BLUE_3, Colors.BRIGHT_YELLOW)
        print_info_line("Количество фолдов", f"{N_FOLDS}", "🎯",
                        Colors.BLUE_3, Colors.BRIGHT_WHITE)
        print_info_line("Количество эпох", f"{self.epochs}", "🔁",
                        Colors.BLUE_3, Colors.MINT)
        print_info_line("Размер батча", f"{self.batch_size:,}", "📦",
                        Colors.BLUE_3, Colors.BRIGHT_GREEN)
        print_info_line("Скорость обучения", f"{self.learning_rate}", "🚀",
                        Colors.BLUE_3, Colors.GOLD_3)

    def _build_and_compile_model(self) -> Model:
        """
        Создает и компилирует архитектуру нейросети Win v1.

        Архитектура:
        1. Входы: radiant_heroes [5] + dire_heroes [5] (int32 индексы)
        2. Shared Embedding [num_heroes → embedding_dim] с L2 регуляризацией
        3. Average Pooling: GlobalAveragePooling1D для каждой команды
        4. Concatenate → объединение представлений команд [2 × embedding_dim]
        5. MLP: Dense → BatchNorm → ReLU → Dropout (повторяется N раз)
        6. Выходной слой: Dense(1, sigmoid) → вероятность победы Radiant

        Особенности реализации:
        - Average pooling создаёт permutation-invariant представление команды
          (порядок героев не влияет на результат). Это простая агрегация, которая
          усредняет векторы всех героев команды без учёта их взаимодействий.
          Выбор между средним и суммой здесь не имеет значения: они отличаются
          лишь постоянным множителем, который поглощается весами следующего
          слоя Dense, поэтому отдельное масштабирование не применяется.
        - Shared embedding: один embedding слой для обеих команд, что означает
          одинаковое представление героя вне зависимости от стороны (Radiant/Dire).

        Компиляция:
        - Loss: binary_crossentropy
        - Optimizer: Adam
        - Metrics: accuracy, AUC, precision, recall

        Returns:
            Model: Скомпилированная модель готовая к обучению
        """

        # Входные слои
        radiant_input = Input(shape=(TEAM_SIZE,), dtype='int32', name='radiant_heroes')
        dire_input = Input(shape=(TEAM_SIZE,), dtype='int32', name='dire_heroes')

        # Общий embedding слой для героев
        hero_embedding = Embedding(
            input_dim=self.loader.hero_mapper.total_heroes,
            output_dim=self.embedding_dim,
            embeddings_regularizer=L2(self.l2_reg),
            name='hero_embedding'
        )

        # Применение embedding к командам
        radiant_emb = hero_embedding(radiant_input)  # [batch, 5, emb_dim]
        dire_emb = hero_embedding(dire_input)

        # Average pooling: агрегация 5 героев команды в один вектор.
        radiant_pooled = GlobalAveragePooling1D(name='radiant_avg')(radiant_emb)
        dire_pooled = GlobalAveragePooling1D(name='dire_avg')(dire_emb)

        # Конкатенация векторов команд
        combined = Concatenate(name='concatenate')([radiant_pooled, dire_pooled])

        # Скрытые слои: последовательность Dense → BatchNorm → ReLU → Dropout
        x = combined
        for layer_idx, units in enumerate(self.hidden_units):
            x = Dense(
                units,
                activation=None,
                kernel_regularizer=L2(self.l2_reg),
                name=f'dense_{layer_idx}'
            )(x)
            x = BatchNormalization(name=f'bn_{layer_idx}')(x)
            x = Activation('relu', name=f'relu_{layer_idx}')(x)
            x = Dropout(self.dropout_rate, name=f'dropout_{layer_idx}')(x)

        # Выходной слой
        output = Dense(1, activation='sigmoid', name='output')(x)

        # Создание модели
        model = Model(
            inputs={'radiant_heroes': radiant_input, 'dire_heroes': dire_input},
            outputs=output,
            name='dota2_win_predictor'
        )

        # Компиляция
        model.compile(
            optimizer=Adam(learning_rate=self.learning_rate),
            loss='binary_crossentropy',
            metrics=[
                'accuracy',
                AUC(name='auc'),
                Precision(name='precision'),
                Recall(name='recall')
            ]
        )

        return model

    def _train_single_fold(self,
                           fold_num: int,
                           train_idx: np.ndarray,
                           val_idx: np.ndarray,
                           features_all: Dict[str, np.ndarray],
                           labels_all: np.ndarray,
                           save_path: str) -> Dict[str, Any]:
        """
        Обучает одну модель на указанном фолде.

        Последовательность действий:
        1. Разбиение данных на train/val по индексам из Dict формата
        2. Создание и компиляция модели
        3. Настройка callbacks (EarlyStopping, ReduceLROnPlateau)
        4. Обучение через model.fit()
        5. Оценка восстановленных лучших весов на валидации (model.evaluate)
        6. Сохранение модели с суффиксом _fold_N
        7. Очистка памяти

        Args:
            fold_num (int): Номер текущего фолда (1-based)
            train_idx (np.ndarray): Индексы тренировочных данных
            val_idx (np.ndarray): Индексы валидационных данных
            features_all (Dict[str, np.ndarray]): Все признаки в Dict формате
            labels_all (np.ndarray): Все метки результатов
            save_path (str): Базовый путь для сохранения модели

        Returns:
            Dict[str, Any]: Результаты фолда с метриками и путем к модели
        """

        print_subsection_header(f"Обучение фолда {fold_num}/{N_FOLDS}", "🎯", Colors.BRIGHT_GREEN)

        fold_start_time = time.time()

        # Подготовка данных фолда из Dict формата
        X_train = {
            'radiant_heroes': features_all['radiant_heroes'][train_idx],
            'dire_heroes': features_all['dire_heroes'][train_idx]
        }
        y_train = labels_all[train_idx]

        X_val = {
            'radiant_heroes': features_all['radiant_heroes'][val_idx],
            'dire_heroes': features_all['dire_heroes'][val_idx]
        }
        y_val = labels_all[val_idx]

        train_size = len(train_idx)
        val_size = len(val_idx)

        print_info_line("Train примеров", f"{train_size:,}", "🏋",
                        Colors.BLUE_3, Colors.BRIGHT_PURPLE)
        print_info_line("Val примеров", f"{val_size:,}", "🔬",
                        Colors.BLUE_3, Colors.BRIGHT_CYAN)
        print()

        # Создание модели
        model = self._build_and_compile_model()

        # Создание Callbacks
        callbacks_list = self._create_callbacks()

        # Обучение
        history = model.fit(
            X_train,
            y_train,
            batch_size=self.batch_size,
            validation_data=(X_val, y_val),
            epochs=self.epochs,
            callbacks=callbacks_list,
            verbose=1
        )

        # Оценка восстановленных лучших весов на валидации.
        val_metrics = model.evaluate(
            X_val, y_val,
            batch_size=self.batch_size,
            verbose=0,
            return_dict=True
        )
        best_val_auc = float(val_metrics['auc'])
        best_val_acc = float(val_metrics['accuracy'])
        best_val_loss = float(val_metrics['loss'])
        epochs_trained = len(history.history['loss'])

        # Сохранение модели
        fold_path = self._create_fold_model_path(save_path, fold_num)
        model.save(fold_path)

        fold_training_time = time.time() - fold_start_time

        # Вывод статистики
        self._print_fold_statistics(
            fold_num, model.count_params(),
            best_val_auc, best_val_acc, best_val_loss, epochs_trained, fold_training_time
        )

        # Очистка памяти: сначала удаляем ссылку на модель, затем очищаем сессию Keras.
        del model
        keras_clear_session()

        return {
            'fold': fold_num,
            'model_path': fold_path,
            'train_size': train_size,
            'val_size': val_size,
            'epochs_trained': epochs_trained,
            'training_time': fold_training_time,
            'best_val_auc': best_val_auc,
            'best_val_accuracy': best_val_acc,
            'best_val_loss': best_val_loss,
            'history': history.history
        }

    @staticmethod
    def _create_callbacks() -> List[Callback]:
        """
        Создает список callbacks для контроля обучения.

        Настраивает:
        - EarlyStopping: остановка при отсутствии улучшения val_loss
        - ReduceLROnPlateau: снижение learning rate при плато val_loss

        Returns:
            List[Callback]: Список настроенных callbacks
        """

        return [
            EarlyStopping(
                monitor=EARLY_STOPPING_MONITOR,
                mode=EARLY_STOPPING_MODE,
                patience=EARLY_STOPPING_PATIENCE,
                min_delta=EARLY_STOPPING_MIN_DELTA,
                restore_best_weights=True,
                verbose=1
            ),
            ReduceLROnPlateau(
                monitor=REDUCE_LR_MONITOR,
                factor=REDUCE_LR_FACTOR,
                patience=REDUCE_LR_PATIENCE,
                cooldown=REDUCE_LR_COOLDOWN,
                min_lr=REDUCE_LR_MIN_LR,
                verbose=1
            )
        ]

    @staticmethod
    def _print_fold_statistics(fold_num: int, model_params: int, best_val_auc: float, best_val_acc: float,
                               best_val_loss: float, epochs_trained: int, training_time: float) -> None:
        """
        Выводит статистику завершенного фолда.

        Args:
            fold_num (int): Номер фолда
            model_params (int): Количество параметров модели
            best_val_auc (float): AUC сохранённой модели на валидации
            best_val_acc (float): Accuracy сохранённой модели на валидации
            best_val_loss (float): Loss сохранённой модели на валидации
            epochs_trained (int): Количество обученных эпох
            training_time (float): Время обучения в секундах
        """

        print_subsection_header(f"Результаты фолда {fold_num}", "📈", Colors.BRIGHT_GREEN)

        print_info_line("Эпох обучено", f"{epochs_trained}", "🔁",
                        Colors.BLUE_3, Colors.MINT)
        print_info_line("Val AUC", f"{best_val_auc:.4f}", "⭐",
                        Colors.BLUE_3, Colors.GOLD_1)
        print_info_line("Val accuracy", f"{best_val_acc:.4f}", "🌟",
                        Colors.BLUE_3, Colors.GOLD_3)
        print_info_line("Val loss", f"{best_val_loss:.4f}", "📉",
                        Colors.BLUE_3, Colors.BRIGHT_PURPLE)
        print_info_line("Параметров модели", f"{model_params:,}", "🧩",
                        Colors.BLUE_3, Colors.BRIGHT_CYAN)
        print_info_line("Время обучения", f"{training_time / 60:.1f} мин.", "⏱️",
                        Colors.BLUE_3, Colors.LAVENDER)

    @staticmethod
    def _print_ensemble_summary(fold_results: List[Dict[str, Any]]) -> None:
        """
        Выводит итоговую статистику по всему ансамблю: отображает средние метрики, стандартное отклонение.

        Args:
            fold_results (List[Dict[str, Any]]): Результаты всех фолдов
        """

        print_section_header("ФИНАЛЬНЫЕ РЕЗУЛЬТАТЫ АНСАМБЛЯ", "🏆", color=Colors.GOLD_1)

        # Расчет средних метрик
        avg_val_auc = float(np.mean([fold_data['best_val_auc'] for fold_data in fold_results]))
        avg_val_acc = float(np.mean([fold_data['best_val_accuracy'] for fold_data in fold_results]))
        std_val_auc = float(np.std([fold_data['best_val_auc'] for fold_data in fold_results]))
        std_val_acc = float(np.std([fold_data['best_val_accuracy'] for fold_data in fold_results]))
        avg_training_time = float(np.mean([fold_data['training_time'] for fold_data in fold_results]))

        print_subsection_header("Средние метрики по фолдам", "🧪", Colors.GOLD_2)
        print_info_line("Средний val AUC", f"{avg_val_auc:.4f} ± {std_val_auc:.4f}", "📏",
                        Colors.BLUE_3, Colors.GOLD_1)
        print_info_line("Средняя val accuracy", f"{avg_val_acc:.4f} ± {std_val_acc:.4f}", "⚖️",
                        Colors.BLUE_3, Colors.GOLD_3)
        print_info_line("Среднее время обучения фолда", f"{avg_training_time / 60:.1f} мин.", "⏰",
                        Colors.BLUE_3, Colors.LAVENDER)

        # Детальная статистика
        print_subsection_header("Детальная статистика фолдов", "📋", Colors.GOLD_2)
        for fold_data in fold_results:
            fold_time = fold_data['training_time']
            fold_value = (
                f"{Colors.GOLD_4}AUC:{Colors.RESET} {Colors.GOLD_1}{fold_data['best_val_auc']:.4f}{Colors.RESET}, "
                f"{Colors.BRIGHT_GREEN}ACC:{Colors.RESET} {Colors.GOLD_3}{fold_data['best_val_accuracy']:.4f}{Colors.RESET}, "
                f"{Colors.TEAL_3}Эпох:{Colors.RESET} {Colors.BRIGHT_WHITE}{fold_data['epochs_trained']}{Colors.RESET}, "
                f"{Colors.BRIGHT_PURPLE}Время:{Colors.RESET} {Colors.LAVENDER}{fold_time / 60:.1f}м{Colors.RESET}")

            print_info_line(f"Фолд {fold_data['fold']}", fold_value, "📝",
                            Colors.BLUE_3, Colors.RESET)
        print()

    @staticmethod
    def _create_fold_model_path(base_path: str, fold_num: int) -> str:
        """
        Генерирует путь для сохранения модели конкретного фолда.

        Добавляет суффикс _fold_N к базовому пути модели.

        Args:
            base_path (str): Базовый путь к модели
            fold_num (int): Номер фолда (1-based)

        Returns:
            str: Путь для сохранения модели фолда
        """

        path_obj = Path(base_path)
        return str(path_obj.with_name(f"{path_obj.stem}_fold_{fold_num}{path_obj.suffix}"))


def print_startup_header() -> None:
    """
    Выводит стартовый заголовок программы.
    """

    print_section_header("ЗАПУСК ОБУЧЕНИЯ WIN-PREDICTOR V1", "🚀", color=Colors.VIOLET_2)
    print_info_line("Версия модели", "Win Prediction v1", "🤖",
                    Colors.BLUE_3, Colors.BRIGHT_CYAN)
    print_info_line("Тип валидации", "K-Fold Cross Validation", "🧪",
                    Colors.BLUE_3, Colors.BRIGHT_GREEN)
    print_info_line("Архитектура", "Hero Embedding + MLP", "🧠",
                    Colors.BLUE_3, Colors.LAVENDER)
    print_info_line("Фреймворк", "Keras (TensorFlow)", "🔧",
                    Colors.BLUE_3, Colors.GOLD_3)


def resolve_next_run_dir(version_dir: Path) -> Path:
    """
    Определяет директорию следующего прогона с инкрементным номером.

    Сканирует version_dir на наличие папок прогонов (run_*), извлекает их номера
    регулярным выражением RUN_DIR_PATTERN и возвращает путь к следующей папке с
    номером max+1, дополненным нулями до трёх цифр (run_001, run_002, ...).

    Номер берётся из цифр сразу после префикса, а необязательная ручная метка после
    цифр (например, run_001_best) игнорируется при извлечении номера. За счёт этого
    нумерация остаётся согласованной даже если папки прогонов переименованы вручную:
    переименованный run_001_best всё равно считается занятым номером 1.

    Ведущие нули обязательны для лексикографической сортировки папок (без них run_10
    встал бы между run_1 и run_2). Трёх цифр хватает на 999 прогонов.

    Args:
        version_dir (Path): Директория версии модели (.../<dota>/<type>/<version>)

    Returns:
        Path: Путь к директории следующего прогона (ещё не созданной)
    """

    existing_numbers = []
    for path in version_dir.glob(f"{RUN_DIR_PREFIX}*"):
        if not path.is_dir():
            continue
        match = RUN_DIR_PATTERN.match(path.name)
        if match:
            existing_numbers.append(int(match.group(1)))

    next_number = max(existing_numbers, default=0) + 1
    return version_dir / f"{RUN_DIR_PREFIX}{next_number:03d}"


def prepare_paths(data_file: str, version_dir: Path) -> Optional[Path]:
    """
    Проверяет источник данных и подготавливает директорию прогона для сохранения моделей.

    Выводит две независимые группы информации: входные данные и хранение моделей.

    Args:
        data_file (str): Путь к HDF5 файлу с данными
        version_dir (Path): Директория версии модели (.../<dota>/<type>/<version>)

    Returns:
        Optional[Path]: Путь к созданной папке прогона при успехе, None при ошибке
    """

    #Блок 1: входные данные
    print_subsection_header("Входные данные", "🗃️", Colors.AMBER_2)

    data_file_path = Path(data_file)
    if not data_file_path.exists():
        print_status_message(f"HDF5 файл не найден: {data_file}", "error", "❌")
        return None

    file_size_mb = data_file_path.stat().st_size / (1024 * 1024)
    print_info_line("Файл", data_file_path.name, "📦",
                    Colors.BLUE_3, Colors.BRIGHT_GREEN)
    print_info_line("Размер", f"{file_size_mb:.2f} MB", "📏",
                    Colors.BLUE_3, Colors.BRIGHT_WHITE)
    print_info_line("Путь", str(data_file_path), "📂",
                    Colors.BLUE_3, Colors.AMBER_4)

    #Блок 2: хранение моделей
    print_subsection_header("Хранение моделей", "💾", Colors.AMBER_2)

    try:
        # Создание цепочки директорий версии и проверка возможности записи
        version_dir.mkdir(parents=True, exist_ok=True)
        test_file = version_dir / ".write_test"
        test_file.touch()
        test_file.unlink()

        # Определение и создание папки текущего прогона (инкрементный номер)
        run_dir = resolve_next_run_dir(version_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        print_status_message(f"Невозможно создать/использовать директорию моделей: {e}", "error", "❌")
        return None

    print_info_line("Тип / версия", f"{MODEL_TYPE} {MODEL_VERSION} (Dota {DOTA_VERSION})", "🧩",
                    Colors.BLUE_3, Colors.BRIGHT_GREEN)
    print_info_line("Прогон", run_dir.name, "🔢",
                    Colors.BLUE_3, Colors.BRIGHT_CYAN)
    print_info_line("Путь", str(run_dir), "📂",
                    Colors.BLUE_3, Colors.AMBER_4)
    print()

    return run_dir


def main() -> None:
    """Точка входа: запуск обучения."""

    # Стартовый заголовок
    print_startup_header()

    # Определение путей
    data_dir = Path(__file__).parent.parent / "datasets"
    models_dir = Path(__file__).parent.parent / "models"

    hdf5_file_path = str(data_dir / HDF5_FILENAME.format(version=DOTA_VERSION))
    version_dir = models_dir / DOTA_VERSION / MODEL_TYPE / MODEL_VERSION    # Директория версии модели

    # Подготовка путей: проверка источника и создание папки прогона
    run_dir = prepare_paths(hdf5_file_path, version_dir)
    if run_dir is None:
        print_status_message("Подготовка путей не пройдена. Завершение программы.", "error")
        sys.exit(1)

    # Базовый путь к моделям внутри папки прогона
    model_output_path = str(run_dir / MODEL_FILENAME.format(version=DOTA_VERSION))

    # Создание тренера и запуск обучения
    trainer = ModelTrainerWinV1(epochs=DEFAULT_EPOCHS)
    trainer.train(data_file_path=hdf5_file_path, base_model_path=model_output_path)


if __name__ == "__main__":
    main()
