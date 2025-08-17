import numpy as np
from datetime import datetime
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, selectinload
from collections import Counter

from config import DATABASE_URL
from data_base.models import Match, MatchPlayer

# --- НАСТРОЙКИ БАЗЫ ДАННЫХ ---
# Замените 'postgresql://user:password@host:port/database' на вашу строку подключения
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def inspect_dota2_database(sample_size=5):
    """
    Проверяет содержимое базы данных с матчами Dota 2, выводит статистику и проверяет валидность.

    :param sample_size: Количество матчей для отображения в качестве примера.
    """
    print(f"\033[36m\033[1m=== Проверка базы данных: {DATABASE_URL} ===\033[0m\n")

    session = SessionLocal()

    try:
        # --- Загрузка данных ---
        print("\033[34mЗагрузка данных из базы... (Это может занять время для больших датасетов)\033[0m")
        # Используем selectinload для эффективной загрузки связанных игроков (избегаем проблемы N+1)
        matches = session.query(Match).options(selectinload(Match.players)).all()
        print(f"\033[32mДанные успешно загружены.\033[0m\n")

        total_matches = len(matches)
        if total_matches == 0:
            print("\033[33mБаза данных не содержит матчей для анализа.\033[0m")
            return

        # --- Проверки целостности ---
        # Проверка на дубликаты (хотя БД должна это предотвращать)
        match_ids_count = session.query(func.count(Match.match_id)).scalar()
        unique_match_ids_count = session.query(func.count(func.distinct(Match.match_id))).scalar()

        match_seq_nums_count = session.query(func.count(Match.match_seq_num)).scalar()
        unique_match_seq_nums_count = session.query(func.count(func.distinct(Match.match_seq_num))).scalar()

        # --- Сбор статистики ---
        radiant_wins = 0
        durations = []
        game_modes = []
        radiant_scores = []
        dire_scores = []
        hero_ids_radiant = []
        hero_ids_dire = []
        invalid_matches = 0

        for match in matches:
            # Проверка количества игроков
            if len(match.players) != 10:
                print(
                    f"\033[31m\033[1mОшибка:\033[0m Матч {match.match_id} содержит неверное количество игроков ({len(match.players)} вместо 10).")
                invalid_matches += 1
                continue

            # Сбор статистики
            radiant_wins += int(match.radiant_win)
            durations.append(match.duration)
            game_modes.append(match.game_mode)
            radiant_scores.append(match.radiant_score)
            dire_scores.append(match.dire_score)

            for player in match.players:
                if player.team_number == 0:  # Radiant
                    hero_ids_radiant.append(player.hero_id)
                else:  # Dire
                    hero_ids_dire.append(player.hero_id)

        # --- Вывод общей статистики ---
        print(f"\033[36m\033[1m=== Общая статистика по датасету ===\033[0m")
        print("\033[34m" + "-" * 40 + "\033[0m")
        print(f"\033[1mОбщее количество матчей:\033[0m \033[32m{total_matches:,}\033[0m")
        print(f"\033[1mНевалидные матчи (не 10 игроков):\033[0m \033[32m{invalid_matches:,}\033[0m")
        print(
            f"\033[1mУникальных match_id:\033[0m \033[32m{unique_match_ids_count:,} (Всего: {match_ids_count:,})\033[0m")
        print(
            f"\033[1mУникальных match_seq_num:\033[0m \033[32m{unique_match_seq_nums_count:,} (Всего: {match_seq_nums_count:,})\033[0m\n")

        # Статистика по radiant_win
        print("\033[34mСтатистика по победам Radiant:\033[0m")
        if total_matches > 0:
            win_percentage = (radiant_wins / total_matches) * 100
            print(
                f"\033[1mПобеды Radiant:\033[0m \033[32m{radiant_wins:,}\033[0m (\033[32m{win_percentage:.2f}%\033[0m)")
        else:
            print("\033[33mНет данных для статистики по победам.\033[0m")

        # Статистика по длительности матчей
        print("\n\033[34mСтатистика по длительности матчей (в секундах):\033[0m")
        if durations:
            print(
                f"\033[1mСредняя:\033[0m \033[32m{np.mean(durations):.0f}\033[0m, \033[1mМин:\033[0m \033[32m{np.min(durations):,}\033[0m, \033[1mМакс:\033[0m \033[32m{np.max(durations):,}\033[0m")
        else:
            print("\033[33mНет данных для статистики по длительности.\033[0m")

        # Статистика по режимам игры
        print("\n\033[34mСтатистика по режимам игры:\033[0m")
        if game_modes:
            mode_counts = Counter(game_modes)
            for mode, count in mode_counts.most_common():
                percentage = (count / total_matches) * 100
                print(
                    f"\033[1mРежим {int(mode)}:\033[0m \033[32m{count:,}\033[0m матчей (\033[32m{percentage:.2f}%\033[0m)")
        else:
            print("\033[33mНет данных для статистики по режимам игры.\033[0m")

        # Статистика по героям
        print("\n\033[34mСтатистика по героям:\033[0m")
        if hero_ids_radiant and hero_ids_dire:
            print(f"\033[1mУникальных героев (Radiant):\033[0m \033[32m{len(set(hero_ids_radiant)):,}\033[0m")
            print(f"\033[1mУникальных героев (Dire):\033[0m \033[32m{len(set(hero_ids_dire)):,}\033[0m")
        else:
            print("\033[33mНет данных для статистики по героям.\033[0m")

        # Примеры матчей
        if total_matches > 0 and sample_size > 0:
            print(f"\n\033[34mПримеры матчей (первые {min(sample_size, total_matches)}):\033[0m")
            for i, match in enumerate(matches[:sample_size]):
                print("\033[34m" + "-" * 40 + "\033[0m")
                print(f"\033[1mМатч {i + 1} (match_id: {match.match_id}):\033[0m")
                print(f"  Победитель: \033[32m{'Radiant' if match.radiant_win else 'Dire'}\033[0m")
                print(f"  Длительность: \033[32m{match.duration:,} сек\033[0m")
                print(
                    f"  Время начала: \033[32m{datetime.fromtimestamp(match.start_time).strftime('%Y-%m-%d %H:%M:%S')}\033[0m")
                print(f"  Счет (R/D): \033[32m{match.radiant_score}/{match.dire_score}\033[0m")
                radiant_heroes = [p.hero_id for p in match.players if p.team_number == 0]
                dire_heroes = [p.hero_id for p in match.players if p.team_number == 1]
                print(f"  Герои Radiant: \033[32m{radiant_heroes}\033[0m")
                print(f"  Герои Dire: \033[32m{dire_heroes}\033[0m")

    except Exception as e:
        print(f"\033[31m\033[1mПроизошла ошибка при работе с базой данных:\033[0m {e}")
    finally:
        session.close()
        print("\n\033[36m\033[1mПроверка завершена.\033[0m")


if __name__ == "__main__":
    inspect_dota2_database(sample_size=5)
