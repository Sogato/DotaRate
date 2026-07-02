"""
Сериализаторы DRF для представления live-матчей Dota 2 и их состава.
"""

# Сторонние библиотеки
from rest_framework import serializers

# Локальные импорты
from .models import Match, MatchPublication, Player


class PlayerSerializer(serializers.ModelSerializer):
    """
    Игрок внутри состава команды матча.

    Используется как вложенный сериализатор в MatchSerializer для обоих
    составов (radiant_players, dire_players).
    """

    class Meta:
        model = Player
        fields = (
            'account_id',
            'nickname',
            'team_number',
            'hero_id',
            'hero_name',
            'hero_variant',
        )


class MatchPublicationSerializer(serializers.ModelSerializer):
    """
    Состояние публикации матча в Telegram.
    """

    class Meta:
        model = MatchPublication
        fields = (
            'telegram_message_id',
            'refresh_flag',
        )


class MatchSerializer(serializers.ModelSerializer):
    """
    Полное представление live-матча для API.

    Связанные поля:
        radiant_players / dire_players — читаются из свойств модели Match;
            требуют prefetch_related('players').
        publication — обратный OneToOne; в '__all__' автоматически не включается,
            поэтому объявлен явно. Требует select_related('publication').
    """

    radiant_players = PlayerSerializer(many=True, read_only=True)
    dire_players = PlayerSerializer(many=True, read_only=True)
    publication = MatchPublicationSerializer(read_only=True)

    class Meta:
        model = Match
        fields = (
            # Идентификация
            'match_id',

            # Турнир
            'league_id',
            'league_name',

            # Команды
            'radiant_team_id',
            'radiant_team_name',
            'dire_team_id',
            'dire_team_name',

            # Тайминги
            'start_time',
            'end_time',
            'stream_delay_s',

            # Ход и результат игры
            'duration',
            'radiant_score',
            'dire_score',
            'net_worth_radiant',
            'net_worth_dire',
            'radiant_series_wins',
            'dire_series_wins',
            'radiant_win',

            # Состояние live
            'live_status',

            # Коэффициенты и ставка
            'radiant_team_coefficient',
            'dire_team_coefficient',
            'bet_status',
            'winline_event_id',

            # Прогнозы моделей
            'predict_win',
            'predict_time',
            'predict_radiant_score',
            'predict_dire_score',

            # Связанные объекты
            'radiant_players',
            'dire_players',
            'publication',
        )
