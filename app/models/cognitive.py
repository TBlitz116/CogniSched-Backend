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


class ProfessorCognitiveScore(Base):
    """Tracks the professor's daily cognitive load based on calendar blocks."""
    __tablename__ = "professor_cognitive_scores"

    id = Column(Integer, primary_key=True, index=True)
    professor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False)
    score = Column(Float, default=0.0)          # 0–100
    block_count = Column(Integer, default=0)    # number of blocks that day
    blocked_hours = Column(Float, default=0.0)  # total hours blocked
    created_at = Column(DateTime, default=datetime.utcnow)
