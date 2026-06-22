"""
Маршруты приложения matches.

Адреса разбиты на три группы: запуск сбора матчей, публикация в Telegram и
чтение данных. Конкретные пути объявлены раньше параметрических, иначе
matches/<int:pk>/ перехватывал бы именованные списки вроде matches/last-day/.
"""

from django.urls import path

from . import views

app_name = 'matches'

urlpatterns = [
    # Запуск сбора матчей
    path('collect/live/', views.get_live_league_games, name='collect-live'),
    path('collect/complete/', views.live_league_games_check_complete, name='collect-complete'),

    # Публикация в Telegram
    path('publication/telegram-id/', views.update_telegram_message_id, name='set-telegram-id'),
    path('publication/refresh-flag/', views.update_refresh_flag, name='reset-refresh-flag'),

    # Чтение данных: служебное
    path('matches/cleanup/', views.delete_matches, name='matches-cleanup'),

    # Чтение данных: списки за период
    path('matches/last-day/', views.LastDayMatchList.as_view(), name='matches-last-day'),
    path('matches/last-week/', views.LastWeekMatchList.as_view(), name='matches-last-week'),
    path('matches/last-month/', views.LastMonthMatchList.as_view(), name='matches-last-month'),
    path('matches/day/<str:day>/', views.DailyMatchList.as_view(), name='matches-day'),
    path('matches/all-time/', views.AllTimeMatchList.as_view(), name='matches-all-time'),

    # Чтение данных: списки по лиге
    path('matches/league/<int:league_id>/', views.MatchListByLeague.as_view(), name='matches-by-league'),
    path('matches/except-league/<int:league_id>/', views.MatchListExceptLeague.as_view(),
         name='matches-except-league'),

    # Чтение данных: полный список и карточка матча
    # (matches/<int:pk>/ — последним в группе, чтобы не перехватывать пути выше)
    path('matches/', views.MatchList.as_view(), name='matches-all'),
    path('matches/<int:pk>/', views.MatchDetail.as_view(), name='matches-detail'),
]
