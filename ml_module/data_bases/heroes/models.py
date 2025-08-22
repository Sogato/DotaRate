from sqlalchemy import Column, Integer, String, Enum
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class Hero(Base):
    __tablename__ = 'heroes'

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, unique=True)
    localized_name = Column(String(255), nullable=False)
    primary_attr = Column(
        Enum("agi", "str", "int", "all", name="primary_attr_enum"),
        nullable=False
    )
    attack_type = Column(
        Enum("Melee", "Ranged", name="attack_type_enum"),
        nullable=False
    )
    roles = Column(
        Enum(
            "Carry", "Escape", "Nuker", "Support",
            "Disabler", "Initiator", "Durable", "Pusher",
            name="role_enum"
        ),
        nullable=False
    )