import os
import sys

from django.apps import AppConfig


class MatchesConfig(AppConfig):
    name = 'matches'
    verbose_name = 'Матчи'

    def ready(self):
        # ready() вызывается при любом запуске Django (migrate, shell и т.д.).
        # Keras-модели нужны только серверу, поэтому отсекаем остальное.
        if 'runserver' not in sys.argv:
            return

        # При автоперезагрузке модели грузит только рабочий процесс (RUN_MAIN);
        # при --noreload процесс один. Иначе загрузка дублируется.
        if not (os.environ.get('RUN_MAIN') == 'true' or '--noreload' in sys.argv):
            return

        # Импорт отложен: на уровне модуля приложения и HeroMapper ещё не готовы.
        from .ml import match_predictor
        match_predictor.load()
