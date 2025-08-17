from sqlalchemy import Column, Integer, BigInteger, Boolean, Float, Enum, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class Match(Base):
    __tablename__ = 'matches'

    match_id = Column(BigInteger, primary_key=True)
    match_seq_num = Column(BigInteger, unique=True, nullable=False)
    radiant_win = Column(Boolean, nullable=False)
    duration = Column(Integer, nullable=False)
    start_time = Column(BigInteger, nullable=False)
    tower_status_radiant = Column(Integer, nullable=False)
    tower_status_dire = Column(Integer, nullable=False)
    barracks_status_radiant = Column(Integer, nullable=False)
    barracks_status_dire = Column(Integer, nullable=False)
    lobby_type = Column(Integer, nullable=False)
    game_mode = Column(Integer, nullable=False)
    radiant_score = Column(Integer, nullable=False)
    dire_score = Column(Integer, nullable=False)

    players = relationship("MatchPlayer", back_populates="match")


class MatchPlayer(Base):
    __tablename__ = 'match_players'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    match_id = Column(BigInteger, ForeignKey('matches.match_id', ondelete='CASCADE'), nullable=False)
    account_id = Column(BigInteger, nullable=False)
    team_number = Column(Integer, nullable=False)
    hero_id = Column(Integer, nullable=False)
    hero_variant = Column(Integer, nullable=False)
    role = Column(Enum("core", "support", name="role_enum"), nullable=False)
    item_0 = Column(Integer, nullable=False)
    item_1 = Column(Integer, nullable=False)
    item_2 = Column(Integer, nullable=False)
    item_3 = Column(Integer, nullable=False)
    item_4 = Column(Integer, nullable=False)
    item_5 = Column(Integer, nullable=False)
    backpack_0 = Column(Integer, nullable=False)
    backpack_1 = Column(Integer, nullable=False)
    backpack_2 = Column(Integer, nullable=False)
    item_neutral = Column(Integer, nullable=False)
    item_neutral2 = Column(Integer, nullable=False)
    kills = Column(Integer, nullable=False)
    deaths = Column(Integer, nullable=False)
    assists = Column(Integer, nullable=False)
    kda = Column(Float, nullable=False)  # Вычисляемый
    last_hits = Column(Integer, nullable=False)
    denies = Column(Integer, nullable=False)
    gold_per_min = Column(Integer, nullable=False)
    xp_per_min = Column(Integer, nullable=False)
    level = Column(Integer, nullable=False)
    net_worth = Column(Integer, nullable=False)
    aghanims_scepter = Column(Integer, nullable=False)
    aghanims_shard = Column(Integer, nullable=False)
    moonshard = Column(Integer, nullable=False)

    match = relationship("Match", back_populates="players")
