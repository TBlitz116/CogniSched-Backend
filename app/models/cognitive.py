import enum
from datetime import datetime, date
from sqlalchemy import Column, Integer, Date, Float, Enum, ForeignKey, DateTime
from app.core.database import Base


class BurnoutRisk(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class CognitiveScore(Base):
    __tablename__ = "cognitive_scores"

    id = Column(Integer, primary_key=True, index=True)
    ta_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False)
    score = Column(Float, default=0.0)
    burnout_risk = Column(Enum(BurnoutRisk), default=BurnoutRisk.LOW)
    meeting_count = Column(Integer, default=0)
    total_gap_minutes = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
