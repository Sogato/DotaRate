import os
import sys

from django.apps import AppConfig


class MatchesConfig(AppConfig):
    name = 'matches'
    verbose_name = 'Матчи'

    def ready(self):
        # Состояние нужно только серверу, поэтому остальные команды
        # (migrate, shell и т.д.) пропускаем.
        if 'runserver' not in sys.argv:
            return

        # runserver с автоперезагрузкой поднимает два процесса; грузим состояние
        # только в рабочем (RUN_MAIN), при --noreload процесс один.
        if not (os.environ.get('RUN_MAIN') == 'true' or '--noreload' in sys.argv):
            return

        # Импорт отложен: на уровне модуля зависимости состояния ещё не готовы.
        from . import app_state
        shared_resources.load()
