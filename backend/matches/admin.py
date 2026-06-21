"""
Настройка панели администрирования приложения matches.

Админка задумана как обзорная панель: данные наполняет сборщик, поэтому
фактические показатели матча (счёт, ценность, тайминги, прогнозы, результат)
доступны только для чтения, а управляющие поля (доступность ставок,
коэффициенты, признак трансляции, событие Winline) оставлены редактируемыми
для ручной корректировки.

Имена героев в составе берутся из справочника HeroCache (shared_resources), загруженного при старте сервера.
"""

# Сторонние библиотеки
from django.contrib import admin
from django.utils.html import format_html

# Локальные импорты
from .models import Match, Player, MatchPublication
from . import shared_resources

# Заголовки панели администрирования.
admin.site.site_header = 'Dota Rate Администрирование'
admin.site.site_title = 'Dota Rate'
admin.site.index_title = 'Управление данными'


def _hero_name(hero_id: int) -> str:
    """Локализованное имя героя из справочника shared_resources."""
    return shared_resources.get_hero_cache().get_hero_name(hero_id)


class PlayerInline(admin.TabularInline):
    """Состав команд внутри карточки матча."""

    model = Player
    extra = 0
    can_delete = False
    ordering = ('team_number',)
    fields = ('team_number', 'nickname', 'account_id', 'hero', 'hero_id')
    readonly_fields = ('hero',)
    verbose_name = 'Игрок'
    verbose_name_plural = 'Состав команд'

    @admin.display(description='Герой')
    def hero(self, obj):
        """Читаемое имя героя по его идентификатору."""
        return _hero_name(obj.hero_id)


class MatchPublicationInline(admin.StackedInline):
    """Состояние публикации матча в Telegram внутри карточки матча."""

    model = MatchPublication
    extra = 0
    can_delete = False
    max_num = 1
    fields = ('telegram_message_id', 'refresh_flag')
    verbose_name = 'Публикация'
    verbose_name_plural = 'Публикация'


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    """Карточка профессионального матча Dota 2 с составом и публикацией."""

    inlines = (PlayerInline, MatchPublicationInline)

    list_display = (
        'match_id',
        'teams',
        'league_name',
        'score',
        'bet_state',
        'coefficients',
        'is_live',
        'win_prediction',
        'result',
        'start_time',
    )
    list_display_links = ('match_id',)
    list_filter = ('bet_status', 'live_status')
    search_fields = (
        'match_id',
        'radiant_team_name',
        'dire_team_name',
        'league_name',
        'winline_event_id',
    )
    date_hierarchy = 'start_time'
    ordering = ('-start_time',)
    list_per_page = 50
    save_on_top = True

    # Фактические показатели наполняет сборщик — правке вручную не подлежат.
    readonly_fields = (
        'match_id',
        'start_time',
        'end_time',
        'duration',
        'radiant_score',
        'dire_score',
        'net_worth_radiant',
        'net_worth_dire',
        'radiant_series_wins',
        'dire_series_wins',
        'predict_win',
        'predict_time',
        'predict_radiant_score',
        'predict_dire_score',
    )

    fieldsets = (
        ('Идентификатор', {
            'fields': ('match_id',),
        }),
        ('Турнир', {
            'fields': ('league_id', 'league_name'),
        }),
        ('Команды', {
            'fields': (
                'radiant_team_id', 'radiant_team_name',
                'dire_team_id', 'dire_team_name',
            ),
        }),
        ('Тайминги', {
            'fields': ('start_time', 'end_time', 'stream_delay_s'),
        }),
        ('Игровые показатели', {
            'fields': (
                'duration',
                'radiant_score', 'dire_score',
                'net_worth_radiant', 'net_worth_dire',
                'radiant_series_wins', 'dire_series_wins',
                'radiant_win',
            ),
        }),
        ('Статус', {
            'fields': ('live_status',),
        }),
        ('Ставки и коэффициенты', {
            'fields': (
                'bet_status',
                'radiant_team_coefficient', 'dire_team_coefficient',
                'winline_event_id',
            ),
        }),
        ('Прогнозы моделей', {
            'fields': (
                'predict_win', 'predict_time',
                'predict_radiant_score', 'predict_dire_score',
            ),
        }),
    )

    @admin.display(description='Матч')
    def teams(self, obj):
        """Соперники одной строкой."""
        return f'{obj.radiant_team_name} vs {obj.dire_team_name}'

    @admin.display(description='Счёт')
    def score(self, obj):
        """Текущий счёт по убийствам."""
        if obj.radiant_score is None and obj.dire_score is None:
            return '—'
        return format_html(
            '<span style="white-space: nowrap">{} : {}</span>',
            obj.radiant_score or 0, obj.dire_score or 0,
        )

    @admin.display(description='Ставки')
    def bet_state(self, obj):
        """Состояние ставок словами по трём значениям bet_status (True/None/False)."""
        states = {
            True: ('Доступны', '#1a7f37'),
            None: ('Ожидание коэффициентов', '#9a6700'),
            False: ('Нет в линии', '#6e7781'),
        }
        label, color = states[obj.bet_status]
        return format_html('<b style="color: {}; white-space: nowrap">{}</b>', color, label)

    @admin.display(description='Коэф R/D')
    def coefficients(self, obj):
        """Коэффициенты Radiant и Dire одной компактной колонкой.

        У завершённого матча (radiant_win задан) коэффициент победившей стороны выделяется жирным.
        """
        r, d = obj.radiant_team_coefficient, obj.dire_team_coefficient
        if r is None and d is None:
            return '—'

        def cell(coef, is_winner):
            if coef is None:
                return format_html('—')
            tpl = '<b>{}</b>' if is_winner else '{}'
            return format_html(tpl, f'{coef:.2f}')

        return format_html(
            '<span style="white-space: nowrap">{} / {}</span>',
            cell(r, obj.radiant_win is True),
            cell(d, obj.radiant_win is False),
        )

    @admin.display(description='Live', boolean=True, ordering='live_status')
    def is_live(self, obj):
        """Признак активной трансляции."""
        return obj.live_status

    @admin.display(description='Прогноз победы', ordering='predict_win')
    def win_prediction(self, obj):
        """Прогноз модели на победу фаворита в процентах."""
        if obj.predict_win is None:
            return '—'
        if obj.predict_win >= 0.5:
            team, chance = 'Radiant', obj.predict_win
        else:
            team, chance = 'Dire', 1 - obj.predict_win
        return format_html(
            '<span style="white-space: nowrap">{} {}%</span>',
            team, f'{chance * 100:.0f}',
        )

    @admin.display(description='Результат', ordering='radiant_win')
    def result(self, obj):
        """Фактический итог матча: победившая сторона."""
        if obj.radiant_win is None:
            return '—'
        team, color = ('Radiant', '#1a7f37') if obj.radiant_win else ('Dire', '#cf222e')
        return format_html(
            '<b style="color: {}; white-space: nowrap">{}</b>', color, team,
        )


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    """Отдельная таблица игроков (в основном просматривается внутри матча)."""

    list_display = ('nickname', 'account_id', 'match', 'team_number', 'hero', 'hero_id')
    list_filter = ('team_number',)
    search_fields = (
        'nickname',
        'account_id',
        'hero_id',
        'match__match_id',
        'match__radiant_team_name',
        'match__dire_team_name',
    )
    list_select_related = ('match',)
    raw_id_fields = ('match',)
    list_per_page = 50

    @admin.display(description='Герой')
    def hero(self, obj):
        """Читаемое имя героя по его идентификатору."""
        return _hero_name(obj.hero_id)
