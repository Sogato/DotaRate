# Сторонние библиотеки
from django.db import models

# Локальные импорты
from dota_core.config import RADIANT_INDEX, DIRE_INDEX


class Match(models.Model):
    """
    Доменная модель профессионального live-матча Dota 2.

    Связи:
        players (relationship)     — один-ко-многим с Player
        publication (relationship) — один-к-одному с MatchPublication

    Attributes:
        match_id (int): 64-битный идентификатор матча от Steam (первичный ключ)
        league_id (int): Идентификатор турнира
        league_name (str): Название турнира
        radiant_team_id (int): Идентификатор команды Radiant
        radiant_team_name (str): Название команды Radiant
        dire_team_id (int): Идентификатор команды Dire
        dire_team_name (str): Название команды Dire
        start_time (datetime): Время начала матча
        end_time (datetime): Время окончания матча
        stream_delay_s (int): Задержка трансляции в секундах
        duration (int): Длительность матча в секундах
        radiant_score (int): Количество убийств команды Radiant
        dire_score (int): Количество убийств команды Dire
        net_worth_radiant (int): Суммарная ценность команды Radiant
        net_worth_dire (int): Суммарная ценность команды Dire
        radiant_series_wins (int): Побед Radiant в текущей серии
        dire_series_wins (int): Побед Dire в текущей серии
        radiant_win (bool): Победила ли команда Radiant
        live_status (bool): Идёт ли матч сейчас (live)
        radiant_team_coefficient (float): Букмекерский коэффициент на Radiant
        dire_team_coefficient (float): Букмекерский коэффициент на Dire
        bet_status (bool): Доступны ли ставки на матч
        winline_event_id (str): Идентификатор события у букмекера Winline
        predict_win (float): Прогноз вероятности победы Radiant
        predict_time (float): Прогноз длительности матча
        predict_radiant_score (float): Прогноз счёта Radiant
        predict_dire_score (float): Прогноз счёта Dire
    """
    # Основная информация
    match_id = models.PositiveBigIntegerField(primary_key=True, verbose_name='ID матча')

    # Турнир
    league_id = models.PositiveIntegerField(db_index=True, verbose_name='ID лиги')
    league_name = models.CharField(max_length=255, null=True, blank=True, verbose_name='Название лиги')

    # Команды
    radiant_team_id = models.PositiveBigIntegerField(db_index=True, verbose_name='ID команды Radiant')
    radiant_team_name = models.CharField(max_length=255, verbose_name='Команда Radiant')
    dire_team_id = models.PositiveBigIntegerField(db_index=True, verbose_name='ID команды Dire')
    dire_team_name = models.CharField(max_length=255, verbose_name='Команда Dire')

    # Тайминги
    start_time = models.DateTimeField(null=True, blank=True, verbose_name='Время начала')
    end_time = models.DateTimeField(null=True, blank=True, verbose_name='Время окончания')
    stream_delay_s = models.PositiveSmallIntegerField(verbose_name='Задержка трансляции')

    # Ход и результат игры
    duration = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Длительность матча')
    radiant_score = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Счёт Radiant')
    dire_score = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Счёт Dire')
    net_worth_radiant = models.PositiveIntegerField(null=True, blank=True, verbose_name='Ценность Radiant')
    net_worth_dire = models.PositiveIntegerField(null=True, blank=True, verbose_name='Ценность Dire')
    radiant_series_wins = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Победы Radiant в серии')
    dire_series_wins = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Победы Dire в серии')
    radiant_win = models.BooleanField(null=True, blank=True, verbose_name='Победа Radiant')

    # Состояние live
    live_status = models.BooleanField(null=True, blank=True, verbose_name='Live')

    # Коэффициенты и ставка
    radiant_team_coefficient = models.FloatField(null=True, blank=True, verbose_name='Коэф Radiant')
    dire_team_coefficient = models.FloatField(null=True, blank=True, verbose_name='Коэф Dire')
    bet_status = models.BooleanField(null=True, blank=True, verbose_name='Статус ставок')
    winline_event_id = models.CharField(max_length=32, null=True, blank=True, verbose_name='ID события Winline')

    # Прогнозы моделей
    predict_win = models.FloatField(null=True, blank=True, verbose_name='Прогноз победы')
    predict_time = models.FloatField(null=True, blank=True, verbose_name='Прогноз длительности')
    predict_radiant_score = models.FloatField(null=True, blank=True, verbose_name='Прогноз счёта Radiant')
    predict_dire_score = models.FloatField(null=True, blank=True, verbose_name='Прогноз счёта Dire')

    class Meta:
        verbose_name = 'Матч'
        verbose_name_plural = 'Матчи'
        ordering = ('-start_time',)

    def __str__(self):
        return f"{self.radiant_team_name} vs {self.dire_team_name}"

    @property
    def radiant_players(self):
        return [p for p in self.players.all() if p.team_number == RADIANT_INDEX]

    @property
    def dire_players(self):
        return [p for p in self.players.all() if p.team_number == DIRE_INDEX]


class MatchPublication(models.Model):
    """
    Состояние публикации матча в Telegram.

    Связи:
        match (OneToOne) — один-к-одному с Match

    Attributes:
        match (Match): Внешний ключ на матч (связь один-к-одному)
        telegram_message_id (int): Идентификатор опубликованного сообщения в Telegram
        refresh_flag (bool): Требуется ли обновить сообщение
    """
    # Связь
    match = models.OneToOneField(
        'Match', on_delete=models.CASCADE, related_name='publication', verbose_name='Матч',
    )

    # Публикация
    telegram_message_id = models.BigIntegerField(null=True, blank=True, verbose_name='ID сообщения TG')
    refresh_flag = models.BooleanField(default=False, verbose_name='Флаг обновления')

    class Meta:
        verbose_name = 'Публикация матча'
        verbose_name_plural = 'Публикации матчей'

    def __str__(self):
        return f"Публикация матча {self.match_id}"


class Player(models.Model):
    """
    Игрок в live-матче.

    Связи:
        match (FK) — многие-к-одному с Match

    Attributes:
        match (Match): Внешний ключ на родительский матч
        team_number (int): Сторона игрока (Radiant или Dire)
        account_id (int): Steam ID игрока
        nickname (str): Никнейм игрока
        hero_id (int): Идентификатор героя из справочника Dota 2
        hero_name (str): Локализованное имя героя
        hero_variant (int): Вариант героя
    """
    TEAM_CHOICES = (
        (RADIANT_INDEX, 'Radiant'),
        (DIRE_INDEX, 'Dire'),
    )

    # Идентификация
    match = models.ForeignKey(
        'Match', on_delete=models.CASCADE, related_name='players', verbose_name='Матч',
    )
    account_id = models.PositiveBigIntegerField(verbose_name='ID игрока')
    nickname = models.CharField(max_length=255, verbose_name='Никнейм')

    # Игровые параметры
    team_number = models.PositiveSmallIntegerField(choices=TEAM_CHOICES, db_index=True, verbose_name='Сторона')
    hero_id = models.PositiveIntegerField(verbose_name='ID героя')
    hero_name = models.CharField(max_length=255, verbose_name='Герой')
    hero_variant = models.PositiveSmallIntegerField(verbose_name='Вариант героя')

    class Meta:
        verbose_name = 'Игрок'
        verbose_name_plural = 'Игроки'

    def __str__(self):
        return f"{self.nickname} (ID {self.account_id})"
