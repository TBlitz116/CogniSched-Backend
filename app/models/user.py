import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Enum, Text
from app.core.database import Base


class UserRole(str, enum.Enum):
    PROFESSOR = "PROFESSOR"
    TA = "TA"
    STUDENT = "STUDENT"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    role = Column(Enum(UserRole), nullable=False)
    timezone = Column(String, default="UTC")
    google_refresh_token = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
