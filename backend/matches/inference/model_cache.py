"""
Модуль кэширования Keras-моделей для предсказаний.

Модуль предоставляет класс ModelCache для единовременной загрузки ансамблей
Keras-моделей с диска и быстрого доступа к ним по типу прогноза
(win/time/score).

Файлы моделей ищутся в settings.MODELS_DIR по шаблону
"model_{type}_{version}_{dota}_fold_N.keras"; версии задаются в
settings.MODEL_VERSIONS.

Основные этапы работы:
1. Создание экземпляра ModelCache (модели не загружаются)
2. Вызов initialize() — поиск файлов по шаблону, загрузка ансамблей с диска
3. Обращение к get_models()

Особенности:
- Модели загружаются только при явном вызове initialize()
- Повторные вызовы безопасны и не вызывают повторной загрузки
- initialize() не бросает исключений: при отсутствии файлов или ошибке загрузки возвращает False
- Память оценивается по количеству параметров моделей — это приближённая оценка весов
"""

# Стандартные библиотеки
from typing import Dict, List

# Сторонние библиотеки
from keras import Model
from keras.models import load_model
from django.conf import settings

# Локальные импорты
from dota_core.config import DOTA_VERSION
from dota_core.utils.memory import format_memory
from dota_core.utils.console import (
    Colors,
    print_subsection_header,
    print_info_line,
)

# Шаблон базового имени файла модели.
MODEL_BASENAME = "model_{type}_{version}_{dota}"

# Размер одного параметра модели в байтах (float32).
BYTES_PER_PARAM = 4


class ModelCache:
    """
    Кэш Keras-моделей для предсказаний.

    Загружает ансамбли моделей с диска один раз при вызове initialize()
    и сохраняет их в памяти на всё время работы сервера. Повторные вызовы
    initialize() безопасны — повторная загрузка не выполняется.

    Attributes:
        _cache (Dict[str, List[Model]]): Словарь {тип_прогноза: список_моделей}
        _initialized (bool): Флаг успешной инициализации кэша

    Example:
        cache = ModelCache()
        cache.initialize()
        win_models = cache.get_ensemble('win')
    """

    def __init__(self) -> None:
        """
        Создаёт пустой экземпляр кэша.

        К диску не обращается, модели не загружает.
        Для загрузки вызовите initialize().
        """

        # Словарь {тип_прогноза: список моделей ансамбля}, заполняется при initialize()
        self._cache: Dict[str, List[Model]] = {}

        # Флаг успешной инициализации, защищает от повторной загрузки
        self._initialized: bool = False

    def initialize(self) -> bool:
        """
        Загружает ансамбли всех типов согласно settings.MODEL_VERSIONS.

        При повторном вызове возвращает True без повторной загрузки.

        Returns:
            bool: True если все ансамбли загружены и готовы к работе,
                  False если для какого-то типа не найдено моделей
                  или произошла ошибка
        """

        # Защита от повторной инициализации
        if self._initialized:
            return True

        try:
            print_subsection_header("Инициализация кэша моделей", "🧠", Colors.BRIGHT_CYAN)

            # Загружаем ансамбль для каждого типа прогноза
            for model_type, model_version in settings.MODEL_VERSIONS.items():
                ensemble = self._load_ensemble(model_type, model_version)

                # Ни одного файла по шаблону — кэш неполный, работать нельзя
                if not ensemble:
                    print_info_line("Статус", "Инициализация не выполнена", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
                    print()
                    self._cache = {}
                    return False

                self._cache[model_type] = ensemble

            # Подтверждение успешной загрузки
            total_models = sum(len(ensemble) for ensemble in self._cache.values())
            print_info_line("Всего моделей", f"{total_models}", "📦", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
            print_info_line("Задействовано памяти", f"~{format_memory(self.memory_usage_bytes())} (веса моделей)", "🧠", Colors.BRIGHT_WHITE, Colors.BRIGHT_CYAN)
            print_info_line("Статус", "Кэш инициализирован успешно", "✅", Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
            print()

            self._initialized = True
            return True

        except Exception as e:
            # Логируем ошибку и возвращаем False без пробрасывания исключения
            print_info_line("Ошибка", str(e), "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
            print_info_line("Статус", "Инициализация не выполнена", "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED)
            print()
            self._cache = {}
            return False

    def _load_ensemble(self, model_type: str, model_version: str) -> List[Model]:
        """
        Загружает все модели одного типа, упорядоченные по номеру.

        Args:
            model_type (str): Тип модели ('win', 'time', 'score')
            model_version (str): Версия модели (например, 'v1')

        Returns:
            List[Model]: Модели ансамбля в порядке возрастания номера.
                Пустой список, если по шаблону не найдено ни одного файла.
        """

        type_dir = settings.MODELS_DIR / model_type
        base_name = MODEL_BASENAME.format(type=model_type, version=model_version, dota=DOTA_VERSION)

        # Номер модели берётся из суффикса '_fold_N' в имени файла и задаёт порядок.
        model_paths = sorted(
            type_dir.glob(f"{base_name}_fold_*.keras"),
            key=lambda p: int(p.stem.split('_fold_')[-1])
        )

        if not model_paths:
            print_info_line(
                f"{model_type} {model_version}",
                f"не найдено моделей в {type_dir} (шаблон {base_name}_fold_*.keras)",
                "❌", Colors.BRIGHT_WHITE, Colors.BRIGHT_RED
            )
            return []

        print_info_line(f"{model_type} {model_version}", f"загружено моделей: {len(model_paths)}", "📦",
                        Colors.BRIGHT_WHITE, Colors.BRIGHT_GREEN)
        return [load_model(str(path)) for path in model_paths]

    def get_models(self) -> Dict[str, List[Model]]:
        """
        Возвращает все загруженные ансамбли.

        Returns:
            Dict[str, List[Model]]: Сопоставление типа прогноза и списка моделей
        """

        return self._cache

    def memory_usage_bytes(self) -> int:
        """
        Возвращает приближённую оценку памяти, занимаемой весами моделей.

        Считается как количество параметров * BYTES_PER_PARAM байта (float32).
        Служебные структуры Keras и графы вычислений не учитываются, поэтому реальное
        потребление процесса будет выше.

        Returns:
            int: Оценка размера весов всех моделей в байтах
        """

        total_params = sum(
            model.count_params()
            for ensemble in self._cache.values()
            for model in ensemble
        )
        return total_params * BYTES_PER_PARAM

    def __len__(self) -> int:
        """
        Возвращает общее количество моделей во всех ансамблях.

        Returns:
            int: Суммарное число загруженных моделей
        """

        return sum(len(ensemble) for ensemble in self._cache.values())

    @property
    def is_initialized(self) -> bool:
        """
        Проверяет статус инициализации кэша.

        Returns:
            bool: True если initialize() завершился успешно, False иначе
        """

        return self._initialized
