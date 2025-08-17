from sqlalchemy import create_engine, text
from config import DATABASE_URL
from models import Match, MatchPlayer

engine = create_engine(DATABASE_URL)


def clear_database():
    match_table = Match.__tablename__
    match_player_table = MatchPlayer.__tablename__

    with engine.begin() as conn:
        conn.execute(text(f'TRUNCATE TABLE "{match_player_table}" RESTART IDENTITY CASCADE'))
        conn.execute(text(f'TRUNCATE TABLE "{match_table}" RESTART IDENTITY CASCADE'))
    print("Данные успешно очищены.")


def check_if_empty():
    match_table = Match.__tablename__
    match_player_table = MatchPlayer.__tablename__

    with engine.connect() as conn:
        match_count = conn.execute(text(f'SELECT COUNT(*) FROM "{match_table}"')).scalar()
        player_count = conn.execute(text(f'SELECT COUNT(*) FROM "{match_player_table}"')).scalar()

    if match_count == 0 and player_count == 0:
        print("База данных пуста.")
        return True
    else:
        print(f"База данных не пуста: {match_count} матчей, {player_count} игроков.")
        return False


if __name__ == "__main__":
    clear_database()
    check_if_empty()
