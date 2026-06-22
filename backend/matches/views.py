"""
Обработчики HTTP-запросов приложения matches.

Отвечают только за приём запросов и формирование ответов, сгруппированы по назначению:
    1. Запуск сбора          — запускают очередной проход collector.
    2. Публикация в Telegram — изменяют состояние MatchPublication.
    3. Очистка БД            — удаляют матчи без ставки.
    4. Чтение для интерфейса — отдают списки и деталь матча через MatchSerializer.

Вся работа по сбору и обработке данных вынесена в collector, здешние обработчики
лишь запускают его проход. Обработчики чтения заранее подгружают связи (players,
publication), которые нужны сериализатору.
"""

# Стандартные библиотеки
import logging
from datetime import datetime, time, timedelta

# Сторонние библиотеки
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework import generics
from rest_framework.decorators import api_view
from rest_framework.response import Response

# Локальные импорты
from . import collector
from .models import Match, MatchPublication
from .serializers import MatchSerializer

logger = logging.getLogger(__name__)


def _match_queryset():
    """
    Базовый QuerySet матчей с предзагруженными связями для MatchSerializer.

    select_related('publication') обслуживает обратную связь OneToOne, а
    prefetch_related('players') нужен свойствам radiant_players и dire_players.
    Без предзагрузки сериализатор делал бы отдельный запрос на каждый матч.
    """
    return Match.objects.select_related('publication').prefetch_related('players')


def _parse_bool(value) -> bool:
    """Приводит строковый флаг из запроса к булеву значению."""
    return str(value).strip().lower() in ('true', '1', 'yes', 'on')


@api_view(["GET"])
def get_live_league_games(request):
    """Запускает очередной проход опроса live-линии и возвращает сводку."""
    summary = collector.run()
    return Response(summary)


@api_view(["GET"])
def live_league_games_check_complete(request):
    """Запускает очередной проход поиска результатов завершённых матчей."""
    summary = collector.finish_completed()
    return Response(summary)


@csrf_exempt
@require_POST
def update_telegram_message_id(request):
    """
    Сохраняет идентификатор опубликованного сообщения в публикацию матча.

    Дополнительно может принять refresh_flag. Запись публикации создаётся вместе
    с матчем в collector; get_or_create здесь страхует от рассогласования.
    """
    match_id = request.POST.get('match_id')
    telegram_message_id = request.POST.get('telegram_message_id')
    if not all((match_id, telegram_message_id)):
        return JsonResponse({"status": "error", "message": "Missing data"}, status=400)

    try:
        match = Match.objects.get(match_id=match_id)
    except Match.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Match not found"}, status=404)

    publication, _ = MatchPublication.objects.get_or_create(match=match)
    publication.telegram_message_id = telegram_message_id

    refresh_flag = request.POST.get('refresh_flag')
    if refresh_flag is not None:
        publication.refresh_flag = _parse_bool(refresh_flag)

    publication.save()
    return JsonResponse({"status": "success"}, status=200)


@csrf_exempt
@require_POST
def update_refresh_flag(request):
    """Снимает признак необходимости обновления у публикации матча."""
    match_id = request.POST.get('match_id')
    if not match_id:
        return JsonResponse({"status": "error", "message": "Missing match_id"}, status=400)

    try:
        match = Match.objects.get(match_id=match_id)
    except Match.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Match not found"}, status=404)

    publication, _ = MatchPublication.objects.get_or_create(match=match)
    publication.refresh_flag = False
    publication.save(update_fields=("refresh_flag",))
    return JsonResponse({"status": "success"}, status=200)


@csrf_exempt
def delete_matches(request):
    """
    Удаляет матчи без ставки среди 500 последних по времени окончания.

    Затрагивает только записи с bet_status, равным False или NULL; активные
    матчи со ставкой остаются нетронутыми.
    """
    last_500_ids = list(
        Match.objects.order_by('-end_time').values_list('match_id', flat=True)[:500]
    )
    deleted, _ = Match.objects.filter(
        Q(bet_status=False) | Q(bet_status__isnull=True),
        match_id__in=last_500_ids,
    ).delete()

    logger.info("Удалено %d матчей", deleted)
    return JsonResponse({"status": "success", "deleted": deleted}, status=200)


class LastDayMatchList(generics.ListAPIView):
    """Матчи, завершившиеся за последние сутки."""
    serializer_class = MatchSerializer

    def get_queryset(self):
        now = timezone.now()
        return _match_queryset().filter(
            end_time__range=(now - timedelta(days=1), now)
        ).order_by('start_time')


class LastWeekMatchList(generics.ListAPIView):
    """Матчи, завершившиеся за последнюю неделю."""
    serializer_class = MatchSerializer

    def get_queryset(self):
        now = timezone.now()
        return _match_queryset().filter(
            end_time__range=(now - timedelta(weeks=1), now)
        ).order_by('start_time')


class LastMonthMatchList(generics.ListAPIView):
    """Матчи, завершившиеся в предыдущем календарном месяце."""
    serializer_class = MatchSerializer

    def get_queryset(self):
        today = timezone.now().date()
        first_of_this_month = today.replace(day=1)
        first_of_prev = (first_of_this_month - timedelta(days=1)).replace(day=1)

        start = timezone.make_aware(datetime.combine(first_of_prev, time.min))
        end = timezone.make_aware(datetime.combine(first_of_this_month, time.min))

        return _match_queryset().filter(
            end_time__gte=start, end_time__lt=end
        ).order_by('start_time')


class DailyMatchList(generics.ListAPIView):
    """Матчи, завершившиеся в указанный день (kwarg 'day' в формате YYYY-MM-DD)."""
    serializer_class = MatchSerializer

    def get_queryset(self):
        day = self.kwargs.get('day')
        day_date = timezone.datetime.strptime(day, '%Y-%m-%d').date()
        day_start = timezone.make_aware(
            timezone.datetime.combine(day_date, timezone.datetime.min.time())
        )
        day_end = timezone.make_aware(
            timezone.datetime.combine(day_date, timezone.datetime.max.time())
        )
        return _match_queryset().filter(
            end_time__range=(day_start, day_end)
        ).order_by('start_time')


class AllTimeMatchList(generics.ListAPIView):
    """Все матчи со ставкой за всё время."""
    serializer_class = MatchSerializer

    def get_queryset(self):
        return _match_queryset().filter(bet_status=True).order_by('start_time')


class MatchListByLeague(generics.ListAPIView):
    """Матчи со ставкой в указанной лиге."""
    serializer_class = MatchSerializer

    def get_queryset(self):
        league_id = self.kwargs['league_id']
        return _match_queryset().filter(
            league_id=league_id, bet_status=True
        ).order_by('-match_id')


class MatchListExceptLeague(generics.ListAPIView):
    """Матчи со ставкой во всех лигах, кроме указанной."""
    serializer_class = MatchSerializer

    def get_queryset(self):
        league_id = self.kwargs['league_id']
        return _match_queryset().filter(
            ~Q(league_id=league_id), bet_status=True
        ).order_by('-match_id')


class MatchList(generics.ListAPIView):
    """Все матчи без фильтрации."""
    serializer_class = MatchSerializer

    def get_queryset(self):
        return _match_queryset().order_by('-match_id')


class MatchDetail(generics.RetrieveAPIView):
    """Один матч по первичному ключу."""
    serializer_class = MatchSerializer
    queryset = _match_queryset()
