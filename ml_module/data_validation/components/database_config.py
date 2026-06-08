"""
Конфигурация подключения к базам данных Dota 2 матчей.

Модуль предоставляет унифицированный интерфейс для работы с двумя типами
баз данных через класс DatabaseConfig: публичные ranked матчи (Dataset)
и профессиональные турнирные матчи (Professional). Инкапсулирует различия
в моделях SQLAlchemy, именах таблиц и поддерживаемых возможностях.

Для создания конфигурации используются фабричные методы:
    config = DatabaseConfig.for_dataset()   # публичные матчи
    config = DatabaseConfig.for_pro()       # профессиональные матчи
"""

# Стандартные библиотеки
from dataclasses import dataclass
from typing import Type, Union

# Локальные импорты
from data_bases.dataset.models import Match, MatchPlayer
from data_bases.pro_matches.models import ProMatch, ProMatchPlayer
from config import DATASET_DATABASE_URL, PRO_DATABASE_URL


@dataclass
class DatabaseConfig:
    """
    Хранит все параметры подключения и возможности конкретной базы данных.

    Содержит модели SQLAlchemy, имена таблиц, URL подключения и флаги
    поддерживаемых полей. Компоненты валидатора получают этот объект
    и работают через него, не зная напрямую с каким типом БД имеют дело.

    Создавать экземпляры напрямую не нужно, используй фабричные методы
    for_dataset() и for_pro().

    Attributes:
        database_url (str): URL подключения к базе данных SQLAlchemy
            Примеры: 'sqlite:///dataset.db', 'postgresql://user:pass@host/db'

        match_model (Type[Union[Match, ProMatch]]): SQLAlchemy модель таблицы матчей
            Match для обычных матчей, ProMatch для профессиональных

        player_model (Type[Union[MatchPlayer, ProMatchPlayer]]): SQLAlchemy модель таблицы игроков
            MatchPlayer для обычных, ProMatchPlayer для профессиональных

        match_table_name (str): Имя таблицы матчей в БД
            'matches' для Dataset, 'pro_matches' для Professional

        player_table_name (str): Имя таблицы игроков в БД
            'match_players' для Dataset, 'pro_match_players' для Professional

        database_type (str): Человекочитаемый тип БД для логирования
            'Dataset' или 'Professional', используется в заголовках вывода

        supports_teams (bool): Флаг поддержки поля team_id
            False для Dataset (публичные матчи без команд),
            True для Professional (матчи организованных команд)

        supports_leagues (bool): Флаг поддержки поля league_id
            False для Dataset (нет турниров),
            True для Professional (турнирные матчи с ID лиги)

        supports_series (bool): Флаг поддержки поля series_id
            False для Dataset (одиночные матчи),
            True для Professional (серии матчей BO3, BO5)
    """

    database_url: str
    match_model: Type[Union[Match, ProMatch]]
    player_model: Type[Union[MatchPlayer, ProMatchPlayer]]
    match_table_name: str
    player_table_name: str
    database_type: str
    supports_teams: bool = False
    supports_leagues: bool = False
    supports_series: bool = False

    @classmethod
    def for_dataset(cls) -> 'DatabaseConfig':
        """
        Создаёт конфигурацию для базы данных публичных матчей.

        Использует модели Match и MatchPlayer.
        Флаги команд, лиг и серий отключены.

        Returns:
            DatabaseConfig: Конфигурация для Dataset БД
        """

        return cls(
            database_url=DATASET_DATABASE_URL,
            match_model=Match,
            player_model=MatchPlayer,
            match_table_name='matches',
            player_table_name='match_players',
            database_type='Dataset',
            supports_teams=False,
            supports_leagues=False,
            supports_series=False
        )

    @classmethod
    def for_pro(cls) -> 'DatabaseConfig':
        """
        Создаёт конфигурацию для базы данных профессиональных турнирных матчей.

        Использует модели ProMatch и ProMatchPlayer.
        Все флаги профессиональных данных включены.

        Returns:
            DatabaseConfig: Конфигурация для Professional БД
        """

        return cls(
            database_url=PRO_DATABASE_URL,
            match_model=ProMatch,
            player_model=ProMatchPlayer,
            match_table_name='pro_matches',
            player_table_name='pro_match_players',
            database_type='Professional',
            supports_teams=True,
            supports_leagues=True,
            supports_series=True
        )
